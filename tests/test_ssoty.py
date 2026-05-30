"""Deterministic tests for ssoty. No network, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

from ssoty.checks import CheckContext, run_checks
from ssoty.cli import build, main
from ssoty.ignore import SsotyIgnore
from ssoty.metrics import compute_context_tax
from ssoty.models import ALWAYS_ON, CONDITIONAL, SKILL_GATED, HarnessSurface, RuleDoc, Severity
from ssoty.redact import redact
from ssoty.resolver import referenced_docs, resolve_all
from ssoty.tokens import count_tokens

REPO = Path(__file__).resolve().parents[1]
MESSY = REPO / "examples" / "messy-setup"
CLEAN = REPO / "examples" / "clean-setup"


# --- resolver: cross-reference parsing ---


def test_referenced_docs_picks_links_and_backticks():
    text = "See `team-rules.md` and [layout](meta-layout.md)."
    assert referenced_docs(text) == {"team-rules.md", "meta-layout.md"}


def test_referenced_docs_ignores_fenced_code():
    text = "```\nrun `fake.md` here\n```\nreal `real.md`"
    assert referenced_docs(text) == {"real.md"}


def test_referenced_docs_strips_paths():
    assert referenced_docs("[x](../common/team-rules.md)") == {"team-rules.md"}


def test_referenced_docs_ignores_non_md():
    assert referenced_docs("`script.py` and `notes.txt`") == set()


def test_referenced_docs_ignores_placeholders_and_globs():
    # prose placeholders / globs must NOT be treated as real references
    text = "use `<topic>.md`, `*.md`, `<file>.md`, [new](<new>.md), and real `team-rules.md`"
    assert referenced_docs(text) == {"team-rules.md"}


def test_dangling_not_found_is_fyi_not_warning():
    doc = RuleDoc(
        harness="claude-code",
        name="a.md",
        path=Path("a.md"),
        load_basis=ALWAYS_ON,
        text="see `external-project-doc.md` for details",
    )
    ctx = CheckContext(
        surfaces={"claude-code": HarnessSurface(harness="claude-code", docs=[doc])},
        ignore=SsotyIgnore(),
    )
    dangling = [f for f in run_checks(ctx) if f.check == "dangling_cross_ref"]
    assert dangling and all(f.severity is Severity.FYI for f in dangling)


# --- resolver: fixture resolution ---


def test_resolve_messy_surfaces():
    surfaces = resolve_all(MESSY)
    assert set(surfaces) == {"claude-code", "codex"}
    claude = surfaces["claude-code"]
    assert "team-rules.md" in claude.names
    assert claude.by_name("shared-style.md").load_basis == ALWAYS_ON
    codex = surfaces["codex"]
    assert codex.by_name("shared-style.md").load_basis == SKILL_GATED


def test_resolve_detects_broken_symlink():
    claude = resolve_all(MESSY)["claude-code"]
    broken = claude.by_name("broken-link.md")
    assert broken is not None and broken.broken is True


def test_resolve_broken_symlink_unit(tmp_path: Path):
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    link = tmp_path / ".claude" / "rules" / "x.md"
    link.symlink_to("does-not-exist.md")
    doc = resolve_all(tmp_path)["claude-code"].by_name("x.md")
    assert doc.broken is True


# --- tokens ---


def test_count_tokens_deterministic():
    a = count_tokens("hello world")
    b = count_tokens("hello world")
    assert a.tokens == b.tokens and a.tokens > 0


def test_count_tokens_heuristic_when_no_tiktoken(monkeypatch):
    import ssoty.tokens as tok

    monkeypatch.setattr(tok, "_tiktoken_count", lambda _t: None)
    tc = tok.count_tokens("a" * 8)
    assert tc.approx is True and tc.tokens == 2


# --- redact ---


def test_redact_masks_home_and_email():
    out = redact("path /home/dev/x mail dev@example.com", home="/home/dev")
    assert "/home/dev" not in out and "dev@example.com" not in out
    assert "$HOME" in out and "<redacted-email>" in out


# --- ignore ---


def test_ssotyignore_loads_and_declares():
    ig = SsotyIgnore.load(MESSY)
    assert ig.declares("meta-layout.md") and not ig.declares("nope.md")


# --- checks: golden on fixtures ---


def _audit(root: Path):
    surfaces = resolve_all(root)
    ctx = CheckContext(surfaces=surfaces, ignore=SsotyIgnore.load(root), root=root)
    return run_checks(ctx)


def _by_check(findings, name):
    return [f for f in findings if f.check == name]


def test_messy_has_two_criticals():
    findings = _audit(MESSY)
    crit = [f for f in findings if f.severity is Severity.CRITICAL]
    assert len(crit) == 2
    assert {f.check for f in crit} == {"broken_symlink", "dangling_cross_ref"}


def test_messy_dangling_distinguishes_intent():
    dangling = _by_check(_audit(MESSY), "dangling_cross_ref")
    crit = [f for f in dangling if f.severity is Severity.CRITICAL]
    fyi = [f for f in dangling if f.severity is Severity.FYI]
    assert any("team-rules.md" in f.message for f in crit)
    assert any("meta-layout.md" in f.message for f in fyi)  # intent-suppressed


def test_messy_load_asymmetry_and_duplicate():
    findings = _audit(MESSY)
    assert _by_check(findings, "load_asymmetry")
    assert _by_check(findings, "duplicate_content")


def test_clean_has_no_criticals():
    crit = [f for f in _audit(CLEAN) if f.severity is Severity.CRITICAL]
    assert crit == []


# --- metrics ---


def test_context_tax_separates_load_basis():
    tax = compute_context_tax(resolve_all(MESSY))
    assert tax["codex"].skill_gated_tokens > 0
    assert tax["claude-code"].always_on_tokens > 0


def test_clean_reduces_within_harness_tax():
    messy = compute_context_tax(resolve_all(MESSY))
    clean = compute_context_tax(resolve_all(CLEAN))
    # deduping + removing the broken doc lowers Claude's always-on surface
    assert clean["claude-code"].always_on_tokens < messy["claude-code"].always_on_tokens
    # codex no longer carries a skill-gated references copy
    assert clean["codex"].skill_gated_tokens < messy["codex"].skill_gated_tokens


def test_no_cross_harness_sum_helper():
    tax = compute_context_tax(resolve_all(MESSY))["codex"]
    assert tax.max_surface_tokens == tax.always_on_tokens + tax.skill_gated_tokens


# --- cli ---


def test_cli_audit_returns_zero_without_ci(capsys):
    assert main(["audit", str(MESSY)]) == 0
    assert "Critical" in capsys.readouterr().out


def test_cli_ci_exit_nonzero_on_critical():
    assert main(["audit", str(MESSY), "--ci"]) == 1
    assert main(["audit", str(CLEAN), "--ci"]) == 0


def test_cli_metrics_json_parses(capsys):
    assert main(["metrics", str(MESSY), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "context_tax" in payload and "claude-code" in payload["context_tax"]


def test_cli_audit_json_has_findings(capsys):
    main(["audit", str(MESSY), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["Critical"] == 2
    assert len(payload["findings"]) > 0


def test_cli_audit_sarif_parses(capsys):
    from ssoty import __version__
    from ssoty.checks import ALL_CHECKS

    assert main(["audit", str(MESSY), "--format", "sarif"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["$schema"].endswith("sarif-schema-2.1.0.json")
    assert payload["version"] == "2.1.0"
    driver = payload["runs"][0]["tool"]["driver"]
    assert driver["name"] == "ssoty"
    assert driver["version"] == __version__
    assert len(driver["rules"]) == len(ALL_CHECKS)
    results = payload["runs"][0]["results"]
    # a known Critical finding (broken_symlink/dangling) surfaces as level 'error'
    assert any(r["level"] == "error" for r in results)


def test_cli_audit_json_alias_keeps_legacy_shape(capsys):
    # --json must still emit the legacy render_json shape (0.1.x back-compat)
    main(["audit", str(MESSY), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "summary" in payload and "findings" in payload and "context_tax" in payload
    assert payload["summary"]["Critical"] == 2


def test_build_returns_result_and_tax():
    result, tax = build(MESSY)
    assert result.has_blocking() is True
    assert set(tax) == {"claude-code", "codex"}


def test_load_asymmetry_suppressed_by_ssotyignore():
    a = RuleDoc(harness="claude-code", name="x.md", path=Path("a/x.md"), load_basis=ALWAYS_ON, text="t")
    b = RuleDoc(harness="codex", name="x.md", path=Path("b/x.md"), load_basis=SKILL_GATED, text="t")
    surfaces = {
        "claude-code": HarnessSurface("claude-code", [a]),
        "codex": HarnessSurface("codex", [b]),
    }
    plain = [
        f for f in run_checks(CheckContext(surfaces=surfaces, ignore=SsotyIgnore())) if f.check == "load_asymmetry"
    ]
    assert plain and all(f.severity is Severity.WARNING for f in plain)
    ignored = [
        f
        for f in run_checks(CheckContext(surfaces=surfaces, ignore=SsotyIgnore(names={"x.md"})))
        if f.check == "load_asymmetry"
    ]
    assert ignored and all(f.severity is Severity.FYI for f in ignored)


def test_cli_resolve_text_lists_load_basis(capsys):
    assert main(["resolve", str(MESSY)]) == 0
    out = capsys.readouterr().out
    assert "always-on" in out and "skill-gated" in out and "shared-style.md" in out


def test_cli_resolve_json_parses(capsys):
    assert main(["resolve", str(MESSY), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "claude-code" in payload and isinstance(payload["claude-code"], list)


def test_resolve_symlinked_directory_is_globbed(tmp_path: Path):
    # C1: a symlinked rules/references dir must be globbed, not collapsed to one doc
    real = tmp_path / "realrefs"
    real.mkdir()
    (real / "x.md").write_text("x", encoding="utf-8")
    (real / "y.md").write_text("y", encoding="utf-8")
    refs = tmp_path / ".codex" / "skills" / "global-agent-rules"
    refs.mkdir(parents=True)
    (refs / "references").symlink_to(real, target_is_directory=True)
    assert resolve_all(tmp_path)["codex"].names == {"x.md", "y.md"}


def test_basename_does_not_shadow_top_level_claude_md(tmp_path: Path):
    # M1: rules/CLAUDE.md must not hide the top-level ~/.claude/CLAUDE.md
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "CLAUDE.md").write_text("rules", encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text("top", encoding="utf-8")
    docs = resolve_all(tmp_path)["claude-code"].docs
    paths = {str(d.path) for d in docs}
    assert any(p.endswith("/.claude/CLAUDE.md") for p in paths)
    assert sum(1 for d in docs if d.name == "CLAUDE.md") == 2


def test_within_doc_duplicate_is_warning():
    # M2: the same block twice inside one always-on doc is token rent (Warning)
    block = "acme rule " * 30  # > 200 chars after strip
    doc = RuleDoc(
        harness="claude-code",
        name="a.md",
        path=Path("a.md"),
        load_basis=ALWAYS_ON,
        text=block + "\n\n" + block,
    )
    ctx = CheckContext(surfaces={"claude-code": HarnessSurface("claude-code", [doc])}, ignore=SsotyIgnore())
    dup = [f for f in run_checks(ctx) if f.check == "duplicate_content"]
    assert dup and any(f.severity is Severity.WARNING for f in dup)


def test_redact_handles_trailing_slash_home():
    # m1: a home with a trailing slash must not eat the path separator
    assert redact("/home/dev/x", home="/home/dev/") == "$HOME/x"


def test_referenced_docs_handles_anchor_title_uppercase():
    # m2: anchors, link titles, and uppercase extensions are real references
    text = '[a](foo.md#sec) [b](bar.md "title") and `BAZ.MD`'
    assert referenced_docs(text) == {"foo.md", "bar.md", "BAZ.MD"}


def test_cursor_mdc_load_basis(tmp_path: Path):
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.mdc").write_text("---\nalwaysApply: true\n---\nbody", encoding="utf-8")
    (rules / "auto.mdc").write_text("---\nglobs: '*.py'\nalwaysApply: false\n---\nbody", encoding="utf-8")
    (tmp_path / ".cursorrules").write_text("legacy", encoding="utf-8")
    cur = resolve_all(tmp_path)["cursor"]
    assert cur.by_name("always.mdc").load_basis == ALWAYS_ON
    assert cur.by_name("auto.mdc").load_basis == CONDITIONAL
    assert cur.by_name(".cursorrules").load_basis == ALWAYS_ON


def test_cursor_mdc_always_apply_with_inline_comment(tmp_path: Path):
    # regression: `alwaysApply: true # primary rule` is valid YAML and must resolve
    # to ALWAYS_ON; an unquoted trailing comment was previously not stripped.
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "commented.mdc").write_text("---\nalwaysApply: true # primary rule\n---\nbody", encoding="utf-8")
    cur = resolve_all(tmp_path)["cursor"]
    assert cur.by_name("commented.mdc").load_basis == ALWAYS_ON


def test_cursor_mdc_false_with_inline_comment_stays_conditional(tmp_path: Path):
    # negative: comment-stripping must not flip a commented false to true
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "off.mdc").write_text("---\nalwaysApply: false # off\n---\nbody", encoding="utf-8")
    cur = resolve_all(tmp_path)["cursor"]
    assert cur.by_name("off.mdc").load_basis == CONDITIONAL


def test_cursor_mdc_quoted_true_value(tmp_path: Path):
    # quoted value is also handled (quote-agnostic for the common case)
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "quoted.mdc").write_text('---\nalwaysApply: "true"\n---\nbody', encoding="utf-8")
    cur = resolve_all(tmp_path)["cursor"]
    assert cur.by_name("quoted.mdc").load_basis == ALWAYS_ON


def test_copilot_resolved(tmp_path: Path):
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("rules", encoding="utf-8")
    assert resolve_all(tmp_path)["copilot"].by_name("copilot-instructions.md").load_basis == ALWAYS_ON


def test_gemini_hierarchical_resolved(tmp_path: Path):
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "GEMINI.md").write_text("global", encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text("project", encoding="utf-8")
    gem = resolve_all(tmp_path)["gemini"]
    assert len(gem.docs) == 2  # global + project, both GEMINI.md (path-deduped, not name)
    assert all(d.load_basis == ALWAYS_ON for d in gem.docs)


def test_cline_dir_resolved(tmp_path: Path):
    rules = tmp_path / ".clinerules"
    rules.mkdir()
    (rules / "style.md").write_text("synthetic cline rule for /home/dev", encoding="utf-8")
    cline = resolve_all(tmp_path)["cline"]
    assert cline.by_name("style.md").load_basis == ALWAYS_ON


def test_cline_legacy_single_file_resolved(tmp_path: Path):
    (tmp_path / ".clinerules").write_text("synthetic legacy cline rule", encoding="utf-8")
    cline = resolve_all(tmp_path)["cline"]
    assert cline.by_name(".clinerules").load_basis == ALWAYS_ON


def test_cline_agents_md_resolved(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("synthetic AGENTS.md rule", encoding="utf-8")
    cline = resolve_all(tmp_path)["cline"]
    assert cline.by_name("AGENTS.md").load_basis == ALWAYS_ON


def test_empty_harnesses_are_dropped(tmp_path: Path):
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "a.md").write_text("x", encoding="utf-8")
    surfaces = resolve_all(tmp_path)
    assert set(surfaces) == {"claude-code"}  # no cursor/copilot/codex present here


def test_robust_on_nonexistent_root(tmp_path: Path, capsys):
    # an empty root (no .claude/.codex) must not crash; just empty surfaces
    empty = tmp_path / "nothing"
    assert main(["audit", str(empty)]) == 0
    assert main(["resolve", str(empty)]) == 0
    result, tax = build(empty)
    assert result.findings == []


# --- diff: cross-model rule divergence ---


def _diverging_root(tmp_path: Path) -> Path:
    """Two harnesses with: only-in-A, only-in-B, a shared rule with different
    load_basis, and a cross-boundary broken ref. claude-code (always-on) vs codex
    (skill-gated references)."""
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    # shared rule (claude=always-on) + a claude-only rule the ref points at
    (claude_rules / "shared-style.md").write_text("shared rule body", encoding="utf-8")
    (claude_rules / "team-rules.md").write_text("synthetic team rule for /home/dev", encoding="utf-8")
    # codex: same shared-style.md (skill-gated) that references a claude-only rule,
    # plus a codex-only rule
    refs = tmp_path / ".codex" / "skills" / "global-agent-rules" / "references"
    refs.mkdir(parents=True)
    (refs / "shared-style.md").write_text("see `team-rules.md` for details", encoding="utf-8")
    (refs / "codex-only.md").write_text("synthetic codex-only rule", encoding="utf-8")
    return tmp_path


def test_diff_pair_surfaces_every_category(tmp_path: Path):
    from ssoty.diff import diff_pair

    surfaces = resolve_all(_diverging_root(tmp_path))
    d = diff_pair(surfaces["claude-code"], surfaces["codex"])
    assert d.a == "claude-code" and d.b == "codex"
    assert "team-rules.md" in d.only_in_a
    assert "codex-only.md" in d.only_in_b
    assert "shared-style.md" in d.shared
    assert any(ld.name == "shared-style.md" for ld in d.different_load)
    ld = next(ld for ld in d.different_load if ld.name == "shared-style.md")
    assert ld.a_basis == ALWAYS_ON and ld.b_basis == SKILL_GATED
    # the codex doc references team-rules.md which loads only in claude-code
    assert any(
        r.src_harness == "codex"
        and r.src_doc == "shared-style.md"
        and r.ref == "team-rules.md"
        and r.present_in == "claude-code"
        for r in d.broken_cross_refs
    )
    assert d.coherent is False


def test_diff_coherent_when_identical(tmp_path: Path):
    from ssoty.diff import diff_pair

    # two harnesses, one identically-named always-on rule, no cross-refs
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "copilot-instructions.md").write_text("body", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("body", encoding="utf-8")
    surfaces = resolve_all(tmp_path)
    d = diff_pair(surfaces["claude-code"], surfaces["copilot"])
    assert d.coherent is True
    assert d.only_in_a == () and d.only_in_b == () and d.different_load == () and d.broken_cross_refs == ()


def test_cli_diff_text_surfaces_each_category(tmp_path: Path, capsys):
    assert main(["diff", str(_diverging_root(tmp_path)), "--a", "claude-code", "--b", "codex"]) == 0
    out = capsys.readouterr().out
    assert "claude-code  vs  codex" in out
    assert "only in claude-code" in out and "team-rules.md" in out
    assert "only in codex" in out and "codex-only.md" in out
    assert "same rule, different load" in out
    assert "shared-style.md  claude-code=always-on  |  codex=skill-gated" in out
    assert "broken cross-references across the boundary" in out
    assert "codex:shared-style.md -> 'team-rules.md'" in out
    assert "do NOT operate under the same rules" in out


def test_cli_diff_json_surfaces_each_category(tmp_path: Path, capsys):
    assert main(["diff", str(_diverging_root(tmp_path)), "--a", "claude-code", "--b", "codex", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and len(payload) == 1
    p = payload[0]
    assert p["a"] == "claude-code" and p["b"] == "codex"
    assert "team-rules.md" in p["only_in_a"]
    assert "codex-only.md" in p["only_in_b"]
    assert "shared-style.md" in p["shared"]
    assert {"name": "shared-style.md", "a_basis": "always-on", "b_basis": "skill-gated"} in p["different_load"]
    assert {
        "src_harness": "codex",
        "src_doc": "shared-style.md",
        "ref": "team-rules.md",
        "present_in": "claude-code",
    } in p["broken_cross_refs"]
    assert p["coherent"] is False
    assert "do NOT operate under the same rules" in p["verdict"]


def test_cli_diff_all_pairs_when_unspecified(tmp_path: Path, capsys):
    # three present harnesses -> C(3,2) = 3 pairs, each unordered pair once, sorted
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("y", encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text("z", encoding="utf-8")
    assert main(["diff", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    pairs = [(p["a"], p["b"]) for p in payload]
    assert pairs == [("claude-code", "copilot"), ("claude-code", "gemini"), ("copilot", "gemini")]


def test_cli_diff_unknown_harness_is_usage_error(tmp_path: Path, capsys):
    _diverging_root(tmp_path)
    assert main(["diff", str(tmp_path), "--a", "claude-code", "--b", "nope"]) == 2
    err = capsys.readouterr().err
    assert "harness not present: nope" in err


def test_cli_diff_half_specified_pair_is_usage_error(tmp_path: Path, capsys):
    _diverging_root(tmp_path)
    assert main(["diff", str(tmp_path), "--a", "claude-code"]) == 2
    assert "--a and --b must be given together" in capsys.readouterr().err


def test_cli_diff_writes_nothing(tmp_path: Path, capsys):
    root = _diverging_root(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert main(["diff", str(root)]) == 0
    assert main(["diff", str(root), "--a", "claude-code", "--b", "codex", "--json"]) == 0
    capsys.readouterr()
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert before == after
    assert not (root / ".ssoty-backup").exists()


# --- fix: dry-run-first, backup-first, idempotent remediation ---


def _make_broken_symlink(root: Path, name: str = "x.md", target: str = "does-not-exist.md") -> Path:
    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    link = rules / name
    link.symlink_to(target)
    return link


def test_fix_dry_run_changes_nothing(tmp_path: Path, capsys):
    link = _make_broken_symlink(tmp_path)
    # default (no --apply) is dry-run
    assert main(["fix", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "WOULD remove broken symlink" in out
    # nothing mutated, no backup dir created
    assert link.is_symlink() and not link.exists()
    assert not (tmp_path / ".ssoty-backup").exists()


def test_fix_apply_removes_broken_symlink_and_backs_up(tmp_path: Path, capsys):
    link = _make_broken_symlink(tmp_path, target="./nope.md")
    assert main(["fix", str(tmp_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "backup written to:" in out
    assert "removed broken symlink" in out
    # the dangling symlink is gone
    assert not link.is_symlink()
    # a backup of the link node exists, preserving the dead target string
    backups = list((tmp_path / ".ssoty-backup").glob("*/.claude/rules/x.md"))
    assert len(backups) == 1
    backup = backups[0]
    assert backup.is_symlink()
    import os

    assert os.readlink(backup) == "./nope.md"


def test_fix_apply_is_idempotent(tmp_path: Path, capsys):
    _make_broken_symlink(tmp_path)
    assert main(["fix", str(tmp_path), "--apply"]) == 0
    capsys.readouterr()
    backups_after_first = sorted((tmp_path / ".ssoty-backup").glob("*"))
    assert len(backups_after_first) == 1
    # second apply: nothing to do, creates no new backup dir, writes nothing
    assert main(["fix", str(tmp_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out
    assert sorted((tmp_path / ".ssoty-backup").glob("*")) == backups_after_first


def test_fix_never_touches_valid_symlink_or_real_file(tmp_path: Path, capsys):
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    # a real rule file
    real = rules / "real.md"
    real.write_text("synthetic rule for /home/dev", encoding="utf-8")
    # a VALID symlink (target resolves)
    target = rules / "target.md"
    target.write_text("synthetic target", encoding="utf-8")
    valid_link = rules / "valid.md"
    valid_link.symlink_to("target.md")
    # plus one broken symlink so there IS work
    _make_broken_symlink(tmp_path, name="dead.md")

    assert main(["fix", str(tmp_path), "--apply"]) == 0
    capsys.readouterr()
    # real file + valid symlink survive untouched
    assert real.is_file() and real.read_text(encoding="utf-8") == "synthetic rule for /home/dev"
    assert valid_link.is_symlink() and valid_link.exists()
    # only the broken one was removed
    assert not (rules / "dead.md").is_symlink()


def test_fix_scaffold_ignore_appends_non_shared(tmp_path: Path, capsys):
    # two harnesses present; a rule only in claude-code is a non_shared_surface FYI
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "solo.md").write_text("synthetic claude-only rule", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("synthetic copilot rule", encoding="utf-8")

    # without --scaffold-ignore, no .ssotyignore append
    assert main(["fix", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "WOULD append to .ssotyignore" not in out

    # dry-run with --scaffold-ignore shows the WOULD line, writes nothing
    assert main(["fix", str(tmp_path), "--scaffold-ignore"]) == 0
    out = capsys.readouterr().out
    assert "WOULD append to .ssotyignore: solo.md" in out
    assert not (tmp_path / ".ssotyignore").exists()

    # apply with --scaffold-ignore creates .ssotyignore with the name
    assert main(["fix", str(tmp_path), "--apply", "--scaffold-ignore"]) == 0
    capsys.readouterr()
    ig = SsotyIgnore.load(tmp_path)
    assert ig.declares("solo.md")
    assert ig.declares("copilot-instructions.md")


def test_fix_scaffold_ignore_skips_already_declared(tmp_path: Path, capsys):
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "solo.md").write_text("synthetic claude-only rule", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("synthetic copilot rule", encoding="utf-8")
    # pre-declare solo.md
    (tmp_path / ".ssotyignore").write_text("solo.md\n", encoding="utf-8")

    assert main(["fix", str(tmp_path), "--scaffold-ignore"]) == 0
    out = capsys.readouterr().out
    # solo.md is already declared -> not offered again; copilot one still offered
    assert "WOULD append to .ssotyignore: solo.md" not in out
    assert "WOULD append to .ssotyignore: copilot-instructions.md" in out


def test_fix_apply_empty_plan_creates_no_backup(tmp_path: Path, capsys):
    # a clean root with no broken symlinks: --apply creates no backup dir
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "a.md").write_text("synthetic rule", encoding="utf-8")
    assert main(["fix", str(tmp_path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out
    assert not (tmp_path / ".ssoty-backup").exists()


def test_fix_redact_masks_home_in_output(tmp_path: Path, capsys):
    _make_broken_symlink(tmp_path)
    home = str(tmp_path)
    monkey_home = home  # the fixture path stands in for $HOME via --redact
    import ssoty.redact as r

    # point redact's home at the tmp root so the printed path is masked
    orig = r.os.path.expanduser
    r.os.path.expanduser = lambda p: monkey_home if p == "~" else orig(p)
    try:
        assert main(["fix", str(tmp_path), "--redact"]) == 0
        out = capsys.readouterr().out
    finally:
        r.os.path.expanduser = orig
    assert home not in out
    assert "$HOME" in out

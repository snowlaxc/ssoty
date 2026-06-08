"""Deterministic tests for ssoty. No network, no LLM."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

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


def test_messy_has_one_structural_critical():
    # dangling_cross_ref no longer emits Critical (0.1.9); the only structural Critical
    # is broken_symlink. The team-rules.md cross-ref is now Warning (real divergence).
    findings = _audit(MESSY)
    crit = [f for f in findings if f.severity is Severity.CRITICAL]
    assert len(crit) == 1
    assert {f.check for f in crit} == {"broken_symlink"}


def test_messy_dangling_distinguishes_intent():
    dangling = _by_check(_audit(MESSY), "dangling_cross_ref")
    warn = [f for f in dangling if f.severity is Severity.WARNING]
    fyi = [f for f in dangling if f.severity is Severity.FYI]
    # a genuine cross-harness divergence is now Warning (was Critical), not suppressed
    assert any("team-rules.md" in f.message for f in warn)
    assert not any(f.severity is Severity.CRITICAL for f in dangling)
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
    assert payload["summary"]["Critical"] == 1  # only broken_symlink is structural Critical
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
    assert payload["summary"]["Critical"] == 1


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
    # a backup of the link node exists, preserving the dead target string.
    # Use iterdir + is_symlink (lstat) — Path.glob skips broken symlink leaves
    # before Python 3.13, so globbing the dangling backup link would miss it.
    ts_dirs = [p for p in (tmp_path / ".ssoty-backup").iterdir() if p.is_dir()]
    assert len(ts_dirs) == 1
    backup = ts_dirs[0] / ".claude" / "rules" / "x.md"
    assert backup.is_symlink()
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
    # two harnesses present; non-entrypoint rules only in one harness are non_shared_surface
    # FYIs and scaffold-able. The copilot entrypoint (copilot-instructions.md) is NOT a
    # non_shared_surface finding (per-harness entrypoint by design) so it is NOT scaffolded.
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "solo.md").write_text("synthetic claude-only rule", encoding="utf-8")
    (claude_rules / "extra.md").write_text("synthetic claude-only extra rule", encoding="utf-8")
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
    # entrypoint is skipped (not a non_shared_surface finding)
    assert "WOULD append to .ssotyignore: copilot-instructions.md" not in out
    assert not (tmp_path / ".ssotyignore").exists()

    # apply with --scaffold-ignore creates .ssotyignore with the non-entrypoint names
    assert main(["fix", str(tmp_path), "--apply", "--scaffold-ignore"]) == 0
    capsys.readouterr()
    ig = SsotyIgnore.load(tmp_path)
    assert ig.declares("solo.md")
    assert ig.declares("extra.md")
    # the per-harness entrypoint is never scaffolded as non-shared
    assert not ig.declares("copilot-instructions.md")


def test_fix_scaffold_ignore_skips_already_declared(tmp_path: Path, capsys):
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "solo.md").write_text("synthetic claude-only rule", encoding="utf-8")
    (claude_rules / "extra.md").write_text("synthetic claude-only extra rule", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("synthetic copilot rule", encoding="utf-8")
    # pre-declare solo.md
    (tmp_path / ".ssotyignore").write_text("solo.md\n", encoding="utf-8")

    assert main(["fix", str(tmp_path), "--scaffold-ignore"]) == 0
    out = capsys.readouterr().out
    # solo.md is already declared -> not offered again; the other non-shared rule still offered
    assert "WOULD append to .ssotyignore: solo.md" not in out
    assert "WOULD append to .ssotyignore: extra.md" in out


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


# --- harnesses: windsurf + continue ---


def test_windsurf_dir_and_legacy_resolved(tmp_path: Path):
    rules = tmp_path / ".windsurf" / "rules"
    rules.mkdir(parents=True)
    (rules / "style.md").write_text("synthetic windsurf rule for /home/dev", encoding="utf-8")
    (tmp_path / ".windsurfrules").write_text("synthetic legacy windsurf rule", encoding="utf-8")
    ws = resolve_all(tmp_path)["windsurf"]
    # directory rules are conditional (Cascade activation modes)
    assert ws.by_name("style.md").load_basis == CONDITIONAL
    # legacy single file is always-on
    assert ws.by_name(".windsurfrules").load_basis == ALWAYS_ON


def test_continue_dir_resolved(tmp_path: Path):
    rules = tmp_path / ".continue" / "rules"
    rules.mkdir(parents=True)
    (rules / "rules.md").write_text("synthetic continue rule for /home/dev", encoding="utf-8")
    cont = resolve_all(tmp_path)["continue"]
    assert cont.by_name("rules.md").load_basis == CONDITIONAL


# --- checks: weak_directive (FYI, never blocking) ---


def _weak(findings):
    return [f for f in findings if f.check == "weak_directive"]


def test_weak_directive_flags_modal_with_hard_signal():
    doc = RuleDoc(
        harness="claude-code",
        name="a.md",
        path=Path("a.md"),
        load_basis=ALWAYS_ON,
        text="You should never log a secret in production.",
    )
    ctx = CheckContext(surfaces={"claude-code": HarnessSurface("claude-code", [doc])}, ignore=SsotyIgnore())
    found = _weak(run_checks(ctx))
    assert found and all(f.severity is Severity.FYI for f in found)
    assert not any(f.severity is Severity.CRITICAL for f in found)


def test_weak_directive_no_false_positive_on_normal_prose():
    # standalone `should`, a hard signal alone, code fence, table row, blockquote,
    # and an anti-rationalization example must NOT be flagged.
    text = (
        "You should write tests for new behavior.\n"
        "Secrets must be stored in a secret manager.\n"
        "Run `should never` examples in code:\n"
        "```\nshould never log a secret\n```\n"
        "| should | never | example row |\n"
        "> should never — quoted example\n"
        '| "try to" hedge a security rule | Rationalization | counter |\n'
    )
    doc = RuleDoc(
        harness="claude-code",
        name="b.md",
        path=Path("b.md"),
        load_basis=ALWAYS_ON,
        text=text,
    )
    ctx = CheckContext(surfaces={"claude-code": HarnessSurface("claude-code", [doc])}, ignore=SsotyIgnore())
    assert _weak(run_checks(ctx)) == []


def test_weak_directive_only_scans_always_on():
    # the same hedged line in a conditional/skill-gated doc is NOT flagged
    doc = RuleDoc(
        harness="cursor",
        name="c.mdc",
        path=Path("c.mdc"),
        load_basis=CONDITIONAL,
        text="You should never force push to a shared branch.",
    )
    ctx = CheckContext(surfaces={"cursor": HarnessSurface("cursor", [doc])}, ignore=SsotyIgnore())
    assert _weak(run_checks(ctx)) == []


def test_weak_directive_word_boundary_no_substring_false_positive():
    # hard-signal SUBSTRINGS inside ordinary words must NOT match:
    # 'prod' in product/reproduce/productivity, 'must' in mustard, 'secret' in secretary
    text = (
        "You should reproduce the product to improve productivity.\n"
        "Try to produce better output where possible.\n"
        "The secretary should file reports.\n"
        "You should add mustard if possible.\n"
    )
    doc = RuleDoc(
        harness="claude-code",
        name="d.md",
        path=Path("d.md"),
        load_basis=ALWAYS_ON,
        text=text,
    )
    ctx = CheckContext(surfaces={"claude-code": HarnessSurface("claude-code", [doc])}, ignore=SsotyIgnore())
    assert _weak(run_checks(ctx)) == []


# --- 0.1.9: precision fixes for cross-harness dangling false positives ---


def test_referenced_docs_drops_frontmatter_provenance():
    # a `source:` provenance path in YAML frontmatter is metadata, not a pointer
    text = (
        "---\n"
        "source: ~/.codex/AGENTS.md\n"
        "absorbed_at: 2026-01-01\n"
        "---\n"
        "Body text — see `team-defaults.md` for the real pointer.\n"
    )
    refs = referenced_docs(text)
    assert "AGENTS.md" not in refs  # provenance, dropped
    assert refs == {"team-defaults.md"}  # genuine prose pointer still extracts


def test_referenced_docs_keeps_horizontal_rule_section_body():
    # a leading `---` that is a horizontal rule (NOT YAML frontmatter, no key: line)
    # must NOT be stripped — a real pointer in that section still extracts
    text = "---\nIntro section, see `root-cause-first.md` first.\n---\nmore"
    assert "root-cause-first.md" in referenced_docs(text)


def test_referenced_docs_drops_allowlist_entrypoint_mentions():
    # an entrypoint filename embedded in a glob/path allowlist list is a permission
    # mention, not a pointer; a lone .md backtick in prose is still a pointer
    text = (
        "Direct writes OK for: `~/.claude/**`, `.omc/**`, `CLAUDE.md`, `AGENTS.md`.\n"
        "But always consult (`root-cause-first.md` first) before editing.\n"
    )
    refs = referenced_docs(text)
    assert "CLAUDE.md" not in refs and "AGENTS.md" not in refs  # allowlist mentions
    assert refs == {"root-cause-first.md"}  # lone prose pointer survives


def test_referenced_docs_lone_entrypoint_pointer_survives():
    # a lone entrypoint backtick in prose (no glob/path sibling on the line) is a pointer
    assert referenced_docs("See `CLAUDE.md` for the project contract.") == {"CLAUDE.md"}


def test_referenced_docs_md_link_in_allowlist_line_untouched():
    # markdown-link refs are always genuine pointers, even on an allowlist-style line
    text = "Allowlist `~/.claude/**`, `.omc/**` but [layout](meta-layout.md) is canonical."
    assert "meta-layout.md" in referenced_docs(text)


def test_dangling_allowlist_mention_produces_no_finding():
    # regression: an allowlist line must not produce a dangling_cross_ref at all
    claude = RuleDoc(
        harness="claude-code",
        name="a.md",
        path=Path("a/a.md"),
        load_basis=ALWAYS_ON,
        text="Direct writes OK for: `~/.claude/**`, `CLAUDE.md`, `AGENTS.md`.",
    )
    codex = RuleDoc(
        harness="codex",
        name="AGENTS.md",
        path=Path("b/AGENTS.md"),
        load_basis=ALWAYS_ON,
        text="codex contract",
    )
    ctx = CheckContext(
        surfaces={
            "claude-code": HarnessSurface("claude-code", [claude]),
            "codex": HarnessSurface("codex", [codex]),
        },
        ignore=SsotyIgnore(),
    )
    dangling = [f for f in run_checks(ctx) if f.check == "dangling_cross_ref"]
    assert dangling == []  # allowlist mention of AGENTS.md was not treated as a ref


def test_dangling_frontmatter_source_produces_no_finding():
    # regression: a frontmatter `source:` path must not produce a dangling_cross_ref
    claude = RuleDoc(
        harness="claude-code",
        name="rule.md",
        path=Path("a/rule.md"),
        load_basis=ALWAYS_ON,
        text="---\nsource: ~/.codex/AGENTS.md\n---\nBody with no pointers.",
    )
    codex = RuleDoc(
        harness="codex",
        name="AGENTS.md",
        path=Path("b/AGENTS.md"),
        load_basis=ALWAYS_ON,
        text="codex",
    )
    ctx = CheckContext(
        surfaces={
            "claude-code": HarnessSurface("claude-code", [claude]),
            "codex": HarnessSurface("codex", [codex]),
        },
        ignore=SsotyIgnore(),
    )
    assert [f for f in run_checks(ctx) if f.check == "dangling_cross_ref"] == []


def test_dangling_entrypoint_target_is_fyi_not_warning():
    # a ref TO an entrypoint present in another harness is FYI (per-harness by design)
    claude = RuleDoc(
        harness="claude-code",
        name="rule.md",
        path=Path("a/rule.md"),
        load_basis=ALWAYS_ON,
        text="See the `AGENTS.md` contract for codex specifics.",
    )
    codex = RuleDoc(
        harness="codex",
        name="AGENTS.md",
        path=Path("b/AGENTS.md"),
        load_basis=ALWAYS_ON,
        text="codex",
    )
    ctx = CheckContext(
        surfaces={
            "claude-code": HarnessSurface("claude-code", [claude]),
            "codex": HarnessSurface("codex", [codex]),
        },
        ignore=SsotyIgnore(),
    )
    dangling = [f for f in run_checks(ctx) if f.check == "dangling_cross_ref"]
    assert dangling and all(f.severity is Severity.FYI for f in dangling)
    assert all(f.severity is not Severity.CRITICAL for f in dangling)


def test_dangling_canonically_shared_symlink_is_fyi_not_critical(tmp_path: Path):
    # a real symlinked canonical file mounted into 2 harnesses: a ref it makes is FYI
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir()
    shared = canonical_dir / "shared-rule.md"
    shared.write_text("see `team-defaults.md` for the routing table", encoding="utf-8")
    # claude-code mounts the canonical file via symlink
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "shared-rule.md").symlink_to(shared)
    (claude_rules / "team-defaults.md").write_text("routing table lives in claude", encoding="utf-8")
    # codex mounts the SAME canonical file via symlink (skill-gated references)
    refs = tmp_path / ".codex" / "skills" / "global-agent-rules" / "references"
    refs.mkdir(parents=True)
    (refs / "shared-rule.md").symlink_to(shared)

    findings = _audit(tmp_path)
    dangling = [f for f in findings if f.check == "dangling_cross_ref"]
    # the codex-side shared-rule.md references team-defaults.md (only in claude), but the
    # referencing doc is canonically shared (same realpath in 2 harnesses) -> FYI, not Critical
    assert dangling
    assert all(f.severity is not Severity.CRITICAL for f in dangling)
    canonical_fyi = [f for f in dangling if "canonically shared" in f.message]
    assert canonical_fyi and all(f.severity is Severity.FYI for f in canonical_fyi)


def test_non_shared_surface_skips_entrypoints(tmp_path: Path):
    # an entrypoint present only in one harness is NOT a non_shared_surface finding;
    # a non-entrypoint rule present only in one harness still is.
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "solo.md").write_text("synthetic claude-only rule", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("copilot rule", encoding="utf-8")
    findings = _audit(tmp_path)
    nss = [f for f in findings if f.check == "non_shared_surface"]
    names = {f.rule_id for f in nss}
    assert "solo.md" in names  # genuine surface asymmetry still reported
    assert "copilot-instructions.md" not in names  # entrypoint skipped


def test_duplicate_content_cross_harness_rolls_up(tmp_path: Path):
    # the same big block mounted once-per-harness across 2 harnesses rolls up into a
    # SINGLE FYI, not one-per-block
    block = "acme shared rule body. " * 20  # > 200 chars
    other = "another acme shared block here. " * 20
    claude_rules = tmp_path / ".claude" / "rules"
    claude_rules.mkdir(parents=True)
    (claude_rules / "a.md").write_text(block + "\n\n" + other, encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(block + "\n\n" + other, encoding="utf-8")
    dup = [f for f in _audit(tmp_path) if f.check == "duplicate_content"]
    cross = [f for f in dup if f.severity is Severity.FYI]
    assert len(cross) == 1  # 2 cross-harness blocks collapsed to 1 rollup FYI
    assert "identical blocks" in cross[0].message and "tokens total" in cross[0].message


def test_guard_genuine_dangling_and_load_asymmetry_still_fire():
    # over-suppression guard: a genuine cross-harness dangling (non-entrypoint target,
    # referencing doc NOT canonically shared, not ignored) still fires as Warning, and a
    # genuine load_asymmetry still fires as Warning.
    a = RuleDoc(
        harness="claude-code",
        name="shared.md",
        path=Path("a/shared.md"),
        load_basis=ALWAYS_ON,
        text="see `team-rules.md` for details",
    )
    team = RuleDoc(
        harness="claude-code",
        name="team-rules.md",
        path=Path("a/team-rules.md"),
        load_basis=ALWAYS_ON,
        text="team rules",
    )
    b = RuleDoc(
        harness="codex",
        name="shared.md",
        path=Path("b/shared.md"),
        load_basis=SKILL_GATED,  # different basis from claude -> load_asymmetry
        text="see `team-rules.md` for details",
    )
    ctx = CheckContext(
        surfaces={
            "claude-code": HarnessSurface("claude-code", [a, team]),
            "codex": HarnessSurface("codex", [b]),
        },
        ignore=SsotyIgnore(),
    )
    findings = run_checks(ctx)
    dangling = [f for f in findings if f.check == "dangling_cross_ref"]
    # codex/shared.md -> team-rules.md (only in claude), distinct realpath, not entrypoint
    assert any(f.severity is Severity.WARNING and "team-rules.md" in f.message for f in dangling)
    asym = [f for f in findings if f.check == "load_asymmetry"]
    assert asym and any(f.severity is Severity.WARNING for f in asym)


def _doc(harness: str, name: str, path: Path, text: str = "", broken: bool = False) -> RuleDoc:
    return RuleDoc(harness=harness, name=name, path=path, load_basis=ALWAYS_ON, text=text, broken=broken)


def _divergence_findings(surfaces: dict) -> list:
    return [
        f for f in run_checks(CheckContext(surfaces=surfaces, ignore=SsotyIgnore())) if f.check == "content_divergence"
    ]


def test_content_divergence_trailing_whitespace_only_no_finding(tmp_path: Path):
    a_path = tmp_path / "a" / "team-defaults.md"
    b_path = tmp_path / "b" / "team-defaults.md"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_text("line one\nline two", encoding="utf-8")
    b_path.write_text("line one   \nline two\n", encoding="utf-8")  # trailing ws + blank
    surfaces = {
        "claude-code": HarnessSurface(
            "claude-code", [_doc("claude-code", "team-defaults.md", a_path, "line one\nline two")]
        ),
        "cursor": HarnessSurface("cursor", [_doc("cursor", "team-defaults.md", b_path, "line one   \nline two\n")]),
    }
    assert _divergence_findings(surfaces) == []
    from ssoty.diff import diff_pair

    d = diff_pair(surfaces["claude-code"], surfaces["cursor"])
    assert d.content_divergence == ()


def test_content_divergence_one_word_drift_is_warning(tmp_path: Path):
    a_path = tmp_path / "a" / "team-defaults.md"
    b_path = tmp_path / "b" / "team-defaults.md"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_text("always prefer composition", encoding="utf-8")
    b_path.write_text("always prefer inheritance", encoding="utf-8")
    surfaces = {
        "claude-code": HarnessSurface(
            "claude-code", [_doc("claude-code", "team-defaults.md", a_path, "always prefer composition")]
        ),
        "cursor": HarnessSurface("cursor", [_doc("cursor", "team-defaults.md", b_path, "always prefer inheritance")]),
    }
    found = _divergence_findings(surfaces)
    assert len(found) == 1
    f = found[0]
    assert f.severity is Severity.WARNING
    assert f.file == "team-defaults.md"
    assert f.harness == "claude-code+cursor"
    assert "claude-code" in f.message and "cursor" in f.message
    from ssoty.diff import diff_pair

    d = diff_pair(surfaces["claude-code"], surfaces["cursor"])
    assert len(d.content_divergence) == 1
    assert d.content_divergence[0].name == "team-defaults.md"
    assert d.coherent is False


def test_content_divergence_symlinked_ssot_no_finding(tmp_path: Path):
    canonical = tmp_path / "canonical" / "team-defaults.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("shared body", encoding="utf-8")
    a_path = tmp_path / "a" / "team-defaults.md"
    b_path = tmp_path / "b" / "team-defaults.md"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.symlink_to(canonical)
    b_path.symlink_to(canonical)
    surfaces = {
        "claude-code": HarnessSurface("claude-code", [_doc("claude-code", "team-defaults.md", a_path, "shared body")]),
        "cursor": HarnessSurface("cursor", [_doc("cursor", "team-defaults.md", b_path, "shared body")]),
    }
    assert _divergence_findings(surfaces) == []
    from ssoty.diff import diff_pair

    d = diff_pair(surfaces["claude-code"], surfaces["cursor"])
    assert d.content_divergence == ()
    assert d.coherent is True


def test_content_divergence_broken_skipped(tmp_path: Path):
    a_path = tmp_path / "a" / "team-defaults.md"
    b_path = tmp_path / "b" / "team-defaults.md"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_text("real body", encoding="utf-8")
    # b is broken: resolver would set text="" — must not be compared as divergence.
    surfaces = {
        "claude-code": HarnessSurface("claude-code", [_doc("claude-code", "team-defaults.md", a_path, "real body")]),
        "cursor": HarnessSurface("cursor", [_doc("cursor", "team-defaults.md", b_path, "", broken=True)]),
    }
    assert _divergence_findings(surfaces) == []
    from ssoty.diff import diff_pair

    d = diff_pair(surfaces["claude-code"], surfaces["cursor"])
    assert d.content_divergence == ()


def test_content_divergence_deterministic(tmp_path: Path):
    a_path = tmp_path / "a" / "team-defaults.md"
    b_path = tmp_path / "b" / "team-defaults.md"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_text("alpha", encoding="utf-8")
    b_path.write_text("beta", encoding="utf-8")
    surfaces = {
        "claude-code": HarnessSurface("claude-code", [_doc("claude-code", "team-defaults.md", a_path, "alpha")]),
        "cursor": HarnessSurface("cursor", [_doc("cursor", "team-defaults.md", b_path, "beta")]),
    }
    ctx = CheckContext(surfaces=surfaces, ignore=SsotyIgnore())
    first = [(f.severity, f.check, f.harness, f.file, f.message) for f in run_checks(ctx)]
    second = [(f.severity, f.check, f.harness, f.file, f.message) for f in run_checks(ctx)]
    assert first == second


# --- sync: manager mode — distribute canonical source into harness targets ---
# Every test operates ONLY on tmp_path (a fake canonical source dir + fake harness
# targets); ssoty sync --apply is NEVER run against a real ~/.claude / ~/.codex.


def _sync_sandbox(tmp_path: Path):
    """Build a fake canonical SOURCE + a manifest under tmp_path; return (root, manifest).

    Layout::
        tmp_path/src/common/common-a.md
        tmp_path/src/claude/claude-b.md
        tmp_path/src/CLAUDE.md
        tmp_path/root/                      (the sync root; targets resolve here)
        tmp_path/root/ssoty.json            (manifest; relative paths resolve to its dir)
    """
    src = tmp_path / "src"
    (src / "common").mkdir(parents=True)
    (src / "claude").mkdir(parents=True)
    (src / "common" / "common-a.md").write_text("synthetic common rule for /home/dev", encoding="utf-8")
    (src / "claude" / "claude-b.md").write_text("synthetic claude rule", encoding="utf-8")
    (src / "CLAUDE.md").write_text("synthetic entrypoint", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "method": "symlink",
                "common": {"sources": [{"dir": "../src/common", "pattern": "*.md"}]},
                "harnesses": {
                    "claude-code": {
                        "target": ".claude/rules",
                        "sources": [{"dir": "../src/claude", "pattern": "*.md"}],
                        "common": True,
                    },
                    "claude-code-entrypoint": {
                        "target": ".claude/CLAUDE.md",
                        "sources": [{"file": "../src/CLAUDE.md"}],
                        "common": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return root, manifest


def test_sync_apply_refuses_symlinked_parent_escape(tmp_path: Path):
    # A target whose parent is a pre-planted symlink pointing OUTSIDE the sync root must be
    # refused at apply time (realpath containment), writing NOTHING outside root. Lexical
    # _require_under_root can't see this; the realpath re-check must.
    root, manifest = _sync_sandbox(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".claude").symlink_to(outside, target_is_directory=True)  # parent of every target escapes
    rc = main(["sync", str(root), "--apply", "--manifest", str(manifest)])
    assert rc != 0  # refused (ManifestError -> exit 2), not a silent escape
    assert list(outside.iterdir()) == []  # nothing was written outside the sync root


def test_sync_dry_run_changes_nothing(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "new link:" in out
    # nothing written: no symlinks, no backup dir
    assert not (root / ".claude" / "rules").exists()
    assert not (root / ".claude" / "CLAUDE.md").exists()
    assert not (root / ".ssoty-backup").exists()


def test_sync_apply_creates_expected_symlinks(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    rules = root / ".claude" / "rules"
    # common -> claude-code AND the per-harness individual rule are both linked
    common_link = rules / "common-a.md"
    indiv_link = rules / "claude-b.md"
    assert common_link.is_symlink() and common_link.exists()
    assert indiv_link.is_symlink() and indiv_link.exists()
    # link target is the ABSOLUTE canonical source path
    assert os.readlink(common_link) == str((tmp_path / "src" / "common" / "common-a.md").resolve())
    # the file target (CLAUDE.md) is a single link
    entry = root / ".claude" / "CLAUDE.md"
    assert entry.is_symlink() and entry.exists()
    assert entry.read_text(encoding="utf-8") == "synthetic entrypoint"


def test_sync_apply_backs_up_preexisting_real_file(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    # a user's hand-edited real CLAUDE.md at the file target
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "CLAUDE.md").write_text("USER HAND-EDITED", encoding="utf-8")
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "backup written to:" in out
    assert "backup+link:" in out
    # now a symlink to the canonical source
    entry = root / ".claude" / "CLAUDE.md"
    assert entry.is_symlink()
    assert entry.read_text(encoding="utf-8") == "synthetic entrypoint"
    # the old hand-edited content is preserved in the backup, recoverable by path
    ts_dirs = [p for p in (root / ".ssoty-backup").iterdir() if p.is_dir()]
    assert len(ts_dirs) == 1
    backup = ts_dirs[0] / ".claude" / "CLAUDE.md"
    assert backup.read_text(encoding="utf-8") == "USER HAND-EDITED"


def test_sync_apply_is_idempotent(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    # first apply may or may not create a backup (all new links -> no backup);
    # the second apply must be a pure no-op with NO new backup dir.
    backups_before = sorted((root / ".ssoty-backup").glob("*")) if (root / ".ssoty-backup").exists() else []
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "0/0 link(s) changed" in out
    backups_after = sorted((root / ".ssoty-backup").glob("*")) if (root / ".ssoty-backup").exists() else []
    assert backups_after == backups_before


def test_sync_first_apply_all_new_links_creates_no_backup(tmp_path: Path, capsys):
    # a fully fresh tree (no pre-existing files at targets): every link is NEW_LINK,
    # which needs no backup -> no backup dir is created.
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    assert not (root / ".ssoty-backup").exists()


def test_sync_relinks_a_differing_symlink_with_backup(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True)
    # a stale symlink at a target name pointing somewhere else
    (tmp_path / "elsewhere.md").write_text("synthetic stale", encoding="utf-8")
    stale = rules / "common-a.md"
    stale.symlink_to(tmp_path / "elsewhere.md")
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "relinked" in out
    # now points at the canonical source, and the old link node is backed up
    assert os.readlink(stale) == str((tmp_path / "src" / "common" / "common-a.md").resolve())
    ts_dirs = [p for p in (root / ".ssoty-backup").iterdir() if p.is_dir()]
    backup = ts_dirs[0] / ".claude" / "rules" / "common-a.md"
    assert backup.is_symlink()
    assert os.readlink(backup) == str(tmp_path / "elsewhere.md")


def test_sync_orphan_cleanup_removes_only_canonical_pointing_dead_links(tmp_path: Path, capsys):
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    rules = root / ".claude" / "rules"
    # an ORPHAN: points into the canonical source but its target vanished
    orphan = rules / "gone.md"
    orphan.symlink_to(tmp_path / "src" / "common" / "gone.md")
    # a FOREIGN dead link: points OUTSIDE the source -> must NOT be touched
    foreign = rules / "foreign.md"
    foreign.symlink_to(tmp_path / "outside" / "thing.md")
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "removed orphan:" in out
    assert not orphan.is_symlink()  # cleaned
    assert foreign.is_symlink()  # foreign dead link preserved


def test_sync_never_writes_outside_declared_targets(tmp_path: Path, capsys):
    # a malicious target escaping the root must be rejected (exit 2) with NO write.
    root = tmp_path / "root"
    (root).mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.md").write_text("synthetic", encoding="utf-8")
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps({"version": 1, "harnesses": {"x": {"target": "../../evil", "sources": [{"file": "../src/x.md"}]}}}),
        encoding="utf-8",
    )
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 2
    err = capsys.readouterr().err
    assert "escapes sync root" in err
    assert not (tmp_path / "evil").exists()
    assert not (root / ".ssoty-backup").exists()


def test_sync_missing_manifest_exits_2(tmp_path: Path, capsys):
    assert main(["sync", str(tmp_path), "--manifest", str(tmp_path / "nope.json")]) == 2
    assert "manifest not found" in capsys.readouterr().err


def test_sync_invalid_json_exits_2(tmp_path: Path, capsys):
    bad = tmp_path / "ssoty.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert main(["sync", str(tmp_path), "--manifest", str(bad)]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_sync_synced_sandbox_passes_audit_clean(tmp_path: Path, capsys):
    # round-trip contract: sync WRITES exactly what audit READS. After --apply, the
    # resolver sees each doc as a non-broken symlink and run_checks yields no Critical.
    root, manifest = _sync_sandbox(tmp_path)
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    surfaces = resolve_all(root)
    claude = surfaces["claude-code"]
    common_doc = claude.by_name("common-a.md")
    assert common_doc is not None
    assert common_doc.is_symlink is True
    assert common_doc.broken is False
    ctx = CheckContext(surfaces=surfaces, ignore=SsotyIgnore.load(root), root=root)
    findings = run_checks(ctx)
    assert not [f for f in findings if f.severity is Severity.CRITICAL]
    # audit --ci exits 0 on the coherent synced tree
    assert main(["audit", str(root), "--ci"]) == 0


def test_sync_redact_masks_home_in_plan(tmp_path: Path, capsys, monkeypatch):
    root, manifest = _sync_sandbox(tmp_path)
    # make tmp_path stand in for $HOME so --redact has something to mask
    monkeypatch.setenv("HOME", str(tmp_path))
    import ssoty.redact as r

    orig = r.os.path.expanduser
    r.os.path.expanduser = lambda p: p.replace("~", str(tmp_path)) if p.startswith("~") else p
    try:
        assert main(["sync", str(root), "--manifest", str(manifest), "--redact"]) == 0
        out = capsys.readouterr().out
    finally:
        r.os.path.expanduser = orig
    assert str(tmp_path) not in out
    assert "$HOME" in out


def test_sync_in_manifest_method_and_file_alias(tmp_path: Path, capsys):
    # a {"source": ...} alias for {"file": ...} resolves the same single-file link.
    src = tmp_path / "src"
    src.mkdir()
    (src / "ENTRY.md").write_text("synthetic entry", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "method": "symlink",
                "harnesses": {"e": {"target": ".x/ENTRY.md", "sources": [{"source": "../src/ENTRY.md"}]}},
            }
        ),
        encoding="utf-8",
    )
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    capsys.readouterr()
    link = root / ".x" / "ENTRY.md"
    assert link.is_symlink() and link.read_text(encoding="utf-8") == "synthetic entry"


def test_sync_rejects_unsupported_method(tmp_path: Path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.md").write_text("synthetic", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps({"version": 1, "method": "copy", "harnesses": {"h": {"target": ".h", "sources": []}}}),
        encoding="utf-8",
    )
    # an in-manifest "method: copy" is rejected (only symlink supported)
    from ssoty.sync import ManifestError, build_plan, load_manifest

    data = load_manifest(manifest)
    try:
        build_plan(root, data, manifest.parent, method=data["method"])
        raised = False
    except ManifestError as exc:
        raised = "unsupported method" in str(exc)
    assert raised


def test_sync_rejects_malformed_manifest_shapes(tmp_path: Path):
    import pytest

    from ssoty.sync import ManifestError, build_plan

    root = tmp_path
    base = tmp_path
    bad_manifests = [
        {},  # 'harnesses' missing
        {"harnesses": {}},  # empty harnesses
        {"harnesses": []},  # harnesses not an object
        {"harnesses": {"h": {"sources": []}}},  # harness target missing
        {"harnesses": {"h": {"target": ".h", "sources": {}}}},  # sources not a list
        {"harnesses": {"h": {"target": ".h", "sources": [{"nope": 1}]}}},  # source missing dir/file
        {"common": [], "harnesses": {"h": {"target": ".h"}}},  # common not an object
    ]
    for bad in bad_manifests:
        with pytest.raises(ManifestError):
            build_plan(root, bad, base)


def test_sync_dir_target_with_no_sources_links_nothing(tmp_path: Path, capsys):
    # a directory target whose sources resolve to no files: plan is empty, --apply is a no-op
    root = tmp_path / "root"
    (root / ".x").mkdir(parents=True)  # exists as a dir -> treated as dir target
    src = tmp_path / "src"
    src.mkdir()  # empty source dir
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps(
            {"version": 1, "harnesses": {"x": {"target": ".x", "sources": [{"dir": "../src", "pattern": "*.md"}]}}}
        ),
        encoding="utf-8",
    )
    assert main(["sync", str(root), "--apply", "--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "0/0 link(s) changed" in out
    assert not (root / ".ssoty-backup").exists()


def test_sync_dir_glob_into_bare_target_is_a_dir_target(tmp_path: Path):
    # a bare (non-existent) target fed by a directory glob is treated as a DIRECTORY target
    # (one link per source basename), documenting the is_dir_target rule.
    from ssoty.sync import build_plan

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("synthetic", encoding="utf-8")
    (src / "b.md").write_text("synthetic", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    manifest = {
        "version": 1,
        "harnesses": {"f": {"target": "rules-dir", "sources": [{"dir": "../src", "pattern": "*.md"}]}},
    }
    plan = build_plan(root, manifest, root)
    assert len(plan.links) == 2


def test_sync_file_target_with_two_file_sources_is_rejected(tmp_path: Path):
    # a bare-file target fed by TWO explicit file sources is contradictory (one link expected).
    import pytest

    from ssoty.sync import ManifestError, build_plan

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("synthetic", encoding="utf-8")
    (src / "b.md").write_text("synthetic", encoding="utf-8")
    root = tmp_path / "root"
    (root / "x").mkdir(parents=True)  # ensure ENTRY.md's parent is not a dir target
    manifest = {
        "version": 1,
        "harnesses": {"f": {"target": "x/ENTRY.md", "sources": [{"file": "../src/a.md"}, {"file": "../src/b.md"}]}},
    }
    with pytest.raises(ManifestError, match="expected 1"):
        build_plan(root, manifest, root)


# --- init: scaffold a starter ssoty.json from present harnesses (tmp_path ONLY) ---
# Every test builds a fake home under tmp_path; ssoty init is NEVER run against a real $HOME.


def _init_home_symlinked(tmp_path: Path) -> Path:
    """Fake home: a canonical agent-rules/common dir, with claude-code rules + codex AGENTS.md
    + gemini GEMINI.md all SYMLINKED into it (so the common source is inferable)."""
    root = tmp_path / "home"
    canon = root / "agent-rules" / "common"
    canon.mkdir(parents=True)
    (canon / "team-defaults.md").write_text("synthetic shared rule for /home/dev", encoding="utf-8")
    (canon / "AGENTS.md").write_text("synthetic agents entrypoint", encoding="utf-8")
    (canon / "GEMINI.md").write_text("synthetic gemini entrypoint", encoding="utf-8")
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    (cr / "team-defaults.md").symlink_to(canon / "team-defaults.md")
    (root / ".codex").mkdir()
    (root / ".codex" / "AGENTS.md").symlink_to(canon / "AGENTS.md")
    (root / ".gemini").mkdir()
    (root / ".gemini" / "GEMINI.md").symlink_to(canon / "GEMINI.md")
    return root


def _init_home_copies(tmp_path: Path) -> Path:
    """Fake home with REAL copied rule files (no symlinks) -> common source not inferable."""
    root = tmp_path / "home"
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    (cr / "a.md").write_text("synthetic real copy", encoding="utf-8")
    (root / "GEMINI.md").write_text("synthetic gemini", encoding="utf-8")
    return root


def test_init_build_manifest_detects_present_harnesses(tmp_path: Path):
    from ssoty.init import build_init_manifest

    root = _init_home_symlinked(tmp_path)
    surfaces = resolve_all(root)
    manifest = build_init_manifest(root, surfaces)
    # (a) detected harnesses == resolve_all keys
    assert set(manifest["harnesses"]) == set(surfaces)
    assert set(surfaces) == {"claude-code", "codex", "gemini"}


def test_init_infers_common_source_from_symlinks(tmp_path: Path):
    from ssoty.init import build_init_manifest

    root = _init_home_symlinked(tmp_path)
    manifest = build_init_manifest(root, resolve_all(root))
    # (b) inferred common source -> no placeholder comment, claude-code uses common:true
    assert "_comment" not in manifest
    assert manifest["common"]["sources"][0]["dir"] == "agent-rules/common"
    assert manifest["harnesses"]["claude-code"] == {"target": ".claude/rules", "common": True}
    # file-target harnesses point at the canonical dir's matching basename
    assert manifest["harnesses"]["codex"]["sources"] == [{"file": "agent-rules/common/AGENTS.md"}]
    assert manifest["harnesses"]["gemini"]["sources"] == [{"file": "agent-rules/common/GEMINI.md"}]


def test_init_modal_parent_ignores_outlier_symlink(tmp_path: Path):
    # Regression: a few outlier symlinks into a SIBLING dir (agent-rules/claude) must NOT pull
    # the inferred common source up to the broad ancestor (agent-rules/). The MAJORITY parent
    # (agent-rules/common) wins; commonpath would wrongly choose agent-rules/.
    from ssoty.init import build_init_manifest

    root = tmp_path / "home"
    common = root / "agent-rules" / "common"
    claude_only = root / "agent-rules" / "claude"
    common.mkdir(parents=True)
    claude_only.mkdir(parents=True)
    for n in ("a.md", "b.md", "c.md"):  # majority: 3 links into common
        (common / n).write_text("shared", encoding="utf-8")
    (claude_only / "_meta.md").write_text("claude-only outlier", encoding="utf-8")  # 1 outlier
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    for n in ("a.md", "b.md", "c.md"):
        (cr / n).symlink_to(common / n)
    (cr / "_meta.md").symlink_to(claude_only / "_meta.md")  # the outlier

    manifest = build_init_manifest(root, resolve_all(root))
    assert manifest["common"]["sources"][0]["dir"] == "agent-rules/common"  # NOT agent-rules
    assert "_comment" not in manifest  # inferred, not placeholder


def test_init_placeholder_when_no_symlinks(tmp_path: Path):
    from ssoty.init import build_init_manifest

    root = _init_home_copies(tmp_path)
    manifest = build_init_manifest(root, resolve_all(root))
    # (c) placeholder when copies: a _comment is present, dir is the placeholder
    assert "_comment" in manifest
    assert "PLACEHOLDER" in manifest["_comment"]
    assert manifest["common"]["sources"][0]["dir"] == "agent-rules/common"
    # without an inferred symlink, claude-code falls back to an explicit sources entry
    assert manifest["harnesses"]["claude-code"]["sources"] == [{"dir": "agent-rules/common", "pattern": "*.md"}]


def test_init_manifest_round_trips_through_build_plan(tmp_path: Path):
    # (d) the emitted manifest is VALID for sync: load_manifest -> build_plan, no error, non-empty plan.
    from ssoty.init import build_init_manifest, render_manifest
    from ssoty.sync import build_plan, load_manifest

    root = _init_home_symlinked(tmp_path)
    manifest = build_init_manifest(root, resolve_all(root))
    mpath = root / "ssoty.json"
    mpath.write_text(render_manifest(manifest), encoding="utf-8")
    loaded = load_manifest(mpath)
    plan = build_plan(root, loaded, mpath.parent)
    assert plan.links  # non-empty
    # round-trip: every target stays under root (build_plan would have raised otherwise)
    for link in plan.links:
        assert str(link.target).startswith(str(root))


def test_init_placeholder_manifest_is_still_valid_for_build_plan(tmp_path: Path):
    from ssoty.init import build_init_manifest, render_manifest
    from ssoty.sync import build_plan, load_manifest

    root = _init_home_copies(tmp_path)
    manifest = build_init_manifest(root, resolve_all(root))
    mpath = root / "ssoty.json"
    mpath.write_text(render_manifest(manifest), encoding="utf-8")
    # build_plan must ignore the unknown "_comment" key and not raise.
    plan = build_plan(root, load_manifest(mpath), mpath.parent)
    assert plan is not None  # placeholder dir absent -> zero links, but a valid plan


def test_init_cursor_mdc_pattern_preserved(tmp_path: Path):
    from ssoty.init import build_init_manifest

    root = tmp_path / "home"
    canon = root / "agent-rules" / "common"
    canon.mkdir(parents=True)
    (canon / "x.mdc").write_text("---\nalwaysApply: true\n---\nsynthetic", encoding="utf-8")
    cur = root / ".cursor" / "rules"
    cur.mkdir(parents=True)
    (cur / "x.mdc").symlink_to(canon / "x.mdc")
    manifest = build_init_manifest(root, resolve_all(root))
    # cursor's *.mdc pattern is carried on its own sources entry (not silently dropped to *.md)
    assert manifest["harnesses"]["cursor"]["sources"] == [{"dir": "agent-rules/common", "pattern": "*.mdc"}]


def test_init_preview_writes_nothing(tmp_path: Path, capsys):
    # (e) preview (default) writes nothing, creates no ssoty.json.
    root = _init_home_symlinked(tmp_path)
    assert main(["init", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PREVIEW" in out
    assert "claude-code" in out
    assert not (root / "ssoty.json").exists()


def test_init_apply_writes_manifest(tmp_path: Path, capsys):
    root = _init_home_symlinked(tmp_path)
    assert main(["init", str(root), "--apply"]) == 0
    out = capsys.readouterr().out
    mpath = root / "ssoty.json"
    assert mpath.exists()
    assert f"wrote {mpath}" in out
    # written content is the same valid JSON, parseable
    data = json.loads(mpath.read_text(encoding="utf-8"))
    assert data["method"] == "symlink"
    assert set(data["harnesses"]) == {"claude-code", "codex", "gemini"}


def test_init_apply_refuses_overwrite_without_force(tmp_path: Path, capsys):
    # (f) --apply on an existing ssoty.json returns 2 and leaves bytes unchanged.
    root = _init_home_symlinked(tmp_path)
    mpath = root / "ssoty.json"
    mpath.write_text("PRE-EXISTING DO NOT CLOBBER", encoding="utf-8")
    before = mpath.read_bytes()
    rc = main(["init", str(root), "--apply"])
    assert rc == 2
    assert mpath.read_bytes() == before  # untouched


def test_init_apply_force_overwrites(tmp_path: Path, capsys):
    root = _init_home_symlinked(tmp_path)
    mpath = root / "ssoty.json"
    mpath.write_text("PRE-EXISTING", encoding="utf-8")
    assert main(["init", str(root), "--apply", "--force"]) == 0
    data = json.loads(mpath.read_text(encoding="utf-8"))
    assert data["version"] == "1"


def test_init_no_harnesses_returns_zero(tmp_path: Path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["init", str(empty)]) == 0
    out = capsys.readouterr().out
    assert "no known harnesses found" in out
    assert not (empty / "ssoty.json").exists()


def test_init_redact_masks_home(tmp_path: Path, capsys, monkeypatch):
    # --redact runs previewed text through redact (home path masking), consistent with other cmds.
    root = _init_home_symlinked(tmp_path)
    monkeypatch.setenv("HOME", str(root))
    assert main(["init", str(root), "--redact"]) == 0
    out = capsys.readouterr().out
    # the absolute root path must not appear verbatim once redacted
    assert str(root) not in out


def test_init_deterministic_same_bytes(tmp_path: Path):
    from ssoty.init import build_init_manifest, render_manifest

    root = _init_home_symlinked(tmp_path)
    first = render_manifest(build_init_manifest(root, resolve_all(root)))
    second = render_manifest(build_init_manifest(root, resolve_all(root)))
    assert first == second


def test_init_tie_between_sibling_dirs_degrades_to_placeholder(tmp_path: Path):
    # symlinks split 1:1 across agent-rules/common AND agent-rules/claude -> no clear majority.
    # Modal-parent must NOT climb to the broad ancestor (agent-rules/, the old commonpath bug,
    # which would sweep claude-only rules into the common source); with no majority it degrades
    # to the placeholder so the user picks deliberately.
    from ssoty.init import infer_common_source

    root = tmp_path / "home"
    ar = root / "agent-rules"
    (ar / "common").mkdir(parents=True)
    (ar / "claude").mkdir(parents=True)
    (ar / "common" / "team-defaults.md").write_text("synthetic", encoding="utf-8")
    (ar / "claude" / "claude-only.md").write_text("synthetic", encoding="utf-8")
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    (cr / "team-defaults.md").symlink_to(ar / "common" / "team-defaults.md")
    (cr / "claude-only.md").symlink_to(ar / "claude" / "claude-only.md")
    common_dir, uses = infer_common_source(root, resolve_all(root))
    assert common_dir is None  # tie -> placeholder, NOT agent-rules
    assert uses == set()


def test_init_placeholder_when_commonpath_too_broad(tmp_path: Path):
    # symlinks straddle root itself (root/a.md and root/sub/b.md) -> commonpath == root -> too
    # broad -> placeholder (None).
    from ssoty.init import infer_common_source

    root = tmp_path / "home"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("synthetic", encoding="utf-8")
    (root / "sub" / "b.md").write_text("synthetic", encoding="utf-8")
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    (cr / "a.md").symlink_to(root / "a.md")
    (cr / "b.md").symlink_to(root / "sub" / "b.md")
    common_dir, uses = infer_common_source(root, resolve_all(root))
    assert common_dir is None
    assert uses == set()


def test_init_to_manifest_path_variants(tmp_path: Path):
    from ssoty.init import _to_manifest_path

    root = tmp_path / "home"
    root.mkdir()
    rr = os.path.realpath(root)
    home = os.path.realpath(os.path.expanduser("~"))
    assert _to_manifest_path(root, rr) == "."
    assert _to_manifest_path(root, rr + "/agent-rules/common") == "agent-rules/common"
    assert _to_manifest_path(root, home) == "~"
    assert _to_manifest_path(root, home + "/shared-rules").startswith("~/")
    # an absolute dir under neither root nor home is emitted verbatim
    assert _to_manifest_path(root, "/opt/rules") == "/opt/rules"


def test_init_file_entry_with_root_source_dir(tmp_path: Path):
    from ssoty.init import _file_entry
    from ssoty.resolver import Source

    src = Source("GEMINI.md", ALWAYS_ON)
    # source_dir == "." -> the file source is the bare basename (no "./" prefix)
    assert _file_entry(src, ".") == {"target": "GEMINI.md", "sources": [{"file": "GEMINI.md"}]}


# --- adopt: bootstrap scattered rule copies into a canonical SSOT ---
# Every test builds a fake home under tmp_path; adopt --apply is NEVER run against a real
# ~/.claude / ~/.codex. Assertions check the sandbox via resolve_all / direct stat only.


def _adopt_home(tmp_path: Path) -> Path:
    """Fake home with: an identical-content rule shared by 2 harnesses (common candidate),
    a one-harness rule (harness-specific), a per-harness entrypoint, and a divergent pair.
    """
    root = tmp_path / "home"
    cr = root / ".claude" / "rules"
    cont = root / ".continue" / "rules"
    cr.mkdir(parents=True)
    cont.mkdir(parents=True)
    # COMMON_CANDIDATE: same name, byte-identical content, two distinct harnesses/realpaths.
    (cr / "shared.md").write_text("synthetic shared rule\nline two\n", encoding="utf-8")
    (cont / "shared.md").write_text("synthetic shared rule\nline two\n", encoding="utf-8")
    # HARNESS_SPECIFIC: present in exactly one harness.
    (cr / "claude-only.md").write_text("synthetic claude-only rule", encoding="utf-8")
    # DIVERGENT: same name, different content, two harnesses.
    (cr / "div.md").write_text("synthetic version A", encoding="utf-8")
    (cont / "div.md").write_text("synthetic version B — different", encoding="utf-8")
    # ENTRYPOINT: per-harness, never movable.
    (root / ".gemini").mkdir()
    (root / ".gemini" / "GEMINI.md").write_text("synthetic gemini entrypoint", encoding="utf-8")
    return root


def test_adopt_preview_classifies_common_vs_harness_specific(tmp_path: Path):
    from ssoty.adopt import (
        COMMON_CANDIDATE,
        DIVERGENT,
        ENTRYPOINT,
        HARNESS_SPECIFIC,
        build_adopt_plan,
    )

    root = _adopt_home(tmp_path)
    plan = build_adopt_plan(root, resolve_all(root))
    by_name = {r.name: r for r in plan.rules}
    assert by_name["shared.md"].kind == COMMON_CANDIDATE
    assert by_name["shared.md"].canonical_rel == "common/shared.md"
    assert by_name["claude-only.md"].kind == HARNESS_SPECIFIC
    assert by_name["claude-only.md"].canonical_rel == "claude-code/claude-only.md"
    assert by_name["div.md"].kind == DIVERGENT
    assert by_name["div.md"].canonical_rel == ""  # never auto-picked
    assert by_name["GEMINI.md"].kind == ENTRYPOINT


def test_adopt_preview_writes_nothing(tmp_path: Path, capsys):
    root = _adopt_home(tmp_path)
    before = sorted(str(p) for p in root.rglob("*"))
    assert main(["adopt", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PREVIEW" in out
    assert "UNRESOLVED DIVERGENCE" in out
    after = sorted(str(p) for p in root.rglob("*"))
    assert before == after  # preview created/moved nothing
    assert not (root / "agent-rules").exists()
    assert not (root / ".ssoty-backup").exists()


def test_adopt_apply_creates_canonical_and_symlinks_with_backup(tmp_path: Path, capsys):
    root = _adopt_home(tmp_path)
    assert main(["adopt", str(root), "--apply", "--canonical-dir", str(root / "agent-rules")]) == 0
    out = capsys.readouterr().out
    assert "backup written to:" in out

    # COMMON_CANDIDATE moved into canonical common/, both originals now symlinks to it.
    canon_shared = root / "agent-rules" / "common" / "shared.md"
    assert canon_shared.is_file() and not canon_shared.is_symlink()
    for original in (root / ".claude" / "rules" / "shared.md", root / ".continue" / "rules" / "shared.md"):
        assert original.is_symlink()
        assert os.path.realpath(original) == os.path.realpath(canon_shared)

    # HARNESS_SPECIFIC moved into canonical <harness>/.
    canon_only = root / "agent-rules" / "claude-code" / "claude-only.md"
    assert canon_only.is_file() and not canon_only.is_symlink()
    assert (root / ".claude" / "rules" / "claude-only.md").is_symlink()

    # Backups exist for every moved/relinked node.
    backups = list((root / ".ssoty-backup").rglob("shared.md"))
    assert backups  # the moved content was preserved before mutation


def test_adopt_divergent_is_flagged_not_merged_both_backed_up(tmp_path: Path, capsys):
    root = _adopt_home(tmp_path)
    assert main(["adopt", str(root), "--apply", "--canonical-dir", str(root / "agent-rules")]) == 0
    out = capsys.readouterr().out
    assert "divergent rule(s) flagged and deferred" in out
    # NEVER writes a single common/div.md from a divergent set.
    assert not (root / "agent-rules" / "common" / "div.md").exists()
    # Originals are LEFT IN PLACE (real files, not symlinks), content preserved.
    a = root / ".claude" / "rules" / "div.md"
    b = root / ".continue" / "rules" / "div.md"
    assert a.is_file() and not a.is_symlink()
    assert b.is_file() and not b.is_symlink()
    assert a.read_text(encoding="utf-8") == "synthetic version A"
    assert b.read_text(encoding="utf-8") == "synthetic version B — different"
    # Both variants were backed up.
    div_backups = list((root / ".ssoty-backup").rglob("div.md"))
    assert len(div_backups) == 2


def test_adopt_apply_is_idempotent(tmp_path: Path, capsys):
    root = _adopt_home(tmp_path)
    canon = ["--canonical-dir", str(root / "agent-rules")]
    assert main(["adopt", str(root), "--apply", *canon]) == 0
    capsys.readouterr()
    # Second apply: no new moves. shared.md now collapses to ONE inode (already-shared),
    # claude-only.md original already symlinks to canonical (skipped via _same_link).
    assert main(["adopt", str(root), "--apply", *canon]) == 0
    out = capsys.readouterr().out
    assert "already shared" in out
    assert "already linked" in out


def test_adopt_no_symlink_originals_leaves_real_files(tmp_path: Path, capsys):
    root = _adopt_home(tmp_path)
    assert (
        main(["adopt", str(root), "--apply", "--no-symlink-originals", "--canonical-dir", str(root / "agent-rules")])
        == 0
    )
    capsys.readouterr()
    # Content is COPIED into canonical, but originals remain real files (not symlinks).
    assert (root / "agent-rules" / "common" / "shared.md").is_file()
    assert (root / ".claude" / "rules" / "shared.md").is_file()
    assert not (root / ".claude" / "rules" / "shared.md").is_symlink()


def test_adopt_canonical_dir_outside_root_is_allowed(tmp_path: Path, capsys):
    # Configurable-home model: the canonical home MAY live outside the scan root — that is the
    # whole point of `--home` / `--canonical-dir`. adopt consolidates there instead of refusing.
    # The home is a trusted location; engine-generated dests ("common/<name>") never escape it.
    root = _adopt_home(tmp_path)
    outside = tmp_path / "outside-home"
    rc = main(["adopt", str(root), "--apply", "--canonical-dir", str(outside)])
    assert rc == 0
    # The shared rule consolidated into the EXTERNAL home, not under the scan root.
    assert (outside / "common" / "shared.md").is_file()
    assert not (root / "agent-rules").exists()
    # Originals under the scan root now symlink into the external home.
    assert (root / ".claude" / "rules" / "shared.md").is_symlink()


def test_adopt_canonical_dir_dotdot_is_refused(tmp_path: Path, capsys):
    # An ABSOLUTE external home is allowed, but a canonical dir that climbs out via ".." is
    # refused (exit 2, nothing written) — dropping root-containment must not become a footgun.
    root = _adopt_home(tmp_path)
    rc = main(["adopt", str(root), "--apply", "--canonical-dir", "../../escape"])
    assert rc == 2
    assert not (tmp_path.parent / "escape").exists()


def test_adopt_symlinked_home_is_refused(tmp_path: Path, capsys):
    # A canonical home that is ITSELF a symlink is refused — writing rules through it would land
    # them at the link target, outside the declared home.
    root = _adopt_home(tmp_path)
    target = tmp_path / "real-target"
    target.mkdir()
    home_link = tmp_path / "home-link"
    home_link.symlink_to(target)
    rc = main(["adopt", str(root), "--apply", "--canonical-dir", str(home_link)])
    assert rc == 2
    assert not (target / "common").exists()


def test_adopt_copy_less_apply_no_cross_harness_symlink(tmp_path: Path):
    # Assigning a codex-only rule to claude-code (copy-less) must NOT, on apply, turn the codex
    # original into a symlink pointing at the claude-code bucket (mutual-exclusion leak).
    from ssoty.adopt import apply_adopt_plan, build_adopt_plan
    from ssoty.tui import build_modified_plan

    root = tmp_path / "home"
    refs = root / ".codex" / "skills" / "global-agent-rules" / "references"
    cr = root / ".claude" / "rules"
    refs.mkdir(parents=True)
    cr.mkdir(parents=True)
    (refs / "preservation.md").write_text("codex only\n", encoding="utf-8")
    (cr / "anchor.md").write_text("claude\n", encoding="utf-8")
    plan = build_adopt_plan(root, resolve_all(root), canonical_dir=str(root / "agent-rules"))
    modified = build_modified_plan(plan, {"preservation.md": ["claude-code"]})
    apply_adopt_plan(modified)
    assert (root / "agent-rules" / "claude-code" / "preservation.md").is_file()
    codex_orig = refs / "preservation.md"
    assert codex_orig.is_file() and not codex_orig.is_symlink()  # untouched, no cross-harness link


def test_adopt_apply_no_crash_when_original_is_already_symlink(tmp_path: Path):
    # Regression: a harness-specific rule whose source IS the original (same path) was backed
    # up twice in one run; once it had become a symlink the second _backup_node hit
    # FileExistsError. _backup_node is now idempotent per run. Re-running adopt --apply on an
    # already-adopted tree (originals are symlinks into canonical) must NOT crash.
    root = tmp_path / "home"
    cr = root / ".claude" / "rules"
    cr.mkdir(parents=True)
    (cr / "only.md").write_text("synthetic harness-only rule\n", encoding="utf-8")
    canon = ["--canonical-dir", str(root / "agent-rules")]
    assert main(["adopt", str(root), "--apply", *canon]) == 0  # first adopt: moves + symlinks original
    assert (cr / "only.md").is_symlink()
    # second adopt --apply over the symlinked original must be a clean no-op, not a crash
    assert main(["adopt", str(root), "--apply", *canon]) == 0
    canon = root / "agent-rules" / "claude-code" / "only.md"
    assert canon.is_file() and not canon.is_symlink()
    assert os.path.realpath(cr / "only.md") == os.path.realpath(canon)


def test_adopt_skips_broken_symlink_no_fabricated_divergence(tmp_path: Path):
    from ssoty.adopt import COMMON_CANDIDATE, build_adopt_plan

    root = tmp_path / "home"
    cr = root / ".claude" / "rules"
    cont = root / ".continue" / "rules"
    cr.mkdir(parents=True)
    cont.mkdir(parents=True)
    (cr / "r.md").write_text("real content\n", encoding="utf-8")
    (cont / "r.md").write_text("real content\n", encoding="utf-8")
    # A broken symlink with the SAME name in a third harness must be filtered (text="")
    # so it does not fabricate divergence against the two identical real copies.
    (root / ".cursor" / "rules").mkdir(parents=True)
    (root / ".cursor" / "rules" / "r.mdc").symlink_to(tmp_path / "does-not-exist.md")
    plan = build_adopt_plan(root, resolve_all(root))
    by_name = {r.name: r for r in plan.rules}
    # r.md still classified as a clean common candidate (broken r.mdc is a different basename here)
    assert by_name["r.md"].kind == COMMON_CANDIDATE


def test_adopt_no_harnesses_exits_zero(tmp_path: Path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["adopt", str(empty)]) == 0
    assert "no known harnesses" in capsys.readouterr().out


def test_adopt_already_shared_proposes_no_move(tmp_path: Path):
    from ssoty.adopt import ALREADY_SHARED, build_adopt_plan

    root = tmp_path / "home"
    canon = root / "agent-rules" / "common"
    canon.mkdir(parents=True)
    (canon / "s.md").write_text("synthetic already shared", encoding="utf-8")
    cr = root / ".claude" / "rules"
    cont = root / ".continue" / "rules"
    cr.mkdir(parents=True)
    cont.mkdir(parents=True)
    (cr / "s.md").symlink_to(canon / "s.md")
    (cont / "s.md").symlink_to(canon / "s.md")
    plan = build_adopt_plan(root, resolve_all(root))
    by_name = {r.name: r for r in plan.rules}
    assert by_name["s.md"].kind == ALREADY_SHARED


# --- adopt TUI: interactive classifier over the deterministic engine ---
# The TUI performs NO filesystem mutation of its own: it rebuilds an AdoptPlan from user
# overrides and calls the SAME engine (adopt_needs_force / apply_adopt_plan) cmd_adopt calls.
# Every test runs against a tmp_path sandbox; the TUI is NEVER run against a real config.

pytest.importorskip("textual")  # skip the whole TUI block if textual is unavailable


def test_tui_lazy_import_keeps_text_commands_textual_free():
    # audit/diff/sync/resolve/fix/metrics must not pull in textual: cli + adopt import clean.
    import importlib

    for mod in ("ssoty.cli", "ssoty.adopt"):
        importlib.import_module(mod)
    # cli importing adopt must NOT have imported textual transitively.
    # A fresh subprocess proves it definitively (no test-session pollution).
    import subprocess

    import ssoty.cli  # noqa: F401

    code = "import sys, ssoty.cli, ssoty.adopt; " "print('textual' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def _tui_plan(tmp_path: Path):
    from ssoty.adopt import build_adopt_plan

    root = _adopt_home(tmp_path)
    return build_adopt_plan(root, resolve_all(root)), root


def _rule_index(app, name: str) -> int:
    return [r.name for r in app._rules].index(name)


async def test_tui_renders_rule_list(tmp_path: Path):
    from ssoty.tui import AdoptTUI

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)):
        assert len(app.query("#rule-list ListItem")) == len(plan.rules)


async def test_tui_preview_updates_on_navigation(tmp_path: Path):
    from ssoty.tui import AdoptTUI

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        # Highlight a known rule and confirm the preview reflects it.
        idx = _rule_index(app, "shared.md")
        app.query_one("#rule-list").index = idx
        await pilot.pause()
        assert "shared.md" in app._preview_str
        assert "synthetic shared rule" in app._preview_str


async def test_tui_classify_common_sets_override(tmp_path: Path):
    from textual.widgets import SelectionList

    from ssoty.tui import AdoptTUI

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        # claude-only.md is HARNESS_SPECIFIC; force it to COMMON via the chooser.
        app.query_one("#rule-list").index = _rule_index(app, "claude-only.md")
        await pilot.pause()
        sl = app.query_one("#classify-area SelectionList", SelectionList)
        sl.select("common")
        await pilot.pause()
        assert app._overrides["claude-only.md"] == "common"


async def test_tui_mutual_exclusion_common_clears_harness(tmp_path: Path):
    from textual.widgets import SelectionList

    from ssoty.tui import AdoptTUI

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        # shared.md has both a common option and per-harness options.
        app.query_one("#rule-list").index = _rule_index(app, "shared.md")
        await pilot.pause()
        sl = app.query_one("#classify-area SelectionList", SelectionList)
        # Select a harness, then select common -> common wins, harness cleared.
        sl.deselect_all()
        await pilot.pause()
        sl.select("claude-code")
        await pilot.pause()
        sl.select("common")
        await pilot.pause()
        assert "common" in sl.selected
        assert "claude-code" not in sl.selected
        assert app._overrides["shared.md"] == "common"


async def test_tui_multi_select_two_harnesses(tmp_path: Path):
    # A rule can have 2+ harnesses selected (without common). Selecting 2+ harnesses means the
    # rule is shared, so build_modified_rules consolidates it into ONE common/<name> canonical
    # copy (precise subset distribution is left to the manifest).
    from textual.widgets import SelectionList

    from ssoty.adopt import COMMON_CANDIDATE
    from ssoty.tui import AdoptTUI, build_modified_rules

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#rule-list").index = _rule_index(app, "shared.md")
        await pilot.pause()
        sl = app.query_one("#classify-area SelectionList", SelectionList)
        sl.deselect_all()
        await pilot.pause()
        sl.select("claude-code")
        await pilot.pause()
        sl.select("continue")
        await pilot.pause()
        assert set(app._overrides["shared.md"]) == {"claude-code", "continue"}
        assert "common" not in sl.selected
        # 2+ harnesses selected -> a single shared canonical copy under common/.
        modified = {r.name: r for r in build_modified_rules(plan, app._overrides)}
        assert modified["shared.md"].kind == COMMON_CANDIDATE
        assert modified["shared.md"].canonical_rel == "common/shared.md"


async def test_tui_divergent_cannot_be_set_common(tmp_path: Path):
    from textual.widgets import SelectionList

    from ssoty.adopt import DIVERGENT
    from ssoty.tui import AdoptTUI, build_modified_rules

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#rule-list").index = _rule_index(app, "div.md")
        await pilot.pause()
        # No SelectionList is mounted for a DIVERGENT rule; a Static warning is shown instead.
        assert len(app.query("#classify-area SelectionList")) == 0
        assert "DIVERGENT" in app._chooser_message
        # Even if a COMMON override is forced into the dict, the engine mapping ignores it.
        app._overrides["div.md"] = "common"
        modified = {r.name: r for r in build_modified_rules(plan, app._overrides)}
        assert modified["div.md"].kind == DIVERGENT
        assert modified["div.md"].canonical_rel == ""
    # Sanity: no SelectionList type was usable on the divergent rule.
    _ = SelectionList


async def test_tui_choice_maps_to_engine_destinations(tmp_path: Path):
    # The choice->engine mapping is exactly what apply WOULD move — asserted on the plan, no fs.
    from ssoty.adopt import COMMON_CANDIDATE, HARNESS_SPECIFIC
    from ssoty.tui import AdoptTUI, build_modified_rules

    plan, _ = _tui_plan(tmp_path)
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        # Force claude-only.md (HARNESS_SPECIFIC) -> COMMON.
        app._overrides["claude-only.md"] = "common"
        # Force shared.md (COMMON_CANDIDATE) -> a single harness.
        app._overrides["shared.md"] = ["claude-code"]
        await pilot.pause()
        modified = {r.name: r for r in build_modified_rules(plan, app._overrides)}
        assert modified["claude-only.md"].kind == COMMON_CANDIDATE
        assert modified["claude-only.md"].canonical_rel == "common/claude-only.md"
        assert modified["shared.md"].kind == HARNESS_SPECIFIC
        assert modified["shared.md"].canonical_rel == "claude-code/shared.md"
        # source_path for the forced-harness rule is that harness's variant.
        src = modified["shared.md"].source_path
        assert src is not None and ".claude" in str(src)


async def test_tui_apply_calls_engine_and_moves_files(tmp_path: Path):
    # Pressing 'a' runs the SAME engine cmd_adopt uses; files land at canonical destinations.
    from ssoty.tui import AdoptTUI

    plan, root = _tui_plan(tmp_path)
    app = AdoptTUI(plan, force=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        assert app._apply_done is True
    # Engine moved the common candidate into canonical and symlinked the originals.
    canon_shared = root / "agent-rules" / "common" / "shared.md"
    assert canon_shared.is_file() and not canon_shared.is_symlink()
    assert (root / ".claude" / "rules" / "shared.md").is_symlink()
    # DIVERGENT never auto-merged.
    assert not (root / "agent-rules" / "common" / "div.md").exists()


def test_tui_module_reuses_engine_not_reimplemented():
    # Structural guard: tui.py must import the engine's mutation funcs, not define its own.
    import ssoty.tui as tui

    assert tui.apply_adopt_plan is __import__("ssoty.adopt", fromlist=["apply_adopt_plan"]).apply_adopt_plan
    assert tui.adopt_needs_force is __import__("ssoty.adopt", fromlist=["adopt_needs_force"]).adopt_needs_force
    src = (Path(tui.__file__)).read_text(encoding="utf-8")
    # No bespoke move/symlink/backup logic in the TUI module.
    assert "os.symlink" not in src
    assert "shutil.copy" not in src
    assert "shutil.move" not in src


def test_cmd_adopt_non_tty_does_not_launch_tui_prints_text(tmp_path: Path, monkeypatch, capsys):
    # Non-TTY (e.g. CI / piped) must NOT launch the TUI; it prints the text preview instead.
    root = _adopt_home(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    # If the TUI were launched, AdoptTUI.run would be called; make it explode to prove it isn't.
    import ssoty.tui as tui

    def _boom(*_a, **_k):
        raise AssertionError("TUI must not launch in a non-TTY context")

    monkeypatch.setattr(tui.AdoptTUI, "run", _boom)

    assert main(["adopt", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PREVIEW" in out
    assert "UNRESOLVED DIVERGENCE" in out
    # Nothing was written by the text preview path.
    assert not (root / "agent-rules").exists()


def test_cmd_adopt_no_tui_flag_forces_text_even_on_tty(tmp_path: Path, monkeypatch, capsys):
    root = _adopt_home(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    import ssoty.tui as tui

    monkeypatch.setattr(tui.AdoptTUI, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no TUI")))

    assert main(["adopt", str(root), "--no-tui"]) == 0
    assert "PREVIEW" in capsys.readouterr().out


def test_cmd_adopt_plan_flag_forces_text_even_on_tty(tmp_path: Path, monkeypatch, capsys):
    root = _adopt_home(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    import ssoty.tui as tui

    monkeypatch.setattr(tui.AdoptTUI, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no TUI")))

    assert main(["adopt", str(root), "--plan"]) == 0
    assert "PREVIEW" in capsys.readouterr().out


# --- add: place ONE new rule into the canonical SSOT ---


def _add_home(tmp_path: Path) -> tuple[Path, Path]:
    """Fake home with one present harness; return (root, new_rule_file)."""
    root = tmp_path / "home"
    (root / ".claude" / "rules").mkdir(parents=True)
    (root / ".claude" / "rules" / "existing.md").write_text("synthetic existing", encoding="utf-8")
    new_rule = tmp_path / "new-rule.md"
    new_rule.write_text("synthetic brand new rule\n", encoding="utf-8")
    return root, new_rule


def test_add_no_choice_previews_and_does_not_guess(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root)]) == 0
    out = capsys.readouterr().out
    assert "--common" in out and "--harness" in out
    # No write happened (no choice => no guess).
    assert not (root / "agent-rules").exists()


def test_add_common_apply_writes_into_canonical_common(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root), "--common", "--apply"]) == 0
    dest = root / "agent-rules" / "common" / "new-rule.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == "synthetic brand new rule\n"
    assert "ssoty sync" in capsys.readouterr().out  # handoff printed


def test_add_harness_apply_writes_into_that_harness(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root), "--harness", "claude-code", "--apply"]) == 0
    capsys.readouterr()
    # No manifest -> canonical <harness>/ dir under the common parent.
    assert (root / "agent-rules" / "claude-code" / "new-rule.md").is_file()


def test_add_harness_uses_manifest_target_when_present(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "method": "symlink",
                "common": {"sources": [{"dir": "agent-rules/common", "pattern": "*.md"}]},
                "harnesses": {"claude-code": {"target": ".claude/rules", "common": True}},
            }
        ),
        encoding="utf-8",
    )
    assert main(["add", str(new_rule), str(root), "--harness", "claude-code", "--apply"]) == 0
    capsys.readouterr()
    # With a manifest, the harness target dir (.claude/rules) is used.
    assert (root / ".claude" / "rules" / "new-rule.md").is_file()


def test_add_unknown_harness_exits_2(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root), "--harness", "bogus", "--apply"]) == 2
    assert "unknown harness" in capsys.readouterr().err


def test_add_missing_rule_file_exits_2(tmp_path: Path, capsys):
    root, _ = _add_home(tmp_path)
    assert main(["add", str(tmp_path / "nope.md"), str(root), "--common", "--apply"]) == 2
    assert "not found" in capsys.readouterr().err


def test_add_dry_run_writes_nothing(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root), "--common"]) == 0
    assert "PREVIEW" in capsys.readouterr().out
    assert not (root / "agent-rules").exists()


def test_add_backup_first_on_overwrite_requires_force(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    dest = root / "agent-rules" / "common" / "new-rule.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("OLD differing content", encoding="utf-8")
    # Without --force: refused (exit 2), original untouched.
    assert main(["add", str(new_rule), str(root), "--common", "--apply"]) == 2
    assert dest.read_text(encoding="utf-8") == "OLD differing content"
    capsys.readouterr()
    # With --force: backed up first, then overwritten.
    assert main(["add", str(new_rule), str(root), "--common", "--apply", "--force"]) == 0
    out = capsys.readouterr().out
    assert "backup written to:" in out
    assert dest.read_text(encoding="utf-8") == "synthetic brand new rule\n"
    assert list((root / ".ssoty-backup").rglob("new-rule.md"))  # old content preserved


def test_add_idempotent_identical_content_skips(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    assert main(["add", str(new_rule), str(root), "--common", "--apply"]) == 0
    capsys.readouterr()
    # Re-run with identical content: no write, no backup, exit 0.
    assert main(["add", str(new_rule), str(root), "--common", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "unchanged" in out
    assert not (root / ".ssoty-backup").exists()


def test_add_never_writes_outside_root(tmp_path: Path, capsys):
    root, new_rule = _add_home(tmp_path)
    # A manifest whose harness target escapes root must be refused at build time.
    manifest = root / "ssoty.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "method": "symlink",
                "common": {"sources": [{"dir": "../escape", "pattern": "*.md"}]},
                "harnesses": {"claude-code": {"target": ".claude/rules", "common": True}},
            }
        ),
        encoding="utf-8",
    )
    assert main(["add", str(new_rule), str(root), "--common", "--apply"]) == 2
    assert not (tmp_path / "escape").exists()


# --- configurable canonical home (~/.ssoty) + persistence + full-harness assignment ---


def test_config_resolve_home_precedence(tmp_path: Path, monkeypatch):
    from ssoty import config

    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # No flag, no config -> default ~/.ssoty under the (isolated) HOME.
    assert config.resolve_home(None) == Path(os.path.normpath(str(tmp_path / "h" / ".ssoty")))
    # A persisted config home is used when no flag is given.
    config.save_home(Path(os.path.normpath(str(tmp_path / "stored"))))
    assert config.resolve_home(None) == Path(os.path.normpath(str(tmp_path / "stored")))
    # An explicit flag wins over the config file.
    assert config.resolve_home(str(tmp_path / "flag")) == Path(os.path.normpath(str(tmp_path / "flag")))


def test_config_save_home_roundtrip(tmp_path: Path, monkeypatch):
    from ssoty import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    home = Path(os.path.normpath(str(tmp_path / "myhome")))
    cfgp = config.save_home(home)
    assert cfgp == config.config_path()
    assert json.loads(cfgp.read_text(encoding="utf-8"))["home"] == str(home)


def test_config_corrupt_file_degrades_to_default(tmp_path: Path, monkeypatch):
    from ssoty import config

    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfgp = config.config_path()
    cfgp.parent.mkdir(parents=True)
    cfgp.write_text("{ not json", encoding="utf-8")
    # A corrupt config must not crash resolution; it falls back to the default home.
    assert config.resolve_home(None) == Path(os.path.normpath(str(tmp_path / "h" / ".ssoty")))


def test_cli_home_persist_failure_is_graceful(tmp_path: Path, monkeypatch, capsys):
    from ssoty import config

    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = _adopt_home(tmp_path)
    # Make config.json a DIRECTORY so the atomic replace in save_home fails — the command must
    # warn and continue, not crash.
    cp = config.config_path()
    cp.parent.mkdir(parents=True)
    cp.mkdir()
    rc = main(["--home", str(tmp_path / "myhome"), "adopt", str(root), "--canonical-dir", str(root / "agent-rules")])
    assert rc == 0  # preview path, did not crash despite the persist failure
    assert "could not persist" in capsys.readouterr().err


def test_cli_home_flag_persists_and_is_reused(tmp_path: Path, monkeypatch):
    from ssoty import config

    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = _adopt_home(tmp_path)
    home = tmp_path / "ssoty-home"
    # Passing --home both consolidates there AND persists the choice.
    assert main(["--home", str(home), "adopt", str(root), "--apply"]) == 0
    assert (home / "common" / "shared.md").is_file()
    # A later no-flag resolution reuses the persisted home.
    assert config.resolve_home(None) == Path(os.path.normpath(str(home)))


def test_cmd_adopt_default_canonical_is_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = _adopt_home(tmp_path)
    # No --home, no config -> canonical defaults to ~/.ssoty (the isolated HOME), NOT the old
    # $HOME/agent-rules default.
    assert main(["adopt", str(root), "--apply"]) == 0
    assert (tmp_path / "h" / ".ssoty" / "common" / "shared.md").is_file()
    assert not (root / "agent-rules").exists()


def test_adopt_plan_carries_scanned_harnesses(tmp_path: Path):
    from ssoty.adopt import build_adopt_plan

    root = _adopt_home(tmp_path)
    plan = build_adopt_plan(root, resolve_all(root))
    assert "claude-code" in plan.scanned_harnesses
    assert "continue" in plan.scanned_harnesses
    assert plan.scanned_harnesses == tuple(sorted(plan.scanned_harnesses))


def test_build_modified_rules_assigns_copy_less_harness(tmp_path: Path):
    from ssoty.adopt import HARNESS_SPECIFIC, build_adopt_plan
    from ssoty.tui import build_modified_rules

    root = tmp_path / "home"
    refs = root / ".codex" / "skills" / "global-agent-rules" / "references"
    cr = root / ".claude" / "rules"
    refs.mkdir(parents=True)
    cr.mkdir(parents=True)
    (refs / "preservation.md").write_text("codex only\n", encoding="utf-8")
    (cr / "anchor.md").write_text("claude anchor\n", encoding="utf-8")  # makes claude-code a scanned harness
    plan = build_adopt_plan(root, resolve_all(root))
    by = {r.name: r for r in plan.rules}
    assert by["preservation.md"].kind == HARNESS_SPECIFIC  # codex-only
    # claude-code is scanned but has NO copy of preservation.md — the user can still assign it.
    modified = {r.name: r for r in build_modified_rules(plan, {"preservation.md": ["claude-code"]})}
    pres = modified["preservation.md"]
    assert pres.canonical_rel == "claude-code/preservation.md"
    assert pres.source_path is not None  # bytes come from the only (codex) copy
    # Copy-less target keeps ZERO variants -> adopt --apply will NOT symlink the codex original
    # into the claude-code bucket (no cross-harness leak; distribution is a later sync's job).
    assert pres.variants == ()


def test_build_modified_rules_rejects_injected_harness_name(tmp_path: Path):
    from ssoty.adopt import build_adopt_plan
    from ssoty.tui import build_modified_rules

    root = _adopt_home(tmp_path)
    plan = build_adopt_plan(root, resolve_all(root))
    # A hand-built overrides dict with a traversal harness name must NOT produce an escaping
    # canonical_rel — an unscanned/garbage harness is ignored and the engine's classification kept.
    by = {r.name: r for r in build_modified_rules(plan, {"claude-only.md": ["../../etc/evil"]})}
    assert ".." not in by["claude-only.md"].canonical_rel
    assert by["claude-only.md"].canonical_rel == "claude-code/claude-only.md"  # engine default kept


def test_build_modified_rules_all_harnesses_collapse_to_common(tmp_path: Path):
    from ssoty.adopt import build_adopt_plan
    from ssoty.tui import build_modified_rules

    root = _adopt_home(tmp_path)
    plan = build_adopt_plan(root, resolve_all(root))
    # claude-only.md is HARNESS_SPECIFIC(claude-code); selecting 2+ harnesses -> common/.
    by = {r.name: r for r in build_modified_rules(plan, {"claude-only.md": ["claude-code", "continue"]})}
    assert by["claude-only.md"].canonical_rel == "common/claude-only.md"


async def test_tui_chooser_offers_copy_less_harness(tmp_path: Path):
    # The chooser for a codex-only rule must OFFER claude-code (a scanned harness with no copy
    # of this rule) as a selectable target — the previously-missing capability.
    from textual.widgets import SelectionList

    from ssoty.adopt import build_adopt_plan
    from ssoty.tui import AdoptTUI

    root = tmp_path / "home"
    refs = root / ".codex" / "skills" / "global-agent-rules" / "references"
    cr = root / ".claude" / "rules"
    refs.mkdir(parents=True)
    cr.mkdir(parents=True)
    (refs / "preservation.md").write_text("codex only\n", encoding="utf-8")
    (cr / "anchor.md").write_text("claude anchor\n", encoding="utf-8")  # makes claude-code a scanned harness
    plan = build_adopt_plan(root, resolve_all(root))
    app = AdoptTUI(plan)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#rule-list").index = _rule_index(app, "preservation.md")
        await pilot.pause()
        sl = app.query_one("#classify-area SelectionList", SelectionList)
        # claude-code is selectable even though preservation.md has no claude copy.
        sl.deselect_all()  # clear the codex seed
        await pilot.pause()
        sl.select("claude-code")
        await pilot.pause()
        assert "claude-code" in sl.selected
        assert app._overrides["preservation.md"] == ["claude-code"]

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


def test_copilot_resolved(tmp_path: Path):
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("rules", encoding="utf-8")
    assert resolve_all(tmp_path)["copilot"].by_name("copilot-instructions.md").load_basis == ALWAYS_ON


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

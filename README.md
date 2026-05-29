# ssoty

**English** | [한국어](README-ko.md)

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Static cross-harness rule coherence auditor for AI coding agents.**
*A symlink shares files. It does not guarantee the rule is applied the same way.*

`ssoty` reads the rule surfaces of multiple agent harnesses (Claude Code, Codex, …)
and finds — **deterministically, with no LLM and no network** — where shared rules
silently fail to apply across a harness boundary, then quantifies the per-turn token
cost ("Context Tax").

---

## The problem

Teams symlink one `AGENTS.md` / `CLAUDE.md` / rule set into every tool to get a
"single source of truth." But a symlink is a **distribution** mechanism, not a
**coherence** mechanism. The same canonical file can:

- load **always-on** in one harness (injected every turn) but **skill-gated** in
  another (loaded only when a skill triggers) — same file, unequal guarantee;
- reference a sibling rule that exists in one harness but was **never distributed**
  to the other — a broken pointer across the boundary;
- be duplicated across files, paying token rent every turn.

These are invisible until an agent in harness B quietly ignores a rule you "share."

## What ssoty does

```
$ uvx ssoty audit examples/messy-setup
ssoty audit — 2 Critical, 3 Warning, 6 FYI

  [Critical] broken_symlink (claude-code)
      .../.claude/rules/broken-link.md
      symlink target does not resolve: ./nope.md

  [Critical] dangling_cross_ref (codex)
      .../.codex/skills/global-agent-rules/references/shared-style.md
      references 'team-rules.md', which exists in another harness but is NOT
      loaded by 'codex' — broken pointer across the harness boundary

  [Warning] load_asymmetry (claude-code+codex)
      shared-style.md
      same rule loads differently per harness (claude-code=always-on,
      codex=skill-gated) — shared file, unequal guarantee
  ...
  [FYI] dangling_cross_ref (codex)
      references 'meta-layout.md' (absent here, intentional per .ssotyignore)
```

It distinguishes a **genuine** broken cross-reference (Critical) from
**intentional** non-sharing you declared in `.ssotyignore` (FYI) — precision over
noise.

## Context Tax (reproducible before/after)

```
$ uvx ssoty metrics examples/messy-setup     $ uvx ssoty metrics examples/clean-setup
  claude-code:                                  claude-code:
      always-on  : 206 tokens                       always-on  : 149 tokens   (-27.7%)
  codex:                                        codex:
      skill-gated: 106 tokens                       skill-gated:   0 tokens
```

Numbers are reported **per harness and never summed across harnesses**: `always-on`
(actual, every turn) and `skill-gated` (potential, only when a skill fires) are
different load guarantees. Compare *within* one harness, before vs after a cleanup.
Token counts are a deterministic `char/4` heuristic by default (portable — same
numbers on any machine); set `SSOTY_EXACT_TOKENS=1` to opt into `tiktoken`.

Reproduce: `uvx ssoty metrics examples/messy-setup` (see [`benchmarks/REPORT.md`](benchmarks/REPORT.md)).

## Checks

| Check | Severity | What it catches |
|---|---|---|
| `broken_symlink` | Critical | symlinked rule whose target is gone |
| `dangling_cross_ref` | Critical / FYI | a rule references a sibling absent in this harness (FYI if declared intentional) |
| `load_asymmetry` | Warning | same rule, different load basis per harness |
| `duplicate_content` | Warning | identical blocks duplicated across files (token rent) |
| `non_shared_surface` | FYI | a rule present in one harness only |
| `skill_integrity` | Warning | skill dir without a `SKILL.md` |

## Install

```bash
# zero-install run
uvx ssoty audit                 # audits $HOME (~/.claude, ~/.codex)
# or install
pipx install ssoty
ssoty audit --redact            # mask home paths + emails in output
ssoty audit --ci                # exit non-zero on any Critical (for CI)
```

### CI (GitHub Action)
```yaml
- uses: snowlaxc/ssoty@v0
  with: { path: . }             # runs `ssoty audit --ci`
```

### Harness adapters (optional)
Thin wrappers so you can run ssoty from inside an agent:
- **Claude Code**: copy `adapters/claude-code/skills/ssoty` into `~/.claude/skills/`
- **Codex**: copy `adapters/codex/skills/ssoty` into `~/.codex/skills/`

The CLI is the product; adapters just shell out to it.

## How it works
`ssoty` resolves each harness's effective rule surface from disk (which files load,
and whether always-on or skill-gated), then runs deterministic checks. No model
calls, no network — same input, same output. It is **harness-agnostic by design**:
a cross-harness tool shouldn't live inside one harness.

## Privacy
ssoty audits *your* config; its output can quote your rules verbatim. It runs
**entirely locally** (no hosted service). This repo ships **synthetic fixtures
only**. See [`SECURITY.md`](SECURITY.md). Never commit ssoty output to a public repo.

## Roadmap (phase 2)
`ssoty fix` (auto-dedup), opt-in live "canary" runtime probe, LLM semantic
conflict detection, Gemini support, marketplace packaging.

## Background
The design rationale lives in [`docs/RFC.md`](docs/RFC.md).

## License
[MIT](LICENSE)

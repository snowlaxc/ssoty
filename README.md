# ssoty

**English** | [한국어](README-ko.md)

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Static cross-harness rule DIVERGENCE auditor for AI coding agents.**
*Two models, one "shared" rule set — but do they actually operate under the same rules? Usually not.*

`ssoty` reads the effective rule surfaces of multiple agent harnesses (Claude Code,
Codex, Cursor, Copilot, Gemini, Cline) and shows — **deterministically, with no LLM
and no network** — where two models diverge: which rules one model applies that the
other never sees, which shared rules load under a *different guarantee* (always-on vs
skill-gated), and which cross-references break across the boundary. It also quantifies
the per-turn token cost ("Context Tax") as a secondary metric.

---

## The problem

You point Claude Code, Codex, and Cursor at one "shared" rule set and expect identical
behavior. They don't behave identically — because each harness resolves a **different
effective rule set**. The same canonical file can:

- load **always-on** in one harness (injected every turn) but **skill-gated** in
  another (loaded only when a skill triggers) — same file, unequal guarantee;
- reference a sibling rule that exists in one harness but was **never distributed**
  to the other — a broken pointer across the boundary;
- be duplicated across files, paying token rent every turn.

The result: the same prompt, the same repo, but **different effective rules per
model** — so they behave inconsistently, and it's invisible until one model quietly
ignores a rule you "share."

## Rule divergence (the headline)

```
$ uvx ssoty diff examples/messy-setup --a claude-code --b codex

  claude-code  vs  codex
      only in claude-code (1): team-rules.md
      same rule, different load (1):
          shared-style.md  claude-code=always-on  |  codex=skill-gated
      broken cross-references across the boundary (1):
          codex:shared-style.md -> 'team-rules.md'  (loads only in claude-code, NOT in codex)
      VERDICT: claude-code and codex do NOT operate under the same rules
               (1 rule only in claude-code, 1 loads differently, 1 broken cross-ref)
```

`ssoty diff` answers the one question that matters: *do these two models operate under
the same rules?* Run it across every present pair (omit `--a/--b`), or compare two
named harnesses. `--json` and `--redact` supported; the command is strictly read-only.

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

## Also measures: Context Tax (token rent)

Secondary metric — the per-turn token cost of each surface and duplicate content paid
every turn. Useful for before/after cleanup, but the *pitch is divergence above*, not
token rent.

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
uvx ssoty diff                  # cross-model rule divergence (the headline; all present pairs)
uvx ssoty audit                 # audits $HOME (~/.claude, ~/.codex)
# or install
pipx install ssoty
ssoty diff --a claude-code --b codex  # compare two named harnesses (read-only)
ssoty audit --redact            # mask home paths + emails in output
ssoty audit --ci                # exit non-zero on any Critical (for CI)
ssoty audit --format sarif      # SARIF 2.1.0 (for github/codeql-action/upload-sarif)
```

`--format {text,json,sarif}` selects the audit output (default `text`); `--json` is
a back-compat alias for `--format json`.

### Fix (dry-run + backup first)
```bash
ssoty fix                       # DRY-RUN: prints what WOULD change, writes nothing
ssoty fix --apply               # perform safe fixes; backs every touched file up first
ssoty fix --apply --scaffold-ignore   # also append non-shared rule names to .ssotyignore
```

`ssoty fix` is **dry-run by default** — it prints exactly what it would do and changes
nothing. Only `--apply` writes, and even then it first copies every file it will touch
into a timestamped backup dir under the audited root (`.ssoty-backup/<timestamp>/`,
path-preserving) and prints that location. It performs only **safe** remediations:
removing a *broken* symlink (its target does not resolve, so no real content is lost)
and, with `--scaffold-ignore`, recording intentionally non-shared rule names in
`.ssotyignore`. It never edits your real rule files, never touches a valid symlink, and
is idempotent (running it again does nothing). Add `.ssoty-backup/` to your gitignore so
backups are never committed.

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

## Supported harnesses
Claude Code (`~/.claude/rules`, `CLAUDE.md`), Codex (`AGENTS.md`,
`global-agent-rules`), Cursor (`.cursor/rules/*.mdc` with `alwaysApply` frontmatter,
legacy `.cursorrules`), GitHub Copilot (`.github/copilot-instructions.md`),
Gemini CLI (`GEMINI.md`, `~/.gemini/GEMINI.md`), and Cline (`.clinerules/` directory,
legacy `.clinerules`, `AGENTS.md`). Empty harnesses are skipped. Point ssoty at `$HOME` or a project root.

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

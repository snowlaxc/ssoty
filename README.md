# ssoty

**English** | [한국어](README-ko.md)

[![PyPI](https://img.shields.io/pypi/v/ssoty.svg)](https://pypi.org/project/ssoty/)
[![CI](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml/badge.svg)](https://github.com/snowlaxc/ssoty/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Static cross-harness rule DIVERGENCE auditor for AI coding agents.**
*Two models, one "shared" rule set — but do they actually operate under the same rules? Usually not.*

`ssoty` reads the effective rule surfaces of eight agent harnesses (Claude Code,
Codex, Cursor, Copilot, Gemini, Cline, Windsurf, Continue) and shows —
**deterministically, with no LLM and no network** — where two models diverge:
which rules one model applies that the other never sees, which shared rules load
under a *different guarantee* (always-on vs skill-gated), which same-named copies
have silently **drifted to different content**, and which cross-references break
across the boundary. It also quantifies the per-turn token cost ("Context Tax")
as a secondary metric.

---

## The problem

You point Claude Code, Codex, and Cursor at one "shared" rule set and expect identical
behavior. They don't behave identically — because each harness resolves a **different
effective rule set**. The same canonical file can:

- load **always-on** in one harness (injected every turn) but **skill-gated** in
  another (loaded only when a skill triggers) — same file, unequal guarantee;
- reference a sibling rule that exists in one harness but was **never distributed**
  to the other — a pointer that resolves on one side but not the other;
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

Beyond load *mechanics* (present/absent, always-on vs skill-gated), ssoty also catches
**content drift**: a `same rule, divergent content` category fires when two harnesses
carry the **same filename but different text in separate copies** (distinct `realpath`) —
the classic copy-instead-of-symlink mistake where two models silently enforce different
versions of the "same" rule. A symlinked single source of truth shares one `realpath` and
is byte-identical by construction, so it never trips this; the warning fires precisely
when the SSOT collapse did *not* happen.

## What ssoty does

```
$ uvx ssoty audit examples/messy-setup
ssoty audit — 1 Critical, 3 Warning, 5 FYI

  [Critical] broken_symlink (claude-code)
      .../.claude/rules/broken-link.md
      symlink target does not resolve: ./nope.md

  [Warning] dangling_cross_ref (codex)
      .../.codex/skills/global-agent-rules/references/shared-style.md
      references 'team-rules.md' — present in another harness but not loaded
      here; verify the pointer is reachable in this harness's context

  [Warning] load_asymmetry (claude-code+codex)
      shared-style.md
      same rule loads differently per harness (claude-code=always-on,
      codex=skill-gated) — shared file, unequal guarantee
  ...
  [FYI] dangling_cross_ref (codex)
      references 'meta-layout.md' (absent here, intentional per .ssotyignore)
```

It tiers a **genuine** cross-harness divergence (Warning) above **intentional**
non-sharing you declared in `.ssotyignore`, a canonically-shared (symlinked) pointer,
or a per-harness entrypoint (all FYI) — precision over noise. The only structural
`Critical` is `broken_symlink` (a symlink whose target is gone), so `--ci` blocks on
a truly broken config, not on an intentional SSOT layout.

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
| `broken_symlink` | Critical | symlinked rule whose target is gone (the only structural Critical) |
| `dangling_cross_ref` | Warning / FYI | a rule references a sibling absent in this harness (Warning = real cross-harness divergence; FYI if declared intentional, canonically-shared via symlink, a per-harness entrypoint, or not found anywhere) |
| `load_asymmetry` | Warning | same rule, different load basis per harness |
| `content_divergence` | Warning | same rule *name* in ≥2 harnesses whose **content** differs across separate copies (distinct `realpath`) — copy-instead-of-symlink drift; symlinked SSOT (shared `realpath`) and broken symlinks are excluded |
| `duplicate_content` | Warning / FYI | identical blocks duplicated within a harness (Warning = token rent); cross-harness expected SSOT sharing rolled up into one FYI |
| `non_shared_surface` | FYI | a non-entrypoint rule present in one harness only (per-harness entrypoints are skipped) |
| `skill_integrity` | Warning | skill dir without a `SKILL.md` |
| `weak_directive` | FYI | a weak modal (`should`, `try to`, …) hedges a hard-requirement signal (`never`, `security`, …) on the same line in an always-on rule |

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

### Init — scaffold a manifest (zero to `ssoty.json`)
New to `sync`? `ssoty init` detects the harnesses already present at a root and writes a
starter `ssoty.json` for you — the one-command on-ramp to `sync`:

```bash
ssoty init                      # PREVIEW: detect harnesses, print the proposed ssoty.json, write nothing
ssoty init --apply              # write ./ssoty.json (refuses to overwrite an existing one)
ssoty init --apply --force      # overwrite an existing ssoty.json
ssoty init && ssoty sync        # scaffold, then preview the link plan
```

`init` **reuses the same detection as `audit`** (no second filesystem walk), so it scaffolds
exactly the harnesses that resolve real rule docs. If your rules already symlink into a shared
directory it infers that as the canonical `common` source (and marks each harness `"common": true`);
otherwise it emits a safe **placeholder** common source with a `_comment` telling you to fill it in.
It is **preview by default** (writes nothing), writes **only** `ssoty.json` (never a rule file), and
**never overwrites** an existing manifest without `--force`. The emitted manifest round-trips straight
into `sync`.

### Sync — from auditor to manager (dry-run + backup first)
`ssoty audit` *tells you* harnesses diverged. `ssoty sync` *fixes the cause*: it
distributes **one canonical rule source** as symlinks into every harness target, so all
models point at byte-identical files (same inode) and divergence collapses at the root.
The auditor becomes the manager — and `audit` becomes the natural post-condition check,
since **sync writes exactly what audit reads**.

```bash
ssoty sync                      # DRY-RUN: prints the exact link plan, writes nothing
ssoty sync --apply              # create/replace the symlinks; backs up anything it replaces
ssoty sync --manifest ssoty.json --apply
ssoty sync --apply && ssoty audit --ci   # distribute, then prove coherence in CI
```

Sync is driven by an **`ssoty.json` manifest** (stdlib JSON only — no extra
dependencies) describing one read-only canonical `source` tree and the per-harness
`target` paths it links into. A directory target receives one symlink per resolved
source basename; a bare-file target (like `CLAUDE.md`) receives a single link. See
[`examples/ssoty.json`](examples/ssoty.json):

```json
{
  "version": 1,
  "method": "symlink",
  "common": { "sources": [{ "dir": "agent-rules/common", "pattern": "*.md" }] },
  "harnesses": {
    "claude-code":            { "target": "~/.claude/rules", "sources": [{ "dir": "agent-rules/claude" }], "common": true },
    "claude-code-entrypoint": { "target": "~/.claude/CLAUDE.md", "sources": [{ "file": "agent-rules/CLAUDE.md" }] },
    "codex":                  { "target": "~/.codex/skills/global-agent-rules/references", "sources": [{ "dir": "agent-rules/codex" }], "common": true }
  }
}
```

Hard safety, mirroring `ssoty fix`: **dry-run is the default** (the exact plan prints,
nothing is written, no backup dir is created); only `--apply` mutates. On `--apply`,
before replacing any existing real file or differing symlink it backs up the node into
`.ssoty-backup/<timestamp>/` (path-preserving) — link-aware, so a replaced symlink's old
target string is recoverable. It only ever writes the manifest-declared `target` paths
(a target escaping the root is rejected, exit 2, before any write) and treats the
canonical `source` as read-only. It is **idempotent** (a second `--apply` is a pure
no-op, no new backups) and cleans only *its own* orphaned symlinks (links pointing into
the canonical source whose target vanished) — your unrelated symlinks are never touched.
`--method symlink` is the default and currently only method.

### Adopt — bootstrap a canonical SSOT from scattered copies (dry-run + backup first)
Before `init`/`sync` there is `adopt`: it scans the harnesses present at a root, classifies
every same-named rule, and proposes a canonical layout — `common/<name>` for rules that are
**byte-identical across two or more harnesses**, `<harness>/<name>` for harness-private rules.
It **builds the canonical source that `init` infers and `sync` distributes**, turning a messy
"copies everywhere" setup into a single SSOT in one command.

```bash
ssoty adopt                       # INTERACTIVE TUI (on a terminal): classify rules, then 'a' to apply
ssoty adopt --plan                # non-interactive TEXT preview (for CI/pipes): classify, write nothing
ssoty adopt --no-tui              # force the text path even on a terminal
ssoty adopt --apply               # move/copy rules into agent-rules/, symlink originals, back up first
ssoty adopt --apply --no-symlink-originals   # just move/copy; leave originals as real files
ssoty adopt --canonical-dir my-rules --apply # custom canonical root (validated under PATH)
ssoty adopt --apply && ssoty init && ssoty sync   # the full lifecycle
```

On a real terminal, `adopt` is now **interactive by default**: a two-pane TUI lists every
classified rule on the left and its content preview + a classify chooser on the right. Toggle a
rule to `common/` (all harnesses) or to a single `<harness>/` — `common` and the per-harness picks
are mutually exclusive — then press `a` to apply or `q` to quit. **DIVERGENT** rules cannot be set
to `common` (no chooser is shown — resolve them manually and re-run). The TUI performs **no**
filesystem mutation of its own: pressing `a` rebuilds the plan from your choices and calls the
exact same deterministic engine the text path uses (same move/backup/symlink, same `--force`
guard). The interactive front-end is powered by [Textual](https://textual.textualize.io/) (a core
dependency); the engine itself stays pure stdlib. When stdin/stdout are not both TTYs (CI, pipes),
or with `--apply`/`--plan`/`--no-tui`, `adopt` automatically uses the non-interactive text path.

`adopt` reuses the **exact content-identity grouping** the auditor's `content_divergence` check
uses — it buckets each name by `(realpath, normalized content)`, so an already-symlinked SSOT
collapses to one bucket. Four outcomes per name: **COMMON_CANDIDATE** (identical across ≥2
harnesses → `common/`), **HARNESS_SPECIFIC** (one harness → `<harness>/`), **ALREADY_SHARED**
(already one inode → no move), and **DIVERGENT** (same name, *different* content). Divergent rules
are **flagged, never auto-merged**: every variant is backed up, the originals are left in place,
and a short content fingerprint per variant is printed so you resolve the conflict deliberately —
`adopt` never writes a single `common/<name>` from a divergent set. Per-harness **entrypoints**
(`CLAUDE.md`/`AGENTS.md`/`GEMINI.md`/…) are excluded from consolidation — each harness owns its own
copy by design — and left in place. Hard safety mirrors `fix`/`sync`: **preview by default**;
`--apply` backs up every moved/replaced node into `.ssoty-backup/<timestamp>/` *before* any
mutation; destinations are validated under the root (an escaping `--canonical-dir` is rejected,
exit 2, before any write); idempotent (a re-run skips already-symlinked originals and
identical-content writes); `--force` is required only to overwrite a canonical dest that differs.

### Add — place ONE new rule into the canonical SSOT
Once you have a canonical source, `ssoty add` drops a single new rule into the right place so it
propagates correctly:

```bash
ssoty add my-new-rule.md                         # no choice -> prints candidate placements, does NOT guess
ssoty add my-new-rule.md --common --apply        # write into the canonical common/ (syncs to ALL harnesses)
ssoty add my-new-rule.md --harness codex --apply # write into one harness's own source
ssoty add my-new-rule.md --common --apply && ssoty sync   # then distribute
```

`add` reads the canonical `common` dir and per-harness targets from your `ssoty.json` manifest
(falling back to the `agent-rules/common` placeholder when there is no manifest yet). `--common`
and `--harness` are **mutually exclusive**; with neither, `add` previews the available placements
and **refuses to guess**. Same safety contract: **preview by default**, `--apply` to write, backup
first on overwrite, `--force` required to overwrite differing content, idempotent (identical content
is skipped), and the destination is always validated under the root. It writes exactly **one file**
and prints the next command (`ssoty sync`) — no implicit chaining, you stay in control.

> **Lifecycle:** `adopt` → `init` → `add` → `sync` → `audit`. `adopt` builds the canonical
> source; `init` scaffolds the manifest that references it; `add` drops in new rules; `sync`
> distributes everything as symlinks; `audit` proves coherence.

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
Gemini CLI (`GEMINI.md`, `~/.gemini/GEMINI.md`), Cline (`.clinerules/` directory,
legacy `.clinerules`, `AGENTS.md`), Windsurf (`.windsurf/rules/*.md`, legacy
`.windsurfrules`), and Continue (`.continue/rules/*.md`). Empty harnesses are skipped.
Point ssoty at `$HOME` or a project root.

## Privacy
ssoty audits *your* config; its output can quote your rules verbatim. It runs
**entirely locally** (no hosted service). This repo ships **synthetic fixtures
only**. See [`SECURITY.md`](SECURITY.md). Never commit ssoty output to a public repo.

## Roadmap (phase 2)
`ssoty sync` auto-dedup, a `copy` method alongside `symlink`, opt-in live "canary"
runtime probe, LLM semantic
conflict detection, Gemini support, marketplace packaging.

## Background
The design rationale lives in [`docs/RFC.md`](docs/RFC.md).

## License
[MIT](LICENSE)

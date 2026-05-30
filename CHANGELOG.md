# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.9] — 2026-05-30
### Changed
- **`dangling_cross_ref` severity recalibrated — no longer emits Critical.** A reference
  that resolves in *another* harness is by definition reachable somewhere, so flagging it
  `Critical` ("config broken") misread intentional symlink/canonical SSOT layouts and could
  false-block `--ci`. It now tiers as **Warning** (genuine cross-harness divergence worth a
  look, non-blocking) and **FYI** (declared in `.ssotyignore`; the referencing doc is
  canonically shared — same realpath symlinked into ≥2 harnesses; the target is a known
  per-harness entrypoint; or the ref is not found in any surface). **`broken_symlink` is now
  the sole structural `Critical`** — the only condition that means the config is actually
  broken. `--ci` still exits non-zero on `broken_symlink`.
### Fixed
- **Three cross-harness dangling false positives eliminated** (precision, deterministic, no
  new deps):
  - `referenced_docs` now strips leading YAML frontmatter before scanning, so a
    `source: ~/.codex/AGENTS.md` provenance line is no longer treated as a pointer. Gated on
    the frontmatter containing a `key:` line so a leading `---` horizontal rule (not YAML)
    keeps its body scanned.
  - `referenced_docs` now drops entrypoint filenames (`CLAUDE.md`, `AGENTS.md`, …) that are
    embedded in a glob/path allowlist list (e.g. ``Direct writes OK for: `~/.claude/**`,
    `CLAUDE.md` ``) — a permission mention, not a pointer. A *lone* `.md` backtick in prose
    is still a genuine pointer and still extracts. Markdown-link refs are never affected.
  - `check_dangling_cross_ref` resolves canonical identity via `os.path.realpath`: a ref made
    by a doc that is the same canonical file in ≥2 harnesses, and a ref *to* a known
    entrypoint present elsewhere, both downgrade to FYI.
- **`non_shared_surface` skips per-harness entrypoints.** `CLAUDE.md`/`AGENTS.md`/… are
  tautologically "present only in one harness" by design and carry no divergence signal; a
  genuine non-entrypoint rule present in one harness only still emits FYI.
- **`duplicate_content` cross-harness sharing rolled up.** Expected cross-harness SSOT
  sharing (once per harness, not token rent) now emits a single summary FYI (block count +
  total tokens) instead of one FYI per block, so it cannot drown the tier. Within-harness
  duplication (real token rent every turn) is unchanged — still one Warning per block.

## [0.1.8] — 2026-05-30
### Fixed
- `ssoty fix` broken-symlink backup is now portable across Python 3.10–3.13.
  It recreates the link via `os.symlink(os.readlink(...))` instead of
  `shutil.copy2(follow_symlinks=False)`, whose copystat-on-a-dangling-symlink
  silently skipped the backup on Linux + Python 3.10/3.11 (CI compat matrix).
  The backup-before-remove safety guarantee now holds on every supported Python.
### Fixed
- Release workflow triggers only on full semver tags (`v*.*.*`) so moving the
  floating `v0` tag no longer starts a duplicate publish; publish is `skip-existing`.

## [0.1.7] — 2026-05-30
### Added
- **Windsurf** and **Continue** harness support. Eight harnesses now. Windsurf:
  `.windsurf/rules/*.md` (conditional — Cascade activation modes) plus the legacy
  always-on `.windsurfrules`. Continue: `.continue/rules/*.md` (conditional — each
  rule block declares its own apply semantics).
- **`weak_directive`** check (FYI, never blocking). Scans only always-on docs — the
  actually enforced surface — and flags the narrow co-occurrence where a weak modal
  (`should`, `try to`, `nice to have`, `where possible`, `if possible`) hedges a
  hard-requirement signal (`never`, `must`, `required`, `security`, `secret`,
  `credential`, `production`/`prod`, `irreversible`, `destructive`, `force push`,
  `drop table`) on the same line. Fenced code, table rows, blockquotes, and
  example/anti-rationalization lines are skipped, and standalone `should` is never
  flagged, keeping false positives low. Deterministic, no LLM, no network.

## [0.1.6] — 2026-05-30
### Added
- **`ssoty diff`** — cross-model rule **divergence** between two harnesses, the new
  headline. Answers the one question that matters: *do these two models operate under
  the same effective rules?* For an ordered pair (A, B) it reports rules only in A,
  rules only in B, shared rules that load under a *different guarantee* (always-on vs
  skill-gated), and cross-references that break across the boundary (a doc in A points
  at a rule that loads only in B). Omit `--a/--b` to diff every present pair
  (deterministic, each unordered pair once); name two with `--a X --b Y`. `--json` and
  `--redact` mirror `resolve`/`metrics`. Strictly **read-only** — like `resolve`, it
  only resolves surfaces and prints; it never writes, backs up, or imports `fix`. Exit
  0 on success (informational, not a gate — use `audit --ci` to gate); exit 2 only on a
  usage error (unknown/half-specified harness). New dependency-free `diff.py`; stdlib
  only, deterministic, no LLM, no network.
### Changed
- README / README-ko reframed so the opening pitch is cross-model rule **divergence**
  ("your Claude and your Codex apply different rules"); `duplicate_content` / Context
  Tax is demoted to a clearly secondary "also measures" metric. No checks, metrics, or
  behavior removed — only reordered and relabeled.

## [0.1.5] — 2026-05-30
### Added
- **`ssoty fix`** — safe, DRY-RUN-first remediation of audit findings. Default prints
  exactly what *would* change and writes nothing; mutation requires explicit `--apply`.
  On `--apply`, every touched file is first copied into a timestamped, path-preserving
  backup dir under the audited root (`.ssoty-backup/<UTC-timestamp>/`) and its location
  is printed before any change. Safe remediations only: (1) remove a *broken* symlink
  (its target does not resolve, so nothing real is lost; re-stat guard at apply time),
  and (2) with `--scaffold-ignore`, append intentionally non-shared rule names to
  `.ssotyignore` (a file ssoty owns, skipping already-declared names). It never edits
  real rule files, never touches a valid symlink, and is idempotent (a second `--apply`
  finds no work, creating no new backup). `--redact` masks home paths/emails like the
  other subcommands. Add `.ssoty-backup/` to your gitignore so backups are never
  committed. Implemented in a new dependency-free `fix.py`; stdlib only, deterministic.

## [0.1.4] — 2026-05-29
### Added
- **Cline** harness support: `.clinerules/` directory (all rule files, always-on),
  legacy single-file `.clinerules`, and `AGENTS.md`. Six harnesses now: Claude Code,
  Codex, Cursor, Copilot, Gemini, Cline.
- **SARIF 2.1.0 output** for `ssoty audit` via `--format {text,json,sarif}` (default
  `text`); `--json` is kept as a back-compat alias for `--format json`. SARIF is
  stdlib-only JSON suitable for `github/codeql-action/upload-sarif`. Severity maps
  Critical→error, Warning→warning, FYI→note. `finding.file` is emitted verbatim as
  the artifact URI (load_asymmetry/duplicate_content URIs are non-clickable in v1).
### Fixed
- Cursor `.mdc` `alwaysApply` is no longer mis-parsed when the YAML value carries an
  inline comment (`alwaysApply: true # primary rule`); an unquoted trailing comment
  is stripped before comparison, so always-on rules are no longer mis-classified
  conditional (which corrupted load_basis, load_asymmetry, and the metrics token split).

## [0.1.3] — 2026-05-29
### Added
- **Gemini CLI** harness support: hierarchical `GEMINI.md` (global `~/.gemini/GEMINI.md`
  + project `./GEMINI.md`), always-on. Five harnesses now: Claude Code, Codex, Cursor,
  Copilot, Gemini.

## [0.1.2] — 2026-05-29
### Added
- **Cursor** harness support: `.cursor/rules/*.mdc` (load basis read from the
  `alwaysApply` frontmatter — always-on vs conditional) and legacy `.cursorrules`.
- **GitHub Copilot** harness support: `.github/copilot-instructions.md`.
- Empty harnesses (no rule files at the audited root) are dropped, so ssoty only
  reports on harnesses actually present.

## [0.1.1] — 2026-05-29
### Fixed
- Symlinked rule *directory* is now globbed instead of collapsing into one bogus
  doc (a whole harness surface could silently vanish). [C1]
- PII gate matches per token, so a real email sharing a line with a synthetic one
  no longer slips through. [C2]
- Basename dedup no longer lets `rules/CLAUDE.md` shadow the top-level `CLAUDE.md`. [M1]
- `duplicate_content` now detects within-doc / within-harness repetition. [M2]
- `referenced_docs` handles `#anchors`, link `"titles"`, and uppercase `.MD`. [m2]
- `redact` no longer drops the separator for a home path with a trailing slash. [m1]
### Changed
- Token counts are char/4 by default for deterministic, portable output; set
  `SSOTY_EXACT_TOKENS=1` to opt into tiktoken for exact counts. [M3]

## [0.1.0] — 2026-05-29
### Added
- Static cross-harness coherence checks: `broken_symlink`, `dangling_cross_ref`,
  `load_asymmetry`, `non_shared_surface`, `duplicate_content`, `skill_integrity`.
- `ssoty audit`, `ssoty metrics` (Context Tax), and `ssoty resolve` (effective
  surface per harness: load basis + per-doc tokens) CLI; `--json`, `--redact`, `--ci`.
- Claude Code + Codex skill adapters; GitHub Action; OIDC Trusted-Publishing release workflow.
- Synthetic fixtures + reproducible benchmark; PII allowlist gate.

### Notes
- `dangling_cross_ref` distinguishes genuine cross-boundary breaks (Critical) from
  intentional non-sharing declared in `.ssotyignore` (FYI).
- `referenced_docs` ignores placeholder/glob tokens (`<topic>.md`, `*.md`).
- `.ssotyignore` also downgrades intentional `load_asymmetry` to FYI.
- Cross-harness identical content is FYI (expected sharing); only within-harness
  duplication is a Warning.

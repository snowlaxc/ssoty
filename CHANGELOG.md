# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.6.0] — 2026-06-08
### Added
- **Configurable canonical *home* (`~/.ssoty/` by default), persisted.** A new global
  `ssoty --home <path>` sets where `adopt` consolidates rules; the choice is saved to
  `${XDG_CONFIG_HOME:-~/.config}/ssoty/config.json` and reused by every later command.
  Resolution precedence: `--home` flag > config file > the `~/.ssoty` default. `adopt`'s
  default canonical is now this home instead of the old `$HOME/agent-rules`. (`--canonical-dir`
  still overrides per-run.) New module `ssoty.config` (`config_path` / `resolve_home` /
  `save_home`, stdlib only). A corrupt config degrades to the default rather than crashing.
- **The canonical home may live OUTSIDE the scan root.** It is a trusted, user-chosen location,
  so adopt no longer refuses a canonical dir outside the scanned `$HOME`. Engine-generated
  destinations under it (`common/<name>` / `<harness>/<name>`) contain no `..`, so nothing
  escapes the home.
- **The adopt TUI now offers EVERY scanned harness as an assignment target** — not only the
  harnesses a rule already has a copy in. A codex-only rule like `preservation.md` can be
  assigned to `claude-code` even though claude has no copy of it (`AdoptPlan.scanned_harnesses`
  carries the candidate set). Selecting 2+ harnesses consolidates the rule into a single shared
  `common/<name>` copy; selecting one yields `<harness>/<name>`.

### Changed
- `ssoty.adopt`: `AdoptPlan` gains a `scanned_harnesses` field; canonical-dir resolution drops
  the scan-root containment constraint (the configurable-home model) while `add` keeps its
  root-contained dest check.

### Security
- adopt **refuses** a canonical dir containing `..` segments and a canonical **home that is a
  symlink** (would write through to the link target) — the configurable home may be absolute and
  external, but cannot escape via `..` or a symlinked node.
- `build_modified_rules` validates harness names against `scanned_harnesses`, so a hand-built
  overrides dict cannot inject a path separator / `..` into `canonical_rel`.
- copy-less harness assignment keeps **zero** variants, so `adopt --apply` never symlinks a
  deselected harness's original into another harness's bucket (no cross-harness leak).
- `config.save_home` writes atomically (tmp + `os.replace`); a persistence failure warns
  instead of crashing.

## [0.5.0] — 2026-06-01
### Added
- **`ssoty adopt` is now interactive by default — a Textual TUI.** On a real terminal, `adopt`
  launches a two-pane classifier: every classified rule on the left (with a kind badge), its
  content preview + a classify chooser on the right. Toggle a rule into the canonical `common/`
  (all harnesses) or into a single `<harness>/` — `common` and the per-harness picks are
  **mutually exclusive** — then press `a` to apply or `q` to quit. **DIVERGENT** rules show no
  chooser and cannot be forced to `common` (resolve them manually and re-run), exactly mirroring
  the engine's hard invariant.
- **The TUI reuses the deterministic engine verbatim — zero new file-mutation code.** Pressing
  `a` rebuilds an `AdoptPlan` from the user's choices and calls the SAME `adopt_needs_force` /
  `apply_adopt_plan` the text path calls (same backup-first move/symlink, same `--force` guard).
  All safety invariants (backups before mutation, root containment, idempotence, divergence never
  auto-merged) are inherited, not re-implemented.
- **New `--plan` and `--no-tui` flags.** `--plan` forces the non-interactive text preview (for CI
  and pipes); `--no-tui` disables the TUI even on a terminal. `adopt` also auto-falls back to the
  text path whenever stdin/stdout are not both TTYs, or when `--apply` is given (explicit
  non-interactive intent). The previous text preview / `--apply` behavior is unchanged on that path.

### Changed
- **`textual>=0.27.0` is now a core dependency** (the floor that introduced `SelectionList`). It
  powers ONLY the `adopt` TUI and is imported **lazily** inside `ssoty.tui` — `audit`, `diff`,
  `sync`, `resolve`, `fix`, and `metrics` never pay its import cost, and the core engine
  (`resolver`/`checks`/`adopt`/`sync`/…) stays pure stdlib, deterministic, and offline.

## [0.4.0] — 2026-05-31
### Added
- **New `ssoty adopt` command — bootstrap a canonical SSOT from scattered copies.** Where
  `init`/`sync` assume a canonical source already exists, `adopt` *builds* it: it scans the
  harnesses present at a root (reusing the auditor's `resolve_all` — no second filesystem walk)
  and classifies every same-named rule using the **exact content-identity grouping** of the
  `content_divergence` check — bucketing each name by `(realpath, normalized content)` so an
  already-symlinked SSOT collapses to one bucket. Four outcomes per name: **COMMON_CANDIDATE**
  (byte-identical across ≥2 harnesses → canonical `common/<name>`), **HARNESS_SPECIFIC** (one
  harness → `<harness>/<name>`), **ALREADY_SHARED** (already one inode → no move), and
  **DIVERGENT** (same name, different content). The proposed canonical tree is printed in the
  readable `init`-style layout, with a content fingerprint per divergent variant.
- **Divergence is flagged, never auto-merged.** A DIVERGENT rule backs up every variant, leaves
  the originals in place, and prints an explicit "resolve manually then re-run" line — `adopt`
  **never** writes a single `common/<name>` from a divergent set (a hard invariant, identical to
  how `audit` defines divergence so the two never contradict). Per-harness entrypoints
  (`CLAUDE.md`/`AGENTS.md`/`GEMINI.md`/…) are excluded from consolidation and left in place;
  broken symlinks (resolver `text=''`) are filtered before bucketing so an empty string never
  fabricates divergence.
- **New `ssoty add` command — place ONE new rule into the canonical SSOT.** `--common` writes
  into the canonical `common/` source (read from the `ssoty.json` manifest, falling back to the
  `agent-rules/common` placeholder); `--harness NAME` writes into that harness's own source
  (manifest target, else a canonical `<harness>/` dir). The two are **mutually exclusive**; with
  neither, `add` previews the candidate placements and **refuses to guess**. It writes exactly one
  file and prints the next command (`ssoty sync`) — no implicit chaining.
- **Lifecycle: `adopt` → `init` → `add` → `sync` → `audit`.** `adopt`'s default canonical root
  (`<root>/agent-rules`) lines up with `init.PLACEHOLDER_DIR` so the stages compose: after `adopt`
  replaces originals with symlinks into canonical, `init` infers the same canonical dir.
### Safety
- **Hard safety, mirroring `fix`/`sync`/`init`.** Both commands are **PREVIEW by default** (write
  nothing, create no backup dir). `--apply` mutates; `--force` is required only to overwrite a
  destination that already exists with *differing* content. On `--apply`, **every** rule file
  moved/replaced and **every** original about to become a symlink is backed up via `fix._backup_node`
  into one timestamped `.ssoty-backup/<stamp>/` *before* any mutation (backup-before-mutate ordering
  is the highest-stakes invariant — both commands touch real rule files). All destinations are
  validated under the root via `sync._require_under_root` **and** `sync._require_realpath_under_root`
  (an escaping `--canonical-dir` or manifest target is rejected, exit 2, before any write).
  Idempotent: a re-run skips originals already symlinked to canonical (`sync._same_link`) and
  identical-content writes (`normalize_content` equality). Deterministic (sorted iteration,
  lexicographic tie-breaks), offline, stdlib-only (`hashlib` for fingerprints is stdlib);
  `dependencies` stays `[]`. Version bumped 0.3.0 → 0.4.0.

## [0.3.0] — 2026-05-30
### Added
- **New `ssoty init` command — zero-to-manifest scaffolding.** It detects the harnesses
  actually present at a root and writes a starter `ssoty.json` so the on-ramp to `ssoty sync`
  is one command. Detection **reuses the auditor's `resolve_all`** — no second filesystem
  walk, identical real/fixture semantics — so the scaffolded harnesses are exactly the ones
  that resolved real rule docs. Each present harness is mapped to a manifest entry: a directory
  source (`.claude/rules`, `.cursor/rules`, …) becomes a directory `target`, and a single-file
  source (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, …) becomes a single-file `target` with one
  `{"file": …}` source — matching `sync`'s dir-vs-file target semantics so the manifest
  round-trips back through `sync` against the same root.
- **Canonical common-source inference (read-only).** `init` reads the `is_symlink` /
  `symlink_target` metadata `resolve_all` already attached to each doc (no extra stat/readlink):
  if a harness's rules symlink into a shared dir, that dir is emitted as `common.sources` and
  the harness gets `"common": true`. When symlinks straddle sibling dirs it falls back to
  `os.path.commonpath`, with a HOME/root floor that degrades to a safe **PLACEHOLDER** (plus a
  guiding `_comment`) rather than emitting a too-broad source. With no symlinks at all (real
  copies), the placeholder skeleton is emitted. Each harness's own glob pattern is preserved
  (Cursor's `*.mdc` is not silently rewritten to `*.md`).
- **Hard safety, mirroring `fix`/`sync`.** `ssoty init` is **PREVIEW by default** — it prints
  the proposed manifest and writes nothing. Only `--apply` writes `root/ssoty.json`; an existing
  manifest is **never overwritten without `--force`** (refusal exits 2, leaving the file
  byte-identical). `init` writes ONLY the manifest — it never creates, links, moves, or deletes
  any rule file (detection and inference are strictly read-only). `--redact` masks home paths
  and emails, consistent with the other subcommands. Deterministic, offline, stdlib-only;
  `dependencies` stays `[]`.

## [0.2.0] — 2026-05-30
### Added
- **New `ssoty sync` command — from auditor to manager.** Where the read-only commands
  *report* cross-harness divergence, `sync` *fixes the cause*: it distributes one read-only
  canonical rule **source** as symlinks into every harness **target**, so all harnesses point
  at byte-identical files (same inode) and content/load divergence collapses at the root.
  The round-trip contract is deliberate — `sync` WRITES exactly the paths `audit` READS
  (`.claude/rules`, `.claude/CLAUDE.md`, `.codex/skills/global-agent-rules/references`,
  `.cursor/rules`, …) — so `ssoty sync --apply && ssoty audit --ci` is a coherent CI gate.
- **`ssoty.json` manifest (stdlib JSON only).** Parsed with `json.load`; no tomllib/tomli/yaml
  and `dependencies` stays `[]`. Schema mirrors the resolver's source model: a `common.sources`
  block linked into every harness with `"common": true`, and per-harness `target` + `sources`
  + `common` (bool). A directory target receives one symlink per resolved source basename; a
  bare-file target (e.g. `CLAUDE.md`) receives exactly one link. Relative `dir`/`file`/`target`
  paths resolve against the manifest's own directory (portable); `~` expands via
  `os.path.expanduser`. Sample at [`examples/ssoty.json`](examples/ssoty.json).
- **Hard safety, mirroring `ssoty fix`.** Dry-run is the DEFAULT (`ssoty sync` prints the exact
  per-link plan — `<target-link> -> <source>` — and writes nothing, creating no backup dir);
  only `--apply` mutates. On `--apply`, before replacing any existing real file or *differing*
  symlink the node is backed up into `.ssoty-backup/<UTC>/` (path-preserving, reusing the `fix`
  backup helpers — link-aware, so a replaced symlink's old target string stays recoverable).
  Link classification mirrors `install.sh`'s `link_safe`: skip-unchanged / backup+relink /
  backup-real-file / new-link, plus `cleanup_orphan_symlinks` (only links pointing INTO the
  canonical source whose target vanished are removed; foreign links are never touched).
  Idempotent: a second `--apply` on a synced tree produces zero backups and zero writes.
- **`--method symlink`** (default and currently only method; reserved so a future `copy` is
  additive), **`--manifest PATH`**, and **`--redact`** flags on `sync`. New `ssoty/sync.py`
  holds the pure plan/apply logic; `cli.py` stays a thin dispatcher (`cmd_sync`).
### Safety
- Full manifest validation precedes the first mutation: a missing file, invalid JSON, or a
  `target` escaping the sync root exits 2 (to stderr) with **no partial write**. Sync only ever
  writes manifest-declared `target` paths and treats the canonical `source` as read-only.

## [0.1.10] — 2026-05-30
### Added
- **New check `content_divergence` (Warning).** For any rule *name* present in ≥2 harness
  surfaces, ssoty now compares the docs' normalized text and flags the case where two
  copies have the **same filename but divergent content and distinct `realpath`** — i.e. a
  copy-instead-of-symlink drift where two models silently enforce different versions of the
  "same" rule. This is the dual of the canonical-realpath insight: a symlinked SSOT collapses
  N mounts to one `realpath` (byte-identical, never fires); `content_divergence` fires exactly
  when that collapse did NOT happen. Orthogonal to `load_asymmetry` (which is about
  `load_basis`, not content): a rule can share a load basis yet diverge in content, or vice
  versa. Emits **one Warning per diverging name** (not per pair). Excludes: canonically-shared
  (same-realpath) docs, broken symlinks (skipped before comparison so an empty `text` can't
  fake divergence), and names present in only one harness. Entrypoints are *not* excluded —
  two harnesses legitimately sharing one entrypoint filename drifting is a real signal.
- **`ssoty diff A B` gained a `same rule, divergent content` category** mirroring the check
  pairwise (text section, JSON `content_divergence` field, verdict tally), and it now counts
  toward the pair's `coherent` verdict.
- **`normalize_content(text)` in `models.py`** — conservative, deterministic normalization
  (`splitlines` for CRLF/LF, `rstrip` per line, drop leading/trailing blank lines). Does NOT
  lowercase, collapse internal whitespace, strip markdown, or sort lines, so a changed word
  mid-line still surfaces as real drift. Shared by `checks.py` and `diff.py`.
- **Audit trust line.** `ssoty audit` text output now prints `(R rule docs across H harnesses
  checked)` and the JSON `summary` carries `rule_docs` / `harnesses`, so a clean report is
  distinguishable from a no-op.

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

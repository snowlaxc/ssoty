# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

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

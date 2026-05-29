# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

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

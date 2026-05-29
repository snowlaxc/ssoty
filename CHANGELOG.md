# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- Static cross-harness coherence checks: `broken_symlink`, `dangling_cross_ref`,
  `load_asymmetry`, `non_shared_surface`, `duplicate_content`, `skill_integrity`.
- `ssoty audit` and `ssoty metrics` (Context Tax) CLI; `--json`, `--redact`, `--ci`.
- Claude Code + Codex skill adapters; GitHub Action; OIDC release workflow.
- Synthetic fixtures + reproducible benchmark.

### Changed
- `dangling_cross_ref` now distinguishes genuine cross-boundary breaks (Critical)
  from intentional non-sharing declared in `.ssotyignore` (FYI).
- `referenced_docs` ignores placeholder/glob tokens (`<topic>.md`, `*.md`).
- Cross-harness identical content is FYI (expected sharing); only within-harness
  duplication is a Warning.

## [0.1.0] — unreleased
Initial MVP.

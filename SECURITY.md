# Security & Privacy

## ssoty audits *your* config — its output quotes your rules verbatim

`ssoty` reads agent-harness configuration (e.g. `~/.claude/rules/`, `~/.codex/`,
`AGENTS.md`, `CLAUDE.md`). Its reports can contain **your absolute paths, email,
org name, and rule contents**.

### Do NOT commit ssoty output to a public repo
- Report files (`*.ssoty.json`, `ssoty-report.*`, snapshots) are git-ignored by
  default. Keep it that way.
- Use `ssoty audit --redact` to mask home paths (`$HOME`) and email addresses in
  any output you share.

### This repository contains synthetic data only
All fixtures under `examples/` use fake identities: `/home/dev`, `dev@example.com`,
`acme-corp`. No real personal or organizational data is committed. CI enforces a
PII gate (allowlist of synthetic patterns + denylist scan over the full history).

### Reporting a vulnerability
Open a private security advisory on GitHub, or a regular issue for non-sensitive
reports. No hosted service exists — ssoty runs entirely locally, so your config
never leaves your machine.

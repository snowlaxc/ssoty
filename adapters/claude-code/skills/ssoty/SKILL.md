---
name: ssoty
description: >-
  Audit cross-harness agent-rule coherence with the ssoty CLI. Use when asked to
  check whether shared rules (AGENTS.md / CLAUDE.md / ~/.claude / ~/.codex) apply
  consistently across Claude Code and Codex, to find broken or dangling rule
  cross-references, load asymmetry, or to measure rule "Context Tax" token cost.
argument-hint: "[audit|metrics] [path] [--redact] [--ci]"
allowed-tools: Bash(ssoty *) Bash(uvx ssoty *)
---

# ssoty — cross-harness rule coherence audit

Thin adapter around the standalone `ssoty` CLI (the real logic lives there).

Run it and interpret the output for the user:

```bash
${CLAUDE_SKILL_DIR}/scripts/run.sh "$ARGUMENTS"
```

- Default command is `audit` against `$HOME` if no arguments are given.
- Findings are labelled `Critical` / `Warning` / `FYI`. Explain Criticals first.
- For sharing output, suggest `--redact` (masks home paths + emails). Never paste
  un-redacted output into a public place — it can quote the user's real rules.

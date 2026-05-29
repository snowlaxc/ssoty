---
name: ssoty
description: >-
  Audit cross-harness agent-rule coherence with the ssoty CLI. Use when asked to
  check whether shared rules apply consistently across Codex and Claude Code, to
  find broken/dangling rule cross-references or load asymmetry, or to measure rule
  "Context Tax" token cost.
---

# ssoty — cross-harness rule coherence audit (Codex)

Thin adapter around the standalone `ssoty` CLI. Run it and interpret the output:

```bash
ssoty audit "$@"     # or: uvx ssoty audit "$@"
```

- Default to `ssoty audit` against `$HOME` when no path is given.
- Findings are `Critical` / `Warning` / `FYI`; explain Criticals first.
- Suggest `--redact` before sharing output; never paste un-redacted output
  publicly — it can quote the user's real rules.

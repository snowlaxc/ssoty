# Benchmark — messy vs clean (reproducible)

All numbers below are produced by ssoty on the **synthetic** fixtures in
`examples/` (no real config, no PII). Reproduce with the commands shown; results
are deterministic (no LLM, no network).

## Findings

| Setup | Critical | Warning | FYI |
|---|---|---|---|
| `examples/messy-setup` | **2** | 3 | 6 |
| `examples/clean-setup` | **0** | 0 | 5 |

```bash
uvx ssoty audit examples/messy-setup    # 2 Critical (broken symlink + cross-boundary dangling)
uvx ssoty audit examples/clean-setup    # 0 Critical
```

The 5 FYI in clean-setup are `non_shared_surface` notes (rules intentionally
present in one harness only) — informational, non-blocking.

## Context Tax (within-harness before/after)

Reported **per harness, never summed across harnesses**. `always-on` = injected
every turn (actual); `skill-gated` = loaded only when a skill triggers (potential).
Compare the *same* harness before vs after the cleanup.

| Harness / load basis | messy | clean | delta |
|---|---|---|---|
| claude-code · always-on | 206 | 149 | **−27.7%** |
| codex · skill-gated | 106 | 0 | **−100%** |

```bash
uvx ssoty metrics examples/messy-setup
uvx ssoty metrics examples/clean-setup
```

What changed between messy and clean:
- removed a duplicated style block (lower always-on surface on claude-code);
- removed a broken symlink doc;
- moved the shared style inline into Codex's always-on `AGENTS.md` instead of a
  separate skill-gated `references/` copy (eliminates the skill-gated potential
  surface, the load asymmetry, and the duplicate).

## Honesty notes
- Token counts use `tiktoken` when installed, otherwise a `char/4` heuristic
  labelled `approx` in the output. The before/after **sign** holds under both.
- A single audit is evidence of *structure*, not of runtime behavior. ssoty makes
  static, file-layer claims only (see roadmap for the opt-in live probe).

# Launch / promo drafts

Drafts only. Posting (and the account/voice) is the maintainer's call. All examples
use redacted/structural results — no real paths or PII.

## Show HN draft

**Title:** Show HN: ssoty – does your Claude and your Codex actually run the same rules?

**Body:**

Most of us point several coding agents (Claude Code, Codex, Cursor, Copilot, Gemini,
Cline, Windsurf, Continue) at one "shared" rule set — an AGENTS.md, a CLAUDE.md, a
symlinked rules dir — and assume they behave the same. They usually don't, because each
harness *resolves a different effective rule set*.

ssoty is a static auditor (no LLM, no network — deterministic) that reads each harness's
real rule surface and shows where two models diverge:

- rules one model loads that the other never sees;
- shared rules that load under a different guarantee (always-on in one, only-when-a-skill-
  fires in another);
- same-named rule files that were copied instead of symlinked and have silently **drifted**
  to different content;
- cross-references that resolve on one side of the boundary but not the other.

`ssoty diff claude-code codex` ends with a blunt verdict: do they operate under the same
rules, or not?

Run it with no install: `uvx ssoty audit` (audits ~/.claude, ~/.codex, …) or
`uvx ssoty diff --a claude-code --b codex`. It runs entirely locally — your rules never
leave your machine.

A real run on my own multi-harness setup: claude-code vs codex shared one canonical rule
set, yet **13 rules loaded under different guarantees** and a couple had drifted to
different content — i.e. the two agents were quietly enforcing different versions. That
gap is invisible until an agent ignores a rule you thought you'd shared.

Repo: https://github.com/snowlaxc/ssoty · PyPI: https://pypi.org/project/ssoty/

It also emits SARIF (`--format sarif`) for GitHub code-scanning, has a GitHub Action, and
a backup-first `ssoty fix` for the safe remediations. Feedback welcome — especially which
harness conventions I've gotten wrong.

## One-liner (for r/ClaudeAI, X, etc.)

Your Claude and your Codex read "the same" rules — but do they? `uvx ssoty diff` shows,
deterministically, where two agents enforce *different* effective rule sets (loaded
differently, drifted, or missing entirely). Local-only, no LLM.

## Honest framing notes (do NOT oversell)
- It's a static auditor, not a fixer of semantics — `ssoty fix` only does safe mechanical
  remediations (broken-symlink removal, backup-first).
- Token "Context Tax" is a char/4 estimate by default; it's a secondary metric, not the pitch.
- The pitch is divergence (do models run the same rules), not "you're wasting tokens."

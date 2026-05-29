# Contributing to ssoty

Thanks for your interest! ssoty is a small, deterministic, dependency-light tool —
contributions that keep it that way are very welcome.

## Principles
- **Deterministic core**: no LLM, no network in `src/ssoty`. Same input → same output.
- **Precision over noise**: a check that cries wolf is worse than no check. New checks
  must distinguish genuine defects from intentional/by-design config.
- **Privacy is non-negotiable**: never commit real config. Fixtures use synthetic
  identities only (`/home/dev`, `dev@example.com`, `acme-corp`). CI enforces a PII gate.

## Dev setup
```bash
git clone https://github.com/snowlaxc/ssoty && cd ssoty
uv run --extra dev pytest          # tests
uv run --extra dev ruff check src tests
uv run --extra dev black src tests
uv run ssoty audit examples/messy-setup   # try it
```

## Workflow
1. Fork → branch (`feat/...`, `fix/...`).
2. Add/adjust tests (coverage gate is 80%). Run ruff + black.
3. Ensure `bash scripts/pii_gate.sh` passes (no real paths/emails).
4. Open a PR to `main`. CI (`test`) must pass; a maintainer reviews.

## Adding a check
- Implement in `src/ssoty/checks.py` returning `Finding`s with the right `Severity`
  (`Critical` = blocking, `Warning` = should-fix, `FYI` = informational).
- Add a fixture case under `examples/` and a golden test in `tests/`.
- If a finding can be intentional, support suppression via `.ssotyignore`.

## Commit messages
Conventional Commits (`feat:`, `fix:`, `docs:`, `ci:`, …). Explain *why*.

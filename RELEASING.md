# Releasing ssoty

Releases publish to PyPI via **OIDC Trusted Publishing** (no API token stored).
Publishing is the one step that requires the maintainer's PyPI account — it
cannot be fully automated.

## One-time setup (maintainer, on pypi.org)
1. Log in to https://pypi.org as the project owner.
2. Go to **Account → Publishing → Add a pending publisher** and enter:
   - PyPI Project Name: `ssoty`
   - Owner: `snowlaxc`
   - Repository name: `ssoty`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repo: **Settings → Environments → New environment → `pypi`**
   (optionally add required reviewers so each publish needs approval).

## Cut a release
```bash
# 1. bump version in pyproject.toml (e.g. 0.1.0 -> 0.1.1) and update CHANGELOG.md
# 2. ensure green + clean
uv run --extra dev pytest -q && bash scripts/pii_gate.sh && uv build
# 3. tag and push -> triggers .github/workflows/release.yml
git tag v0.1.0
git push origin v0.1.0
```
The `release.yml` workflow then: runs the PII gate + tests, builds, publishes to
PyPI (OIDC), and creates a GitHub Release.

## After first publish
- `uvx ssoty` / `pipx install ssoty` start working.
- Move the floating major tag so the Action resolves: `git tag -f v0 v0.1.0 && git push -f origin v0`.

## Versioning
SemVer. `fix:` → patch, `feat:` → minor, `BREAKING CHANGE:` → major.
PyPI versions are immutable — never reuse a version; bump and re-tag instead.

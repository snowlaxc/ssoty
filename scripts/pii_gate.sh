#!/usr/bin/env bash
# PII gate: committed files must contain SYNTHETIC identities only.
# Allowlist: emails @example.com, paths /home/dev. Anything else is a leak.
# Generic by design — does not hardcode the author's real org/path, so the gate
# itself leaks nothing. Run in CI and before any push.
#
# Token-level matching (grep -o): if a line mixes a real address with a synthetic
# @example.com one, the real token alone still fails the gate (not the whole line).
# NUL-delimited file list (-z / -0) is safe for paths with spaces.
set -euo pipefail

if ! git rev-parse --git-dir >/dev/null 2>&1 || [[ -z "$(git ls-files)" ]]; then
  echo "pii_gate: no tracked files (run inside a git repo)"; exit 0
fi

fail=0
report() { echo "PII GATE FAILURE: $1"; echo "$2"; fail=1; }
scan() { git ls-files -z | xargs -0 grep -hoIE "$1" 2>/dev/null || true; }

# 1) Non-synthetic email addresses (anything not @example.com)
emails="$(scan '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' | grep -vE '@example\.com$' | sort -u || true)"
[[ -n "$emails" ]] && report "non-synthetic email" "$emails"

# 2) macOS-style real home paths
users="$(scan '/Users/[A-Za-z0-9._-]+' | sort -u || true)"
[[ -n "$users" ]] && report "/Users/ absolute path" "$users"

# 3) Linux home paths other than the synthetic /home/dev (username has no dot)
homes="$(scan '/home/[A-Za-z0-9_-]+' | grep -vxE '/home/dev' | sort -u || true)"
[[ -n "$homes" ]] && report "non-synthetic /home path" "$homes"

if [[ "$fail" -eq 0 ]]; then
  echo "pii_gate: OK — synthetic identities only."
fi
exit "$fail"

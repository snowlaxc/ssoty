#!/usr/bin/env bash
# PII gate: committed files must contain SYNTHETIC identities only.
# Allowlist: emails @example.com, paths /home/dev. Anything else is a leak.
# Generic by design — does not hardcode the author's real org/path, so the gate
# itself leaks nothing. Run in CI and before any push.
set -euo pipefail

files="$(git ls-files 2>/dev/null || true)"
if [[ -z "$files" ]]; then
  echo "pii_gate: no tracked files (run inside a git repo)"; exit 0
fi

fail=0
report() { echo "PII GATE FAILURE: $1"; echo "$2"; fail=1; }

# 1) Non-synthetic email addresses (anything not @example.com)
emails="$(printf '%s\n' "$files" | xargs grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' 2>/dev/null \
  | grep -vE '@example\.com' || true)"
[[ -n "$emails" ]] && report "non-synthetic email" "$emails"

# 2) macOS-style real home paths
users="$(printf '%s\n' "$files" | xargs grep -nIE '/Users/[A-Za-z0-9._-]+' 2>/dev/null || true)"
[[ -n "$users" ]] && report "/Users/ absolute path" "$users"

# 3) Linux home paths other than the synthetic /home/dev
#    Username segment excludes '.' (usernames have none), so "/home/dev." in
#    prose is treated as the allowlisted /home/dev, while /home/alice is flagged.
homes="$(printf '%s\n' "$files" | xargs grep -nIE '/home/[A-Za-z0-9_-]+' 2>/dev/null \
  | grep -vE '/home/dev(/|[^A-Za-z0-9_-]|$)' || true)"
[[ -n "$homes" ]] && report "non-synthetic /home path" "$homes"

if [[ "$fail" -eq 0 ]]; then
  echo "pii_gate: OK — synthetic identities only."
fi
exit "$fail"

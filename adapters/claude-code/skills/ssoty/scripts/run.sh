#!/usr/bin/env bash
# Thin dispatcher: forward args to the ssoty CLI (installed, or via uvx).
set -euo pipefail

args=("$@")
# Default to `audit` when no subcommand is given.
if [[ ${#args[@]} -eq 0 ]]; then
  args=("audit")
fi

if command -v ssoty >/dev/null 2>&1; then
  exec ssoty "${args[@]}"
elif command -v uvx >/dev/null 2>&1; then
  exec uvx ssoty "${args[@]}"
else
  echo "ssoty not found. Install with: pipx install ssoty   (or use uvx)" >&2
  exit 127
fi

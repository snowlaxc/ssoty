"""Redaction for output that may quote the user's real config.

Masks home-directory absolute paths and email addresses. Best-effort only;
the canonical PII guarantee is "don't commit ssoty output" (see SECURITY.md).
"""

from __future__ import annotations

import os
import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(text: str, home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    out = text
    if home and home != "/":
        out = out.replace(home, "$HOME")
    out = _EMAIL_RE.sub("<redacted-email>", out)
    return out

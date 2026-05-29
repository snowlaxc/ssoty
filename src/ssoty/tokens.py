"""Token counting.

By DEFAULT uses a char/4 heuristic (labelled ``approx=True``) so output is
**deterministic and portable** — the same repo audited on any machine yields the
same numbers. Set ``SSOTY_EXACT_TOKENS=1`` to opt into tiktoken for exact counts
(``approx=False``); this trades portability for accuracy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_CHARS_PER_TOKEN = 4  # rough GPT-family heuristic


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    approx: bool  # True when derived from the char/4 heuristic


def _tiktoken_count(text: str) -> int | None:
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # pragma: no cover - defensive; fall back to heuristic
        return None


def count_tokens(text: str) -> TokenCount:
    """Count tokens. Deterministic char/4 by default; tiktoken only when
    ``SSOTY_EXACT_TOKENS`` is set (and importable)."""
    if os.environ.get("SSOTY_EXACT_TOKENS"):
        exact = _tiktoken_count(text)
        if exact is not None:
            return TokenCount(tokens=exact, approx=False)
    return TokenCount(tokens=(len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN, approx=True)

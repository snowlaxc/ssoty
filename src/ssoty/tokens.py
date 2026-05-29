"""Token counting. Uses tiktoken when available, else an explicit char/4 heuristic.

The heuristic is intentionally crude and always labelled ``approx=True`` so a
reader never mistakes it for an exact count.
"""

from __future__ import annotations

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
    """Count tokens, preferring tiktoken; otherwise char/4 (approx)."""
    exact = _tiktoken_count(text)
    if exact is not None:
        return TokenCount(tokens=exact, approx=False)
    return TokenCount(tokens=(len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN, approx=True)

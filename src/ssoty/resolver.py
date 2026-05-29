"""Resolve each harness's effective rule surface from disk.

A harness is described by a :class:`HarnessSpec` listing where its rule docs
live relative to a root (``$HOME`` in real use, a fixture dir in tests) and the
load semantics of each source. This keeps real and fixture resolution identical.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ssoty.models import ALWAYS_ON, SKILL_GATED, HarnessSurface, RuleDoc


@dataclass(frozen=True)
class Source:
    """A glob of rule docs under ``rel`` with a given load basis."""

    rel: str  # path relative to root; file or directory
    load_basis: str
    pattern: str = "*.md"  # used only when rel is a directory


@dataclass(frozen=True)
class HarnessSpec:
    harness: str
    sources: tuple[Source, ...]


# Default real-world layouts (relative to $HOME).
CLAUDE_CODE_SPEC = HarnessSpec(
    harness="claude-code",
    sources=(
        Source(".claude/rules", ALWAYS_ON),
        Source(".claude/CLAUDE.md", ALWAYS_ON),
    ),
)
CODEX_SPEC = HarnessSpec(
    harness="codex",
    sources=(
        Source(".codex/AGENTS.md", ALWAYS_ON),
        Source(".codex/skills/global-agent-rules/references", SKILL_GATED),
    ),
)
DEFAULT_SPECS: tuple[HarnessSpec, ...] = (CLAUDE_CODE_SPEC, CODEX_SPEC)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _make_doc(harness: str, path: Path, load_basis: str) -> RuleDoc:
    is_link = path.is_symlink()
    target = os.readlink(path) if is_link else None
    broken = is_link and not path.exists()
    return RuleDoc(
        harness=harness,
        name=path.name,
        path=path,
        load_basis=load_basis,
        text="" if broken else _read(path),
        is_symlink=is_link,
        symlink_target=target,
        broken=broken,
    )


def _collect(root: Path, source: Source) -> list[Path]:
    target = root / source.rel
    # A symlink (even broken) reports is_file() False, so check the link first.
    if target.is_symlink() or target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.glob(source.pattern) if p.is_file() or p.is_symlink())
    return []


def resolve_surface(root: Path, spec: HarnessSpec) -> HarnessSurface:
    surface = HarnessSurface(harness=spec.harness)
    seen: set[str] = set()
    for source in spec.sources:
        for path in _collect(root, source):
            if path.name in seen:
                continue
            seen.add(path.name)
            surface.docs.append(_make_doc(spec.harness, path, source.load_basis))
    return surface


def resolve_all(root: Path, specs: tuple[HarnessSpec, ...] = DEFAULT_SPECS) -> dict[str, HarnessSurface]:
    return {spec.harness: resolve_surface(root, spec) for spec in specs}


# --- cross-reference extraction ---

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK_RE = re.compile(r"\]\(\s*([^)\s]+?\.md)\s*\)")
_BACKTICK_RE = re.compile(r"`([^`\n]+?\.md)`")


def referenced_docs(text: str) -> set[str]:
    """Return basenames of ``*.md`` rule docs referenced from ``text``.

    Looks at markdown links ``](x.md)`` and inline code ``` `x.md` ```; fenced
    code blocks are stripped first to avoid matching example snippets.
    """
    body = _FENCE_RE.sub("", text)
    names: set[str] = set()
    for match in _MD_LINK_RE.findall(body) + _BACKTICK_RE.findall(body):
        name = match.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if name.endswith(".md"):
            names.add(name)
    return names

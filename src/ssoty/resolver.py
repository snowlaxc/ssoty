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

from ssoty.models import ALWAYS_ON, CONDITIONAL, SKILL_GATED, HarnessSurface, RuleDoc


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
# Cursor: project-level. .mdc rules carry an `alwaysApply` frontmatter flag that
# decides load semantics per file (resolved in _effective_load_basis).
CURSOR_SPEC = HarnessSpec(
    harness="cursor",
    sources=(
        Source(".cursor/rules", CONDITIONAL, "*.mdc"),
        Source(".cursorrules", ALWAYS_ON),  # legacy single file, always applied
    ),
)
COPILOT_SPEC = HarnessSpec(
    harness="copilot",
    sources=(Source(".github/copilot-instructions.md", ALWAYS_ON),),
)
DEFAULT_SPECS: tuple[HarnessSpec, ...] = (CLAUDE_CODE_SPEC, CODEX_SPEC, CURSOR_SPEC, COPILOT_SPEC)


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
    # is_dir() follows symlinks-to-dirs, so a symlinked rules directory is globbed
    # (not collapsed into one bogus doc). Check it BEFORE the symlink/file branch.
    if target.is_dir():
        return sorted(p for p in target.glob(source.pattern) if p.is_file() or p.is_symlink())
    # A broken symlink reports is_file()==False, so test is_symlink() too; it falls
    # through to _make_doc which flags it broken.
    if target.is_symlink() or target.is_file():
        return [target]
    return []


def _mdc_always_apply(path: Path) -> bool:
    """True if a Cursor ``.mdc`` rule declares ``alwaysApply: true`` in frontmatter."""
    text = _read(path)
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    frontmatter = text[3:end] if end != -1 else ""
    for line in frontmatter.splitlines():
        if line.strip().lower().startswith("alwaysapply:"):
            return line.split(":", 1)[1].strip().lower() == "true"
    return False


def _effective_load_basis(path: Path, default: str) -> str:
    # Cursor .mdc: alwaysApply -> always-on; otherwise conditional (globs/agent-requested).
    if path.suffix.lower() == ".mdc":
        return ALWAYS_ON if _mdc_always_apply(path) else CONDITIONAL
    return default


def resolve_surface(root: Path, spec: HarnessSpec) -> HarnessSurface:
    surface = HarnessSurface(harness=spec.harness)
    seen: set[str] = set()  # dedup by full path, not basename, so e.g. a rules/CLAUDE.md
    for source in spec.sources:  # does not shadow the top-level ~/.claude/CLAUDE.md
        for path in _collect(root, source):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            basis = _effective_load_basis(path, source.load_basis)
            surface.docs.append(_make_doc(spec.harness, path, basis))
    return surface


def resolve_all(root: Path, specs: tuple[HarnessSpec, ...] = DEFAULT_SPECS) -> dict[str, HarnessSurface]:
    # Only harnesses actually present at this root (drop empty surfaces) so ssoty
    # reasons about real harnesses, not phantom ones.
    surfaces = {spec.harness: resolve_surface(root, spec) for spec in specs}
    return {h: s for h, s in surfaces.items() if s.docs}


# --- cross-reference extraction ---

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# Markdown link: capture the .md path, tolerating a #fragment and/or a "title".
_MD_LINK_RE = re.compile(r'\]\(\s*([^)\s#]+?\.md)(?:#[^)\s]*)?(?:\s+"[^"]*")?\s*\)', re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`\n]+?\.md)`", re.IGNORECASE)
# A real rule-doc filename: word chars, dot, dash only. Excludes prose
# placeholders and globs such as `<topic>.md`, `*.md`, `<file>.md`.
_VALID_DOC_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$", re.IGNORECASE)


def referenced_docs(text: str) -> set[str]:
    """Return basenames of ``*.md`` rule docs referenced from ``text``.

    Looks at markdown links ``](x.md)`` and inline code ``` `x.md` ```; fenced
    code blocks are stripped first to avoid matching example snippets. Placeholder
    and glob tokens (``<topic>.md``, ``*.md``) are rejected so they are not
    reported as references.
    """
    body = _FENCE_RE.sub("", text)
    names: set[str] = set()
    for match in _MD_LINK_RE.findall(body) + _BACKTICK_RE.findall(body):
        name = match.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if _VALID_DOC_NAME_RE.match(name):
            names.add(name)
    return names

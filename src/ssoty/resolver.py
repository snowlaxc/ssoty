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

from ssoty.models import ALWAYS_ON, CONDITIONAL, ENTRYPOINTS, SKILL_GATED, HarnessSurface, RuleDoc


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
# Gemini CLI: hierarchical GEMINI.md — global ~/.gemini/GEMINI.md + project ./GEMINI.md,
# both always loaded into context.
GEMINI_SPEC = HarnessSpec(
    harness="gemini",
    sources=(
        Source(".gemini/GEMINI.md", ALWAYS_ON),
        Source("GEMINI.md", ALWAYS_ON),
    ),
)
# Cline: reads a `.clinerules/` directory (all rule files, always-on) and a legacy
# single-file `.clinerules`, plus AGENTS.md. _collect checks is_dir() before the
# symlink/file branch, so when `.clinerules` is a directory the dir source resolves
# and the same-rel file source is a no-op; when it is a file the dir source is empty
# and the file source resolves. Both coexist safely.
CLINE_SPEC = HarnessSpec(
    harness="cline",
    sources=(
        Source(".clinerules", ALWAYS_ON, "*.md"),  # directory form: all rule files
        Source(".clinerules", ALWAYS_ON),  # legacy single-file form
        Source("AGENTS.md", ALWAYS_ON),
    ),
)
# Windsurf (Cascade): a `.windsurf/rules/` directory whose files are loaded
# conditionally (activation modes: glob/model-decision/manual), plus the legacy
# single-file `.windsurfrules`, which is always applied.
WINDSURF_SPEC = HarnessSpec(
    harness="windsurf",
    sources=(
        Source(".windsurf/rules", CONDITIONAL, "*.md"),
        Source(".windsurfrules", ALWAYS_ON),  # legacy single file, always applied
    ),
)
# Continue: project rules live under `.continue/rules/`; each block declares its
# own apply semantics (globs / always / agent-requested), so the directory is
# treated as conditional at the surface level.
CONTINUE_SPEC = HarnessSpec(
    harness="continue",
    sources=(Source(".continue/rules", CONDITIONAL, "*.md"),),
)
DEFAULT_SPECS: tuple[HarnessSpec, ...] = (
    CLAUDE_CODE_SPEC,
    CODEX_SPEC,
    CURSOR_SPEC,
    COPILOT_SPEC,
    GEMINI_SPEC,
    CLINE_SPEC,
    WINDSURF_SPEC,
    CONTINUE_SPEC,
)


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
            value = line.split(":", 1)[1]
            # Strip an unquoted trailing YAML comment (`true # primary rule`) and a
            # value that is wholly a comment, then strip surrounding quotes, before
            # comparing. Quote-agnostic for the common case; not a full YAML parser
            # (no new deps).
            value = value.split(" #", 1)[0]
            value = value.strip()
            if value.startswith("#"):
                value = ""
            value = value.strip("\"'")
            return value.strip().lower() == "true"
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
# Any inline code span (not just .md) — used to detect glob/path siblings on a line.
_BACKTICK_SPAN_RE = re.compile(r"`([^`\n]+?)`")
# A backtick code span that names a ``.md`` file (basename or path).
_BACKTICK_MD_RE = re.compile(r"^[^`\n]+?\.md$", re.IGNORECASE)
# A real rule-doc filename: word chars, dot, dash only. Excludes prose
# placeholders and globs such as `<topic>.md`, `*.md`, `<file>.md`.
_VALID_DOC_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$", re.IGNORECASE)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block from ``text`` for ref extraction.

    Frontmatter (``source: ~/.codex/AGENTS.md`` provenance, etc.) is metadata, never
    a 'see X.md' pointer — the SAME strip-before-scan treatment already given to fenced
    code. Reuses the ``\\n---`` closing-delimiter logic from ``_mdc_always_apply``.

    Gated on the frontmatter containing a ``key:`` line so a document whose BODY merely
    opens with a ``---`` horizontal rule (no YAML keys) is left intact — its content
    must still be scanned for genuine pointers.
    """
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    frontmatter = text[3:end]
    has_key = any(re.match(r"\s*[A-Za-z0-9_-]+\s*:", line) for line in frontmatter.splitlines())
    if not has_key:
        return text
    # Drop through the closing delimiter line so the scanned body excludes the block.
    rest = text[end + len("\n---") :]
    newline = rest.find("\n")
    return rest[newline + 1 :] if newline != -1 else ""


def _is_glob_or_path_span(span: str) -> bool:
    """True if an inline code span looks like a glob/path/allowlist token, not a pointer.

    Matches the shapes that appear in a permission allowlist list such as
    ``Direct writes OK for: `~/.claude/**`, `.omc/**`, `CLAUDE.md`, `AGENTS.md`.``:
    glob (``**``/``*``), a trailing ``/`` (directory), or a leading ``~/`` / ``/`` / ``.``
    (home/absolute/dotpath).
    """
    s = span.strip()
    if not s:
        return False
    if "**" in s or "*" in s:
        return True
    if s.endswith("/"):
        return True
    return s.startswith(("~/", "/", "."))


def _backtick_md_refs(body: str) -> list[str]:
    """Inline-code ``.md`` refs, line-aware: drop bare-entrypoint mentions in an allowlist.

    A genuine prose pointer is a LONE ``.md`` backtick in a sentence. A permission
    allowlist is a comma-separated list of code spans where the ``.md`` token sits among
    glob/path tokens. Per line: if the line carries a glob/path-like code span, exclude
    bare-entrypoint ``.md`` spans (``CLAUDE.md`` etc.) on that line; otherwise keep all
    ``.md`` spans. Markdown-link refs are handled separately and never affected here.
    """
    refs: list[str] = []
    for line in body.splitlines():
        spans = _BACKTICK_SPAN_RE.findall(line)
        if not spans:
            continue
        line_has_glob = any(_is_glob_or_path_span(s) for s in spans)
        for span in spans:
            inner = span.strip()
            if not _BACKTICK_MD_RE.match(inner):
                continue
            if line_has_glob and inner in ENTRYPOINTS:
                continue  # entrypoint filename embedded in a glob/path allowlist = mention
            refs.append(inner)
    return refs


def referenced_docs(text: str) -> set[str]:
    """Return basenames of ``*.md`` rule docs referenced from ``text``.

    Looks at markdown links ``](x.md)`` and inline code ``` `x.md` ```; fenced
    code blocks and leading YAML frontmatter are stripped first to avoid matching
    example snippets and provenance metadata. Placeholder and glob tokens
    (``<topic>.md``, ``*.md``) are rejected so they are not reported as references.
    Inline-code entrypoint filenames embedded in a glob/path allowlist list (a
    permission allowlist, not a pointer) are also dropped; a lone ``.md`` backtick
    in prose is still a genuine pointer and is kept.
    """
    body = _strip_frontmatter(text)
    body = _FENCE_RE.sub("", body)
    names: set[str] = set()
    for match in _MD_LINK_RE.findall(body) + _backtick_md_refs(body):
        name = match.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if _VALID_DOC_NAME_RE.match(name):
            names.add(name)
    return names

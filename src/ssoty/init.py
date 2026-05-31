"""Scaffold a starter ``ssoty.json`` from the harnesses actually present (``ssoty init``).

``ssoty init`` is the *zero-to-manifest* on-ramp to the manager command ``ssoty sync``.
It reuses the auditor's :func:`ssoty.resolver.resolve_all` for detection — **no new
filesystem walking** — so the harnesses it scaffolds are exactly the ones that resolved
real rule docs at this root (identical real/fixture semantics).

For each present harness it maps the harness's :class:`~ssoty.resolver.Source` tuples into
manifest ``harnesses[<name>]`` entries:

  * a DIRECTORY source (``.claude/rules``, ``.cursor/rules``, …) -> a directory target,
    fed by the inferred ``common`` source (``"common": true``) when its docs symlink into
    the canonical dir, otherwise an explicit ``sources`` entry carrying the source pattern;
  * a single-FILE source (``CLAUDE.md``, ``AGENTS.md``, …) -> a single-file target with
    exactly one ``{"file": ...}`` source (``build_plan`` rejects >1 source for a file target).

The canonical common source is INFERRED read-only from the symlink targets ``resolve_all``
already recorded on each :class:`~ssoty.models.RuleDoc` (``is_symlink`` / ``os.readlink`` in
the resolver) — no extra stat/readlink. When nothing is inferable a safe PLACEHOLDER manifest
is emitted with a ``_comment`` guiding the user to fill it in (unknown keys are ignored by
``load_manifest``/``build_plan``, so the manifest stays valid).

Pure functions, stdlib-only (``json``/``os``/``pathlib``/``collections``), deterministic,
no network/LLM. The CLI stays thin: this module exposes a plan builder + a renderer, and
``init`` *never* writes a rule file — only ``root/ssoty.json``.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from ssoty.models import HarnessSurface
from ssoty.resolver import DEFAULT_SPECS, HarnessSpec, Source

# Placeholder canonical source used when no symlink lets us infer the real one. Kept in
# sync with examples/ssoty.json so the scaffold reads familiarly. build_plan ignores the
# adjacent "_comment" key (it reads only version/method/common/harnesses).
PLACEHOLDER_DIR = "agent-rules/common"
PLACEHOLDER_PATTERN = "*.md"
PLACEHOLDER_COMMENT = (
    "PLACEHOLDER common source - create this dir, put your shared rules in it, " "then run: ssoty sync --apply."
)


def _spec_by_harness() -> dict[str, HarnessSpec]:
    return {spec.harness: spec for spec in DEFAULT_SPECS}


def _is_dir_source(root: Path, source: Source) -> bool:
    """True if ``source`` resolves as a directory glob (vs a bare single file) at ``root``.

    Mirrors :func:`ssoty.resolver._collect`: ``is_dir()`` follows symlinks-to-dirs, so a
    symlinked rules directory still counts as a directory source.
    """
    return (root / source.rel).is_dir()


def _classify_docs(root: Path, spec: HarnessSpec, surface: HarnessSurface) -> tuple[Source | None, list[Source]]:
    """Split a harness's resolved docs into the (single dir source, file sources) actually present.

    ``resolve_all`` already deduped docs by full path, so a harness's docs map back onto at
    most one directory Source (the dir form) plus zero or more single-file Sources. We
    inspect which Sources actually produced the resolved doc paths — NOT the static spec —
    so legacy/no-op spec variants (e.g. the ``.clinerules`` single-file source when the dir
    form resolved) are skipped.

    Returns ``(dir_source_or_None, [file_sources])`` taking the FIRST dir source whose
    directory holds resolved docs and every file source whose exact path resolved.
    """
    resolved_paths = {str(doc.path) for doc in surface.docs}
    dir_source: Source | None = None
    file_sources: list[Source] = []
    seen_rel: set[str] = set()
    for source in spec.sources:
        if source.rel in seen_rel:
            continue
        target = root / source.rel
        if _is_dir_source(root, source):
            # Did this directory actually contribute any resolved doc? (it always does for a
            # present harness, but guard against a dir with no matching pattern files).
            if dir_source is None and any(p.startswith(str(target) + os.sep) for p in resolved_paths):
                dir_source = source
                seen_rel.add(source.rel)
        elif str(target) in resolved_paths:
            file_sources.append(source)
            seen_rel.add(source.rel)
    return dir_source, file_sources


def infer_common_source(root: Path, surfaces: dict[str, HarnessSurface]) -> tuple[str | None, set[str]]:
    """Infer the canonical common-source dir from already-resolved symlink targets (read-only).

    Returns ``(manifest_dir_string_or_None, harnesses_that_link_into_it)``.

    Algorithm (deterministic, stdlib only; reuses the ``is_symlink``/``symlink_target`` the
    resolver already populated — no extra disk walk):

    1. For every non-broken symlinked doc, take ``os.path.realpath(doc.path)`` and record its
       PARENT dir as a candidate canonical dir, tracking the owning harness.
    2. No usable symlinks -> ``(None, set())`` (caller emits the PLACEHOLDER manifest).
    3. All parent dirs identical -> that dir is canonical.
    4. Parents differ -> the MODAL (majority) parent dir, NOT ``commonpath`` (a few outlier
       links must not pull the source up to a broad ancestor). Ties break lexicographically.
       If the chosen dir is ``/`` / ``~`` / the sync root (too broad) -> ``(None, ...)`` (PLACEHOLDER).
    5. A harness is in the returned set iff at least one of its non-broken symlinked docs
       resolves under the chosen dir.

    Determinism: the parent tally is order-independent and ties break on the smallest parent;
    broken symlinks are excluded (they cannot anchor a canonical source and would mis-point sync).
    """
    realpaths: list[str] = []
    parents = Counter()  # parent dir -> count (for the all-identical fast path)
    by_harness: dict[str, list[str]] = {}
    for harness, surface in surfaces.items():
        for doc in surface.docs:
            if not (doc.is_symlink and not doc.broken):
                continue
            real = os.path.realpath(doc.path)
            realpaths.append(real)
            parents[os.path.dirname(real)] += 1
            by_harness.setdefault(harness, []).append(real)

    if not realpaths:
        return None, set()

    home = os.path.realpath(os.path.expanduser("~"))
    root_real = os.path.realpath(root)
    floors = {"/", "", home, root_real}

    if len(parents) == 1:
        chosen = next(iter(parents))
    else:
        # MODAL-PARENT (majority) selection, NOT commonpath. A few outlier symlinks
        # (e.g. one harness-specific rule linked from agent-rules/claude) must not pull
        # the inferred source up to a broad ancestor (agent-rules/) that would then sweep
        # harness-private and repo-meta files into every harness. The dominant parent dir
        # is the canonical common source; only common/* is shared across harnesses, while
        # claude/*, codex/* stay harness-private. Ties break on the lexicographically
        # smallest parent for determinism.
        top = max(parents.values())
        leaders = [p for p, c in parents.items() if c == top]
        if len(leaders) > 1:
            # No clear majority (tie) -> do NOT guess a canonical dir (and never climb to a
            # broad ancestor that would sweep harness-private rules in). Degrade to PLACEHOLDER
            # so the user fills it in deliberately.
            return None, set()
        chosen = leaders[0]

    # Reject a too-broad source (== or above the sync root / HOME / "/"): it would sweep in
    # unrelated files. A dir strictly BELOW root or HOME (the normal canonical case) is kept.
    below_a_floor = chosen.startswith(home + os.sep) or chosen.startswith(root_real + os.sep)
    if chosen in floors or not below_a_floor:
        return None, set()

    uses_common: set[str] = set()
    chosen_prefix = chosen + os.sep
    for harness, reals in by_harness.items():
        if any(r == chosen or r.startswith(chosen_prefix) for r in reals):
            uses_common.add(harness)
    return _to_manifest_path(root, chosen), uses_common


def _to_manifest_path(root: Path, abs_dir: str) -> str:
    """Render an absolute dir as a manifest string that ``sync._expand(base=root)`` reproduces.

    Under root -> a path relative to root (so sources stay portable with the manifest). Under
    ``~`` -> a ``~/...`` string (``expanduser`` round-trips). Otherwise the absolute path.
    Sources are allowed to live outside root (only TARGETS are constrained).
    """
    root_real = os.path.realpath(root)
    if abs_dir == root_real:
        return "."
    if abs_dir.startswith(root_real + os.sep):
        return os.path.relpath(abs_dir, root_real)
    home = os.path.realpath(os.path.expanduser("~"))
    if abs_dir == home:
        return "~"
    if abs_dir.startswith(home + os.sep):
        return "~/" + os.path.relpath(abs_dir, home)
    return abs_dir


def build_init_manifest(root: Path, surfaces: dict[str, HarnessSurface]) -> dict:
    """Build a starter manifest dict (valid for ``sync.build_plan``) for the present harnesses.

    Pure: takes the already-resolved ``surfaces`` (from ``resolve_all``) and returns a dict —
    no filesystem mutation, no extra disk walk beyond reading the symlink metadata the resolver
    already attached to each doc.
    """
    specs = _spec_by_harness()
    common_dir, uses_common = infer_common_source(root, surfaces)
    inferred = common_dir is not None
    source_dir = common_dir if inferred else PLACEHOLDER_DIR

    harnesses: dict[str, dict] = {}
    for name in sorted(surfaces):
        spec = specs.get(name)
        surface = surfaces[name]
        if spec is None:  # defensive: unknown harness key (cannot happen with DEFAULT_SPECS)
            continue
        dir_source, file_sources = _classify_docs(root, spec, surface)

        if dir_source is not None:
            entry = _dir_entry(name, dir_source, source_dir, inferred, uses_common)
        elif file_sources:
            entry = _file_entry(file_sources[0], source_dir)
        else:  # no classifiable source (shouldn't happen for a present harness); skip
            continue
        harnesses[name] = entry

    if not harnesses:
        # Every present harness was unclassifiable — keep the manifest valid (build_plan
        # requires a non-empty harnesses object) by seeding the placeholder skeleton.
        harnesses[next(iter(sorted(surfaces)))] = {
            "target": specs[next(iter(sorted(surfaces)))].sources[0].rel,
            "common": True,
        }

    manifest: dict = {
        "version": "1",
        "method": "symlink",
        # common.sources defaults to *.md (every dir harness but cursor uses it); cursor
        # carries its own *.mdc on its harness entry, so the common pattern stays *.md.
        "common": {"sources": [{"dir": source_dir, "pattern": PLACEHOLDER_PATTERN}]},
        "harnesses": harnesses,
    }
    if not inferred:
        manifest["_comment"] = PLACEHOLDER_COMMENT
    return manifest


def _dir_entry(name: str, dir_source: Source, source_dir: str, inferred: bool, uses_common: set[str]) -> dict:
    """Manifest entry for a directory-target harness. ``name`` is the harness key (for ``uses_common``).

    ``common: true`` when this harness's docs symlink into the inferred canonical dir AND the
    harness uses the common pattern (``*.md``). Otherwise an explicit ``sources`` entry carrying
    the harness's own pattern (so cursor's ``*.mdc`` is preserved and not silently dropped).
    """
    pattern = dir_source.pattern
    target = dir_source.rel
    if inferred and name in uses_common and pattern == PLACEHOLDER_PATTERN:
        return {"target": target, "common": True}
    # Either no inferred common link for this harness, or a non-default pattern (cursor .mdc):
    # emit an explicit source carrying the right pattern.
    return {"target": target, "sources": [{"dir": source_dir, "pattern": pattern}]}


def _file_entry(file_source: Source, source_dir: str) -> dict:
    """Manifest entry for a single-file-target harness.

    Exactly one ``{"file": ...}`` source (``build_plan`` rejects >1 for a file target),
    pointing at ``<source_dir>/<basename>`` so it round-trips back through sync against the
    same canonical dir. If that basename is absent in the canonical dir, sync simply links
    nothing for this harness (harmless no-op).
    """
    basename = Path(file_source.rel).name
    file_path = f"{source_dir}/{basename}" if source_dir not in (".", "") else basename
    return {"target": file_source.rel, "sources": [{"file": file_path}]}


def render_manifest(manifest: dict) -> str:
    """Render the manifest dict to a deterministic, readable JSON string (trailing newline)."""
    return json.dumps(manifest, indent=2) + "\n"

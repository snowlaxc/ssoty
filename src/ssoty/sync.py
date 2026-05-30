"""Distribute a single canonical rule source into every harness target (``ssoty sync``).

``ssoty sync`` is the *manager* counterpart to the *auditor* commands. Where ``audit``
READS each harness surface and reports divergence, ``sync`` WRITES the surface: it reads
a stdlib-JSON manifest (``ssoty.json``) describing one read-only canonical SOURCE and the
per-harness TARGET paths, then links every resolved source file into every target so all
harnesses point at byte-identical canonical files (same inode). Audit becomes the natural
post-condition check — what sync writes is exactly what audit reads.

Hard safety contract (mirrors ``ssoty fix``):
  * Default is DRY-RUN. The plan is computed and printed; nothing is written, no backup
    dir is created. Mutation requires an explicit ``--apply``.
  * On apply, every existing real file or *differing* symlink at a target is backed up
    into a timestamped backup dir BEFORE it is replaced/removed (reuses ``fix`` helpers).
  * Link semantics mirror ``install.sh``'s ``link_safe`` + ``cleanup_orphan_symlinks``:
    skip-unchanged / backup+relink / backup-real-file / new-link / orphan-cleanup.
  * Only manifest-declared TARGET paths are ever written or removed; the canonical SOURCE
    is read-only (sync only reads basenames and links to them, never follows into it).
  * Idempotent: a second ``--apply`` on a synced tree produces zero backups, zero writes.
  * Manifest parsed with stdlib ``json`` only (no tomllib/tomli/yaml); ``dependencies`` stays [].
  * Full validation precedes the first mutation, so a bad manifest never leaves a partial sync.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ssoty.fix import _backup_node, ensure_backup_dir, make_backup_dir_name  # noqa: F401

# Plan action kinds (classified at apply time against the live filesystem).
SKIP_UNCHANGED = "skip_unchanged"  # dst already symlinks to intended source
BACKUP_RELINK = "backup_relink"  # dst is a symlink pointing elsewhere
BACKUP_REAL = "backup_real_file"  # dst is a real file/dir
NEW_LINK = "new_link"  # dst does not exist
ORPHAN_CLEANUP = "orphan_cleanup"  # stale canonical-pointing symlink, target gone

DEFAULT_PATTERN = "*.md"
ONLY_METHOD = "symlink"


class ManifestError(Exception):
    """A manifest is missing, unparseable, or declares an unsafe/invalid path.

    Mapped to CLI exit code 2. Raised BEFORE any filesystem mutation so a bad
    manifest can never produce a partial sync.
    """


@dataclass(frozen=True)
class PlannedLink:
    """One intended (source_file -> target_link) link derived from the manifest.

    ``source`` is the ABSOLUTE canonical source path that ``target`` should symlink to
    (matching install.sh's ``ln -s "$src"`` with absolute ``$src``). ``harness`` and
    ``dir_target`` carry context for orphan scanning and rendering.
    """

    harness: str
    source: Path  # absolute canonical source file (read-only)
    target: Path  # absolute target link path (the only thing ever written)
    dir_target: bool  # True if the harness target is a directory (vs a single-file link)


@dataclass(frozen=True)
class LinkResult:
    """Outcome of classifying/applying one node at apply time."""

    action: str
    target: Path
    source: str  # intended source string, or (for orphan cleanup) the dead target string
    done: bool  # True if a write happened; False for skip-unchanged / dry classification
    note: str


@dataclass(frozen=True)
class SyncPlan:
    """The fully-resolved, pre-validated plan: per-link entries + directory targets to scan."""

    links: list[PlannedLink] = field(default_factory=list)
    # directory target -> set of declared source roots (absolute) feeding it (for orphan scan).
    dir_targets: dict[Path, set[Path]] = field(default_factory=dict)
    method: str = ONLY_METHOD

    def has_work(self) -> bool:
        return bool(self.links) or bool(self.dir_targets)


# --------------------------------------------------------------------------- #
# Manifest loading + validation (stdlib json only).
# --------------------------------------------------------------------------- #


def manifest_path(root: Path, explicit: str | None) -> Path:
    """Resolve the manifest location: explicit path wins, else ``ssoty.json`` in root."""
    if explicit:
        return Path(explicit).expanduser()
    return root / "ssoty.json"


def load_manifest(path: Path) -> dict:
    """Parse a manifest with stdlib ``json`` only. Raises :class:`ManifestError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"manifest not found: {path} ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"manifest {path} must be a JSON object")
    return data


def _expand(base: Path, rel: str) -> Path:
    """Expand ``~`` then resolve ``rel`` against ``base`` (the manifest's own directory).

    ``~`` expands via :func:`os.path.expanduser`; env vars are left unchanged. A relative
    path is anchored at ``base`` so manifests are portable. Returns an absolute, normalized
    (``os.path.normpath``) path WITHOUT following symlinks (so source/target strings stay
    declarative and ``..`` escapes are detectable before any I/O).
    """
    expanded = os.path.expanduser(rel)
    p = Path(expanded)
    if not p.is_absolute():
        p = base / p
    return Path(os.path.normpath(str(p)))


def _resolve_sources(base: Path, raw_sources: list, where: str) -> list[tuple[Path, str]]:
    """Turn a manifest ``sources`` list into (absolute_dir_or_file, pattern) tuples.

    Each entry is either ``{"dir": ..., "pattern": ...}`` or ``{"file": ...}``. A bare
    ``{"source": ...}`` is accepted as an alias for ``file``. Patterns default per the
    harness (``*.md``; cursor uses ``*.mdc`` in its own manifest entry).
    """
    out: list[tuple[Path, str]] = []
    if not isinstance(raw_sources, list):
        raise ManifestError(f"{where}: 'sources' must be a list")
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ManifestError(f"{where}: each source must be an object, got {entry!r}")
        if "dir" in entry:
            pattern = entry.get("pattern", DEFAULT_PATTERN)
            if not isinstance(pattern, str):
                raise ManifestError(f"{where}: 'pattern' must be a string")
            out.append((_expand(base, str(entry["dir"])), pattern))
        elif "file" in entry or "source" in entry:
            key = "file" if "file" in entry else "source"
            out.append((_expand(base, str(entry[key])), ""))  # "" pattern => single file
        else:
            raise ManifestError(f"{where}: source needs a 'dir' or 'file' key, got {entry!r}")
    return out


def _glob_source(src: Path, pattern: str) -> list[Path]:
    """Resolve one source spec to concrete source files (sorted, deterministic).

    A directory source globs ``pattern``; a file source yields itself if it exists. The
    canonical source is read-only — only basenames matter, never the contents.
    """
    if pattern:  # directory source
        if not src.is_dir():
            return []
        return sorted(p for p in src.glob(pattern) if p.is_file())
    # file source
    return [src] if src.is_file() else []


def build_plan(root: Path, manifest: dict, manifest_dir: Path, method: str = ONLY_METHOD) -> SyncPlan:
    """Resolve the manifest into a fully-validated :class:`SyncPlan` (no mutation).

    All target paths are validated to live under ``root`` BEFORE returning, so a malformed
    or malicious ``target`` (``../../etc``) is rejected with :class:`ManifestError` and the
    caller never starts writing. Sources are read-only and may live anywhere reachable.
    """
    if method != ONLY_METHOD:
        raise ManifestError(f"unsupported method: {method!r} (only {ONLY_METHOD!r} is supported)")

    common_raw = manifest.get("common", {})
    if not isinstance(common_raw, dict):
        raise ManifestError("'common' must be an object")
    common_sources = _resolve_sources(manifest_dir, common_raw.get("sources", []), "common")

    harnesses = manifest.get("harnesses")
    if not isinstance(harnesses, dict) or not harnesses:
        raise ManifestError("'harnesses' must be a non-empty object")

    root_abs = Path(os.path.normpath(str(root)))
    links: list[PlannedLink] = []
    dir_targets: dict[Path, set[Path]] = {}

    for name, spec in harnesses.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"harness {name!r}: must be an object")
        target_raw = spec.get("target")
        if not isinstance(target_raw, str) or not target_raw:
            raise ManifestError(f"harness {name!r}: missing 'target' string")
        target = _expand(manifest_dir, target_raw)
        _require_under_root(target, root_abs, name)

        own_sources = _resolve_sources(manifest_dir, spec.get("sources", []), f"harness {name!r}")
        use_common = bool(spec.get("common", False))
        effective = list(own_sources)
        if use_common:
            effective.extend(common_sources)

        resolved_files: list[Path] = []
        source_roots: set[Path] = set()
        is_dir_target = _is_dir_target(target, effective)
        for src, pattern in effective:
            source_roots.add(src if pattern else src.parent)
            resolved_files.extend(_glob_source(src, pattern))

        if is_dir_target:
            dir_targets[target] = source_roots
            seen: set[str] = set()
            for sf in resolved_files:
                if sf.name in seen:
                    continue  # first source wins on basename collision (deterministic)
                seen.add(sf.name)
                link_target = target / sf.name
                _require_under_root(link_target, root_abs, name)
                _require_realpath_under_root(link_target, root_abs, name)
                links.append(PlannedLink(name, sf.resolve(), link_target, dir_target=True))
        else:
            # File target: exactly one link expected.
            if len(resolved_files) > 1:
                raise ManifestError(
                    f"harness {name!r}: file target {target_raw!r} has {len(resolved_files)} sources; expected 1"
                )
            if resolved_files:
                _require_realpath_under_root(target, root_abs, name)
                links.append(PlannedLink(name, resolved_files[0].resolve(), target, dir_target=False))

    return SyncPlan(links=links, dir_targets=dir_targets, method=method)


def _is_dir_target(target: Path, effective_sources: list[tuple[Path, str]]) -> bool:
    """Decide whether a harness target is a directory (contents synced) or a single file.

    A target is a directory target if it already exists as a dir, OR any of its sources is
    a directory glob (pattern != ""). A bare file source into a non-existent target is a
    single-file link (the CLAUDE.md case).
    """
    if target.is_dir() and not target.is_symlink():
        return True
    return any(pattern for _src, pattern in effective_sources)


def _require_under_root(path: Path, root_abs: Path, harness: str) -> None:
    """Reject any target path that escapes ``root`` (``..`` / absolute outside)."""
    try:
        Path(path).relative_to(root_abs)
    except ValueError as exc:
        raise ManifestError(
            f"harness {harness!r}: target {path} escapes sync root {root_abs} (refusing to write outside)"
        ) from exc


def _require_realpath_under_root(path: Path, root: Path, harness: str) -> None:
    """Reject a target whose nearest EXISTING ancestor resolves (realpath, following
    symlinked components) outside ``root``. The lexical :func:`_require_under_root` cannot
    see a pre-existing symlink in a parent component pointing outside root; this realpath
    re-check at apply time closes that escape before any mutation."""
    root_real = os.path.realpath(root)
    # Check the directory the link is WRITTEN INTO (path.parent), not the link node
    # itself — the node may legitimately point at the canonical source outside root.
    anc = path.parent
    while not anc.exists() and anc != anc.parent:
        anc = anc.parent
    anc_real = os.path.realpath(anc)
    if anc_real != root_real and not anc_real.startswith(root_real + os.sep):
        raise ManifestError(
            f"harness {harness!r}: target {path} resolves outside sync root via a symlinked "
            f"path component ({anc_real} not under {root_real}) — refusing to write outside"
        )


# --------------------------------------------------------------------------- #
# Classification + apply (mirrors install.sh link_safe / cleanup_orphan_symlinks).
# --------------------------------------------------------------------------- #


def _same_link(dst: Path, intended: Path) -> bool:
    """True if ``dst`` is a symlink already resolving to the intended source.

    Compares both the literal readlink string and the canonicalized realpath, so an
    equivalent relative link is recognized as unchanged and not needlessly relinked.
    """
    if not dst.is_symlink():
        return False
    current = os.readlink(dst)
    if current == str(intended):
        return True
    return os.path.realpath(dst) == os.path.realpath(intended)


def classify(link: PlannedLink) -> str:
    """Classify one planned link against the live filesystem (re-stat = idempotence/TOCTOU)."""
    dst = link.target
    if _same_link(dst, link.source):
        return SKIP_UNCHANGED
    if dst.is_symlink():  # symlink pointing elsewhere
        return BACKUP_RELINK
    if dst.exists():  # real file or dir
        return BACKUP_REAL
    return NEW_LINK


class _BackupManager:
    """Create the timestamped backup dir lazily — only when a node is actually backed up.

    A plan whose only work is NEW_LINK entries (nothing pre-existing to preserve) writes
    zero backups, so no backup dir should appear (mirrors install.sh "No backups created").
    The dir is materialized on the first :meth:`backup` call and reused thereafter.
    """

    def __init__(self, root: Path):
        self._root = root
        self._dir: Path | None = None

    @property
    def dir(self) -> Path | None:
        return self._dir

    @property
    def root(self) -> Path:
        return self._root

    def backup(self, node: Path) -> None:
        if self._dir is None:
            self._dir = ensure_backup_dir(self._root)
        _backup_node(node, self._root, self._dir)


def _apply_link(link: PlannedLink, backups: _BackupManager) -> LinkResult:
    """Classify -> (backup) -> mutate, never the reverse. Returns the result line."""
    action = classify(link)
    dst, src = link.target, link.source
    if action == SKIP_UNCHANGED:
        return LinkResult(action, dst, str(src), done=False, note=f"unchanged: {dst} -> {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    if action == BACKUP_RELINK:
        old = os.readlink(dst)
        backups.backup(dst)
        dst.unlink()
        os.symlink(str(src), dst)
        return LinkResult(action, dst, str(src), done=True, note=f"relinked (was {old}): {dst} -> {src}")
    if action == BACKUP_REAL:
        backups.backup(dst)
        if dst.is_dir() and not dst.is_symlink():
            import shutil

            shutil.rmtree(dst)
        else:
            dst.unlink()
        os.symlink(str(src), dst)
        return LinkResult(action, dst, str(src), done=True, note=f"backup+link: {dst} -> {src}")
    # NEW_LINK
    os.symlink(str(src), dst)
    return LinkResult(action, dst, str(src), done=True, note=f"new link: {dst} -> {src}")


def _orphans(plan: SyncPlan) -> list[tuple[Path, str, Path]]:
    """Find stale canonical-pointing symlinks to clean up (after linking).

    A symlink in a directory target is an orphan iff: its resolved/literal target lies
    strictly under a declared source root for that target, AND its target no longer exists,
    AND its basename is not in the current plan. Mirrors install.sh ``cleanup_orphan_symlinks``
    (``target == CANONICAL/* && ! -e target``). The basename + source-prefix guards leave a
    user's own unrelated symlinks (pointing outside the source) untouched.

    Returns (link_path, dead_target_string, dir_target) tuples.
    """
    planned_names: dict[Path, set[str]] = {}
    for link in plan.links:
        if link.dir_target:
            planned_names.setdefault(link.target.parent, set()).add(link.target.name)

    found: list[tuple[Path, str, Path]] = []
    for dir_target, source_roots in plan.dir_targets.items():
        if not dir_target.is_dir():
            continue
        keep = planned_names.get(dir_target, set())
        for child in sorted(dir_target.iterdir()):
            if not child.is_symlink():
                continue
            if child.name in keep:
                continue
            if child.exists():  # target still resolves -> not an orphan
                continue
            link_str = os.readlink(child)
            resolved = os.path.realpath(child)
            if _points_into_source(link_str, resolved, source_roots):
                found.append((child, link_str, dir_target))
    return found


def _points_into_source(link_str: str, resolved: str, source_roots: set[Path]) -> bool:
    """True if a (broken) symlink's target is strictly under a declared source root.

    Checks both the canonicalized realpath and the literal target string prefix, so a
    relative or absolute canonical link is caught. Each source root is compared in both
    its declared form and its ``realpath`` form (so a ``/tmp`` vs ``/private/tmp`` style
    symlinked-prefix mismatch does not cause a false negative). Foreign links (pointing
    outside every source root) are never matched -> never deleted.
    """
    for root in source_roots:
        for root_s in {str(root), os.path.realpath(root)}:
            if resolved == root_s or resolved.startswith(root_s + os.sep):
                return True
            if link_str == root_s or link_str.startswith(root_s + os.sep):
                return True
    return False


def apply_plan(plan: SyncPlan, root: Path) -> tuple[list[LinkResult], Path | None]:
    """Apply every planned link, then clean orphans. Backups precede every mutation.

    Returns ``(results, backup_dir)`` where ``backup_dir`` is ``None`` if nothing needed
    backing up (e.g. an all-new-links plan), so the caller can render accordingly and no
    empty backup dir is left behind.
    """
    backups = _BackupManager(root)
    results: list[LinkResult] = []
    for link in plan.links:
        results.append(_apply_link(link, backups))
    for orphan, dead_target, _dir in _orphans(plan):
        backups.backup(orphan)
        orphan.unlink()
        results.append(
            LinkResult(
                ORPHAN_CLEANUP, orphan, dead_target, done=True, note=f"removed orphan: {orphan} -> {dead_target}"
            )
        )
    return results, backups.dir


def plan_has_apply_work(plan: SyncPlan) -> bool:
    """True if applying the plan would mutate anything (used to skip empty backup dirs).

    A plan whose links are ALL skip-unchanged and which has no orphans is a pure no-op,
    so no backup dir is created (idempotence).
    """
    if any(classify(link) != SKIP_UNCHANGED for link in plan.links):
        return True
    return bool(_orphans(plan))


# --------------------------------------------------------------------------- #
# Rendering.
# --------------------------------------------------------------------------- #


def render_plan_text(plan: SyncPlan) -> str:
    """Dry-run text: the EXACT plan, one verb + ``<target> -> <source>`` per line."""
    if not plan.links and not plan.dir_targets:
        return "ssoty sync — nothing to do (manifest resolved no source files).\n"
    lines = [
        f"ssoty sync (DRY-RUN, method={plan.method}) — "
        f"{len(plan.links)} link(s) planned; pass --apply to perform them.",
        "",
    ]
    for link in plan.links:
        action = classify(link)
        verb = {
            SKIP_UNCHANGED: "unchanged",
            BACKUP_RELINK: "relink ",
            BACKUP_REAL: "backup+link",
            NEW_LINK: "new link",
        }[action]
        lines.append(f"  {verb}: {link.target} -> {link.source}")
    for orphan, dead_target, _dir in _orphans(plan):
        lines.append(f"  orphan: {orphan} -> {dead_target} (target gone; would remove)")
    return "\n".join(lines)


def render_apply_text(backup_dir: Path | None, results: list[LinkResult]) -> str:
    """Apply text: backup location first (if any), then a per-link result line."""
    lines: list[str] = []
    if backup_dir is not None:
        lines.append(f"backup written to: {backup_dir}")
        lines.append("")
    done = sum(1 for r in results if r.done)
    lines.append(f"ssoty sync (APPLIED) — {done}/{len(results)} link(s) changed.")
    lines.append("")
    for r in results:
        lines.append(f"  {r.note}")
    return "\n".join(lines)

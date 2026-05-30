"""Safe, dry-run-first remediation of audit findings.

``ssoty fix`` derives its action set ONLY from existing :class:`Finding`s produced
by the audit — it never re-scans the filesystem for new write targets, so the set
of files it may touch is bounded by what the audit already reported.

Hard safety contract:
  * Default is DRY-RUN. Writing requires an explicit ``--apply`` flag.
  * On apply, every file that will be modified/removed is copied into a
    timestamped backup dir under the audited root BEFORE any mutation.
  * Only SAFE remediations: remove a *broken* symlink (its target does not
    resolve, so nothing real is lost), and optionally append intentionally
    non-shared rule names to ``.ssotyignore`` (a file ssoty owns).
  * Never edits real rule files, never touches a VALID symlink, no reorg.
  * Idempotent: a second run re-stats / re-checks declarations and does nothing.
  * stdlib only (shutil/pathlib/datetime/os); deterministic; no network/LLM.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ssoty.ignore import SsotyIgnore
from ssoty.models import AuditResult

# Remediation action kinds.
REMOVE_BROKEN_SYMLINK = "remove_broken_symlink"
APPEND_IGNORE = "append_ignore"

_IGNORE_HEADER = (
    "# .ssotyignore — rule docs that are intentionally NOT shared across harnesses.\n"
    "# One basename per line; '#' comments and blank lines are ignored.\n"
    "# A declared name downgrades its cross-reference finding from Critical to FYI.\n"
)


@dataclass(frozen=True)
class Remediation:
    """One planned safe action. ``path`` is the node acted on; ``detail`` is context."""

    action: str  # REMOVE_BROKEN_SYMLINK | APPEND_IGNORE
    path: Path  # symlink to remove, or the .ssotyignore file to append to
    detail: str  # dead target string, or the rule name to append


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying a single :class:`Remediation`."""

    remediation: Remediation
    done: bool  # True if the action was performed; False if skipped (guard tripped)
    note: str  # human-readable result line


def make_backup_dir_name(now: datetime | None = None) -> str:
    """UTC timestamp like ``20260530T101500Z`` for the backup subdir."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def plan_remediations(result: AuditResult, root: Path, scaffold_ignore: bool = False) -> list[Remediation]:
    """Compute safe remediations from existing findings only (no filesystem rescan).

    ``broken_symlink`` removals are always planned. ``non_shared_surface`` ignore
    appends are planned only when ``scaffold_ignore`` is True and the name is not
    already declared in ``.ssotyignore`` (idempotence + skip-already-declared).
    """
    plan: list[Remediation] = []
    for f in result.findings:
        if f.check == "broken_symlink":
            doc = _find_doc(result, f)
            target = doc.symlink_target if doc and doc.symlink_target is not None else ""
            plan.append(Remediation(REMOVE_BROKEN_SYMLINK, Path(f.file), target))

    if scaffold_ignore:
        ignore = SsotyIgnore.load(root)
        declared = set(ignore.names)
        ignore_path = root / ".ssotyignore"
        seen: set[str] = set()
        for f in result.findings:
            if f.check != "non_shared_surface":
                continue
            name = f.rule_id
            if not name or name in declared or name in seen:
                continue
            seen.add(name)
            plan.append(Remediation(APPEND_IGNORE, ignore_path, name))
    return plan


def _find_doc(result: AuditResult, finding):
    """Locate the RuleDoc behind a broken_symlink finding (for its dead target)."""
    for surface in result.surfaces.values():
        for doc in surface.docs:
            if str(doc.path) == finding.file and doc.name == finding.rule_id:
                return doc
    return None


def has_work(plan: list[Remediation]) -> bool:
    return bool(plan)


def _backup_node(src: Path, root: Path, backup_dir: Path) -> Path:
    """Copy ``src`` into ``backup_dir`` preserving its path relative to ``root``.

    Symlinks (broken or not) are copied as the link node itself so the dead
    target string remains recoverable. Returns the backup destination path.
    """
    try:
        rel = src.relative_to(root)
    except ValueError:
        # src is outside root (should not happen for in-tree findings); fall back
        # to the basename so the backup is still written somewhere under backup_dir.
        rel = Path(src.name)
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        # copy2(follow_symlinks=False) recreates the link with its (dead) target.
        shutil.copy2(src, dst, follow_symlinks=False)
    else:
        shutil.copy2(src, dst)
    return dst


def apply_remediations(plan: list[Remediation], root: Path, backup_dir: Path) -> list[ApplyResult]:
    """Apply the plan, backing up each touched file BEFORE mutating it.

    Per-action guards make the whole operation idempotent:
      * remove_broken_symlink: act only if ``path.is_symlink() and not path.exists()``
        is *still* true at apply time (TOCTOU defense + idempotence).
      * append_ignore: act only if the name is not already declared.
    The backup dir is created lazily here only when there is real work.
    """
    results: list[ApplyResult] = []
    for rem in plan:
        if rem.action == REMOVE_BROKEN_SYMLINK:
            results.append(_apply_remove(rem, root, backup_dir))
        elif rem.action == APPEND_IGNORE:
            results.append(_apply_append(rem, root, backup_dir))
    return results


def _apply_remove(rem: Remediation, root: Path, backup_dir: Path) -> ApplyResult:
    path = rem.path
    # Re-stat guard: only a still-broken symlink. A valid symlink (exists()==True)
    # or a real file is never touched. A second run finds nothing here.
    if not (path.is_symlink() and not path.exists()):
        return ApplyResult(rem, False, f"skip (no longer a broken symlink): {path}")
    _backup_node(path, root, backup_dir)
    path.unlink()
    return ApplyResult(rem, True, f"removed broken symlink: {path} -> {rem.detail}")


def _apply_append(rem: Remediation, root: Path, backup_dir: Path) -> ApplyResult:
    ignore_path = rem.path
    name = rem.detail
    # Re-load to guard against duplicates (idempotence + concurrent edits).
    if SsotyIgnore.load(root).declares(name):
        return ApplyResult(rem, False, f"skip (already declared): {name}")
    existed = ignore_path.is_file()
    if existed:
        _backup_node(ignore_path, root, backup_dir)
        current = ignore_path.read_text(encoding="utf-8", errors="replace")
        suffix = "" if current.endswith("\n") or current == "" else "\n"
        ignore_path.write_text(current + suffix + name + "\n", encoding="utf-8")
    else:
        ignore_path.write_text(_IGNORE_HEADER + name + "\n", encoding="utf-8")
    return ApplyResult(rem, True, f"appended to .ssotyignore: {name}")


def ensure_backup_dir(root: Path, stamp: str | None = None) -> Path:
    """Create and return ``<root>/.ssoty-backup/<stamp>/`` (mkdir parents)."""
    stamp = stamp or make_backup_dir_name()
    backup_dir = root / ".ssoty-backup" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def render_plan_text(plan: list[Remediation]) -> str:
    """Dry-run text: exactly what WOULD change, writing nothing."""
    if not plan:
        return "ssoty fix — nothing to do (no broken symlinks; no undeclared names).\n"
    lines = [f"ssoty fix (DRY-RUN) — {len(plan)} action(s); pass --apply to perform them.", ""]
    for rem in plan:
        if rem.action == REMOVE_BROKEN_SYMLINK:
            arrow = f" -> {rem.detail}" if rem.detail else ""
            lines.append(f"  WOULD remove broken symlink: {rem.path}{arrow}")
        elif rem.action == APPEND_IGNORE:
            lines.append(f"  WOULD append to .ssotyignore: {rem.detail}")
    return "\n".join(lines)


def render_apply_text(backup_dir: Path, results: list[ApplyResult]) -> str:
    """Apply text: backup location first, then a per-action result line."""
    lines = [f"backup written to: {backup_dir}", ""]
    done = sum(1 for r in results if r.done)
    lines.append(f"ssoty fix (APPLIED) — {done}/{len(results)} action(s) performed.")
    lines.append("")
    for r in results:
        lines.append(f"  {r.note}")
    return "\n".join(lines)

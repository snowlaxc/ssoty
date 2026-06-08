"""Adopt scattered rule copies into a canonical SSOT, and add a single new rule.

``ssoty adopt`` is the *bootstrap* manager command: it scans the harnesses present at a
root (reusing the auditor's :func:`ssoty.resolver.resolve_all`), classifies every
same-named rule by the EXACT content-identity grouping proven in
:func:`ssoty.checks.check_content_divergence` — bucket by ``(realpath,
normalize_content(text))`` so an already-symlinked SSOT collapses to one bucket — and
proposes a canonical layout (``common/<name>`` for identical-content rules shared across
harnesses, ``<harness>/<name>`` for harness-private rules). It is the on-ramp that BUILDS
the canonical source that ``ssoty init`` then references and ``ssoty sync`` distributes.

``ssoty add`` places ONE new rule into that canonical SSOT (``--common`` for all harnesses,
``--harness NAME`` for one) so it propagates correctly via a subsequent ``ssoty sync``.

Hard safety contract (mirrors ``fix`` / ``sync`` / ``init``):
  * Default is PREVIEW. Mutation requires an explicit ``--apply``; ``--force`` is required
    to overwrite a destination that already exists with differing content.
  * On apply, EVERY rule file moved/replaced and EVERY original about to become a symlink
    is backed up via :func:`ssoty.fix._backup_node` BEFORE any mutation, under one
    timestamped backup dir per run (created lazily on the first real backup).
  * DIVERGENT rules (same name, >1 distinct normalized-content bucket) are FLAGGED and
    NEVER auto-merged into a single ``common/<name>``. Every variant is backed up and the
    originals are left in place; the user resolves the divergence manually.
  * ENTRYPOINT files (``CLAUDE.md`` / ``AGENTS.md`` / …) are excluded from classification —
    each harness owns its own copy by design — and recorded as a non-movable bucket.
  * Broken symlinks (resolver sets ``text=''``) are filtered before bucketing so an empty
    string never fabricates divergence.
  * The canonical home (where rules consolidate) is a TRUSTED, configurable location
    (``--canonical-dir`` / ``ssoty --home`` / the ``~/.ssoty`` default) and may live OUTSIDE
    the scan root by design. Engine-generated destinations under it (``common/<name>`` /
    ``<harness>/<name>``) contain no ``..``, so nothing escapes the home. ``add`` still
    validates its single dest under ``root`` via ``sync._require_*``.
  * Idempotent: a re-run is a no-op (originals already symlinked to canonical are skipped
    via ``sync._same_link``; identical-content writes are skipped via ``normalize_content``).
  * stdlib only (``os``/``shutil``/``pathlib``/``hashlib``/``datetime``); deterministic
    (sorted iteration, lexicographic tie-breaks); no network/LLM.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ssoty.fix import _backup_node, ensure_backup_dir
from ssoty.init import PLACEHOLDER_DIR
from ssoty.models import ENTRYPOINTS, HarnessSurface, RuleDoc, normalize_content
from ssoty.resolver import DEFAULT_SPECS, HarnessSpec
from ssoty.sync import (
    ManifestError,
    _require_realpath_under_root,
    _require_under_root,
    _same_link,
    load_manifest,
    manifest_path,
)

# Classification kinds for adopt.
COMMON_CANDIDATE = "common_candidate"  # >=2 harnesses, all live copies share ONE content bucket
HARNESS_SPECIFIC = "harness_specific"  # appears in exactly ONE harness
ALREADY_SHARED = "already_shared"  # >=2 harnesses but all docs collapse to a single realpath
DIVERGENT = "divergent"  # >=2 harnesses with >1 distinct normalized-content bucket
ENTRYPOINT = "entrypoint"  # per-harness entrypoint, never movable

# Per-move action kinds (decided at apply time against the live filesystem).
ACTION_MOVE = "move"  # move/copy a rule into canonical, then optionally symlink the original
ACTION_FLAG = "flag-divergent"  # back up every variant; never write common/<name>
ACTION_ALREADY = "already-shared"  # no move; report only


# --------------------------------------------------------------------------- #
# Planning data model.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Variant:
    """One concrete copy of a rule (a harness + its resolved doc + a content fingerprint)."""

    harness: str
    path: Path
    fingerprint: str  # short deterministic digest of normalize_content(text)
    first_line: str  # first non-empty normalized line, for a human-readable preview


@dataclass(frozen=True)
class ProposedRule:
    """One classified rule name and where adopt proposes it should live."""

    name: str
    kind: str  # COMMON_CANDIDATE | HARNESS_SPECIFIC | ALREADY_SHARED | DIVERGENT | ENTRYPOINT
    # The canonical destination relative to the canonical root (e.g. "common/x.md" or
    # "claude-code/x.md"). Empty for DIVERGENT / ENTRYPOINT (no single canonical target).
    canonical_rel: str
    variants: tuple[Variant, ...]  # every live copy contributing to this name
    # The representative doc whose content moves into canonical (COMMON_CANDIDATE /
    # HARNESS_SPECIFIC). None for ALREADY_SHARED / DIVERGENT / ENTRYPOINT.
    source_path: Path | None = None


@dataclass(frozen=True)
class AdoptPlan:
    """The fully-classified, pre-validated adopt plan (no mutation)."""

    root: Path
    canonical_dir: Path  # absolute, validated under root
    canonical_rel: str  # canonical_dir rendered relative to root (for display)
    symlink_originals: bool
    rules: tuple[ProposedRule, ...] = ()
    # Every harness the scan found at this root (e.g. ("claude-code", "codex", ...)). This is
    # the candidate set the TUI offers as assignment targets — a rule can be re-bucketed into
    # ANY scanned harness, not only the ones it already has a copy in. Sorted, de-duplicated.
    scanned_harnesses: tuple[str, ...] = ()

    def by_kind(self, kind: str) -> list[ProposedRule]:
        return [r for r in self.rules if r.kind == kind]


@dataclass(frozen=True)
class AdoptResult:
    """Outcome of applying one :class:`ProposedRule`."""

    rule: ProposedRule
    action: str  # ACTION_MOVE | ACTION_FLAG | ACTION_ALREADY
    done: bool
    note: str


# --------------------------------------------------------------------------- #
# Adopt: classification.
# --------------------------------------------------------------------------- #


def _fingerprint(text: str) -> str:
    """Short deterministic digest of normalized content (stdlib hashlib, no new dep)."""
    digest = hashlib.sha256(normalize_content(text).encode("utf-8")).hexdigest()
    return digest[:12]


def _first_line(text: str) -> str:
    for line in normalize_content(text).splitlines():
        if line.strip():
            return line.strip()
    return ""


def _make_variant(harness: str, doc: RuleDoc) -> Variant:
    return Variant(
        harness=harness,
        path=doc.path,
        fingerprint=_fingerprint(doc.text),
        first_line=_first_line(doc.text),
    )


def _classify_name(name: str, entries: list[tuple[str, RuleDoc]], canonical_rel: str) -> ProposedRule:
    """Classify one rule name into a :class:`ProposedRule` (pure, deterministic).

    ``entries`` are (harness, doc) pairs for this basename. Broken docs are filtered
    (resolver sets ``text=''`` for broken symlinks; an empty string would fabricate
    divergence — mirror ``check_content_divergence``'s ``live`` filter). Buckets are keyed
    by ``(realpath, normalize_content(text))`` so a same-realpath symlinked SSOT collapses
    to one bucket, exactly like ``check_content_divergence``.
    """
    live = [(h, d) for h, d in entries if not d.broken]
    # Deterministic ordering: sort by harness then path.
    live.sort(key=lambda hd: (hd[0], str(hd[1].path)))
    variants = tuple(_make_variant(h, d) for h, d in live)
    harnesses = {h for h, _ in live}

    # Bucket exactly as check_content_divergence does.
    buckets: dict[tuple[str, str], RuleDoc] = {}
    realpaths: set[str] = set()
    for _h, doc in live:
        realpath = os.path.realpath(str(doc.path))
        realpaths.add(realpath)
        key = (realpath, normalize_content(doc.text))
        buckets.setdefault(key, doc)
    distinct_norms = {norm for _rp, norm in buckets}

    if len(harnesses) < 2:
        # Exactly one harness -> harness-private.
        rep = live[0][1]
        harness = live[0][0]
        return ProposedRule(
            name=name,
            kind=HARNESS_SPECIFIC,
            canonical_rel=f"{harness}/{name}",
            variants=variants,
            source_path=rep.path,
        )

    if len(realpaths) == 1:
        # All copies collapse to a single inode -> already a shared SSOT.
        return ProposedRule(name=name, kind=ALREADY_SHARED, canonical_rel="", variants=variants)

    if len(distinct_norms) <= 1:
        # >=2 harnesses, separate copies, but byte-identical normalized content -> safe to
        # consolidate into one canonical common/<name>. Pick a deterministic representative.
        rep = sorted(buckets.values(), key=lambda d: str(d.path))[0]
        return ProposedRule(
            name=name,
            kind=COMMON_CANDIDATE,
            canonical_rel=f"common/{name}",
            variants=variants,
            source_path=rep.path,
        )

    # >=2 harnesses, >1 distinct normalized content -> DIVERGENT. Never auto-pick.
    return ProposedRule(name=name, kind=DIVERGENT, canonical_rel="", variants=variants)


def _resolve_canonical_dir(root: Path, canonical_dir: str | None) -> tuple[Path, str]:
    """Resolve the canonical home dir; return (absolute, relative-to-root-for-display).

    The canonical home is where adopt consolidates rules. It is a TRUSTED location — an
    explicit ``--home``/``--canonical-dir`` or the ``~/.ssoty`` default — and is deliberately
    NOT constrained to the scan root: the whole point of a configurable home is that it can
    live outside the scanned ``$HOME`` (e.g. ``~/.ssoty`` while scanning ``$HOME``, or any
    ``--home`` path). Destinations under it use engine-generated relative paths
    (``common/<name>`` / ``<harness>/<name>``) that contain no ``..``, so nothing escapes the
    home. A relative ``canonical_dir`` is still anchored at ``root`` for backward compat.
    """
    root_abs = Path(os.path.normpath(str(root)))
    if canonical_dir:
        chosen = canonical_dir
    else:
        # PLACEHOLDER_DIR is "agent-rules/common"; adopt's canonical ROOT is its parent so
        # that "common/<name>" and "<harness>/<name>" land at "agent-rules/common/..." and
        # "agent-rules/<harness>/..." respectively — the layout init/sync compose with.
        chosen = str(Path(PLACEHOLDER_DIR).parent)  # "agent-rules"
    p = Path(os.path.expanduser(chosen))
    if not p.is_absolute():
        p = root_abs / p
    p = Path(os.path.normpath(str(p)))
    try:
        rel = str(p.relative_to(root_abs))
    except ValueError:
        # Home lives outside the scan root (the configurable-home case) — display the
        # absolute path rather than a root-relative one.
        rel = str(p)
    return p, rel


def build_adopt_plan(
    root: Path,
    surfaces: dict[str, HarnessSurface],
    canonical_dir: str | None = None,
    symlink_originals: bool = True,
) -> AdoptPlan:
    """Classify every rule name across the present harnesses into an :class:`AdoptPlan`.

    Pure: takes already-resolved ``surfaces`` (from ``resolve_all``) and returns a plan —
    no filesystem mutation. ENTRYPOINTS are bucketed separately and never classified for a
    move. Names are sorted; harness ordering within a name is lexicographic.
    """
    canon_abs, canon_rel = _resolve_canonical_dir(root, canonical_dir)

    docs_by_name: dict[str, list[tuple[str, RuleDoc]]] = {}
    for harness in sorted(surfaces):
        surface = surfaces[harness]
        for doc in surface.docs:
            docs_by_name.setdefault(doc.name, []).append((harness, doc))

    rules: list[ProposedRule] = []
    for name in sorted(docs_by_name):
        entries = docs_by_name[name]
        if name in ENTRYPOINTS:
            live = [(h, d) for h, d in entries if not d.broken]
            live.sort(key=lambda hd: (hd[0], str(hd[1].path)))
            rules.append(
                ProposedRule(
                    name=name,
                    kind=ENTRYPOINT,
                    canonical_rel="",
                    variants=tuple(_make_variant(h, d) for h, d in live),
                )
            )
            continue
        live = [(h, d) for h, d in entries if not d.broken]
        if not live:
            continue  # only broken copies — nothing live to adopt
        rules.append(_classify_name(name, entries, canon_rel))

    return AdoptPlan(
        root=Path(os.path.normpath(str(root))),
        canonical_dir=canon_abs,
        canonical_rel=canon_rel,
        symlink_originals=symlink_originals,
        rules=tuple(rules),
        scanned_harnesses=tuple(sorted(surfaces)),
    )


def adopt_needs_force(plan: AdoptPlan) -> bool:
    """True if any MOVE destination already exists with DIFFERING content (needs --force).

    Re-stats the live filesystem. A destination that does not exist, or exists with
    byte-identical normalized content (idempotent re-run), does not require ``--force``.
    """
    for rule in plan.rules:
        if rule.kind not in (COMMON_CANDIDATE, HARNESS_SPECIFIC):
            continue
        dest = _dest_path(plan, rule)
        if dest.exists() and not dest.is_symlink():
            existing = _read(dest)
            incoming = _read(rule.source_path) if rule.source_path else ""
            if normalize_content(existing) != normalize_content(incoming):
                return True
    return False


def _dest_path(plan: AdoptPlan, rule: ProposedRule) -> Path:
    """Absolute canonical destination for a movable rule (``common/x`` or ``<harness>/x``)."""
    # canonical_rel is like "common/<name>" or "<harness>/<name>"; join under canonical_dir.
    return plan.canonical_dir / rule.canonical_rel


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Adopt: apply.
# --------------------------------------------------------------------------- #


class _Backups:
    """Lazily create one timestamped backup dir per run; back up a node before mutation."""

    def __init__(self, root: Path, stamp: str | None = None):
        self._root = root
        self._stamp = stamp
        self._dir: Path | None = None

    @property
    def dir(self) -> Path | None:
        return self._dir

    def backup(self, node: Path) -> None:
        if self._dir is None:
            self._dir = ensure_backup_dir(self._root, self._stamp)
        _backup_node(node, self._root, self._dir)


def apply_adopt_plan(plan: AdoptPlan, stamp: str | None = None) -> tuple[list[AdoptResult], Path | None]:
    """Apply the plan: move/copy rules into canonical, flag divergence, optionally symlink.

    Backups precede every mutation. Returns ``(results, backup_dir)`` where ``backup_dir``
    is ``None`` if nothing needed backing up.
    """
    backups = _Backups(plan.root, stamp)
    results: list[AdoptResult] = []
    for rule in plan.rules:
        if rule.kind in (COMMON_CANDIDATE, HARNESS_SPECIFIC):
            results.append(_apply_move(plan, rule, backups))
        elif rule.kind == DIVERGENT:
            results.append(_apply_flag_divergent(plan, rule, backups))
        elif rule.kind == ALREADY_SHARED:
            results.append(AdoptResult(rule, ACTION_ALREADY, False, f"already shared (single inode): {rule.name}"))
        # ENTRYPOINT: left in place, not reported as a move (recorded only in the preview).
    return results, backups.dir


def _apply_move(plan: AdoptPlan, rule: ProposedRule, backups: _Backups) -> AdoptResult:
    """Move/copy a rule into canonical, back up first, then optionally symlink originals.

    Idempotent: if the canonical dest already holds byte-identical content and every
    original is already symlinked to it, the whole action is a skip.
    """
    assert rule.source_path is not None
    dest = _dest_path(plan, rule)
    incoming = _read(rule.source_path)

    # Idempotence: dest exists with identical normalized content?
    dest_same = (
        dest.exists() and not dest.is_symlink() and normalize_content(_read(dest)) == normalize_content(incoming)
    )

    if not dest_same:
        if dest.exists() or dest.is_symlink():
            backups.backup(dest)
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        # Back up the source content before moving it (its bytes are relocating).
        backups.backup(rule.source_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # COPY the representative into canonical (we may need each original location for the
        # symlink step; a move would remove one of them). Originals are replaced below.
        shutil.copy2(rule.source_path, dest)

    linked = 0
    skipped = 0
    if plan.symlink_originals:
        for variant in rule.variants:
            original = variant.path
            if _same_link(original, dest):
                skipped += 1
                continue
            backups.backup(original)
            if original.is_dir() and not original.is_symlink():
                shutil.rmtree(original)
            elif original.exists() or original.is_symlink():
                original.unlink()
            original.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(dest), original)
            linked += 1

    verb = "unchanged" if dest_same and not linked else ("copied" if not dest_same else "relinked")
    note = f"{verb}: {rule.name} -> {plan.canonical_rel}/{rule.canonical_rel}"
    if plan.symlink_originals:
        note += f" (symlinked {linked} original(s)" + (f", {skipped} already linked)" if skipped else ")")
    done = (not dest_same) or linked > 0
    return AdoptResult(rule, ACTION_MOVE, done, note)


def _apply_flag_divergent(plan: AdoptPlan, rule: ProposedRule, backups: _Backups) -> AdoptResult:
    """Back up EVERY divergent variant; never write a single common/<name>. Leave originals."""
    for variant in rule.variants:
        if variant.path.exists() or variant.path.is_symlink():
            backups.backup(variant.path)
    fps = ", ".join(f"{v.harness}={v.fingerprint}" for v in rule.variants)
    note = (
        f"UNRESOLVED divergence: {rule.name} — {len(rule.variants)} variant(s) backed up, "
        f"originals left in place; resolve manually then re-run ({fps})"
    )
    return AdoptResult(rule, ACTION_FLAG, True, note)


# --------------------------------------------------------------------------- #
# Adopt: rendering.
# --------------------------------------------------------------------------- #


def _counts(plan: AdoptPlan) -> dict[str, int]:
    return {
        COMMON_CANDIDATE: len(plan.by_kind(COMMON_CANDIDATE)),
        HARNESS_SPECIFIC: len(plan.by_kind(HARNESS_SPECIFIC)),
        DIVERGENT: len(plan.by_kind(DIVERGENT)),
        ALREADY_SHARED: len(plan.by_kind(ALREADY_SHARED)),
        ENTRYPOINT: len(plan.by_kind(ENTRYPOINT)),
    }


def render_adopt_preview(plan: AdoptPlan, n_harnesses: int) -> str:
    """Preview a canonical TREE in the readable style of ``init.render_manifest`` output."""
    c = _counts(plan)
    lines = [
        f"ssoty adopt (PREVIEW) — {n_harnesses} harness(es) scanned; "
        f"{c[COMMON_CANDIDATE]} common, {c[HARNESS_SPECIFIC]} harness-specific, "
        f"{c[DIVERGENT]} divergent, {c[ALREADY_SHARED]} already-shared.",
        f"canonical root: {plan.canonical_rel}/",
        "",
    ]

    # Group movable rules by their first path segment (common / <harness>).
    groups: dict[str, list[ProposedRule]] = {}
    for rule in plan.rules:
        if rule.kind in (COMMON_CANDIDATE, HARNESS_SPECIFIC):
            seg = rule.canonical_rel.split("/", 1)[0]
            groups.setdefault(seg, []).append(rule)
    for seg in sorted(groups):
        lines.append(f"  {seg}/")
        for rule in sorted(groups[seg], key=lambda r: r.name):
            origins = ", ".join(sorted({v.harness for v in rule.variants}))
            action = "MOVE" if rule.kind == COMMON_CANDIDATE else "COPY"
            if plan.symlink_originals:
                action += "+SYMLINK"
            lines.append(f"    {rule.name}  [{action}; from {origins}]")

    already = plan.by_kind(ALREADY_SHARED)
    if already:
        lines.append("")
        lines.append("  ALREADY-SHARED (single inode, no move):")
        for rule in sorted(already, key=lambda r: r.name):
            lines.append(f"    {rule.name}")

    entrypoints = plan.by_kind(ENTRYPOINT)
    if entrypoints:
        lines.append("")
        lines.append("  ENTRYPOINTS (per-harness, left in place):")
        for rule in sorted(entrypoints, key=lambda r: r.name):
            origins = ", ".join(sorted({v.harness for v in rule.variants}))
            lines.append(f"    {rule.name}  [from {origins}]")

    divergent = plan.by_kind(DIVERGENT)
    if divergent:
        lines.append("")
        lines.append("  UNRESOLVED DIVERGENCE (NOT merged — resolve manually):")
        for rule in sorted(divergent, key=lambda r: r.name):
            lines.append(f"    {rule.name}:")
            for v in rule.variants:
                head = f' "{v.first_line[:48]}"' if v.first_line else ""
                lines.append(f"      {v.harness}: {v.path}  [{v.fingerprint}]{head}")

    lines.append("")
    lines.append("review, then re-run with --apply")
    lines.append("pairs with: ssoty init then ssoty sync")
    return "\n".join(lines)


def render_adopt_apply(backup_dir: Path | None, results: list[AdoptResult]) -> str:
    """Apply text: backup location first (if any), then a per-action result line."""
    lines: list[str] = []
    if backup_dir is not None:
        lines.append(f"backup written to: {backup_dir}")
        lines.append("")
    done = sum(1 for r in results if r.done)
    lines.append(f"ssoty adopt (APPLIED) — {done}/{len(results)} action(s) performed.")
    divergent = sum(1 for r in results if r.action == ACTION_FLAG)
    if divergent:
        lines.append(f"  ({divergent} divergent rule(s) flagged and deferred — resolve manually)")
    lines.append("")
    for r in results:
        lines.append(f"  {r.note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Add: place ONE new rule into the canonical SSOT.
# --------------------------------------------------------------------------- #

# Placement choices.
PLACE_COMMON = "common"
PLACE_HARNESS = "harness"


class AddError(Exception):
    """A bad rule path or unknown harness — mapped to CLI exit code 2."""


@dataclass(frozen=True)
class AddPlan:
    """One planned single-rule placement (no mutation)."""

    root: Path
    rule_name: str
    source_text: str  # the rule's content (read from the source file)
    dest: Path  # absolute destination, validated under root
    dest_rel: str  # dest rendered relative to root (for display)
    placement: str  # PLACE_COMMON | PLACE_HARNESS
    harness: str | None  # set when placement == PLACE_HARNESS
    next_step: str  # the exact follow-up command to print


@dataclass(frozen=True)
class AddResult:
    """Outcome of applying an :class:`AddPlan`."""

    plan: AddPlan
    done: bool
    note: str


def _known_harnesses() -> set[str]:
    return {spec.harness for spec in DEFAULT_SPECS}


def _spec_for(name: str) -> HarnessSpec | None:
    for spec in DEFAULT_SPECS:
        if spec.harness == name:
            return spec
    return None


def _common_dir_from_manifest(root: Path, manifest_explicit: str | None) -> str:
    """Resolve the canonical common dir: manifest's ``common.sources[0].dir`` else placeholder.

    Reads the manifest only if present; a missing/invalid manifest falls back to
    ``init.PLACEHOLDER_DIR`` ('agent-rules/common') so ``add`` works before ``init``.
    """
    mpath = manifest_path(root, manifest_explicit)
    try:
        manifest = load_manifest(mpath)
    except ManifestError:
        return PLACEHOLDER_DIR
    common = manifest.get("common", {})
    sources = common.get("sources", []) if isinstance(common, dict) else []
    if isinstance(sources, list):
        for entry in sources:
            if isinstance(entry, dict) and entry.get("dir"):
                return str(entry["dir"])
    return PLACEHOLDER_DIR


def _harness_target_from_manifest(root: Path, harness: str, manifest_explicit: str | None) -> str | None:
    """The harness's manifest ``target`` dir, if a manifest exists and declares it."""
    mpath = manifest_path(root, manifest_explicit)
    try:
        manifest = load_manifest(mpath)
    except ManifestError:
        return None
    harnesses = manifest.get("harnesses", {})
    if not isinstance(harnesses, dict):
        return None
    spec = harnesses.get(harness)
    if isinstance(spec, dict) and isinstance(spec.get("target"), str):
        return spec["target"]
    return None


def add_choices(root: Path, surfaces: dict[str, HarnessSurface], manifest_explicit: str | None) -> str:
    """Render the candidate placements when no --common/--harness was given (preview, no guess)."""
    common_dir = _common_dir_from_manifest(root, manifest_explicit)
    lines = [
        "ssoty add — choose a placement (no --common/--harness given; not guessing):",
        "",
        f"  --common              -> {common_dir}/  (syncs to ALL harnesses)",
    ]
    for harness in sorted(surfaces):
        target = _harness_target_from_manifest(root, harness, manifest_explicit) or f"{harness}/ (canonical)"
        lines.append(f"  --harness {harness:<14}-> {target}")
    lines.append("")
    lines.append("re-run with one of the above, then: ssoty sync")
    return "\n".join(lines)


def build_add_plan(
    root: Path,
    rule: str,
    placement: str,
    harness: str | None,
    surfaces: dict[str, HarnessSurface],
    manifest_explicit: str | None = None,
    canonical_dir: str | None = None,
) -> AddPlan:
    """Build a single-rule placement plan. Raises :class:`AddError` on a bad rule/harness.

    ``rule`` is primarily a PATH to an existing file (its content is read and copied). The
    destination basename keeps the rule's filename. Destinations are validated under ``root``.
    """
    rule_path = Path(os.path.expanduser(rule))
    if not rule_path.is_file():
        raise AddError(f"rule file not found: {rule} (pass a path to an existing rule file)")
    name = rule_path.name
    source_text = _read(rule_path)

    root_abs = Path(os.path.normpath(str(root)))

    if placement == PLACE_COMMON:
        common_dir = canonical_dir or _common_dir_from_manifest(root, manifest_explicit)
        dest_dir = _abs_under_root(common_dir, root_abs)
        dest = dest_dir / name
        next_step = "next: ssoty sync   (preview) then --apply  — links the new common rule into every harness"
        resolved_harness = None
    elif placement == PLACE_HARNESS:
        if harness is None:
            raise AddError("internal: --harness placement requires a harness name")
        known = _known_harnesses()
        if harness not in known:
            raise AddError(f"unknown harness: {harness} (known: {', '.join(sorted(known))})")
        if harness not in surfaces:
            raise AddError(
                f"harness not present: {harness} "
                f"(present: {', '.join(sorted(surfaces)) or 'none'}; known: {', '.join(sorted(known))})"
            )
        target = _harness_target_from_manifest(root, harness, manifest_explicit)
        if target is None:
            # No manifest target — fall back to a canonical <harness>/ dir under the common parent.
            base = canonical_dir or str(Path(PLACEHOLDER_DIR).parent)  # "agent-rules"
            dest_dir = _abs_under_root(f"{base}/{harness}", root_abs)
        else:
            dest_dir = _abs_under_root(target, root_abs)
        dest = dest_dir / name
        next_step = f"next: the rule now lives in {harness}'s own source; run ssoty sync if that source feeds others"
        resolved_harness = harness
    else:  # pragma: no cover - argparse constrains this
        raise AddError(f"invalid placement: {placement}")

    dest = Path(os.path.normpath(str(dest)))
    _require_under_root(dest, root_abs, "add")
    _require_realpath_under_root(dest, root_abs, "add")
    try:
        dest_rel = str(dest.relative_to(root_abs))
    except ValueError:  # pragma: no cover - guarded above
        dest_rel = str(dest)

    return AddPlan(
        root=root_abs,
        rule_name=name,
        source_text=source_text,
        dest=dest,
        dest_rel=dest_rel,
        placement=placement,
        harness=resolved_harness,
        next_step=next_step,
    )


def _abs_under_root(rel_or_abs: str, root_abs: Path) -> Path:
    p = Path(os.path.expanduser(rel_or_abs))
    if not p.is_absolute():
        p = root_abs / p
    return Path(os.path.normpath(str(p)))


def add_needs_force(plan: AddPlan) -> bool:
    """True if the destination already exists with DIFFERING content (needs --force)."""
    if plan.dest.exists() and not plan.dest.is_symlink():
        return normalize_content(_read(plan.dest)) != normalize_content(plan.source_text)
    return False


def apply_add_plan(plan: AddPlan, stamp: str | None = None) -> tuple[AddResult, Path | None]:
    """Write the one rule into its canonical destination, backing up first on overwrite.

    Idempotent: if the destination already holds byte-identical normalized content, skip.
    """
    backups = _Backups(plan.root, stamp)
    dest = plan.dest
    if dest.exists() and not dest.is_symlink():
        if normalize_content(_read(dest)) == normalize_content(plan.source_text):
            return AddResult(plan, False, f"unchanged (identical content): {plan.dest_rel}"), None
        backups.backup(dest)
        dest.unlink()
    elif dest.is_symlink():
        backups.backup(dest)
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(plan.source_text, encoding="utf-8")
    return AddResult(plan, True, f"wrote: {plan.dest_rel}"), backups.dir


def render_add_preview(plan: AddPlan) -> str:
    """Preview the single placement: dest + whether it overwrites + the next step."""
    overwrites = plan.dest.exists() or plan.dest.is_symlink()
    where = "common (all harnesses)" if plan.placement == PLACE_COMMON else f"harness '{plan.harness}'"
    lines = [
        f"ssoty add (PREVIEW) — place '{plan.rule_name}' into {where}.",
        "",
        f"  dest: {plan.dest_rel}" + ("  [OVERWRITES existing — needs --force]" if overwrites else ""),
        "",
        "pass --apply to write" + (" (and --force to overwrite)" if overwrites else ""),
        plan.next_step,
    ]
    return "\n".join(lines)


def render_add_apply(backup_dir: Path | None, result: AddResult) -> str:
    """Apply text: backup location first (if any), then the result line + next step."""
    lines: list[str] = []
    if backup_dir is not None:
        lines.append(f"backup written to: {backup_dir}")
        lines.append("")
    lines.append("ssoty add (APPLIED).")
    lines.append("")
    lines.append(f"  {result.note}")
    lines.append(result.plan.next_step)
    return "\n".join(lines)

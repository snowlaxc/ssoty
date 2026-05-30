"""``ssoty`` command-line entry point.

Usage:
    ssoty diff    [PATH] [--a HARNESS --b HARNESS] [--json] [--redact]
    ssoty audit   [PATH] [--format {text,json,sarif}] [--json] [--redact] [--ci]
    ssoty metrics [PATH] [--json] [--redact]
    ssoty resolve [PATH] [--json] [--redact]
    ssoty fix     [PATH] [--apply] [--redact] [--scaffold-ignore]
    ssoty sync    [PATH] [--apply] [--method symlink] [--manifest PATH] [--redact]

PATH is the root that contains ``.claude`` / ``.codex`` (defaults to $HOME).
For fixtures, pass the fixture dir, e.g. ``ssoty audit examples/messy-setup``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from ssoty import __version__
from ssoty.checks import CheckContext, run_checks
from ssoty.diff import diff_pair
from ssoty.fix import (
    apply_remediations,
    ensure_backup_dir,
    plan_remediations,
    render_apply_text,
    render_plan_text,
)
from ssoty.ignore import SsotyIgnore
from ssoty.metrics import HarnessTax, compute_context_tax
from ssoty.models import AuditResult
from ssoty.redact import redact
from ssoty.report import (
    render_diff_json,
    render_diff_text,
    render_findings_text,
    render_json,
    render_metrics_text,
    render_resolve_json,
    render_resolve_text,
    render_sarif,
)
from ssoty.resolver import resolve_all
from ssoty.sync import (
    ManifestError,
    apply_plan,
    build_plan,
    load_manifest,
    manifest_path,
    plan_has_apply_work,
)
from ssoty.sync import (
    render_apply_text as render_sync_apply_text,
)
from ssoty.sync import (
    render_plan_text as render_sync_plan_text,
)


def _resolve_root(path: str | None) -> Path:
    return Path(path).expanduser() if path else Path.home()


def build(root: Path) -> tuple[AuditResult, dict[str, HarnessTax]]:
    surfaces = resolve_all(root)
    ctx = CheckContext(surfaces=surfaces, ignore=SsotyIgnore.load(root), root=root)
    result = AuditResult(surfaces=surfaces, findings=run_checks(ctx))
    return result, compute_context_tax(surfaces)


def _redactor(enabled: bool) -> Callable[[str], str]:
    return redact if enabled else (lambda s: s)


def cmd_audit(args: argparse.Namespace) -> int:
    result, tax = build(_resolve_root(args.path))
    redactor = _redactor(args.redact)
    # --json is a back-compat alias for --format json (preserve 0.1.x behavior).
    fmt = "json" if args.json else args.format
    if fmt == "sarif":
        print(render_sarif(result, redactor))
    elif fmt == "json":
        print(render_json(result, tax, redactor))
    else:
        print(render_findings_text(result, redactor))
    if args.ci and result.has_blocking():
        return 1
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    _, tax = build(_resolve_root(args.path))
    redactor = _redactor(args.redact)
    if args.json:
        print(render_json(AuditResult(), tax, redactor))
    else:
        print(render_metrics_text(tax, redactor))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    surfaces = resolve_all(_resolve_root(args.path))
    redactor = _redactor(args.redact)
    if args.json:
        print(render_resolve_json(surfaces, redactor))
    else:
        print(render_resolve_text(surfaces, redactor))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    surfaces = resolve_all(_resolve_root(args.path))  # read-only; same as resolve
    redactor = _redactor(args.redact)
    if (args.a is None) != (args.b is None):
        print("ssoty diff: --a and --b must be given together", file=sys.stderr)
        return 2
    if args.a is not None:
        missing = [h for h in (args.a, args.b) if h not in surfaces]
        if missing:
            print(
                f"ssoty diff: harness not present: {', '.join(missing)} " f"(present: {', '.join(sorted(surfaces))})",
                file=sys.stderr,
            )
            return 2
        pairs = [(args.a, args.b)]
    else:
        present = sorted(surfaces)
        pairs = [(a, b) for i, a in enumerate(present) for b in present[i + 1 :]]
    diffs = [diff_pair(surfaces[a], surfaces[b]) for a, b in pairs]
    if args.json:
        print(render_diff_json(diffs, redactor))
    else:
        print(render_diff_text(diffs, redactor))
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    root = _resolve_root(args.path)
    result, _ = build(root)
    redactor = _redactor(args.redact)
    plan = plan_remediations(result, root, scaffold_ignore=args.scaffold_ignore)
    if not args.apply:
        # Dry-run is the DEFAULT: print exactly what WOULD change, write nothing.
        print(redactor(render_plan_text(plan)))
        return 0
    if not plan:
        # No work -> create no backup dir, write nothing (idempotent).
        print(redactor(render_plan_text(plan)))
        return 0
    backup_dir = ensure_backup_dir(root)
    results = apply_remediations(plan, root, backup_dir)
    print(redactor(render_apply_text(backup_dir, results)))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    root = _resolve_root(args.path)
    redactor = _redactor(args.redact)
    try:
        mpath = manifest_path(root, args.manifest)
        manifest = load_manifest(mpath)
        # CLI --method overrides any in-manifest "method"; default is symlink.
        method = args.method or manifest.get("method", "symlink")
        plan = build_plan(root, manifest, mpath.parent, method=method)
    except ManifestError as exc:
        # Full validation precedes any mutation: a bad manifest exits 2, never a partial write.
        print(f"ssoty sync: {redactor(str(exc))}", file=sys.stderr)
        return 2
    if not args.apply:
        # Dry-run is the DEFAULT: print the exact plan, write nothing, create no backup dir.
        print(redactor(render_sync_plan_text(plan)))
        return 0
    if not plan_has_apply_work(plan):
        # Fully-synced (or empty) tree: no backup dir, no writes (idempotent).
        print(redactor(render_sync_apply_text(None, [])))
        return 0
    # apply_plan creates the backup dir lazily — only if a real node is backed up; an
    # all-new-links plan leaves no backup dir behind.
    results, backup_dir = apply_plan(plan, root)
    print(redactor(render_sync_apply_text(backup_dir, results)))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssoty", description="Static cross-harness rule coherence auditor.")
    parser.add_argument("--version", action="version", version=f"ssoty {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="report coherence findings")
    audit.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    audit.add_argument(
        "--format",
        choices=("text", "json", "sarif"),
        default="text",
        help="output format (sarif: SARIF 2.1.0 for code-scanning upload)",
    )
    audit.add_argument("--json", action="store_true", help="emit JSON (alias for --format json)")
    audit.add_argument("--redact", action="store_true", help="mask home paths and emails")
    audit.add_argument("--ci", action="store_true", help="exit non-zero on any Critical finding")
    audit.set_defaults(func=cmd_audit)

    diff = sub.add_parser("diff", help="compare effective rule surfaces of two harnesses (divergence)")
    diff.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    diff.add_argument(
        "--a", dest="a", metavar="HARNESS", help="first harness (omit both --a/--b to diff all present pairs)"
    )
    diff.add_argument("--b", dest="b", metavar="HARNESS", help="second harness")
    diff.add_argument("--json", action="store_true", help="emit JSON")
    diff.add_argument("--redact", action="store_true", help="mask home paths and emails")
    diff.set_defaults(func=cmd_diff)

    metrics = sub.add_parser("metrics", help="report Context Tax per harness")
    metrics.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    metrics.add_argument("--json", action="store_true", help="emit JSON")
    metrics.add_argument("--redact", action="store_true", help="mask home paths and emails")
    metrics.set_defaults(func=cmd_metrics)

    resolve = sub.add_parser("resolve", help="show the effective rule surface per harness")
    resolve.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    resolve.add_argument("--json", action="store_true", help="emit JSON")
    resolve.add_argument("--redact", action="store_true", help="mask home paths and emails")
    resolve.set_defaults(func=cmd_resolve)

    fix = sub.add_parser("fix", help="safely remediate findings (DRY-RUN by default)")
    fix.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    fix.add_argument("--apply", action="store_true", help="perform changes (default: dry-run, writes nothing)")
    fix.add_argument("--redact", action="store_true", help="mask home paths and emails")
    fix.add_argument(
        "--scaffold-ignore",
        action="store_true",
        help="also append intentionally non-shared rule names to .ssotyignore",
    )
    fix.set_defaults(func=cmd_fix)

    sync = sub.add_parser("sync", help="distribute the canonical rule source into harness targets (DRY-RUN by default)")
    sync.add_argument("path", nargs="?", help="root the manifest's relative paths resolve against (default: $HOME)")
    sync.add_argument("--apply", action="store_true", help="perform the plan (default: dry-run, writes nothing)")
    sync.add_argument(
        "--method",
        choices=("symlink",),
        default="symlink",
        help="link method (symlink only; reserved for future 'copy'); overrides in-manifest method",
    )
    sync.add_argument("--manifest", help="manifest path (default: ssoty.json in PATH)")
    sync.add_argument("--redact", action="store_true", help="mask home paths and emails")
    sync.set_defaults(func=cmd_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

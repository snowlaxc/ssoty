"""``ssoty`` command-line entry point.

Usage:
    ssoty audit   [PATH] [--json] [--redact] [--ci]
    ssoty metrics [PATH] [--json] [--redact]
    ssoty resolve [PATH] [--json] [--redact]

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
from ssoty.ignore import SsotyIgnore
from ssoty.metrics import HarnessTax, compute_context_tax
from ssoty.models import AuditResult
from ssoty.redact import redact
from ssoty.report import (
    render_findings_text,
    render_json,
    render_metrics_text,
    render_resolve_json,
    render_resolve_text,
)
from ssoty.resolver import resolve_all


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
    if args.json:
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssoty", description="Static cross-harness rule coherence auditor.")
    parser.add_argument("--version", action="version", version=f"ssoty {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="report coherence findings")
    audit.add_argument("path", nargs="?", help="root containing .claude/.codex (default: $HOME)")
    audit.add_argument("--json", action="store_true", help="emit JSON")
    audit.add_argument("--redact", action="store_true", help="mask home paths and emails")
    audit.add_argument("--ci", action="store_true", help="exit non-zero on any Critical finding")
    audit.set_defaults(func=cmd_audit)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

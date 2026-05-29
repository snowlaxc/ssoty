"""Render audit/metrics results as human text or JSON."""

from __future__ import annotations

import json
from collections.abc import Callable

from ssoty.metrics import HarnessTax
from ssoty.models import AuditResult, Finding, HarnessSurface, Severity
from ssoty.tokens import count_tokens

_IDENTITY: Callable[[str], str] = lambda s: s  # noqa: E731


def _summary_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {s.value: 0 for s in Severity}
    for f in findings:
        counts[f.severity.value] += 1
    return counts


def render_findings_text(result: AuditResult, redactor: Callable[[str], str] = _IDENTITY) -> str:
    lines: list[str] = []
    counts = _summary_counts(result.findings)
    lines.append(f"ssoty audit — {counts['Critical']} Critical, " f"{counts['Warning']} Warning, {counts['FYI']} FYI")
    lines.append("")
    if not result.findings:
        lines.append("  no findings — surfaces are coherent.")
        return redactor("\n".join(lines))
    for f in result.findings:
        lines.append(f"  [{f.severity.value}] {f.check} ({f.harness})")
        lines.append(f"      {f.file}")
        lines.append(f"      {f.message}")
        lines.append("")
    return redactor("\n".join(lines).rstrip())


def render_metrics_text(tax: dict[str, HarnessTax], redactor: Callable[[str], str] = _IDENTITY) -> str:
    lines = ["ssoty Context Tax (per harness — NOT summed across harnesses)", ""]
    for harness, t in sorted(tax.items()):
        approx = " (approx, char/4)" if t.approx else ""
        lines.append(f"  {harness}:")
        lines.append(f"      always-on  (actual,    every turn) : {t.always_on_tokens} tokens{approx}")
        lines.append(f"      skill-gated(potential, on trigger) : {t.skill_gated_tokens} tokens{approx}")
        lines.append(f"      docs                               : {t.doc_count}")
        lines.append("")
    lines.append("  note: always-on and skill-gated are different load guarantees;")
    lines.append("        compare before/after WITHIN one harness, not across harnesses.")
    return redactor("\n".join(lines))


def render_json(result: AuditResult, tax: dict[str, HarnessTax], redactor: Callable[[str], str] = _IDENTITY) -> str:
    payload = {
        "summary": _summary_counts(result.findings),
        "findings": [
            {
                "severity": f.severity.value,
                "check": f.check,
                "harness": f.harness,
                "file": f.file,
                "message": f.message,
                "rule_id": f.rule_id,
            }
            for f in result.findings
        ],
        "context_tax": {
            h: {
                "always_on_tokens": t.always_on_tokens,
                "skill_gated_tokens": t.skill_gated_tokens,
                "doc_count": t.doc_count,
                "approx": t.approx,
            }
            for h, t in tax.items()
        },
    }
    return redactor(json.dumps(payload, indent=2, sort_keys=True))


def _doc_rows(surfaces: dict[str, HarnessSurface]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for harness, surface in surfaces.items():
        rows = []
        for doc in surface.docs:
            tc = count_tokens(doc.text)
            rows.append(
                {
                    "name": doc.name,
                    "load_basis": doc.load_basis,
                    "tokens": tc.tokens,
                    "approx": tc.approx,
                    "is_symlink": doc.is_symlink,
                    "broken": doc.broken,
                    "path": str(doc.path),
                }
            )
        out[harness] = rows
    return out


def render_resolve_text(surfaces: dict[str, HarnessSurface], redactor: Callable[[str], str] = _IDENTITY) -> str:
    lines = ["ssoty resolve — effective rule surface per harness", ""]
    rows = _doc_rows(surfaces)
    for harness in sorted(rows):
        lines.append(f"  {harness} ({len(rows[harness])} docs):")
        for r in rows[harness]:
            if r["broken"]:
                lines.append(f"      [{r['load_basis']}] {r['name']}  (BROKEN symlink)")
            else:
                approx = "~" if r["approx"] else ""
                link = " (symlink)" if r["is_symlink"] else ""
                lines.append(f"      [{r['load_basis']}] {r['name']}  ({approx}{r['tokens']} tok){link}")
        lines.append("")
    return redactor("\n".join(lines).rstrip())


def render_resolve_json(surfaces: dict[str, HarnessSurface], redactor: Callable[[str], str] = _IDENTITY) -> str:
    return redactor(json.dumps(_doc_rows(surfaces), indent=2, sort_keys=True))

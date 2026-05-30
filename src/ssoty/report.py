"""Render audit/metrics results as human text or JSON."""

from __future__ import annotations

import json
from collections.abc import Callable

from ssoty import __version__
from ssoty.checks import ALL_CHECKS
from ssoty.diff import SurfaceDiff
from ssoty.metrics import HarnessTax
from ssoty.models import AuditResult, Finding, HarnessSurface, Severity
from ssoty.tokens import count_tokens


def _identity(s: str) -> str:
    return s


_IDENTITY: Callable[[str], str] = _identity


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


_SARIF_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.WARNING: "warning",
    Severity.FYI: "note",
}


def _check_ids() -> list[str]:
    # Check function __name__ is `check_<id>`; the <id> matches Finding.check.
    return [fn.__name__.removeprefix("check_") for fn in ALL_CHECKS]


def render_sarif(result: AuditResult, redactor: Callable[[str], str] = _IDENTITY) -> str:
    """Render findings as SARIF 2.1.0 (plain JSON, stdlib-only, deterministic).

    finding.file is emitted verbatim as the artifact URI. It is inconsistent across
    checks (real path for broken_symlink/dangling/non_shared, bare basename for
    load_asymmetry, composite string for duplicate_content), so load_asymmetry /
    duplicate_content URIs are not clickable in v1. The Finding model is left intact;
    a structured locations[] is a separate deferred change.
    """
    rules = [{"id": cid, "name": cid} for cid in _check_ids()]
    results = [
        {
            "ruleId": f.check,
            "level": _SARIF_LEVEL[f.severity],
            "message": {"text": f.message},
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": f.file}}},
            ],
        }
        for f in result.findings
    ]
    payload = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ssoty",
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
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


# --- diff (cross-model rule divergence) ---


def _verdict_text(d: SurfaceDiff) -> str:
    if d.coherent:
        return f"{d.a} and {d.b} operate under the same effective rules"
    return f"{d.a} and {d.b} do NOT operate under the same rules"


def _verdict_tally(d: SurfaceDiff) -> str:
    """Parenthetical count of each non-empty divergence category (incoherent only)."""
    parts: list[str] = []
    if d.only_in_a:
        parts.append(f"{len(d.only_in_a)} rule{'s' if len(d.only_in_a) != 1 else ''} only in {d.a}")
    if d.only_in_b:
        parts.append(f"{len(d.only_in_b)} rule{'s' if len(d.only_in_b) != 1 else ''} only in {d.b}")
    if d.different_load:
        parts.append(f"{len(d.different_load)} loads differently")
    if d.broken_cross_refs:
        n = len(d.broken_cross_refs)
        parts.append(f"{n} broken cross-ref{'s' if n != 1 else ''}")
    return ", ".join(parts)


def render_diff_text(diffs: list[SurfaceDiff], redactor: Callable[[str], str] = _IDENTITY) -> str:
    lines = ["ssoty diff — effective rule divergence between harnesses", ""]
    for d in diffs:
        lines.append(f"  {d.a}  vs  {d.b}")
        if d.only_in_a:
            lines.append(f"      only in {d.a} ({len(d.only_in_a)}): {', '.join(d.only_in_a)}")
        if d.only_in_b:
            lines.append(f"      only in {d.b} ({len(d.only_in_b)}): {', '.join(d.only_in_b)}")
        if d.different_load:
            lines.append(f"      same rule, different load ({len(d.different_load)}):")
            for ld in d.different_load:
                lines.append(f"          {ld.name}  {d.a}={ld.a_basis}  |  {d.b}={ld.b_basis}")
        if d.broken_cross_refs:
            lines.append(f"      broken cross-references across the boundary ({len(d.broken_cross_refs)}):")
            for ref in d.broken_cross_refs:
                lines.append(
                    f"          {ref.src_harness}:{ref.src_doc} -> '{ref.ref}'  "
                    f"(loads only in {ref.present_in}, NOT in {ref.src_harness})"
                )
        if d.coherent:
            lines.append(f"      VERDICT: {_verdict_text(d)}")
        else:
            lines.append(f"      VERDICT: {_verdict_text(d)}")
            lines.append(f"               ({_verdict_tally(d)})")
        lines.append("")
    return redactor("\n".join(lines).rstrip())


def render_diff_json(diffs: list[SurfaceDiff], redactor: Callable[[str], str] = _IDENTITY) -> str:
    payload = [
        {
            "a": d.a,
            "b": d.b,
            "only_in_a": list(d.only_in_a),
            "only_in_b": list(d.only_in_b),
            "shared": list(d.shared),
            "different_load": [
                {"name": ld.name, "a_basis": ld.a_basis, "b_basis": ld.b_basis} for ld in d.different_load
            ],
            "broken_cross_refs": [
                {
                    "src_harness": ref.src_harness,
                    "src_doc": ref.src_doc,
                    "ref": ref.ref,
                    "present_in": ref.present_in,
                }
                for ref in d.broken_cross_refs
            ],
            "coherent": d.coherent,
            "verdict": _verdict_text(d),
        }
        for d in diffs
    ]
    return redactor(json.dumps(payload, indent=2, sort_keys=True))

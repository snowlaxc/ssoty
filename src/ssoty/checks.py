"""Deterministic coherence checks. Each returns a list of Findings.

No LLM, no network. Same input -> same output. The headline check is
``dangling_cross_ref`` which distinguishes a genuine cross-boundary dangling
reference (Critical) from intentional, declared non-sharing (FYI).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ssoty.ignore import SsotyIgnore
from ssoty.models import Finding, HarnessSurface, Severity
from ssoty.resolver import referenced_docs
from ssoty.tokens import count_tokens

_DUP_MIN_CHARS = 200  # ignore trivial shared snippets


@dataclass
class CheckContext:
    surfaces: dict[str, HarnessSurface]
    ignore: SsotyIgnore
    root: Path | None = None


def check_broken_symlink(ctx: CheckContext) -> list[Finding]:
    out: list[Finding] = []
    for surface in ctx.surfaces.values():
        for doc in surface.docs:
            if doc.broken:
                out.append(
                    Finding(
                        severity=Severity.CRITICAL,
                        check="broken_symlink",
                        harness=surface.harness,
                        file=str(doc.path),
                        message=f"symlink target does not resolve: {doc.symlink_target}",
                        rule_id=doc.name,
                    )
                )
    return out


def check_dangling_cross_ref(ctx: CheckContext) -> list[Finding]:
    out: list[Finding] = []
    names_by_harness = {h: s.names for h, s in ctx.surfaces.items()}
    for harness, surface in ctx.surfaces.items():
        mine = names_by_harness[harness]
        others = {n for h, names in names_by_harness.items() if h != harness for n in names}
        for doc in surface.docs:
            for ref in sorted(referenced_docs(doc.text)):
                if ref in mine:
                    continue
                if ref in others:
                    if ctx.ignore.declares(ref):
                        out.append(
                            Finding(
                                Severity.FYI,
                                "dangling_cross_ref",
                                harness,
                                str(doc.path),
                                f"references '{ref}' (absent here, intentional per .ssotyignore)",
                                doc.name,
                            )
                        )
                    else:
                        out.append(
                            Finding(
                                Severity.CRITICAL,
                                "dangling_cross_ref",
                                harness,
                                str(doc.path),
                                f"references '{ref}', which exists in another harness but is NOT "
                                f"loaded by '{harness}' — broken pointer across the harness boundary",
                                doc.name,
                            )
                        )
                else:
                    out.append(
                        Finding(
                            Severity.FYI,
                            "dangling_cross_ref",
                            harness,
                            str(doc.path),
                            f"references '{ref}', not found in any resolved harness surface "
                            f"(may be an external or project-local doc)",
                            doc.name,
                        )
                    )
    return out


def check_load_asymmetry(ctx: CheckContext) -> list[Finding]:
    basis_by_name: dict[str, dict[str, str]] = defaultdict(dict)
    for harness, surface in ctx.surfaces.items():
        for doc in surface.docs:
            basis_by_name[doc.name][harness] = doc.load_basis
    out: list[Finding] = []
    for name, per_harness in sorted(basis_by_name.items()):
        distinct = set(per_harness.values())
        if len(per_harness) >= 2 and len(distinct) > 1:
            detail = ", ".join(f"{h}={b}" for h, b in sorted(per_harness.items()))
            out.append(
                Finding(
                    Severity.WARNING,
                    "load_asymmetry",
                    "+".join(sorted(per_harness)),
                    name,
                    f"same rule loads differently per harness ({detail}) — shared file, " f"unequal guarantee",
                    name,
                )
            )
    return out


def check_non_shared_surface(ctx: CheckContext) -> list[Finding]:
    all_harnesses = set(ctx.surfaces)
    out: list[Finding] = []
    for harness, surface in ctx.surfaces.items():
        elsewhere = {n for h, s in ctx.surfaces.items() if h != harness for n in s.names}
        for doc in surface.docs:
            if doc.name not in elsewhere and len(all_harnesses) > 1:
                out.append(
                    Finding(
                        Severity.FYI,
                        "non_shared_surface",
                        harness,
                        str(doc.path),
                        f"'{doc.name}' present only in '{harness}', absent from other harnesses",
                        doc.name,
                    )
                )
    return out


def _paragraphs(text: str) -> list[str]:
    blocks = [b.strip() for b in text.split("\n\n")]
    return [" ".join(b.split()) for b in blocks if len(b.strip()) >= _DUP_MIN_CHARS]


def check_duplicate_content(ctx: CheckContext) -> list[Finding]:
    locations: dict[str, set[str]] = defaultdict(set)
    for surface in ctx.surfaces.values():
        for doc in surface.docs:
            for para in _paragraphs(doc.text):
                locations[para].add(f"{surface.harness}:{doc.name}")
    out: list[Finding] = []
    for para, where in sorted(locations.items()):
        if len(where) < 2:
            continue
        harnesses = {w.split(":", 1)[0] for w in where}
        # within-harness duplication = real token rent (loaded every turn);
        # cross-harness only = expected SSOT sharing (symlinked to both), not rent.
        within = any(sum(1 for w in where if w.startswith(f"{h}:")) >= 2 for h in harnesses)
        dup_tokens = count_tokens(para)
        kind = "approx" if dup_tokens.approx else "exact"
        if within:
            sev, note = Severity.WARNING, "duplicated within a harness (token rent every turn)"
        else:
            sev, note = Severity.FYI, "shared across harnesses (expected SSOT sharing, not rent)"
        out.append(
            Finding(
                sev,
                "duplicate_content",
                "+".join(sorted(harnesses)),
                ", ".join(sorted(where)),
                f"identical {dup_tokens.tokens}-token block ({kind}) {note}",
                "",
            )
        )
    return out


def check_skill_integrity(ctx: CheckContext) -> list[Finding]:
    if ctx.root is None:
        return []
    out: list[Finding] = []
    skills_dirs = [ctx.root / ".claude" / "skills", ctx.root / ".codex" / "skills"]
    for skills_dir in skills_dirs:
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and not (entry / "SKILL.md").is_file():
                out.append(
                    Finding(
                        Severity.WARNING,
                        "skill_integrity",
                        skills_dir.parent.name,
                        str(entry),
                        f"skill directory '{entry.name}' has no SKILL.md",
                        entry.name,
                    )
                )
    return out


ALL_CHECKS = (
    check_broken_symlink,
    check_dangling_cross_ref,
    check_load_asymmetry,
    check_non_shared_surface,
    check_duplicate_content,
    check_skill_integrity,
)


def run_checks(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(ctx))
    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.FYI: 2}
    findings.sort(key=lambda f: (order[f.severity], f.check, f.harness, f.file))
    return findings

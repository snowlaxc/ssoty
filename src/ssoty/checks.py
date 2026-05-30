"""Deterministic coherence checks. Each returns a list of Findings.

No LLM, no network. Same input -> same output. The headline check is
``dangling_cross_ref`` which distinguishes a genuine cross-boundary dangling
reference (Critical) from intentional, declared non-sharing (FYI).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ssoty.ignore import SsotyIgnore
from ssoty.models import ALWAYS_ON, Finding, HarnessSurface, Severity
from ssoty.resolver import referenced_docs
from ssoty.tokens import count_tokens

_DUP_MIN_CHARS = 200  # ignore trivial shared snippets

# weak_directive: a fenced-code stripper (reuse the resolver's pattern locally) and
# the two token vocabularies. A line is flagged ONLY when a weak modal co-occurs with
# a hard-requirement signal on the SAME line — plain standalone `should` is never
# flagged (it is the primary false-positive source).
_WEAK_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# Weak modals: phrase forms first so the message names the full hedge.
_WEAK_MODALS = ("nice to have", "where possible", "if possible", "should", "try to")
_HARD_SIGNALS = (
    "never",
    "must",
    "required",
    "security",
    "secret",
    "credential",
    "production",
    "prod",
    "irreversible",
    "destructive",
    "force push",
    "drop table",
)
# Illustrative/example markers — lines that are clearly not live directives.
_WEAK_SKIP_MARKERS = ("변명", "rationalization", "anti-pattern", "예시", "example")


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
            if ctx.ignore.declares(name):
                sev, note = Severity.FYI, " (intentional per .ssotyignore)"
            else:
                sev, note = Severity.WARNING, ""
            out.append(
                Finding(
                    sev,
                    "load_asymmetry",
                    "+".join(sorted(per_harness)),
                    name,
                    f"same rule loads differently per harness ({detail}) — shared file, " f"unequal guarantee{note}",
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
    locations: dict[str, list[str]] = defaultdict(list)  # list, not set: count repeats
    for surface in ctx.surfaces.values():
        for doc in surface.docs:
            for para in _paragraphs(doc.text):
                locations[para].append(f"{surface.harness}:{doc.name}")
    out: list[Finding] = []
    for para, where in sorted(locations.items()):
        if len(where) < 2:
            continue
        harnesses = {w.split(":", 1)[0] for w in where}
        # within-harness repetition (incl. the same block twice in one doc) = real
        # token rent loaded every turn; once-per-harness across harnesses = expected
        # SSOT sharing (e.g. symlinked to both), not rent.
        within = any(sum(1 for w in where if w.split(":", 1)[0] == h) >= 2 for h in harnesses)
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
                ", ".join(sorted(set(where))),
                f"identical {dup_tokens.tokens}-token block ({kind}, x{len(where)}) {note}",
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


def _match_token(tokens: tuple[str, ...], lowered: str) -> str | None:
    """Return the first token present as a WHOLE WORD in ``lowered``, else None.

    Word-boundary matching (not substring) so 'prod' does not match 'production'
    or 'reproduce', 'must' does not match 'mustard', 'secret' not 'secretary'.
    """
    for token in tokens:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return token
    return None


def _is_illustrative_line(stripped: str, lowered: str) -> bool:
    """A line that is a table row, blockquote, or example/anti-pattern marker."""
    if stripped.startswith("|") or stripped.startswith(">"):
        return True
    return any(marker in lowered for marker in _WEAK_SKIP_MARKERS)


def check_weak_directive(ctx: CheckContext) -> list[Finding]:
    """Flag (FYI) a weak modal hedging a hard requirement on the same line.

    Scans ONLY always-on docs — the actually enforced surface. Skips fenced code,
    table rows, blockquotes, and example/anti-rationalization lines. A line is flagged
    only when a weak modal (e.g. ``should``) co-occurs with a hard-requirement signal
    (e.g. ``never``, ``security``) on that line; standalone ``should`` is never flagged.
    Never blocking — conservative co-occurrence gating keeps false positives low.
    """
    out: list[Finding] = []
    for surface in ctx.surfaces.values():
        for doc in surface.docs:
            if doc.load_basis != ALWAYS_ON:
                continue
            body = _WEAK_FENCE_RE.sub("", doc.text)
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                lowered = stripped.lower()
                if _is_illustrative_line(stripped, lowered):
                    continue
                modal = _match_token(_WEAK_MODALS, lowered)
                if modal is None:
                    continue
                signal = _match_token(_HARD_SIGNALS, lowered)
                if signal is None:
                    continue
                out.append(
                    Finding(
                        Severity.FYI,
                        "weak_directive",
                        surface.harness,
                        str(doc.path),
                        f"weak modal '{modal}' hedges a hard-requirement signal '{signal}' "
                        f"on the same line — an enforced rule should state the requirement firmly",
                        signal,
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
    check_weak_directive,
)


def run_checks(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(ctx))
    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.FYI: 2}
    findings.sort(key=lambda f: (order[f.severity], f.check, f.harness, f.file))
    return findings

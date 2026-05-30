"""Cross-model rule divergence — pairwise comparison of two harness surfaces.

Deterministic, stdlib + existing imports only (no LLM, no network, no mutation).
The headline question ssoty answers: *do two models operate under the same
effective rules?* This module computes, for an ordered pair (A, B):

- only-in-A / only-in-B : rule names one harness loads that the other never sees;
- different-load        : a shared rule whose ``load_basis`` differs (e.g. always-on
                          in A vs skill-gated in B) — same file, unequal guarantee;
- broken cross-refs     : a doc in A references a rule that does NOT load in A but
                          DOES load in B (and the symmetric case) — A relies on a
                          pointer that only resolves inside B's context.

Every collection is a sorted tuple keyed on stable string fields, so the same
input yields identical bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ssoty.models import HarnessSurface, normalize_content
from ssoty.resolver import referenced_docs


@dataclass(frozen=True)
class LoadDivergence:
    """A rule name shared by both harnesses but with a different load basis."""

    name: str
    a_basis: str  # e.g. "always-on"
    b_basis: str  # e.g. "skill-gated"


@dataclass(frozen=True)
class BrokenCrossRef:
    """A doc references a rule that loads only on the OTHER side of the pair."""

    src_harness: str  # harness whose doc holds the reference
    src_doc: str  # basename of the referencing doc
    ref: str  # referenced basename
    present_in: str  # the OTHER harness where ref DOES load


@dataclass(frozen=True)
class ContentDivergence:
    """A rule name shared by both harnesses whose content differs across separate copies."""

    name: str
    a_path: str  # path of A's copy
    b_path: str  # path of B's copy


@dataclass(frozen=True)
class SurfaceDiff:
    a: str  # harness A name
    b: str  # harness B name
    only_in_a: tuple[str, ...]  # rule names in A, absent in B (sorted)
    only_in_b: tuple[str, ...]  # rule names in B, absent in A (sorted)
    shared: tuple[str, ...]  # rule names in both (sorted)
    different_load: tuple[LoadDivergence, ...]  # shared names whose basis differs (sorted by name)
    broken_cross_refs: tuple[BrokenCrossRef, ...]  # sorted by (src_harness, src_doc, ref)
    content_divergence: tuple[ContentDivergence, ...]  # shared names, divergent content (sorted by name)

    @property
    def coherent(self) -> bool:
        return not (
            self.only_in_a or self.only_in_b or self.different_load or self.broken_cross_refs or self.content_divergence
        )


def _broken_refs(src: HarnessSurface, other: HarnessSurface) -> list[BrokenCrossRef]:
    """Refs in ``src`` docs that miss in ``src`` but resolve in ``other``."""
    src_names = src.names
    other_names = other.names
    out: list[BrokenCrossRef] = []
    for doc in src.docs:
        for ref in referenced_docs(doc.text):
            if ref not in src_names and ref in other_names:
                out.append(
                    BrokenCrossRef(
                        src_harness=src.harness,
                        src_doc=doc.name,
                        ref=ref,
                        present_in=other.harness,
                    )
                )
    return out


def diff_pair(sa: HarnessSurface, sb: HarnessSurface) -> SurfaceDiff:
    a_names = sa.names
    b_names = sb.names
    only_in_a = tuple(sorted(a_names - b_names))
    only_in_b = tuple(sorted(b_names - a_names))
    shared = tuple(sorted(a_names & b_names))

    different: list[LoadDivergence] = []
    for name in shared:
        da = sa.by_name(name)
        db = sb.by_name(name)
        if da is not None and db is not None and da.load_basis != db.load_basis:
            different.append(LoadDivergence(name=name, a_basis=da.load_basis, b_basis=db.load_basis))

    broken = _broken_refs(sa, sb) + _broken_refs(sb, sa)
    broken.sort(key=lambda r: (r.src_harness, r.src_doc, r.ref))

    divergent: list[ContentDivergence] = []
    for name in shared:
        da = sa.by_name(name)
        db = sb.by_name(name)
        if da is None or db is None or da.broken or db.broken:
            continue
        if os.path.realpath(str(da.path)) == os.path.realpath(str(db.path)):
            # same canonical file symlinked into both — identical by construction.
            continue
        if normalize_content(da.text) != normalize_content(db.text):
            divergent.append(ContentDivergence(name=name, a_path=str(da.path), b_path=str(db.path)))

    return SurfaceDiff(
        a=sa.harness,
        b=sb.harness,
        only_in_a=only_in_a,
        only_in_b=only_in_b,
        shared=shared,
        different_load=tuple(different),
        broken_cross_refs=tuple(broken),
        content_divergence=tuple(divergent),
    )

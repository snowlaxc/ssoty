"""Context Tax — the per-turn token cost of a harness's rule surface.

HONESTY RULE (see plan Principle 3): ``always-on`` tokens (actual, injected every
turn) and ``skill-gated`` tokens (potential, loaded only when a skill triggers)
are reported SEPARATELY and never summed or ranked across harnesses. Within one
harness, ``always_on + skill_gated`` is the upper bound if every skill fires.
"""

from __future__ import annotations

from dataclasses import dataclass

from ssoty.models import ALWAYS_ON, HarnessSurface
from ssoty.tokens import count_tokens


@dataclass(frozen=True)
class HarnessTax:
    harness: str
    always_on_tokens: int  # actual — injected every turn
    skill_gated_tokens: int  # potential — only when a skill triggers
    doc_count: int
    approx: bool

    @property
    def max_surface_tokens(self) -> int:
        """Within-harness upper bound (all skills fire). Never sum across harnesses."""
        return self.always_on_tokens + self.skill_gated_tokens


def compute_harness_tax(surface: HarnessSurface) -> HarnessTax:
    always_on = 0
    skill_gated = 0
    approx = False
    for doc in surface.docs:
        if doc.broken:
            continue
        tc = count_tokens(doc.text)
        approx = approx or tc.approx
        if doc.load_basis == ALWAYS_ON:
            always_on += tc.tokens
        else:
            skill_gated += tc.tokens
    return HarnessTax(
        harness=surface.harness,
        always_on_tokens=always_on,
        skill_gated_tokens=skill_gated,
        doc_count=len(surface.docs),
        approx=approx,
    )


def compute_context_tax(surfaces: dict[str, HarnessSurface]) -> dict[str, HarnessTax]:
    return {h: compute_harness_tax(s) for h, s in surfaces.items()}

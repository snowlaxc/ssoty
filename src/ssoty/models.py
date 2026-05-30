"""Core data model. Plain dataclasses, no behavior beyond simple helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Load semantics — how a rule reaches the agent's effective context.
ALWAYS_ON = "always-on"  # injected every turn (actual surface)
SKILL_GATED = "skill-gated"  # loaded only when a skill triggers (potential surface)
CONDITIONAL = "conditional"  # loaded by rule frontmatter/globs (e.g. Cursor .mdc not alwaysApply)

# Per-harness entrypoint filenames. Every harness owns its OWN copy by design, so
# such a file can never be "shared" and a pointer to it names the canonical entrypoint
# concept, not a broken file. Defined here (not in checks/resolver) so both the resolver
# and the checks module can import it without a circular dependency (resolver is imported
# BY checks, never the reverse).
ENTRYPOINTS = frozenset(
    {
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
        "copilot-instructions.md",
        ".windsurfrules",
        ".cursorrules",
        ".clinerules",
    }
)


class Severity(str, Enum):
    """Review severity labels (see team review conventions)."""

    CRITICAL = "Critical"
    WARNING = "Warning"
    FYI = "FYI"


@dataclass(frozen=True)
class RuleDoc:
    """One rule document within a harness's effective surface."""

    harness: str
    name: str  # basename, e.g. "team-defaults.md"
    path: Path
    load_basis: str  # ALWAYS_ON | SKILL_GATED
    text: str = ""
    is_symlink: bool = False
    symlink_target: str | None = None
    broken: bool = False  # symlink whose target does not resolve


@dataclass
class HarnessSurface:
    """The set of rule docs a harness loads, with their load semantics."""

    harness: str
    docs: list[RuleDoc] = field(default_factory=list)

    @property
    def names(self) -> set[str]:
        return {d.name for d in self.docs}

    def by_name(self, name: str) -> RuleDoc | None:
        for d in self.docs:
            if d.name == name:
                return d
        return None


@dataclass(frozen=True)
class Finding:
    """A single coherence finding."""

    severity: Severity
    check: str
    harness: str
    file: str
    message: str
    rule_id: str = ""


@dataclass
class AuditResult:
    surfaces: dict[str, HarnessSurface] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def has_blocking(self) -> bool:
        """True if any Critical finding exists (used for --ci exit code)."""
        return any(f.severity is Severity.CRITICAL for f in self.findings)

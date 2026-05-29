"""``.ssotyignore`` — declares intentional non-sharing so it isn't flagged as a bug.

Format: one rule-doc basename per line. ``#`` comments and blank lines ignored.
A declared name means "this rule is intentionally not shared across harnesses";
a cross-reference to it is downgraded from Critical to FYI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SsotyIgnore:
    names: set[str] = field(default_factory=set)

    def declares(self, name: str) -> bool:
        return name in self.names

    @classmethod
    def load(cls, root: Path) -> SsotyIgnore:
        path = root / ".ssotyignore"
        if not path.is_file():
            return cls()
        names: set[str] = set()
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                names.add(line)
        return cls(names=names)

"""Persisted ssoty configuration — the canonical *home* directory.

The ssoty *home* is the canonical SSOT directory ssoty owns: ``adopt`` consolidates
scattered rule copies into it (``home/common/<name>``, ``home/<harness>/<name>``) and
``sync`` distributes from it back into each harness. The home is **configurable** and
**persisted** so a user sets it once (or accepts the ``~/.ssoty`` default) and every later
command reuses it.

The pointer to the home must live *outside* the home (so the home itself can move), at an
XDG-standard path: ``${XDG_CONFIG_HOME:-~/.config}/ssoty/config.json``.

Resolution precedence (highest first):
  1. an explicit ``--home`` flag (and that choice is then persisted),
  2. the ``"home"`` key in ``config.json``,
  3. the default ``~/.ssoty``.

stdlib only (``os``/``json``/``pathlib``) — matches ssoty's dependency-free engine contract.
No directory is created on read; the home dir is created lazily by ``adopt --apply``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_HOME_NAME = ".ssoty"
_CONFIG_SUBDIR = "ssoty"
_CONFIG_FILE = "config.json"
_HOME_KEY = "home"


def default_home() -> Path:
    """The default canonical home: ``~/.ssoty`` (absolute, normalized)."""
    return Path(os.path.normpath(str(Path.home() / DEFAULT_HOME_NAME)))


def config_path() -> Path:
    """Location of the persisted config: ``${XDG_CONFIG_HOME:-~/.config}/ssoty/config.json``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / _CONFIG_SUBDIR / _CONFIG_FILE


def _load_config() -> dict:
    """Parse ``config.json`` with stdlib json; return {} if absent or unreadable.

    A corrupt config never crashes a command — it degrades to the default home. This is a
    read-only convenience file, not a manifest, so leniency is intentional.
    """
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_home(value: str) -> Path:
    """Expand ``~`` and normalize to an absolute path (no filesystem access)."""
    expanded = os.path.expanduser(value)
    return Path(os.path.normpath(os.path.abspath(expanded)))


def resolve_home(explicit: str | None) -> Path:
    """Resolve the canonical home using precedence: explicit > config file > default.

    Pure resolution — reads the config file but writes nothing and touches no home dir.
    """
    if explicit:
        return _normalize_home(explicit)
    stored = _load_config().get(_HOME_KEY)
    if isinstance(stored, str) and stored:
        return _normalize_home(stored)
    return default_home()


def save_home(home: Path) -> Path:
    """Persist ``home`` to ``config.json`` (creating the config dir). Returns the config path.

    Only the explicit-``--home`` path calls this; ordinary resolution never writes, so a
    plain ``ssoty audit`` leaves no config behind.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_config()
    data[_HOME_KEY] = str(home)
    # Atomic write (tmp + os.replace) to match the engine's deterministic/idempotent contract:
    # a crash mid-write never leaves a half-written config. OSError (e.g. config.json exists as
    # a directory, or a read-only dir) propagates to the caller, which degrades gracefully.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path

"""Interactive ``ssoty adopt`` TUI — a thin Textual front-end over the deterministic engine.

This module is the *only* place ``textual`` is imported, and the import is wrapped in a
``try/except ImportError`` guard so that ``audit``/``diff``/``sync``/``resolve``/``fix``/
``metrics`` (and ``adopt`` itself in its text path) never pay the import cost and keep working
if textual is somehow unavailable. ``cli.cmd_adopt`` imports :class:`AdoptTUI` lazily, only when
it has already decided to launch the interactive path.

Design contract (hard invariant): the TUI performs **zero** filesystem mutation of its own. It
collects per-rule user overrides, rebuilds an :class:`~ssoty.adopt.AdoptPlan` from the original
plan, and calls the EXACT same engine functions ``cmd_adopt`` calls —
:func:`~ssoty.adopt.adopt_needs_force` and :func:`~ssoty.adopt.apply_adopt_plan`. No move,
backup, or symlink logic is reimplemented here.
"""

from __future__ import annotations

from pathlib import Path

from ssoty.adopt import (
    ALREADY_SHARED,
    COMMON_CANDIDATE,
    DIVERGENT,
    ENTRYPOINT,
    HARNESS_SPECIFIC,
    AdoptPlan,
    ProposedRule,
    adopt_needs_force,
    apply_adopt_plan,
    render_adopt_apply,
)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import Screen
    from textual.widgets import Footer, ListItem, ListView, SelectionList, Static
    from textual.widgets.selection_list import Selection

    _TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when textual is absent
    _TEXTUAL_AVAILABLE = False


# Override sentinel for "user chose COMMON". A harness override is a ``list[str]`` of harness
# names. A missing key means "use the engine's proposed kind unchanged".
_OVERRIDE_COMMON = "common"

# Kinds that can be re-bucketed by the user (movable, not divergent/entrypoint/already-shared).
_MOVABLE = (COMMON_CANDIDATE, HARNESS_SPECIFIC)


def _kind_badge(kind: str) -> str:
    return {
        COMMON_CANDIDATE: "[COMMON]",
        HARNESS_SPECIFIC: "[HARNESS]",
        DIVERGENT: "[DIVERGENT]",
        ALREADY_SHARED: "[ALREADY]",
        ENTRYPOINT: "[ENTRYPOINT]",
    }.get(kind, f"[{kind}]")


def _rule_harnesses(rule: ProposedRule) -> list[str]:
    """Deterministic, de-duplicated harness names contributing to a rule."""
    return sorted({v.harness for v in rule.variants})


def _representative_path_for_common(rule: ProposedRule) -> Path:
    """Pick a deterministic representative source when the user forces COMMON.

    Mirrors the engine's own tie-break (lexicographic by path) so the bytes that land in
    ``common/<name>`` are reproducible regardless of how the user clicked.
    """
    return sorted(rule.variants, key=lambda v: str(v.path))[0].path


def _variant_path_for_harness(rule: ProposedRule, harness: str) -> Path | None:
    for v in sorted(rule.variants, key=lambda v: str(v.path)):
        if v.harness == harness:
            return v.path
    return None


def build_modified_rules(plan: AdoptPlan, overrides: dict[str, object]) -> tuple[ProposedRule, ...]:
    """Translate user overrides into a new ``rules`` tuple (pure — no filesystem access).

    Exposed at module scope so headless tests can assert the choice->engine mapping without
    constructing an App. Rules without an override are returned unchanged. DIVERGENT and
    ENTRYPOINT rules are NEVER re-bucketed (a COMMON override on them is ignored).
    """
    out: list[ProposedRule] = []
    for rule in plan.rules:
        override = overrides.get(rule.name)
        if override is None or rule.kind not in _MOVABLE:
            out.append(rule)
            continue
        if override == _OVERRIDE_COMMON:
            out.append(
                ProposedRule(
                    name=rule.name,
                    kind=COMMON_CANDIDATE,
                    canonical_rel=f"common/{rule.name}",
                    variants=rule.variants,
                    source_path=_representative_path_for_common(rule),
                )
            )
            continue
        if isinstance(override, list) and len(override) == 1:
            harness = override[0]
            src = _variant_path_for_harness(rule, harness)
            if src is None:
                # The picked harness has no variant for this rule — ambiguous; keep engine's.
                out.append(rule)
                continue
            out.append(
                ProposedRule(
                    name=rule.name,
                    kind=HARNESS_SPECIFIC,
                    canonical_rel=f"{harness}/{rule.name}",
                    variants=rule.variants,
                    source_path=src,
                )
            )
            continue
        # >1 harness without COMMON is ambiguous (no single destination) — keep engine's
        # classification rather than guess. The mutual-exclusion UI makes this rare.
        out.append(rule)
    return out


def build_modified_plan(plan: AdoptPlan, overrides: dict[str, object]) -> AdoptPlan:
    """Rebuild an :class:`AdoptPlan` with user overrides applied (same root/canonical config)."""
    return AdoptPlan(
        root=plan.root,
        canonical_dir=plan.canonical_dir,
        canonical_rel=plan.canonical_rel,
        symlink_originals=plan.symlink_originals,
        rules=build_modified_rules(plan, overrides),
    )


if _TEXTUAL_AVAILABLE:

    class ResultsScreen(Screen):
        """Fullscreen result text shown after a successful apply."""

        BINDINGS = [Binding("q", "dismiss", "Close")]

        def __init__(self, text: str) -> None:
            super().__init__()
            self._text = text

        def compose(self) -> ComposeResult:
            yield Static(self._text, id="results", expand=True)
            yield Footer()

        def action_dismiss(self) -> None:
            self.app.exit(0)

    class AdoptTUI(App):
        """Two-pane classifier for an :class:`AdoptPlan`. Apply delegates to the engine."""

        TITLE = "ssoty adopt"
        SUB_TITLE = "classify rules — Enter=focus chooser, a=apply, q=quit"

        CSS = """
        #body { height: 1fr; }
        #rule-list-pane { width: 35%; border-right: solid $accent; }
        #detail-pane { width: 65%; }
        #preview { height: 1fr; overflow: auto; padding: 1; }
        #classify-area { height: auto; max-height: 12; border-top: solid $accent; padding: 1; }
        #results { padding: 1; }
        """

        BINDINGS = [
            Binding("a", "apply", "Apply"),
            Binding("q", "quit", "Quit"),
        ]

        def __init__(self, plan: AdoptPlan, force: bool = False) -> None:
            super().__init__()
            self._plan = plan
            self._force = force
            self._rules: tuple[ProposedRule, ...] = plan.rules
            # rule.name -> "common" | list[harness] ; missing == engine default.
            self._overrides: dict[str, object] = {}
            self._selected_index: int = 0
            self._apply_done: bool = False
            # Plain-string mirrors of the right pane, for stable headless introspection
            # (Textual's Static stores content in a name-mangled private attr).
            self._preview_str: str = ""
            self._chooser_message: str = ""
            # Snapshot of the current chooser's selection, to detect what was JUST toggled.
            self._prev_selected: list[str] = []

        # -- composition --------------------------------------------------- #

        def compose(self) -> ComposeResult:
            with Horizontal(id="body"):
                with Vertical(id="rule-list-pane"):
                    yield ListView(id="rule-list")
                with Vertical(id="detail-pane"):
                    yield Static(id="preview", expand=True)
                    yield Vertical(id="classify-area")
            yield Footer()

        def on_mount(self) -> None:
            lv = self.query_one("#rule-list", ListView)
            for rule in self._rules:
                lv.append(ListItem(Static(f"{_kind_badge(rule.kind)} {rule.name}")))
            if self._rules:
                self._selected_index = 0
                self._refresh_detail()

        # -- navigation ---------------------------------------------------- #

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            if event.list_view.id != "rule-list":
                return
            idx = event.list_view.index
            if idx is None:
                return
            self._selected_index = idx
            self._refresh_detail()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            # Enter on a list item: move focus into the chooser so the user can toggle it.
            if event.list_view.id != "rule-list":
                return
            try:
                self.query_one("#classify-area SelectionList", SelectionList).focus()
            except Exception:
                pass

        # -- detail / chooser refresh -------------------------------------- #

        @property
        def _current_rule(self) -> ProposedRule | None:
            if not self._rules:
                return None
            idx = max(0, min(self._selected_index, len(self._rules) - 1))
            return self._rules[idx]

        def _refresh_detail(self) -> None:
            rule = self._current_rule
            if rule is None:
                return
            self._preview_str = self._preview_text(rule)
            self.query_one("#preview", Static).update(self._preview_str)
            area = self.query_one("#classify-area", Vertical)
            area.remove_children()
            self._chooser_message = ""
            self._prev_selected = []
            self._mount_chooser(area, rule)

        def _preview_text(self, rule: ProposedRule) -> str:
            lines = [f"{rule.name}  {_kind_badge(rule.kind)}", ""]
            if rule.kind == DIVERGENT:
                for v in rule.variants:
                    lines.append(f"── {v.harness}  [{v.fingerprint}] ──")
                    body = v.path.read_text(encoding="utf-8", errors="replace") if v.path.exists() else ""
                    for ln in body.splitlines()[:30]:
                        lines.append(ln)
                    lines.append("")
            else:
                rep = rule.source_path or (rule.variants[0].path if rule.variants else None)
                body = ""
                if rep is not None and Path(rep).exists():
                    body = Path(rep).read_text(encoding="utf-8", errors="replace")
                lines.extend(body.splitlines())
            return "\n".join(lines)

        def _mount_chooser(self, area: Vertical, rule: ProposedRule) -> None:
            if rule.kind == DIVERGENT:
                self._chooser_message = (
                    "DIVERGENT — cannot auto-merge. Variants shown in preview. " "Resolve manually then re-run."
                )
                area.mount(Static(f"[yellow]{self._chooser_message}[/yellow]"))
                return
            if rule.kind == ALREADY_SHARED:
                self._chooser_message = "Already shared (single inode) — no action needed."
                area.mount(Static(self._chooser_message))
                return
            if rule.kind == ENTRYPOINT:
                self._chooser_message = "Entrypoint — left in place by design."
                area.mount(Static(self._chooser_message))
                return

            # Movable (COMMON_CANDIDATE / HARNESS_SPECIFIC): COMMON toggle + one per harness.
            override = self._overrides.get(rule.name)
            common_selected = override == _OVERRIDE_COMMON
            harness_overrides = set(override) if isinstance(override, list) else set()
            # No explicit override yet -> seed the chooser from the engine's proposed kind.
            if override is None:
                if rule.kind == COMMON_CANDIDATE:
                    common_selected = True
                else:  # HARNESS_SPECIFIC
                    harness_overrides = {v.harness for v in rule.variants}

            options = [Selection("→ common/ (all harnesses)", _OVERRIDE_COMMON, common_selected)]
            for h in _rule_harnesses(rule):
                options.append(Selection(f"→ {h}/", h, h in harness_overrides))
            # Seed the previous-selection snapshot so the FIRST toggle diffs against the
            # initial (seeded-from-engine) state, not an empty set.
            self._prev_selected = [_OVERRIDE_COMMON] if common_selected else list(harness_overrides)
            area.mount(SelectionList[str](*options))

        # -- mutual exclusion ---------------------------------------------- #

        def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
            rule = self._current_rule
            if rule is None or rule.kind not in _MOVABLE:
                return
            sl = event.selection_list
            selected = list(sl.selected)
            prev = set(self._prev_selected)
            now = set(selected)
            just_added = now - prev
            # Enforce mutual exclusion using WHAT WAS JUST ADDED (most-recent intent wins):
            if _OVERRIDE_COMMON in just_added and (now - {_OVERRIDE_COMMON}):
                # User just picked COMMON -> clear every per-harness pick (common wins).
                for h in list(now - {_OVERRIDE_COMMON}):
                    sl.deselect(h)
            elif _OVERRIDE_COMMON in now and (just_added - {_OVERRIDE_COMMON}):
                # User just picked a harness while COMMON was on -> drop COMMON (harness wins).
                sl.deselect(_OVERRIDE_COMMON)
            selected = list(sl.selected)
            self._prev_selected = list(selected)
            if _OVERRIDE_COMMON in selected:
                self._overrides[rule.name] = _OVERRIDE_COMMON
            else:
                self._overrides[rule.name] = [h for h in selected]

        # -- apply --------------------------------------------------------- #

        def _build_modified_plan(self) -> AdoptPlan:
            return build_modified_plan(self._plan, self._overrides)

        async def action_apply(self) -> None:
            modified = self._build_modified_plan()
            if adopt_needs_force(modified) and not self._force:
                self.notify(
                    "Destination exists with differing content — re-run with --force",
                    severity="error",
                )
                return
            results, backup_dir = apply_adopt_plan(modified)
            self._apply_done = True
            await self.push_screen(ResultsScreen(render_adopt_apply(backup_dir, results)))

else:  # pragma: no cover - only when textual is missing

    class AdoptTUI:  # type: ignore[no-redef]
        """Placeholder so ``from ssoty.tui import AdoptTUI`` still imports without textual.

        Instantiating it is an error: the CLI's TTY gate falls back to text when the real
        import guard reports textual is unavailable, so this is never constructed in practice.
        """

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ImportError("textual is not installed; the adopt TUI is unavailable")

"""Discoverable, responsive controls for the optional Omapath panels."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..compat import set_accessible_label
from ..panel_layout import (
    PANEL_DEFINITIONS,
    PanelControlPresentation,
    PanelDefinition,
    PanelId,
    panel_control_presentation,
)
from .terminal_panel import TerminalPanel


class PanelControlsController:
    """Build panel affordances while leaving panel ownership with the host.

    The controller deliberately knows only how to present controls.  It sends
    commands to the host's ActionRegistry and asks the host for the effective
    visible state after each command, so settings and actual responsive
    presentation cannot silently drift apart.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.widget: Gtk.Box | None = None
        self.expanded_box: Gtk.Box | None = None
        self.collapsed_button: Gtk.MenuButton | None = None
        self.popover: Gtk.Popover | None = None
        self.menu_checks: dict[PanelId, Gtk.CheckButton] = {}
        self._syncing = False

    def build(self) -> Gtk.Box:
        """Create one compact Panels affordance once."""

        if self.widget is not None:
            return self.widget

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        shell.add_css_class("panel-controls-shell")
        shell.set_valign(Gtk.Align.CENTER)
        shell.set_hexpand(False)

        # Keep the attribute for compatibility with callers that inspect the
        # responsive surface, but do not build four permanent panel buttons.
        self.expanded_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.expanded_box.add_css_class("panel-controls-expanded")
        self.expanded_box.set_visible(False)

        collapsed = Gtk.MenuButton()
        collapsed.add_css_class("toolbar-button")
        collapsed.add_css_class("panel-menu-button")
        collapsed.set_tooltip_text("Panels")
        set_accessible_label(collapsed, "Panels menu")
        icon = Gtk.Image.new_from_icon_name("view-sidebar-symbolic")
        icon.set_pixel_size(18)
        collapsed.set_child(icon)
        collapsed_popover = self._build_collapsed_popover()
        collapsed.set_popover(collapsed_popover)
        shell.append(collapsed)
        self.collapsed_button = collapsed
        self.widget = shell
        self.sync()
        return shell

    def _build_collapsed_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.add_css_class("panel-controls-popover")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("simple-context-menu")
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        heading = Gtk.Label(label="Panels")
        heading.add_css_class("sidebar-section-label")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        for definition in PANEL_DEFINITIONS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("panel-menu-row")
            if definition.panel_id is PanelId.TERMINAL and not self._terminal_available():
                external = Gtk.Button(label="Open Terminal Here")
                external.add_css_class("context-action")
                external.set_hexpand(True)
                external.set_halign(Gtk.Align.FILL)
                external.set_tooltip_text(self._tooltip(definition, False))
                set_accessible_label(external, f"Open Terminal Here ({definition.shortcut})")
                external.connect("clicked", self._on_external_terminal_clicked, definition)
                row.append(external)
            else:
                check = Gtk.CheckButton(label=definition.label)
                check.set_hexpand(True)
                check.set_halign(Gtk.Align.FILL)
                check.set_tooltip_text(self._tooltip(definition, False))
                set_accessible_label(
                    check,
                    f"{definition.label} panel, show or hide ({definition.shortcut})",
                )
                check.connect("toggled", self._on_menu_toggled, definition)
                row.append(check)
                self.menu_checks[definition.panel_id] = check
            shortcut = Gtk.Label(label=definition.shortcut)
            shortcut.add_css_class("panel-shortcut")
            shortcut.set_halign(Gtk.Align.END)
            row.append(shortcut)
            box.append(row)

        popover.set_child(box)
        self.popover = popover
        return popover

    def _on_menu_toggled(self, _button: Gtk.CheckButton, definition: PanelDefinition) -> None:
        if self._syncing:
            return
        self._execute(definition)

    def _on_external_terminal_clicked(
        self,
        _button: Gtk.Button,
        definition: PanelDefinition,
    ) -> None:
        if self.popover is not None:
            self.popover.popdown()
        self._execute(definition)

    def _execute(self, definition: PanelDefinition) -> None:
        try:
            self.host._execute_action(definition.action_id)
        finally:
            # The action owns the real panel mutation; this only reflects its
            # result back into both surfaces, including external Esc/settings
            # changes that happen while a popover is open.
            self.sync()

    def sync(self) -> None:
        """Synchronize visibility, active states, tooltips and breakpoints."""

        if self.widget is None:
            return
        mode = getattr(self.host, "responsive_mode", None)
        presentation = panel_control_presentation(mode)
        if self.expanded_box is not None:
            self.expanded_box.set_visible(presentation is PanelControlPresentation.EXPANDED)
        if self.collapsed_button is not None:
            self.collapsed_button.set_visible(presentation is PanelControlPresentation.COLLAPSED)

        active_any = False
        self._syncing = True
        try:
            for definition in PANEL_DEFINITIONS:
                active = self._panel_visible(definition.panel_id)
                active_any = active_any or active
                check = self.menu_checks.get(definition.panel_id)
                if check is not None:
                    check.set_active(active)
                    check.set_sensitive(self._action_enabled(definition.action_id))
                    check.set_tooltip_text(self._tooltip(definition, active))
        finally:
            self._syncing = False

        if self.collapsed_button is not None:
            if active_any:
                self.collapsed_button.add_css_class("panel-menu-active")
            else:
                self.collapsed_button.remove_css_class("panel-menu-active")

    def _panel_visible(self, panel_id: PanelId) -> bool:
        callback = getattr(self.host, "panel_control_visible", None)
        if callable(callback):
            try:
                return bool(callback(panel_id.value))
            except Exception:
                return False
        return False

    def _action_enabled(self, action_id: str) -> bool:
        actions = getattr(self.host, "actions", None)
        getter = getattr(actions, "get", None)
        action = getter(action_id) if callable(getter) else None
        if action is None:
            return True
        try:
            return bool(action.is_enabled())
        except Exception:
            return False

    def _terminal_available(self) -> bool:
        callback = getattr(self.host, "panel_terminal_available", None)
        if callable(callback):
            try:
                return bool(callback())
            except Exception:
                return False
        return TerminalPanel.available()

    def _tooltip(self, definition: PanelDefinition, active: bool) -> str:
        if definition.panel_id is PanelId.TERMINAL and not self._terminal_available():
            return f"Open Terminal Here ({definition.shortcut})"
        verb = "Hide" if active else "Show"
        return f"{verb} {definition.label} ({definition.shortcut})"


__all__ = ["PanelControlsController"]

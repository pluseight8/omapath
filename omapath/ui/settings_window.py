"""Compact, category-based Omapath settings window."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango

from ..actions import ActionRegistry, ShortcutConflict, normalize_shortcut, shortcut_from_key_event
from ..responsive import ResponsiveMode
from .terminal_panel import TerminalPanel


class SettingsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, settings: dict[str, Any], actions: ActionRegistry, on_apply: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(title="Omapath Settings", transient_for=parent, modal=True)
        self.set_default_size(820, 620)
        self.set_resizable(True)
        self.settings = dict(settings)
        self.actions = actions
        self.on_apply = on_apply
        self.controls: dict[str, Gtk.Widget] = {}
        self.shortcut_values: dict[str, str] = {
            action.action_id: action.shortcut for action in self.actions.all()
        }
        self.shortcut_buttons: dict[str, Gtk.Button] = {}
        self.shortcut_error: Gtk.Label | None = None
        self.settings_mode = ResponsiveMode.WIDE

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        shell.add_css_class("settings-window")
        selector = Gtk.ComboBoxText()
        selector.add_css_class("settings-section-selector")
        selector.set_hexpand(True)
        selector.set_margin_top(8)
        selector.set_margin_start(8)
        selector.set_margin_end(8)
        selector.set_visible(False)
        self.settings_selector = selector
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content.set_hexpand(True)
        content.set_vexpand(True)
        sidebar = Gtk.StackSidebar()
        stack = Gtk.Stack()
        stack.set_hexpand(True)
        stack.set_vexpand(True)
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        sidebar.set_stack(stack)
        self.settings_sidebar = sidebar
        self.settings_stack = stack
        selector.connect("changed", self._on_settings_selector_changed)
        stack.connect("notify::visible-child-name", self._on_settings_stack_changed)
        content.append(sidebar)
        content.append(stack)
        shell.append(selector)
        shell.append(content)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)
        footer.set_margin_top(8)
        footer.set_margin_bottom(8)
        footer.set_margin_start(8)
        footer.set_margin_end(8)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_args: self.close())
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("action-button")
        apply.connect("clicked", lambda *_args: self._apply())
        footer.append(cancel)
        footer.append(apply)
        shell.append(footer)
        self.set_child(shell)

        for name, title, builder in (
            ("general", "General", self._general_page),
            ("startup", "Startup", self._startup_page),
            ("view", "View", self._view_page),
            ("navigation", "Navigation", self._navigation_page),
            ("search", "Search", self._search_page),
            ("previews", "Previews", self._preview_page),
            ("confirmations", "Confirmations", self._confirmation_page),
            ("tabs", "Tabs", self._tabs_page),
            ("panels", "Panels", self._panels_page),
            ("services", "Services", self._services_page),
            ("shortcuts", "Shortcuts", self._shortcuts_page),
            ("trash", "Trash", self._trash_page),
            ("advanced", "Advanced", self._advanced_page),
        ):
            page = Gtk.ScrolledWindow()
            page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            page.set_hexpand(True)
            page.set_vexpand(True)
            page.set_child(builder())
            stack.add_titled(page, name, title)
            selector.append(name, title)
        selector.set_active_id("general")
        self.connect("notify::width", self._on_settings_geometry_changed)
        self.connect("notify::height", self._on_settings_geometry_changed)
        self._apply_settings_responsive_layout()

    def _on_settings_selector_changed(self, selector: Gtk.ComboBoxText) -> None:
        name = selector.get_active_id()
        if name and self.settings_stack.get_visible_child_name() != name:
            self.settings_stack.set_visible_child_name(name)

    def _on_settings_stack_changed(self, *_args: object) -> None:
        name = self.settings_stack.get_visible_child_name()
        if name and self.settings_selector.get_active_id() != name:
            self.settings_selector.set_active_id(name)

    def _on_settings_geometry_changed(self, *_args: object) -> None:
        self._apply_settings_responsive_layout()

    def _apply_settings_responsive_layout(self) -> None:
        width = self.get_width() or 820
        narrow = width < 700
        self.settings_mode = ResponsiveMode.NARROW if narrow else ResponsiveMode.WIDE
        self.settings_sidebar.set_visible(not narrow)
        self.settings_selector.set_visible(narrow)
        if narrow:
            self.add_css_class("responsive-settings-narrow")
        else:
            self.remove_css_class("responsive-settings-narrow")

    def _page(self, title: str) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_top(18)
        page.set_margin_bottom(18)
        page.set_margin_start(18)
        page.set_margin_end(18)
        heading = Gtk.Label(label=title.upper())
        heading.add_css_class("section-label")
        heading.set_halign(Gtk.Align.START)
        page.append(heading)
        return page

    def _switch(self, page: Gtk.Box, key: str, label: str, description: str = "") -> Gtk.Switch:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=label)
        title.set_halign(Gtk.Align.START)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(title)
        if description:
            hint = Gtk.Label(label=description)
            hint.add_css_class("item-count")
            hint.set_halign(Gtk.Align.START)
            hint.set_wrap(True)
            text.append(hint)
        switch = Gtk.Switch()
        switch.set_active(bool(self.settings.get(key, False)))
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_hexpand(False)
        row.append(text)
        row.append(switch)
        page.append(row)
        self.controls[key] = switch
        return switch

    def _combo(self, page: Gtk.Box, key: str, label: str, values: list[tuple[str, str]]) -> Gtk.ComboBoxText:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label=label)
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        combo = Gtk.ComboBoxText()
        for identifier, text in values:
            combo.append(identifier, text)
        current = str(self.settings.get(key, values[0][0]))
        combo.set_active_id(current if any(identifier == current for identifier, _text in values) else values[0][0])
        row.append(title)
        row.append(combo)
        page.append(row)
        self.controls[key] = combo
        return combo

    def _entry(self, page: Gtk.Box, key: str, label: str, placeholder: str = "") -> Gtk.Entry:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label=label)
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_size_request(0, -1)
        entry.set_text(str(self.settings.get(key, "") or ""))
        if placeholder:
            entry.set_placeholder_text(placeholder)
        row.append(title)
        row.append(entry)
        page.append(row)
        self.controls[key] = entry
        return entry

    def _spin(self, page: Gtk.Box, key: str, label: str, lower: int, upper: int, step: int = 1) -> Gtk.SpinButton:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label=label)
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        spin = Gtk.SpinButton.new_with_range(float(lower), float(upper), float(step))
        spin.set_value(float(self.settings.get(key, lower)))
        row.append(title)
        row.append(spin)
        page.append(row)
        self.controls[key] = spin
        return spin

    def _general_page(self) -> Gtk.Box:
        page = self._page("General")
        self._switch(page, "follow_omarchy_theme", "Follow Omarchy theme")
        self._switch(page, "show_tooltips", "Show tooltips")
        self._switch(page, "natural_sort", "Natural sorting")
        self._switch(page, "single_click_open", "Open items with single click")
        self._switch(page, "show_selection_marker", "Show selection marker")
        self._switch(page, "inline_rename", "Rename inline")
        return page

    def _startup_page(self) -> Gtk.Box:
        page = self._page("Startup")
        self._combo(page, "start_location", "Startup location", [("home", "Home"), ("last", "Last location"), ("custom", "Custom folder")])
        self._entry(page, "startup_location", "Custom folder", "Path or URI")
        self._switch(page, "remember_tabs", "Restore tabs and locations")
        self._switch(page, "split_view", "Start in split view")
        return page

    def _view_page(self) -> Gtk.Box:
        page = self._page("View")
        self._combo(page, "default_view", "Default view", [("grid", "Icons"), ("compact", "Compact"), ("details", "Details")])
        self._switch(page, "show_hidden", "Show hidden files")
        self._switch(page, "folders_first", "Folders first")
        self._switch(page, "show_groups", "Show in groups")
        self._combo(page, "group_criterion", "Group by", [("none", "None"), ("name", "Name"), ("type", "Type"), ("date", "Date"), ("size", "Size")])
        self._switch(page, "show_additional_info", "Show additional information")
        self._switch(page, "show_git_status", "Show Git status")
        self._spin(page, "icon_zoom", "Icon size", 32, 96, 4)
        return page

    def _navigation_page(self) -> Gtk.Box:
        page = self._page("Navigation")
        self._switch(page, "auto_refresh", "Live directory refresh")
        self._switch(page, "show_folders_panel", "Show folders panel")
        self._switch(page, "show_inspector", "Show information panel")
        return page

    def _search_page(self) -> Gtk.Box:
        page = self._page("Search")
        self._switch(page, "search_recursive", "Include subfolders")
        self._switch(page, "search_show_hidden", "Include hidden files")
        self._switch(page, "search_case_sensitive", "Case sensitive")
        self._switch(page, "search_contents", "Search inside text")
        return page

    def _preview_page(self) -> Gtk.Box:
        page = self._page("Previews")
        self._switch(page, "show_previews", "Show previews")
        self._switch(page, "image_preview", "Image previews")
        self._switch(page, "text_preview", "Text previews")
        self._switch(page, "media_preview", "Audio/video previews")
        return page

    def _confirmation_page(self) -> Gtk.Box:
        page = self._page("Confirmations")
        self._switch(page, "confirm_delete", "Confirm moving to Trash")
        self._switch(page, "confirm_permanent_delete", "Confirm permanent delete")
        self._combo(page, "conflict_behavior", "File conflicts", [("ask", "Ask every time"), ("skip", "Skip incoming"), ("rename", "Keep both"), ("replace", "Replace existing")])
        return page

    def _tabs_page(self) -> Gtk.Box:
        page = self._page("Tabs")
        self._switch(page, "remember_tabs", "Restore tab session")
        self._switch(page, "new_tab_inherit_view", "New tabs inherit view settings")
        return page

    def _panels_page(self) -> Gtk.Box:
        page = self._page("Panels")
        self._switch(page, "show_sidebar", "Places sidebar")
        self._switch(page, "show_folders_panel", "Folders panel")
        self._switch(page, "show_inspector", "Information panel")
        self._spin(page, "sidebar_width", "Sidebar width", 160, 320, 10)
        self._spin(page, "info_panel_width", "Information panel width", 220, 440, 10)
        if TerminalPanel.available():
            self._switch(page, "show_terminal_panel", "Embedded terminal panel")
            self._spin(page, "terminal_panel_height", "Terminal height", 120, 480, 10)
        else:
            hint = Gtk.Label(label="Embedded terminal unavailable (VTE is not installed). F4 opens an external terminal.")
            hint.set_halign(Gtk.Align.START)
            hint.set_wrap(True)
            hint.add_css_class("item-count")
            page.append(hint)
        self._switch(page, "show_status_bar", "Status bar")
        return page

    def _services_page(self) -> Gtk.Box:
        page = self._page("Services")
        self._switch(page, "services_enabled", "Enable Omapath services")
        hint = Gtk.Label(label="User services: ~/.local/share/omapath/services")
        hint.set_halign(Gtk.Align.START)
        hint.add_css_class("item-count")
        page.append(hint)
        return page

    def _shortcuts_page(self) -> Gtk.Box:
        page = self._page("Shortcuts")
        hint = Gtk.Label(label="Select a shortcut, then press a key combination. Changes apply when you click Apply.")
        hint.set_halign(Gtk.Align.START)
        hint.set_wrap(True)
        hint.add_css_class("item-count")
        page.append(hint)
        self.shortcut_error = Gtk.Label()
        self.shortcut_error.set_halign(Gtk.Align.START)
        self.shortcut_error.set_wrap(True)
        self.shortcut_error.add_css_class("filter-error")
        page.append(self.shortcut_error)
        reset_all = Gtk.Button(label="Reset All to Defaults")
        reset_all.set_halign(Gtk.Align.START)
        reset_all.connect("clicked", self._reset_all_shortcuts)
        page.append(reset_all)
        for action in self.actions.all():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_hexpand(True)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            label = Gtk.Label(label=action.label)
            label.set_halign(Gtk.Align.START)
            identifier = Gtk.Label(label=action.action_id)
            identifier.set_halign(Gtk.Align.START)
            identifier.add_css_class("item-count")
            text.append(label)
            text.append(identifier)
            text.set_hexpand(True)
            button = Gtk.Button(label=self._shortcut_label(action.action_id))
            button.set_tooltip_text("Press a shortcut while this control is focused")
            button.connect("clicked", self._focus_shortcut_button, action.action_id)
            controller = Gtk.EventControllerKey()
            controller.connect("key-pressed", self._on_shortcut_key_pressed, action.action_id, button)
            button.add_controller(controller)
            clear = Gtk.Button(label="Clear")
            clear.connect("clicked", self._clear_shortcut, action.action_id)
            reset = Gtk.Button(label="Default")
            reset.connect("clicked", self._reset_shortcut, action.action_id)
            row.append(text)
            row.append(button)
            row.append(clear)
            row.append(reset)
            page.append(row)
            self.shortcut_buttons[action.action_id] = button
        return page

    def _shortcut_label(self, action_id: str) -> str:
        return self.shortcut_values.get(action_id) or "Unassigned"

    def _focus_shortcut_button(self, _button: Gtk.Button, _action_id: str) -> None:
        _button.grab_focus()
        if self.shortcut_error is not None:
            self.shortcut_error.set_text("Press the new shortcut, or use Clear.")

    def _set_shortcut_error(self, message: str) -> None:
        if self.shortcut_error is not None:
            self.shortcut_error.set_text(message)

    def _refresh_shortcut_button(self, action_id: str) -> None:
        button = self.shortcut_buttons.get(action_id)
        if button is not None:
            button.set_label(self._shortcut_label(action_id))

    def _clear_shortcut(self, _button: Gtk.Button, action_id: str) -> None:
        self.shortcut_values[action_id] = ""
        self._refresh_shortcut_button(action_id)
        self._set_shortcut_error("")

    def _reset_shortcut(self, _button: Gtk.Button, action_id: str) -> None:
        action = self.actions.get(action_id)
        if action is None:
            return
        conflicts = self._shortcut_conflicts(action_id, action.default_shortcut)
        if conflicts:
            self._set_shortcut_error(
                f"{action.default_shortcut} is already assigned to {', '.join(conflicts)}",
            )
            return
        self.shortcut_values[action_id] = action.default_shortcut
        self._refresh_shortcut_button(action_id)
        self._set_shortcut_error("")

    def _shortcut_conflicts(self, action_id: str, shortcut: str) -> list[str]:
        normalized = normalize_shortcut(shortcut)
        if not normalized:
            return []
        return [
            other_id
            for other_id, other_shortcut in self.shortcut_values.items()
            if other_id != action_id and other_shortcut.casefold() == normalized.casefold()
        ]

    def _on_shortcut_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        action_id: str,
        _button: Gtk.Button,
    ) -> bool:
        key_name = Gdk.keyval_name(keyval)
        if not key_name:
            return True
        modifiers: list[str] = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifiers.append("Ctrl")
        if state & Gdk.ModifierType.ALT_MASK:
            modifiers.append("Alt")
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifiers.append("Shift")
        if state & Gdk.ModifierType.SUPER_MASK:
            modifiers.append("Super")
        try:
            shortcut = shortcut_from_key_event(key_name, modifiers)
        except ValueError as error:
            self._set_shortcut_error(str(error))
            return True
        conflicts = self._shortcut_conflicts(action_id, shortcut)
        if conflicts:
            self._confirm_shortcut_replacement(action_id, shortcut, conflicts)
        else:
            self._commit_shortcut(action_id, shortcut)
        return True

    def _commit_shortcut(self, action_id: str, shortcut: str, *, replace: bool = False) -> None:
        normalized = normalize_shortcut(shortcut)
        if replace:
            for conflict in self._shortcut_conflicts(action_id, normalized):
                self.shortcut_values[conflict] = ""
                self._refresh_shortcut_button(conflict)
        self.shortcut_values[action_id] = normalized
        self._refresh_shortcut_button(action_id)
        self._set_shortcut_error("")

    def _confirm_shortcut_replacement(self, action_id: str, shortcut: str, conflicts: list[str]) -> None:
        dialog = Gtk.Dialog()
        dialog.set_title("Shortcut conflict")
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        message = Gtk.Label(
            label=f"{shortcut} is already assigned to {', '.join(conflicts)}. Replace those bindings?",
        )
        message.set_wrap(True)
        message.set_halign(Gtk.Align.START)
        content.append(message)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Replace", Gtk.ResponseType.OK)
        dialog.connect(
            "response",
            lambda current, response: (
                self._commit_shortcut(action_id, shortcut, replace=True)
                if response == Gtk.ResponseType.OK
                else self._set_shortcut_error("Shortcut unchanged")
            ) or current.close(),
        )
        dialog.present()

    def _reset_all_shortcuts(self, _button: Gtk.Button) -> None:
        self.shortcut_values = {action.action_id: action.default_shortcut for action in self.actions.all()}
        for action_id in self.shortcut_buttons:
            self._refresh_shortcut_button(action_id)
        self._set_shortcut_error("")

    def _trash_page(self) -> Gtk.Box:
        page = self._page("Trash")
        self._switch(page, "confirm_empty_trash", "Confirm empty Trash")
        return page

    def _advanced_page(self) -> Gtk.Box:
        page = self._page("Advanced")
        self._switch(page, "animations", "Enable animations")
        self._switch(page, "external_clipboard", "Use external URI clipboard")
        return page

    def _apply(self) -> None:
        for key, control in self.controls.items():
            if isinstance(control, Gtk.Switch):
                self.settings[key] = control.get_active()
            elif isinstance(control, Gtk.ComboBoxText):
                active = control.get_active_id()
                if active is not None:
                    self.settings[key] = active
            elif isinstance(control, Gtk.Entry):
                self.settings[key] = control.get_text().strip()
            elif isinstance(control, Gtk.SpinButton):
                self.settings[key] = control.get_value_as_int()
        try:
            self.actions.apply_shortcuts(self.shortcut_values)
        except (ValueError, ShortcutConflict) as error:
            self._set_shortcut_error(str(error))
            return
        self.settings["shortcuts"] = self.actions.user_shortcuts()
        self.on_apply(self.settings)
        self.close()


__all__ = ["SettingsWindow"]

"""Topbar and view-options popover presentation."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Graphene, Gtk, Pango

from ..grouping import GroupCriterion
from ..sorting import SORT_KEYS, SORT_LABELS
from ..view_modes import ViewMode


class TopbarController:
    """Build topbar widgets and delegate all commands to the host."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.breadcrumb_popover: Gtk.Popover | None = None

    def build(self) -> Gtk.Box:
        host = self.host
        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        topbar.add_css_class("topbar")
        toolbar_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        toolbar_group.add_css_class("toolbar-group")
        topbar.append(toolbar_group)

        host.sidebar_toggle_button = self._icon_button(
            "sidebar-show-symbolic",
            "Show sidebar",
            host.toggle_sidebar_drawer,
        )
        # The responsive panel controller owns the discoverable sidebar/panel
        # affordances.  Keep this historical drawer button hidden so narrow
        # layouts do not expose two competing sidebar controls.
        host.sidebar_toggle_button.set_visible(False)
        toolbar_group.append(host.sidebar_toggle_button)
        host.back_button = self._icon_button("go-previous-symbolic", "Back", host.go_back)
        toolbar_group.append(host.back_button)
        host.forward_button = self._icon_button("go-next-symbolic", "Forward", host.go_forward)
        toolbar_group.append(host.forward_button)
        host.up_button = self._icon_button("go-up-symbolic", "Up", host.go_up)
        toolbar_group.append(host.up_button)

        host.breadcrumbs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        host.breadcrumbs.add_css_class("breadcrumbs")
        host.breadcrumbs.set_halign(Gtk.Align.FILL)
        host.breadcrumbs.set_hexpand(True)
        host.breadcrumbs.set_tooltip_text("Click empty path space to edit location")
        host.breadcrumbs.connect("notify::width", host._on_breadcrumb_width_changed)
        background_gesture = Gtk.GestureClick()
        background_gesture.set_button(1)
        background_gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        background_gesture.connect("pressed", host._on_breadcrumb_background_pressed)
        host.breadcrumbs.add_controller(background_gesture)
        host.location_stack = Gtk.Stack()
        host.location_stack.set_hexpand(True)
        host.location_stack.set_halign(Gtk.Align.FILL)
        host.location_stack.add_named(host.breadcrumbs, "breadcrumbs")
        host.location_entry = Gtk.Entry()
        host.location_entry.add_css_class("location-entry")
        host.location_entry.set_hexpand(True)
        host.location_entry.set_placeholder_text("Path or URI")
        host.location_entry.set_tooltip_text("Type a path or URI and press Enter")
        host.location_entry.connect("activate", host._on_location_entry_activate)
        host.location_entry.connect("changed", host._on_location_entry_changed)
        location_key = Gtk.EventControllerKey()
        location_key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        location_key.connect("key-pressed", host._on_location_entry_key_pressed)
        host.location_entry.add_controller(location_key)
        host.location_stack.add_named(host.location_entry, "location-entry")
        completion_popover = Gtk.Popover()
        completion_popover.add_css_class("location-completion-popover")
        completion_popover.set_position(Gtk.PositionType.BOTTOM)
        completion_popover.set_autohide(False)
        completion_list = Gtk.ListBox()
        completion_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        completion_list.set_hexpand(True)
        completion_list.connect("row-activated", host._on_location_completion_activated)
        completion_popover.set_child(completion_list)
        completion_popover.set_parent(host.location_entry)
        host.location_completion_popover = completion_popover
        host.location_completion_list = completion_list

        host.location_stack.add_named(host.search_controls.build_topbar_search(), "search")
        host.location_stack.add_named(host.search_controls.build_filter_bar(), "filter")
        host.location_stack.set_visible_child_name("breadcrumbs")
        topbar.append(host.location_stack)

        host.location_error_label = Gtk.Label()
        host.location_error_label.add_css_class("location-error")
        host.location_error_label.set_ellipsize(Pango.EllipsizeMode.END)
        host.location_error_label.set_max_width_chars(24)
        host.location_error_label.set_visible(False)
        topbar.append(host.location_error_label)

        host.search_button = self._icon_button("system-search-symbolic", "Search (Ctrl+F)", host.toggle_search_bar)
        topbar.append(host.search_button)

        topbar.append(host.panel_controls.build())

        host.status_label = Gtk.Label(label="Ready")
        host.status_label.add_css_class("item-count")
        host.status_label.set_halign(Gtk.Align.END)
        host.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        host.status_label.set_max_width_chars(18)
        host.status_label.set_tooltip_text("Folder item count")
        topbar.append(host.status_label)

        host.view_menu_button = Gtk.MenuButton()
        host.view_menu_button.add_css_class("toolbar-button")
        view_icon = Gtk.Image.new_from_icon_name("open-menu-symbolic")
        view_icon.set_pixel_size(18)
        host.view_menu_button.set_child(view_icon)
        host.view_menu_button.set_tooltip_text("View options")
        host.view_menu_button.set_popover(self.build_view_popover())
        topbar.append(host.view_menu_button)
        return topbar

    @staticmethod
    def _icon_button(icon_name: str, tooltip: str, callback: Any) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("toolbar-button")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        button.set_child(icon)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", callback)
        return button

    def on_breadcrumb_middle_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        _x: float,
        _y: float,
        location: str,
    ) -> None:
        if gesture.get_current_button() != 2:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.host.open_location_new_tab(location)

    def on_breadcrumb_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        x: float,
        y: float,
        location: str,
        source: Gtk.Widget,
    ) -> None:
        if gesture.get_current_button() != 3:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.add_css_class("simple-context-menu")
        menu.set_margin_top(6)
        menu.set_margin_bottom(6)
        menu.set_margin_start(6)
        menu.set_margin_end(6)

        self._append_breadcrumb_action(menu, "Open in New Tab", lambda: self.host.open_location_new_tab(location))
        self._append_breadcrumb_action(menu, "Copy Location", lambda: self.host.copy_breadcrumb_location(location))
        local_directory = self.host.is_local_directory(location)
        self._append_breadcrumb_action(
            menu,
            "Add to Places",
            lambda: self.host.add_breadcrumb_to_places(location),
            local_directory,
        )
        self._append_breadcrumb_action(
            menu,
            "Open Terminal Here",
            lambda: self.host.open_terminal_at(location),
            local_directory,
        )
        self._append_breadcrumb_action(menu, "Properties", lambda: self.host.show_location_properties(location))
        self._show_breadcrumb_popover(menu, source, x, y)

    def _append_breadcrumb_action(
        self,
        menu: Gtk.Box,
        label: str,
        action: Any,
        sensitive: bool = True,
    ) -> None:
        button = Gtk.Button(label=label)
        button.add_css_class("context-action")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.set_sensitive(sensitive)
        button.connect("clicked", lambda _button: (self.close_breadcrumb_context(), action()))
        menu.append(button)

    def _show_breadcrumb_popover(self, menu: Gtk.Box, source: Gtk.Widget, x: float, y: float) -> None:
        breadcrumbs = self.host.breadcrumbs
        if breadcrumbs is None:
            return
        if self.breadcrumb_popover is None:
            self.breadcrumb_popover = Gtk.Popover()
            self.breadcrumb_popover.add_css_class("simple-context-popover")
            self.breadcrumb_popover.set_parent(breadcrumbs)
            self.host.breadcrumb_popover = self.breadcrumb_popover
        self.breadcrumb_popover.set_child(menu)
        translated_x, translated_y = self._coordinates(source, breadcrumbs, x, y)
        rect = Gdk.Rectangle()
        rect.x = max(0, int(translated_x))
        rect.y = max(0, int(translated_y))
        rect.width = 1
        rect.height = 1
        self.breadcrumb_popover.set_pointing_to(rect)
        self.breadcrumb_popover.popup()

    @staticmethod
    def _coordinates(source: Gtk.Widget, target: Gtk.Widget, x: float, y: float) -> tuple[float, float]:
        try:
            point = Graphene.Point().init(float(x), float(y))
            success, translated = source.compute_point(target, point)
            if success and translated is not None:
                return max(0.0, float(translated.x)), max(0.0, float(translated.y))
        except Exception:
            pass
        return max(0.0, float(x)), max(0.0, float(y))

    def close_breadcrumb_context(self) -> None:
        if self.breadcrumb_popover is not None:
            self.breadcrumb_popover.popdown()

    def build_view_popover(self) -> Gtk.Popover:
        host = self.host
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("simple-context-menu")
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        view_label = Gtk.Label(label="View mode")
        view_label.add_css_class("sidebar-section-label")
        view_label.set_halign(Gtk.Align.START)
        box.append(view_label)
        for mode in (ViewMode.ICONS, ViewMode.COMPACT, ViewMode.DETAILS):
            shortcut = "Ctrl+1" if mode is ViewMode.ICONS else "Ctrl+2" if mode is ViewMode.COMPACT else "Ctrl+3"
            button = Gtk.Button(label=f"{mode.value.title()}  ({shortcut})")
            button.add_css_class("context-action")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, selected=mode: host.set_view_mode(selected))
            box.append(button)

        host.show_hidden_check = Gtk.CheckButton(label="Show Hidden Files")
        host.show_hidden_check.connect("toggled", host._on_show_hidden_toggled)
        box.append(host.show_hidden_check)
        sort_label = Gtk.Label(label="Sort by")
        sort_label.add_css_class("sidebar-section-label")
        sort_label.set_halign(Gtk.Align.START)
        box.append(sort_label)
        for key in SORT_KEYS:
            button = Gtk.Button(label=SORT_LABELS[key])
            button.add_css_class("context-action")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, selected=key: host.set_sort_key(selected))
            box.append(button)

        host.sort_direction_check = Gtk.CheckButton(label="Descending")
        host.sort_direction_check.connect("toggled", host._on_sort_direction_toggled)
        box.append(host.sort_direction_check)
        host.folders_first_check = Gtk.CheckButton(label="Folders First")
        host.folders_first_check.connect("toggled", host._on_folders_first_toggled)
        box.append(host.folders_first_check)
        host.previews_check = Gtk.CheckButton(label="Show Previews")
        host.previews_check.connect("toggled", host._on_previews_toggled)
        box.append(host.previews_check)
        host.groups_check = Gtk.CheckButton(label="Show in Groups")
        host.groups_check.connect("toggled", host._on_groups_toggled)
        box.append(host.groups_check)

        group_label = Gtk.Label(label="Group by")
        group_label.add_css_class("sidebar-section-label")
        group_label.set_halign(Gtk.Align.START)
        box.append(group_label)
        ungroup_button = Gtk.Button(label="Ungroup")
        ungroup_button.add_css_class("context-action")
        ungroup_button.set_halign(Gtk.Align.FILL)
        ungroup_button.connect("clicked", lambda _button: host.set_group_criterion(GroupCriterion.NONE))
        box.append(ungroup_button)
        for criterion in (GroupCriterion.NAME, GroupCriterion.TYPE, GroupCriterion.DATE, GroupCriterion.SIZE):
            button = Gtk.Button(label=criterion.value.title())
            button.add_css_class("context-action")
            button.connect("clicked", lambda _button, selected=criterion: host.set_group_criterion(selected))
            box.append(button)

        host.zoom_label = Gtk.Label()
        host.zoom_label.add_css_class("sidebar-section-label")
        host.zoom_label.set_halign(Gtk.Align.START)
        box.append(host.zoom_label)
        zoom_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for label, callback in (("−", host.zoom_out), ("Reset", host.zoom_reset), ("+", host.zoom_in)):
            button = Gtk.Button(label=label)
            button.add_css_class("context-action")
            button.connect("clicked", callback)
            zoom_buttons.append(button)
        box.append(zoom_buttons)
        settings_button = Gtk.Button(label="Settings")
        settings_button.add_css_class("context-action")
        settings_button.connect("clicked", host.show_settings)
        box.append(settings_button)
        current_folder_button = Gtk.Button(label="Set for Current Folder")
        current_folder_button.add_css_class("context-action")
        current_folder_button.connect("clicked", host.save_current_folder_view)
        box.append(current_folder_button)
        all_folders_button = Gtk.Button(label="Set as Default for All Folders")
        all_folders_button.add_css_class("context-action")
        all_folders_button.connect("clicked", host.save_all_folder_view)
        box.append(all_folders_button)
        popover.set_child(box)
        host._sync_view_menu()
        return popover


__all__ = ["TopbarController"]

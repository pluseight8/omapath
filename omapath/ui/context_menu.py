"""Context-menu presentation for the active GTK4 Omapath window.

The controller owns menu construction and one reusable popover.  It delegates
filesystem actions to its host, so the menu has no second operation/model
pipeline and does not grow another application-wide state store.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gtk

from ..archive import archive_kind
from ..capabilities import item_capabilities, location_capabilities
from ..services import discover_services
from ..vcs import GitAdapter


TRASH_URI = "trash:///"


class ContextMenuController:
    """Build context actions from the host's current selection/capabilities."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.popover: Gtk.Popover | None = None

    def append_menu_action(
        self,
        menu: Gtk.Box,
        label: str,
        action: Callable[[], None],
        sensitive: bool = True,
    ) -> None:
        button = Gtk.Button(label=label)
        button.add_css_class("context-action")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.set_sensitive(sensitive)
        button.connect("clicked", self._on_context_action, action)
        menu.append(button)

    @staticmethod
    def append_separator(menu: Gtk.Box) -> None:
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.add_css_class("context-separator")
        menu.append(separator)

    def _on_context_action(self, _button: Gtk.Button, action: Callable[[], None]) -> None:
        self.close()
        action()

    def show(self, row: Any | None, x: float, y: float) -> None:
        host = self.host
        file_view = host._active_file_view() if hasattr(host, "_active_file_view") else host.file_view
        if file_view is None:
            return
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.add_css_class("simple-context-menu")
        menu.set_margin_top(6)
        menu.set_margin_bottom(6)
        menu.set_margin_start(6)
        menu.set_margin_end(6)
        selected = host._selected_rows()
        if row is not None:
            if not selected:
                selected = [row]
            active_location = host._active_location() if hasattr(host, "_active_location") else host.current_location
            local_selection = bool(selected) and all(item.file.get_path() is not None for item in selected)
            item_states = [item_capabilities(item.file) for item in selected]
            can_rename = len(item_states) == 1 and item_states[0].can_rename
            can_copy = bool(item_states) and all(state.can_copy for state in item_states)
            can_move = bool(item_states) and all(state.can_move for state in item_states)
            can_trash = bool(item_states) and all(state.can_trash for state in item_states)
            if active_location == TRASH_URI:
                self.append_menu_action(menu, "Restore", host.restore_selected, bool(selected))
                self.append_menu_action(menu, "Delete Permanently", host.delete_permanently_selected, bool(selected))
                self.append_separator(menu)
                self.append_menu_action(menu, "Properties", host.show_properties, len(selected) == 1)
                self.append_menu_action(menu, "Edit Tags & Rating", host.edit_metadata_selected, len(selected) == 1 and local_selection)
            else:
                self.append_menu_action(menu, "Open", host.open_selected, len(selected) == 1)
                self.append_menu_action(menu, "Open With", host.open_with, len(selected) == 1)
                if host.search_active:
                    self.append_menu_action(menu, "Open Parent Folder", host.open_parent_selected, len(selected) == 1)
                if len(selected) == 2:
                    self.append_menu_action(menu, "Compare Selected", host.compare_selected, local_selection)
                if row.is_dir:
                    self.append_menu_action(menu, "Open in New Tab", host.open_selected_new_tab, len(selected) == 1)
                self.append_separator(menu)
                self.append_menu_action(menu, "Cut", host.cut_selected, can_move)
                self.append_menu_action(menu, "Copy", host.copy_selected, can_copy)
                self.append_menu_action(menu, "Copy Location", host.copy_location, bool(selected))
                self.append_separator(menu)
                self.append_menu_action(menu, "Rename", host.rename_selected, can_rename)
                self.append_menu_action(menu, "Duplicate Here", host.duplicate_selected, local_selection)
                self.append_menu_action(menu, "Create Link Here", host.create_link_selected, local_selection)
                self.append_menu_action(menu, "Compress Selection", host.compress_selected, local_selection)
                if len(selected) == 1 and local_selection and archive_kind(selected[0].file.get_path() or "") is not None:
                    self.append_menu_action(menu, "Extract Archive", host.extract_selected)
                if row.is_dir and local_selection and len(selected) == 1:
                    self.append_menu_action(menu, "Add to Places", host.add_selected_to_places)
                    if any(entry.location == row.file.get_path() for entry in host.places_store.pinned):
                        self.append_menu_action(menu, "Remove from Places", host.remove_selected_from_places)
                if local_selection:
                    starred = all(
                        (item.file.get_path() or item.location) in host.places_store.starred
                        for item in selected
                    )
                    self.append_menu_action(
                        menu,
                        "Remove from Starred" if starred else "Add to Starred",
                        host.remove_selected_from_starred if starred else host.add_selected_to_starred,
                    )
                self.append_separator(menu)
                self.append_menu_action(
                    menu,
                    "Move to Trash" if can_trash else "Delete Permanently",
                    host.move_to_trash if can_trash else host.delete_remote_selected,
                    can_trash or all(state.can_delete for state in item_states),
                )
                if getattr(host, "split_pane_state", None) is not None and getattr(host, "split_file_view", None) is not None:
                    self.append_separator(menu)
                    self.append_menu_action(menu, "Copy to Other Pane", host.copy_to_other_pane, local_selection)
                    self.append_menu_action(menu, "Move to Other Pane", host.move_to_other_pane, local_selection)
                    self.append_menu_action(menu, "Open in Other Pane", host.open_in_other_pane, len(selected) == 1 and row.is_dir)
                self.append_separator(menu)
                self.append_menu_action(menu, "Properties", host.show_properties, len(selected) == 1)
                services = discover_services() if bool(host.settings.get("services_enabled", True)) else []
                if services:
                    self.append_separator(menu)
                    for service in services:
                        self.append_menu_action(
                            menu,
                            service.name,
                            lambda selected_service=service: host.run_service_action(selected_service),
                            local_selection,
                        )
                directory = host._current_directory_path()
                if directory is not None and GitAdapter(directory).available:
                    self.append_separator(menu)
                    self.append_menu_action(menu, "Git Status", host.show_git_status)
                    self.append_menu_action(menu, "Git Diff", lambda: host.run_git_action("diff"))
                    self.append_menu_action(menu, "Git Log", lambda: host.run_git_action("log"))
                    self.append_menu_action(menu, "Git Stage", lambda: host.run_git_action("add"), local_selection)
                    self.append_menu_action(menu, "Git Unstage", lambda: host.run_git_action("unstage"), local_selection)
                    self.append_menu_action(menu, "Git Restore", host.restore_git_selected, local_selection)
        else:
            current_directory = host._current_directory_path()
            active_location = host._active_location() if hasattr(host, "_active_location") else host.current_location
            location_state = location_capabilities(active_location or "")
            can_paste = bool(host.clipboard_paths and location_state.can_paste)
            if active_location == TRASH_URI:
                self.append_menu_action(
                    menu,
                    "Empty Trash",
                    host.empty_trash,
                    bool(file_view.store.get_n_items()),
                )
                self.append_separator(menu)
            else:
                self.append_menu_action(menu, "New Folder", host.create_folder, location_state.can_create)
                self.append_menu_action(menu, "New File", host.create_file, location_state.can_create)
                self.append_separator(menu)
                self.append_menu_action(menu, "Paste", host.paste_clipboard, can_paste)
                self.append_separator(menu)
                self.append_menu_action(menu, "Open Terminal Here", host.open_terminal_here, location_state.can_terminal)
                self.append_separator(menu)
                if GitAdapter(current_directory or "").available:
                    self.append_menu_action(menu, "Git Status", host.show_git_status)
                    self.append_menu_action(menu, "Git Commit", host.commit_git)
                    self.append_separator(menu)
            self.append_menu_action(menu, "Refresh", host.refresh)
        if hasattr(host, "plugins"):
            context = {
                "row": row,
                "selection": tuple(selected),
                "location": host._active_location() if hasattr(host, "_active_location") else host.current_location,
                "directory": host._current_directory_path(),
            }
            plugin_items = host.plugins.context_menu_items(context)
            if plugin_items:
                self.append_separator(menu)
                for item in plugin_items:
                    self.append_menu_action(
                        menu,
                        item.label,
                        lambda selected_item=item: host.plugins.invoke_menu_item(selected_item),
                        item.enabled,
                    )
        self.show_popover(menu, x, y)

    def show_popover(self, child: Gtk.Widget, x: float, y: float) -> None:
        host = self.host
        file_view = host._active_file_view() if hasattr(host, "_active_file_view") else host.file_view
        if file_view is None:
            return
        # A context menu can outlive a realized list item while a trash,
        # rename, or monitor refresh replaces rows.  Anchor one reusable
        # popover to the window-owned root instead of the current view, which
        # may be detached by split/panel changes.
        parent = getattr(host, "root_widget", None)
        if parent is None:
            parent = file_view
        if self.popover is None:
            self.popover = Gtk.Popover()
            self.popover.add_css_class("simple-context-popover")
            self.popover.set_parent(parent)
            self.popover.connect("closed", self._on_popover_closed)
        self.popover.set_child(child)
        try:
            point = Graphene.Point().init(float(x), float(y))
            success, translated = file_view.compute_point(parent, point)
            if success and translated is not None:
                x, y = float(translated.x), float(translated.y)
        except (AttributeError, TypeError, ValueError):
            pass
        rect = Gdk.Rectangle()
        rect.x = max(0, int(x))
        rect.y = max(0, int(y))
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        host.context_popover = self.popover
        self.popover.popup()

    def _on_popover_closed(self, popover: Gtk.Popover) -> None:
        # Keep one anchored popover for the lifetime of the file view.  GTK
        # owns the parent/child relationship; no manual unparent lifecycle is
        # needed when a menu closes.
        if self.popover is popover:
            self.host.context_popover = popover

    def close(self) -> None:
        if self.popover is not None:
            self.popover.popdown()


__all__ = ["ContextMenuController"]

"""Sidebar presentation for Places, recent locations and devices."""

from __future__ import annotations

import os
from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango

from ..dnd import locations_from_uri_list
from .dnd import attach_drop_target, file_content_provider, set_drop_highlight


TRASH_URI = "trash:///"
RECENT_URI = "recent:///"


class SidebarController:
    """Render sidebar widgets while delegating state and actions to the host."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.sidebar_buttons: dict[str, Gtk.Button] = getattr(host, "sidebar_buttons", {})
        self.pinned_box: Gtk.Box | None = None
        self.pinned_label: Gtk.Label | None = None
        self.starred_box: Gtk.Box | None = None
        self.starred_label: Gtk.Label | None = None
        self.tags_box: Gtk.Box | None = None
        self.tags_label: Gtk.Label | None = None
        self.recent_box: Gtk.Box | None = None
        self.recent_button: Gtk.Button | None = None
        self.devices_box: Gtk.Box | None = None
        self.sidebar: Gtk.Box | None = None
        self.popover: Gtk.Popover | None = None

    def build(self) -> Gtk.Widget:
        host = self.host
        self.sidebar_buttons.clear()
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.add_css_class("sidebar")
        sidebar.set_hexpand(True)
        sidebar.set_vexpand(True)
        self.sidebar = sidebar

        places = [
            ("Home", host.home, True, "user-home-symbolic"),
            (
                "Documents",
                host._special_directory(GLib.UserDirectory.DIRECTORY_DOCUMENTS),
                False,
                "folder-documents-symbolic",
            ),
            (
                "Downloads",
                host._special_directory(GLib.UserDirectory.DIRECTORY_DOWNLOAD),
                False,
                "folder-download-symbolic",
            ),
            (
                "Pictures",
                host._special_directory(GLib.UserDirectory.DIRECTORY_PICTURES),
                False,
                "folder-pictures-symbolic",
            ),
        ]

        sidebar.append(self._section_label("PLACES"))
        for name, location, always_enabled, icon_name in places:
            sidebar.append(self.build_row(name, location, always_enabled, icon_name))

        sidebar.append(self._section_label("SMART"))
        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.append(self.recent_box)
        self._sync_host_boxes()
        self.render_recent()

        self.pinned_label = self._section_label("PINNED")
        sidebar.append(self.pinned_label)
        self.pinned_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.append(self.pinned_box)
        self._sync_host_boxes()
        self.render_pinned()

        self.starred_label = self._section_label("STARRED")
        sidebar.append(self.starred_label)
        self.starred_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.append(self.starred_box)
        self._sync_host_boxes()
        self.render_starred()

        self.tags_label = self._section_label("TAGS")
        self.tags_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.append(self.tags_label)
        sidebar.append(self.tags_box)
        self._sync_host_boxes()
        self.render_tags()

        devices_label = self._section_label("DEVICES")
        devices_label.set_margin_top(12)
        sidebar.append(devices_label)
        self.devices_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.append(self.devices_box)
        self._sync_host_boxes()
        self.render_devices()

        spacer = Gtk.Box()
        spacer.add_css_class("sidebar-spacer")
        sidebar.append(spacer)
        trash_row = self.build_row("Trash", TRASH_URI, True, "user-trash-symbolic")
        trash_row.set_margin_top(4)
        sidebar.append(trash_row)
        scroller = Gtk.ScrolledWindow()
        scroller.add_css_class("sidebar-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_width(160)
        scroller.set_hexpand(False)
        scroller.set_vexpand(True)
        scroller.set_child(sidebar)
        self.host.sidebar_scroll = scroller
        return scroller

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("sidebar-section-label")
        label.set_halign(Gtk.Align.START)
        return label

    def _sync_host_boxes(self) -> None:
        host = self.host
        host.pinned_box = self.pinned_box
        host.starred_box = self.starred_box
        host.recent_box = self.recent_box
        host.devices_box = self.devices_box
        host.sidebar_buttons = self.sidebar_buttons

    def build_row(
        self,
        name: str,
        location: str | None,
        always_enabled: bool,
        icon_name: str,
        *,
        pinned_index: int | None = None,
    ) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("sidebar-row")
        button.add_css_class("flat")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.set_tooltip_text(location or f"{name} is unavailable")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_hexpand(True)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("sidebar-icon")
        icon.set_pixel_size(18)
        label = Gtk.Label(label=name)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(22)
        row.append(icon)
        row.append(label)
        button.set_child(row)

        if not always_enabled and not location:
            button.set_sensitive(False)
        button.connect("clicked", lambda _button, target=location: self.host.navigate(target) if target else None)
        if location:
            middle = Gtk.GestureClick()
            middle.set_button(2)
            middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            middle.connect("pressed", self._on_middle_pressed, location)
            button.add_controller(middle)
            context = Gtk.GestureClick()
            context.set_button(3)
            context.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            context.connect("pressed", self._on_context_pressed, location, name, button)
            button.add_controller(context)
            attach_drop_target(
                button,
                on_drop=lambda value, drop, destination=location, target_index=pinned_index: self._handle_sidebar_drop(
                    value, destination, drop, target_index
                ),
                on_enter=lambda drop, _x, _y, destination=location: set_drop_highlight(
                    button, self.host.drop_location_valid(destination)
                ),
                on_motion=lambda drop, _x, _y, destination=location: set_drop_highlight(
                    button, self.host.drop_location_valid(destination)
                ),
                on_leave=lambda _drop: set_drop_highlight(button, False),
                name="sidebar-drop",
            )
            path = Gio.File.new_for_uri(location).get_path() if "://" in location else location
            if path is not None and os.path.lexists(path):
                source = Gtk.DragSource()
                source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)

                def prepare(
                    _source: Gtk.DragSource,
                    _x: float,
                    _y: float,
                    source_path: str = path,
                ) -> Gdk.ContentProvider | None:
                    return file_content_provider([source_path])

                source.connect("prepare", prepare)
                button.add_controller(source)
        if location:
            self.sidebar_buttons[location] = button
        return button

    def _handle_sidebar_drop(
        self,
        value: object,
        destination: str,
        target: Gtk.DropTarget,
        pinned_index: int | None,
    ) -> bool:
        locations = locations_from_uri_list(value)
        if pinned_index is not None and len(locations) == 1:
            source_path = Gio.File.new_for_uri(locations[0]).get_path()
            if source_path is None:
                return False
            source = os.path.abspath(source_path)
            pinned = [os.path.abspath(entry.location) for entry in self.host.places_store.pinned]
            if source in pinned and os.path.abspath(destination) in pinned:
                self.host.reorder_pinned(source_path, pinned_index)
                return True
        return bool(self.host.handle_drop_to_location(value, destination, target))

    def _on_middle_pressed(
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

    def _on_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        x: float,
        y: float,
        location: str,
        name: str,
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
        self._append_action(menu, "Open in New Tab", lambda: self.host.open_location_new_tab(location))
        self._append_action(menu, "Copy Location", lambda: self.host.copy_breadcrumb_location(location))
        if location == TRASH_URI:
            self._append_action(menu, "Empty Trash", self.host.empty_trash)
        else:
            local_directory = self.host.is_local_directory(location)
            pinned = any(entry.location == location for entry in self.host.places_store.pinned)
            if pinned:
                self._append_action(menu, "Remove from Places", lambda: self.host.remove_sidebar_place(location))
            elif local_directory:
                self._append_action(menu, "Add to Places", lambda: self.host.add_sidebar_place(location))
            self._append_action(menu, "Open Terminal Here", lambda: self.host.open_terminal_at(location), local_directory)
        self._append_action(menu, "Properties", lambda: self.host.show_location_properties(location, display_name=name))
        self._show_popover(menu, source, x, y)

    def _append_action(self, menu: Gtk.Box, label: str, action: Any, sensitive: bool = True) -> None:
        button = Gtk.Button(label=label)
        button.add_css_class("context-action")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.set_sensitive(sensitive)
        button.connect("clicked", lambda _button: (self.close_context(), action()))
        menu.append(button)

    def _show_popover(self, menu: Gtk.Box, source: Gtk.Widget, x: float, y: float) -> None:
        if self.sidebar is None:
            return
        if self.popover is None:
            self.popover = Gtk.Popover()
            self.popover.add_css_class("simple-context-popover")
            self.popover.set_parent(self.sidebar)
        self.popover.set_child(menu)
        try:
            point = Graphene.Point().init(float(x), float(y))
            success, translated = source.compute_point(self.sidebar, point)
            if success and translated is not None:
                x, y = translated.x, translated.y
        except Exception:
            pass
        rect = Gdk.Rectangle()
        rect.x = max(0, int(x))
        rect.y = max(0, int(y))
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def close_context(self) -> None:
        if self.popover is not None:
            self.popover.popdown()

    @staticmethod
    def _clear_box(box: Gtk.Box | None) -> None:
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            box.remove(child)
            child = next_child

    def render_pinned(self) -> None:
        if self.pinned_box is None:
            return
        self._clear_box(self.pinned_box)
        pinned = tuple(self.host.places_store.pinned)
        visible = bool(pinned)
        self.pinned_box.set_visible(visible)
        if self.pinned_label is not None:
            self.pinned_label.set_visible(visible)
        for index, entry in enumerate(pinned):
            self.pinned_box.append(
                self.build_row(entry.label, entry.location, True, entry.icon, pinned_index=index)
            )

    def render_starred(self) -> None:
        if self.starred_box is None:
            return
        self._clear_box(self.starred_box)
        starred = tuple(self.host.places_store.starred)
        visible = bool(starred)
        self.starred_box.set_visible(visible)
        if self.starred_label is not None:
            self.starred_label.set_visible(visible)
        for location in starred:
            self.starred_box.append(
                self.build_row(self.location_label(location), location, True, "starred-symbolic")
            )

    def render_recent(self) -> None:
        if self.recent_box is None:
            return
        # Keep history in the model, but expose one stable virtual location in
        # the sidebar.  Navigation must never append another sidebar row.
        if self.recent_button is None:
            self.recent_button = self.build_row(
                "Recent", RECENT_URI, True, "document-open-recent-symbolic"
            )
        if self.recent_button.get_parent() is None:
            self.recent_box.append(self.recent_button)

    def render_tags(self) -> None:
        if self.tags_box is None or self.tags_label is None:
            return
        self._clear_box(self.tags_box)
        counts = self.host.metadata_store.tag_counts()
        visible = bool(counts)
        self.tags_box.set_visible(visible)
        self.tags_label.set_visible(visible)
        for tag, count in list(counts.items())[:32]:
            button = Gtk.Button()
            button.add_css_class("sidebar-row")
            button.add_css_class("flat")
            button.set_halign(Gtk.Align.FILL)
            button.set_hexpand(True)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image.new_from_icon_name("tag-symbolic")
            icon.set_pixel_size(18)
            label = Gtk.Label(label=f"{tag}  ·  {count}")
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(icon)
            row.append(label)
            button.set_child(row)
            button.set_tooltip_text(f"Search tag: {tag}")
            button.connect("clicked", lambda _button, selected=tag: self.host.search_for_tag(selected))
            self.tags_box.append(button)

    def location_label(self, location: str) -> str:
        if location == TRASH_URI:
            return "Trash"
        if location == RECENT_URI:
            return "Recent"
        if "://" in location:
            return Gio.File.new_for_uri(location).get_basename() or location
        path = os.path.abspath(location)
        if path == os.path.abspath(self.host.home):
            return "Home"
        return os.path.basename(os.path.normpath(path)) or path

    def render_devices(self) -> None:
        if self.devices_box is None:
            return
        self._clear_box(self.devices_box)
        self.devices_box.append(self.build_row("Filesystem", os.path.sep, True, "drive-harddisk-symbolic"))
        monitor = self.host.device_monitor
        if monitor is None:
            return
        for device in monitor.list_devices():
            if device.uri:
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
                line.set_hexpand(True)
                line.append(self.build_row(device.name, device.uri, True, "drive-removable-media-symbolic"))
                mount = device.volume.get_mount() if device.volume is not None else None
                if mount is not None:
                    eject = Gtk.Button()
                    eject.add_css_class("sidebar-eject")
                    eject.set_tooltip_text("Eject device" if device.can_eject else "Unmount device")
                    eject.set_child(Gtk.Image.new_from_icon_name("media-eject-symbolic"))
                    eject.connect(
                        "clicked",
                        lambda _button, target=mount, drive=device.drive: self.host._eject_device(target, drive)
                        if device.can_eject or device.can_stop
                        else self.host._unmount_device(target),
                    )
                    line.append(eject)
                self.devices_box.append(line)
            elif device.volume is not None and device.can_mount:
                button = Gtk.Button(label=f"{device.name} (Mount)")
                button.add_css_class("sidebar-row")
                button.set_halign(Gtk.Align.FILL)
                button.connect("clicked", lambda _button, volume=device.volume: self.host._mount_device(volume))
                self.devices_box.append(button)


__all__ = ["RECENT_URI", "SidebarController", "TRASH_URI"]

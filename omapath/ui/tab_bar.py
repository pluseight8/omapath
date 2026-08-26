"""Compact tab-strip presentation for the active Omapath window."""

from __future__ import annotations

import os
from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from ..diagnostics import LOGGER
from .dnd import attach_drop_target, set_drop_highlight


TRASH_URI = "trash:///"


class TabBarController:
    """Own tab-strip widgets while delegating tab state/actions to the host."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.tab_context_popover: Gtk.Popover | None = None
        self.tab_hover_sources: dict[int, int] = {}

    def build(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        bar.add_css_class("tab-bar")
        bar.set_hexpand(True)
        bar.set_visible(False)
        self.host.tab_bar = bar
        return bar

    def render(self) -> None:
        bar = self.host.tab_bar
        if bar is None:
            return
        self._cancel_tab_hover_timers()
        child = bar.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            bar.remove(child)
            child = next_child
        multiple = len(self.host.tabs) > 1
        bar.set_visible(multiple)
        if not multiple:
            return
        for index, tab in enumerate(self.host.tabs):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
            row.add_css_class("tab-row")
            select = Gtk.Button()
            select.add_css_class("tab-button")
            select.set_hexpand(True)
            select.set_halign(Gtk.Align.FILL)
            if index == self.host.active_tab_index:
                select.add_css_class("tab-button-active")
            label = Gtk.Label(label=self.tab_label(tab.location))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(22)
            select.set_child(label)
            select.set_tooltip_text(tab.location)
            select.connect("clicked", lambda _button, target=index: self.host.switch_tab(target))

            context = Gtk.GestureClick()
            context.set_button(3)
            context.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
            context.connect("pressed", self.on_context_pressed, index, row)
            row.add_controller(context)

            middle = Gtk.GestureClick()
            middle.set_button(2)
            middle.connect(
                "pressed",
                lambda gesture, _count, _x, _y, target=index: self.on_middle_pressed(gesture, target),
            )
            row.add_controller(middle)

            attach_drop_target(
                row,
                on_drop=lambda value, drop, target=index: self.host.handle_drop_to_tab(
                    value, target, drop
                ),
                on_enter=lambda drop, _x, _y, target=index, source=row: self._tab_drag_enter(
                    target, source
                ),
                on_motion=lambda drop, _x, _y, target=index, source=row: self._tab_drag_enter(
                    target, source
                ),
                on_leave=lambda _drop, target=index, source=row: self._tab_drag_leave(
                    target, source
                ),
                name="tab-file-drop",
            )

            tab_source = Gtk.DragSource()
            tab_source.set_actions(Gdk.DragAction.MOVE)

            def prepare_tab(
                _source: Gtk.DragSource,
                _x: float,
                _y: float,
                tab_index: int = index,
            ) -> Gdk.ContentProvider:
                return Gdk.ContentProvider.new_for_value(tab_index)

            tab_source.connect("prepare", prepare_tab)
            row.add_controller(tab_source)

            tab_reorder = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)

            def drop_tab(
                _target: Gtk.DropTarget,
                value: object,
                _x: float,
                _y: float,
                target_index: int = index,
            ) -> bool:
                try:
                    source_index = int(value)
                except (TypeError, ValueError):
                    return False
                return bool(self.host.reorder_tab(source_index, target_index))

            tab_reorder.connect("drop", drop_tab)
            row.add_controller(tab_reorder)

            close = Gtk.Button()
            close.add_css_class("tab-close")
            close_icon = Gtk.Image.new_from_icon_name("window-close-symbolic")
            close_icon.set_pixel_size(14)
            close.set_child(close_icon)
            close.set_tooltip_text("Close tab")
            close.connect("clicked", lambda _button, target=index: self.host.close_tab(target))
            row.append(select)
            row.append(close)
            bar.append(row)

    def _cancel_tab_hover_timers(self) -> None:
        for source in self.tab_hover_sources.values():
            try:
                GLib.source_remove(source)
            except (GLib.Error, TypeError, ValueError):
                pass
        self.tab_hover_sources.clear()

    def _tab_drag_enter(self, index: int, row: Gtk.Widget) -> Gdk.DragAction:
        set_drop_highlight(row, True)
        if index != self.host.active_tab_index and index not in self.tab_hover_sources:
            self.tab_hover_sources[index] = GLib.timeout_add(700, self._activate_tab_after_drag, index)
        return Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK

    def _tab_drag_leave(self, index: int, row: Gtk.Widget) -> None:
        set_drop_highlight(row, False)
        source = self.tab_hover_sources.pop(index, None)
        if source:
            try:
                GLib.source_remove(source)
            except (GLib.Error, TypeError, ValueError):
                pass

    def _activate_tab_after_drag(self, index: int) -> bool:
        self.tab_hover_sources.pop(index, None)
        if 0 <= index < len(self.host.tabs):
            self.host.switch_tab(index)
        return False

    def on_middle_pressed(self, gesture: Gtk.GestureClick, index: int) -> None:
        if gesture.get_current_button() == 2:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.host.close_tab(index)

    def on_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        x: float,
        y: float,
        index: int,
        parent: Gtk.Widget,
    ) -> None:
        if gesture.get_current_button() != 3:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.add_css_class("simple-context-menu")
        for label, action in (
            ("Duplicate Tab", lambda index=index: self.host.duplicate_tab(index)),
            ("Move Left", lambda index=index: self.host.move_tab_left(index)),
            ("Move Right", lambda index=index: self.host.move_tab_right(index)),
            ("Close Tab", lambda index=index: self.host.close_tab(index)),
            ("Close Tabs to Left", lambda index=index: self.host.close_tabs_left(index)),
            ("Close Tabs to Right", lambda index=index: self.host.close_tabs_right(index)),
            ("Close Other Tabs", lambda index=index: self.host.close_other_tabs(index)),
        ):
            self.host._append_menu_action(
                menu,
                label,
                lambda action=action: (self.close_context(), action()),
                (
                    len(self.host.tabs) > 1
                    and not (
                        (label == "Move Left" and index == 0)
                        or (label == "Move Right" and index == len(self.host.tabs) - 1)
                    )
                )
                or label in {"Duplicate Tab", "Close Tab"},
            )
        bar = self.host.tab_bar
        if bar is None:
            return
        if self.tab_context_popover is None:
            self.tab_context_popover = Gtk.Popover()
            self.tab_context_popover.add_css_class("simple-context-popover")
            self.tab_context_popover.set_parent(bar)
            self.host.tab_context_popover = self.tab_context_popover
        self.tab_context_popover.set_child(menu)
        translated_x, translated_y = self._coordinates(parent, bar, x, y)
        rect = Gdk.Rectangle()
        rect.x = max(0, int(translated_x))
        rect.y = max(0, int(translated_y))
        rect.width = 1
        rect.height = 1
        self.tab_context_popover.set_pointing_to(rect)
        self.tab_context_popover.popup()

    @staticmethod
    def _coordinates(
        source: Gtk.Widget,
        target: Gtk.Widget,
        x: float,
        y: float,
    ) -> tuple[float, float]:
        try:
            point = Graphene.Point().init(float(x), float(y))
            result = source.compute_point(target, point)
            success, translated = result
            if success and translated is not None:
                return max(0.0, float(translated.x)), max(0.0, float(translated.y))
        except Exception as error:
            LOGGER.debug("tab context point translation failed: %s", error)
        return max(0.0, float(x)), max(0.0, float(y))

    def tab_label(self, location: str) -> str:
        if location == TRASH_URI:
            return "Trash"
        if "://" in location:
            return Gio.File.new_for_uri(location).get_basename() or "Location"
        path = os.path.abspath(location)
        if path == os.path.abspath(self.host.home):
            return "Home"
        return os.path.basename(os.path.normpath(path)) or "Filesystem"

    def close_context(self) -> None:
        if self.tab_context_popover is not None:
            self.tab_context_popover.popdown()


__all__ = ["TabBarController"]

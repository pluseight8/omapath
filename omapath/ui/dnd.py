"""Small GTK4 drag-and-drop adapters for the active Omapath UI."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from ..diagnostics import LOGGER
from ..dnd import uri_list


DROP_ACTIONS = Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK


def _enter_signal(
    target: Gtk.DropTarget,
    x: float,
    y: float,
    callback: Callable[[Gtk.DropTarget, float, float], Gdk.DragAction],
) -> Gdk.DragAction:
    """Adapt the real GTK4 enter signal: target, x, y, then user data."""

    return callback(target, x, y)


def _motion_signal(
    target: Gtk.DropTarget,
    x: float,
    y: float,
    callback: Callable[[Gtk.DropTarget, float, float], Gdk.DragAction],
) -> Gdk.DragAction:
    """Adapt the real GTK4 motion signal: target, x, y, then user data."""

    return callback(target, x, y)


def _leave_signal(
    target: Gtk.DropTarget,
    callback: Callable[[Gtk.DropTarget], None],
) -> None:
    """Adapt the real GTK4 leave signal: target, then user data."""

    callback(target)


def _drop_signal(
    target: Gtk.DropTarget,
    value: object,
    x: float,
    y: float,
    callback: Callable[[object, Gtk.DropTarget], bool],
    name: str,
) -> bool:
    """Adapt the real GTK4 drop signal: target, value, x, y, then user data."""

    del x, y
    try:
        return bool(callback(value, target))
    except Exception as error:
        LOGGER.error("drop callback failed target=%s: %s", name, error)
        return False


def file_content_provider(paths: list[str]) -> Gdk.ContentProvider | None:
    """Publish both native Gdk.FileList and standard URI-list data."""

    files = [
        Gio.File.new_for_uri(path) if "://" in str(path) else Gio.File.new_for_path(path)
        for path in paths
        if path
    ]
    if not files:
        return None
    try:
        file_list = Gdk.FileList.new_from_list(files)
        native = Gdk.ContentProvider.new_for_value(file_list)
        uris = Gdk.ContentProvider.new_for_bytes(
            "text/uri-list",
            GLib.Bytes.new(uri_list(paths).encode("utf-8")),
        )
        return Gdk.ContentProvider.new_union([native, uris])
    except Exception as error:
        LOGGER.error("drag content creation failed: %s", error)
        return None


def attach_drop_target(
    widget: Gtk.Widget,
    *,
    on_drop: Callable[[object, Gtk.DropTarget], bool],
    on_enter: Callable[[Gtk.DropTarget, float, float], Gdk.DragAction] | None = None,
    on_motion: Callable[[Gtk.DropTarget, float, float], Gdk.DragAction] | None = None,
    on_leave: Callable[[Gtk.DropTarget], None] | None = None,
    name: str = "file-drop",
) -> Gtk.DropTarget:
    """Attach one reusable local/external file drop target to a widget."""

    target = Gtk.DropTarget.new(Gdk.FileList, DROP_ACTIONS)
    target.set_gtypes([Gdk.FileList, GObject.TYPE_STRING])
    target.set_preload(True)
    target.set_static_name(name)

    target.connect("drop", _drop_signal, on_drop, name)
    if on_enter is not None:
        target.connect("enter", _enter_signal, on_enter)
    if on_motion is not None:
        target.connect("motion", _motion_signal, on_motion)
    if on_leave is not None:
        target.connect("leave", _leave_signal, on_leave)
    widget.add_controller(target)
    return target


def set_drop_highlight(widget: Gtk.Widget, enabled: bool) -> Gdk.DragAction:
    """Apply restrained visual feedback and return the GTK action mask."""

    if enabled:
        widget.add_css_class("dnd-drop-target")
        return DROP_ACTIONS
    widget.remove_css_class("dnd-drop-target")
    return Gdk.DragAction.NONE


__all__ = ["DROP_ACTIONS", "attach_drop_target", "file_content_provider", "set_drop_highlight"]

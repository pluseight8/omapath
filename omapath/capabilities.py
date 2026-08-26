"""Small capability snapshots consumed by the active GTK presentation.

The file manager must not infer local filesystem permissions for a GVfs URI,
and it must not advertise a mutation that the current provider cannot perform.
These helpers are intentionally lightweight: local checks are synchronous and
remote checks are best-effort GIO attribute reads with a conservative fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


@dataclass(frozen=True, slots=True)
class LocationCapabilities:
    location: str
    is_local: bool
    is_directory: bool
    exists: bool
    can_create: bool
    can_paste: bool
    can_drop: bool
    can_monitor: bool
    can_terminal: bool
    can_trash: bool


@dataclass(frozen=True, slots=True)
class ItemCapabilities:
    location: str
    is_local: bool
    exists: bool
    is_directory: bool
    can_open: bool
    can_copy: bool
    can_move: bool
    can_rename: bool
    can_trash: bool
    can_delete: bool
    can_preview: bool


def _file_for_location(location: str) -> Gio.File:
    return Gio.File.new_for_uri(location) if "://" in location else Gio.File.new_for_path(location)


def _attribute_boolean(file: Gio.File, name: str, default: bool = False) -> bool:
    try:
        info = file.query_info(name, Gio.FileQueryInfoFlags.NONE, None)
        return bool(info.get_attribute_boolean(name))
    except (GLib.Error, TypeError, ValueError):
        return default


def location_capabilities(location: str, *, query_remote: bool = True) -> LocationCapabilities:
    """Return safe destination capabilities for a folder-like location."""

    normalized = str(location or "")
    if normalized == "trash:///":
        return LocationCapabilities(normalized, False, True, True, False, False, True, False, False, True)
    try:
        file = _file_for_location(normalized)
        path = file.get_path()
    except (GLib.Error, TypeError, ValueError):
        path = None
        file = None
    if path is not None:
        exists = os.path.isdir(path)
        writable = exists and os.access(path, os.W_OK | os.X_OK)
        return LocationCapabilities(
            normalized,
            True,
            exists,
            exists,
            writable,
            writable,
            writable,
            exists,
            writable,
            writable,
        )
    if file is None or not query_remote:
        return LocationCapabilities(normalized, False, False, False, False, False, False, False, False, False)
    try:
        info = file.query_info(
            "standard::type,access::can-write,access::can-delete,access::can-trash",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        is_directory = info.get_file_type() == Gio.FileType.DIRECTORY
        exists = info.get_file_type() != Gio.FileType.UNKNOWN
        writable = bool(info.get_attribute_boolean("access::can-write"))
        can_delete = bool(info.get_attribute_boolean("access::can-delete"))
        can_trash = bool(info.get_attribute_boolean("access::can-trash"))
    except (GLib.Error, TypeError, ValueError):
        return LocationCapabilities(normalized, False, False, False, False, False, False, False, False, False)
    return LocationCapabilities(
        normalized,
        False,
        is_directory,
        exists,
        writable,
        writable,
        writable,
        exists,
        False,
        can_trash,
    )


def item_capabilities(item: Any) -> ItemCapabilities:
    """Return item capabilities without following symlinked directories."""

    file = item if isinstance(item, Gio.File) else _file_for_location(str(item))
    location = file.get_uri()
    path = file.get_path()
    if path is not None:
        exists = os.path.lexists(path)
        is_directory = os.path.isdir(path)
        parent = os.path.dirname(path)
        parent_writable = os.path.isdir(parent) and os.access(parent, os.W_OK | os.X_OK)
        return ItemCapabilities(
            location,
            True,
            exists,
            is_directory,
            exists,
            exists,
            exists and parent_writable,
            exists and parent_writable,
            exists and parent_writable,
            exists and parent_writable,
            exists and not is_directory,
        )
    try:
        info = file.query_info(
            "standard::type,access::can-read,access::can-write,access::can-delete,access::can-trash,access::can-rename",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
        file_type = info.get_file_type()
        exists = file_type != Gio.FileType.UNKNOWN
        is_directory = file_type == Gio.FileType.DIRECTORY
        can_read = bool(info.get_attribute_boolean("access::can-read"))
        can_write = bool(info.get_attribute_boolean("access::can-write"))
        can_delete = bool(info.get_attribute_boolean("access::can-delete"))
        can_trash = bool(info.get_attribute_boolean("access::can-trash"))
        can_rename = bool(info.get_attribute_boolean("access::can-rename"))
    except (GLib.Error, TypeError, ValueError):
        return ItemCapabilities(location, False, False, False, False, False, False, False, False, False, False)
    return ItemCapabilities(
        location,
        False,
        exists,
        is_directory,
        exists and can_read,
        exists and can_read,
        exists and can_write,
        exists and can_rename,
        exists and can_trash,
        exists and can_delete,
        exists and not is_directory and can_read,
    )


__all__ = ["ItemCapabilities", "LocationCapabilities", "item_capabilities", "location_capabilities"]

"""GIO-backed Trash management with explicit permanent-delete operations."""

from __future__ import annotations

import time
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


TRASH_URI = "trash:///"
TRASH_ATTRIBUTES = "standard::name,standard::display-name,standard::size,time::modified,trash::orig-path"


def attribute_text(info: Gio.FileInfo, attribute: str) -> str | None:
    """Read a provider string without using the wrong GIO typed getter.

    The freedesktop trash backend exposes ``trash::orig-path`` as a
    ``BYTE_STRING`` on some systems and as a ``STRING`` on others.  Calling
    ``get_attribute_string`` for the former causes a GLib-GIO critical.  The
    type check is deliberately explicit so optional provider attributes stay
    quiet and predictable.
    """

    attribute_type = info.get_attribute_type(attribute)
    if attribute_type == Gio.FileAttributeType.INVALID:
        return None
    if attribute_type == Gio.FileAttributeType.STRING:
        return info.get_attribute_string(attribute)
    if attribute_type == Gio.FileAttributeType.BYTE_STRING:
        value = info.get_attribute_byte_string(attribute)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)
    if attribute_type == Gio.FileAttributeType.STRINGV:
        values = info.get_attribute_stringv(attribute)
        return values[0] if values else None
    return info.get_attribute_as_string(attribute)


@dataclass(frozen=True, slots=True)
class TrashItem:
    uri: str
    name: str
    original_path: str | None
    size: int
    modified: int | None


def list_trash() -> list[TrashItem]:
    root = Gio.File.new_for_uri(TRASH_URI)
    result: list[TrashItem] = []
    try:
        enumerator = root.enumerate_children(TRASH_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return []
    try:
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            name = info.get_name()
            result.append(
                TrashItem(
                    root.get_child(name).get_uri(),
                    info.get_display_name() or name,
                    attribute_text(info, "trash::orig-path"),
                    int(info.get_attribute_uint64("standard::size")),
                    int(info.get_attribute_uint64("time::modified")) or None,
                )
            )
    except GLib.Error:
        return result
    finally:
        try:
            enumerator.close(None)
        except GLib.Error:
            pass
    return result


def empty_trash() -> tuple[int, list[str]]:
    completed = 0
    errors: list[str] = []
    for item in list_trash():
        try:
            Gio.File.new_for_uri(item.uri).delete(None)
            completed += 1
        except GLib.Error as error:
            errors.append(f"{item.name}: {error.message}")
    return completed, errors


def delete_permanently(uri: str) -> None:
    Gio.File.new_for_uri(uri).delete(None)


def restore(item: TrashItem, *, destination: str | None = None) -> str:
    target = destination or item.original_path
    if not target:
        raise ValueError("Trash item has no original path")
    source = Gio.File.new_for_uri(item.uri)
    destination_file = Gio.File.new_for_path(target)
    source.move(destination_file, Gio.FileCopyFlags.NONE, None)
    return target


def purge_older_than(days: int, *, now: int | None = None) -> tuple[int, list[str]]:
    if days < 0:
        raise ValueError("days must be non-negative")
    cutoff = int(now if now is not None else time.time()) - days * 86400
    completed = 0
    errors: list[str] = []
    for item in list_trash():
        if item.modified is None or item.modified >= cutoff:
            continue
        try:
            delete_permanently(item.uri)
            completed += 1
        except GLib.Error as error:
            errors.append(f"{item.name}: {error.message}")
    return completed, errors


__all__ = [
    "TRASH_URI",
    "TrashItem",
    "attribute_text",
    "delete_permanently",
    "empty_trash",
    "list_trash",
    "purge_older_than",
    "restore",
]

"""Filesystem location parsing shared by the GTK location entry."""

from __future__ import annotations

import os
from collections.abc import Iterable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib


LOCATION_HISTORY_LIMIT = 24


def parse_location(value: str, home: str, current: str | None = None) -> str | None:
    """Parse a typed path or URI through GIO.

    ``current`` is used only for relative input typed into the location field;
    ordinary navigation keeps its existing absolute/URI semantics.  Relative
    paths on a GVfs location are resolved by GIO instead of a custom URI
    parser.
    """

    text = value.strip()
    if not text:
        return None
    if text == "~":
        expanded = home
    elif text.startswith("~/"):
        expanded = os.path.join(home, text[2:])
    else:
        expanded = os.path.expanduser(text)
    if current and not _has_uri_scheme(expanded) and not os.path.isabs(expanded):
        try:
            base = Gio.File.new_for_uri(current) if "://" in current else Gio.File.new_for_path(current)
            base_path = base.get_path()
            if base_path is not None:
                expanded = os.path.join(base_path, expanded)
            else:
                return base.resolve_relative_path(expanded).get_uri()
        except (GLib.Error, TypeError, ValueError):
            return None
    try:
        file = Gio.File.new_for_commandline_arg(expanded)
    except (GLib.Error, TypeError, ValueError):
        return None
    path = file.get_path()
    if path is not None:
        return os.path.abspath(path)
    uri = file.get_uri()
    return uri or None


def _has_uri_scheme(value: str) -> bool:
    return "://" in value or value.startswith("file:")


def local_location_error(location: str) -> str | None:
    """Return a compact validation message for a local folder location."""

    try:
        if location.startswith("file://"):
            path = Gio.File.new_for_uri(location).get_path()
        elif "://" in location:
            return None
        else:
            path = location
    except (GLib.Error, TypeError, ValueError):
        return "Location does not exist"
    if path is None or not os.path.lexists(path):
        return "Location does not exist"
    if not os.path.isdir(path):
        return "Not a folder"
    if not os.access(path, os.R_OK | os.X_OK):
        return "Permission denied"
    return None


def completion_location_parts(
    value: str,
    home: str,
    current: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(local parent, typed prefix)`` for path completion.

    Completion intentionally stays local.  Remote locations are left to the
    provider's normal navigation flow instead of synchronously enumerating a
    potentially slow network directory.
    """

    text = value.strip()
    if not text:
        target = current or home
        target_path = Gio.File.new_for_uri(target).get_path() if "://" in target else target
        return (target_path, "") if target_path else None
    if "://" in text and not text.startswith("file://"):
        return None
    resolved = parse_location(text, home, current=current)
    if resolved is None:
        return None
    path = Gio.File.new_for_uri(resolved).get_path() if resolved.startswith("file://") else resolved
    if path is None:
        return None
    if text.endswith(os.sep):
        return path, ""
    parent = os.path.dirname(path) or os.path.sep
    return parent, os.path.basename(path)


def filter_completion_names(
    names: Iterable[str],
    prefix: str,
    *,
    limit: int = 64,
) -> list[str]:
    """Filter and order a bounded set of local completion names."""

    folded_prefix = prefix.casefold()
    visible = [
        name
        for name in names
        if name
        and (prefix.startswith(".") or not name.startswith("."))
        and name.casefold().startswith(folded_prefix)
    ]
    return sorted(visible, key=lambda name: (name.casefold(), name))[: max(1, min(limit, 128))]


def record_location_history(
    history: Iterable[str],
    location: str,
    *,
    limit: int = LOCATION_HISTORY_LIMIT,
) -> list[str]:
    """Prepend one successful manual location while keeping history bounded."""

    bound = max(1, min(int(limit), 64))
    value = location.strip()
    if not value:
        return [item for item in history if isinstance(item, str) and item.strip()][:bound]
    result = [value]
    for item in history:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in result:
            result.append(item)
        if len(result) >= bound:
            break
    return result


def location_display_text(location: str, home: str) -> str:
    """Return the real path/URI shown while Ctrl+L is active."""

    if location == "trash:///":
        return location
    parsed = parse_location(location, home)
    return parsed or location

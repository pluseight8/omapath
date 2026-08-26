"""View-mode and additional-information contracts for the GTK adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .filesystem import format_date, format_size


class ViewMode(StrEnum):
    ICONS = "icons"
    COMPACT = "compact"
    DETAILS = "details"


VIEW_MODE_LABELS = {
    ViewMode.ICONS: "Icons",
    ViewMode.COMPACT: "Compact",
    ViewMode.DETAILS: "Details",
}

VIEW_MODE_SHORTCUTS = {
    ViewMode.ICONS: "Ctrl+1",
    ViewMode.COMPACT: "Ctrl+2",
    ViewMode.DETAILS: "Ctrl+3",
}

DETAIL_COLUMNS = ("name", "size", "type", "modified")
DETAIL_COLUMN_LABELS = {
    "name": "Name",
    "size": "Size",
    "type": "Type",
    "modified": "Modified",
    "created": "Created",
    "accessed": "Accessed",
    "permissions": "Permissions",
    "owner": "Owner",
    "group": "Group",
    "path": "Path",
    "link_target": "Link Target",
    "mime": "MIME",
    "item_count": "Item Count",
}


def normalize_view_mode(value: object) -> ViewMode:
    text = str(value or "").casefold()
    if text in {"grid", "icon", "icons"}:
        return ViewMode.ICONS
    if text in {"list", "details", "detail"}:
        return ViewMode.DETAILS
    if text == "compact":
        return ViewMode.COMPACT
    return ViewMode.ICONS


def detail_value(row: Any, column: str) -> str:
    """Format a row field for a details/secondary-information label."""

    if column == "name":
        return str(getattr(row, "display_name", getattr(row, "name", "")))
    if column == "size":
        return format_size(int(getattr(row, "size", 0) or 0))
    if column == "type":
        content_type = str(getattr(row, "content_type", "") or "")
        return content_type or ("Folder" if getattr(row, "is_dir", False) else "File")
    if column == "modified":
        return format_date(getattr(row, "modified", None))
    if column == "path":
        file = getattr(row, "file", None)
        getter = getattr(file, "get_path", None) or getattr(file, "get_uri", None)
        try:
            return str(getter() or "") if getter is not None else ""
        except Exception:
            return ""
    return str(getattr(row, column, "") or "")


__all__ = [
    "DETAIL_COLUMNS",
    "DETAIL_COLUMN_LABELS",
    "VIEW_MODE_LABELS",
    "VIEW_MODE_SHORTCUTS",
    "ViewMode",
    "detail_value",
    "normalize_view_mode",
]

"""Independent grouping of already-sorted directory entries."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class GroupCriterion(StrEnum):
    NONE = "none"
    NAME = "name"
    TYPE = "type"
    DATE = "date"
    SIZE = "size"


@dataclass(frozen=True, slots=True)
class EntryGroup:
    key: str
    label: str
    items: tuple[Any, ...]


def normalize_group_criterion(value: object) -> GroupCriterion:
    try:
        return GroupCriterion(str(value).casefold())
    except ValueError:
        return GroupCriterion.NONE


def _bucket(entry: Any, criterion: GroupCriterion) -> tuple[str, str]:
    if criterion is GroupCriterion.TYPE:
        content_type = str(getattr(entry, "content_type", "unknown") or "unknown")
        if content_type.startswith("inode/directory"):
            return content_type, "Folders"
        major = content_type.split("/", 1)[0].casefold()
        labels = {
            "image": "Images",
            "video": "Videos",
            "audio": "Audio",
            "text": "Documents",
            "application": "Applications & Archives",
        }
        label = labels.get(major, "Other")
        return content_type, label
    if criterion is GroupCriterion.DATE:
        timestamp = getattr(entry, "modified", None)
        if not timestamp:
            return "unknown", "Unknown date"
        value = dt.datetime.fromtimestamp(int(timestamp)).date()
        key = value.isoformat()
        return key, value.strftime("%Y-%m-%d")
    if criterion is GroupCriterion.SIZE:
        size = int(getattr(entry, "size", 0) or 0)
        if size < 1024 * 1024:
            return "small", "Small (< 1 MiB)"
        if size < 1024 * 1024 * 100:
            return "medium", "Medium (1–100 MiB)"
        return "large", "Large (≥ 100 MiB)"
    name = str(getattr(entry, "display_name", getattr(entry, "name", "")))
    if criterion is GroupCriterion.NAME:
        first = name[:1].upper() or "#"
        return first if first.isalnum() else "#", first if first.isalnum() else "#"
    return "all", "All items"


def group_entries(entries: Iterable[Any], criterion: GroupCriterion | str) -> list[EntryGroup]:
    selected = normalize_group_criterion(criterion)
    values = list(entries)
    if selected is GroupCriterion.NONE:
        return [EntryGroup("all", "All items", tuple(values))] if values else []
    grouped: dict[str, tuple[str, list[Any]]] = {}
    for entry in values:
        key, label = _bucket(entry, selected)
        grouped.setdefault(key, (label, []))[1].append(entry)
    return [EntryGroup(key, grouped[key][0], tuple(grouped[key][1])) for key in sorted(grouped)]


__all__ = ["EntryGroup", "GroupCriterion", "group_entries", "normalize_group_criterion"]

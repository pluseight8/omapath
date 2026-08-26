"""Small, deterministic directory sorting helpers for the simple runtime."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


SORT_KEYS = ("name", "size", "type", "modified")
SORT_LABELS = {
    "name": "Name",
    "size": "Size",
    "type": "Type",
    "modified": "Modified",
}
NATURAL_PARTS = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    """Return a Unicode-aware natural key (``file2`` before ``file10``)."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in NATURAL_PARTS.split(normalized)
    )


def sort_entries(
    entries: Iterable[Any],
    sort_key: str = "name",
    descending: bool = False,
    folders_first: bool = True,
    natural: bool = True,
) -> list[Any]:
    """Sort directory-like objects without recursively measuring folders."""

    mode = sort_key if sort_key in SORT_KEYS else "name"
    result = list(entries)

    def name_key(entry: Any) -> tuple[tuple[int, object], ...]:
        value = str(getattr(entry, "display_name", getattr(entry, "name", "")))
        if natural:
            return natural_sort_key(value)
        return ((1, unicodedata.normalize("NFKC", value).casefold()),)

    def primary(entry: Any) -> object:
        if mode == "size":
            return (int(getattr(entry, "size", 0) or 0), name_key(entry))
        if mode == "type":
            content_type = str(getattr(entry, "content_type", "") or "").casefold()
            return (content_type, name_key(entry))
        if mode == "modified":
            modified = getattr(entry, "modified", None)
            # Missing timestamps are deterministic and remain safe for remote
            # backends that cannot provide metadata.
            return (modified is None, int(modified or 0), name_key(entry))
        return name_key(entry)

    result.sort(key=primary, reverse=bool(descending))
    if folders_first:
        # This stable pass intentionally remains after reverse sorting so
        # folders stay first in both directions.
        result.sort(key=lambda entry: not bool(getattr(entry, "is_dir", False)))
    return result


def normalize_sort_key(value: object) -> str:
    return str(value) if value in SORT_KEYS else "name"

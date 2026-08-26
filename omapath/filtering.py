"""Instant current-directory filtering used by Filter Bar and tests."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class FilterMode(StrEnum):
    PLAIN = "plain"
    GLOB = "glob"
    REGEX = "regex"


@dataclass(frozen=True, slots=True)
class FilterResult:
    items: tuple[Any, ...]
    error: str | None = None


def normalize_filter_mode(value: object) -> FilterMode:
    try:
        return FilterMode(str(value).casefold())
    except ValueError:
        return FilterMode.PLAIN


def _entry_text(entry: Any, include_path: bool) -> str:
    name = str(getattr(entry, "display_name", getattr(entry, "name", "")))
    tags = getattr(entry, "tags", ())
    tag_text = " ".join(str(tag) for tag in tags) if tags else ""
    if not include_path:
        return name
    file = getattr(entry, "file", None)
    getter = getattr(file, "get_path", None) or getattr(file, "get_uri", None)
    path = ""
    if getter is not None:
        try:
            path = str(getter() or "")
        except Exception:
            path = ""
    value = f"{path}\n{name}" if path else name
    return f"{value}\n{tag_text}" if tag_text else value


def filter_entries(
    entries: Iterable[Any],
    query: str,
    mode: FilterMode | str = FilterMode.PLAIN,
    *,
    case_sensitive: bool = False,
    include_path: bool = False,
) -> FilterResult:
    """Filter entries without filesystem access or recursive search.

    Invalid regexes are returned as a visible error instead of being allowed to
    escape into the GTK search callback.
    """

    text = str(query or "")
    if not text:
        return FilterResult(tuple(entries))
    selected_mode = normalize_filter_mode(mode)
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled: re.Pattern[str] | None = None
    if selected_mode is FilterMode.REGEX:
        try:
            compiled = re.compile(text, flags)
        except re.error as error:
            return FilterResult((), f"Invalid regular expression: {error}")

    result: list[Any] = []
    folded_query = text if case_sensitive else text.casefold()
    for entry in entries:
        candidate = _entry_text(entry, include_path)
        if selected_mode is FilterMode.PLAIN:
            haystack = candidate if case_sensitive else candidate.casefold()
            matched = folded_query in haystack
        elif selected_mode is FilterMode.GLOB:
            pattern = text if case_sensitive else text.casefold()
            value = candidate if case_sensitive else candidate.casefold()
            matched = fnmatch.fnmatchcase(value, pattern)
        else:
            matched = compiled.search(candidate) is not None if compiled is not None else False
        if matched:
            result.append(entry)
    return FilterResult(tuple(result))


__all__ = ["FilterMode", "FilterResult", "filter_entries", "normalize_filter_mode"]

"""Small, toolkit-agnostic helpers for Nautilus-style integrations.

The GTK adapter owns presentation and process launching.  Keeping discovery and
list shaping here makes the sidebar cheap to refresh and easy to test without a
display server.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


MAX_RECENT_LOCATIONS = 24
MAX_STARRED_LOCATIONS = 64
MAX_TEMPLATES = 64
MAX_SCRIPTS = 64


def canonical_location(value: str) -> str:
    """Return a stable local path or remote URI for persistence."""

    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path or "/")
        if parsed.netloc and parsed.netloc != "localhost":
            value = f"//{parsed.netloc}{value}"
    if value in {"trash:", "trash://", "trash:///"}:
        return "trash:///"
    if "://" in value:
        return value.rstrip("/") or value
    return os.path.abspath(os.path.expanduser(value))


def push_recent(values: list[str], value: str, limit: int = MAX_RECENT_LOCATIONS) -> list[str]:
    """Put a location first, remove duplicates, and cap the history."""

    canonical = canonical_location(value)
    if not canonical:
        return list(values[:limit])
    result = [canonical]
    for entry in values:
        normalized = canonical_location(entry)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= max(1, limit):
            break
    return result


def remove_location(values: list[str], value: str) -> list[str]:
    """Remove a location using the same canonical identity as ``push_recent``."""

    canonical = canonical_location(value)
    return [
        entry
        for entry in values
        if canonical_location(entry) and canonical_location(entry) != canonical
    ]


def clean_locations(value: object, limit: int) -> list[str]:
    """Validate persisted location lists without touching the filesystem."""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        normalized = canonical_location(entry)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= max(1, limit):
            break
    return result


def template_directory(home: str | None = None) -> Path:
    return Path(home or Path.home()) / "Templates"


def template_paths(home: str | None = None, limit: int = MAX_TEMPLATES) -> list[Path]:
    """Return visible regular files from the XDG-compatible Templates folder."""

    directory = template_directory(home)
    try:
        entries = [
            entry
            for entry in directory.iterdir()
            if not entry.name.startswith(".") and entry.is_file()
        ]
    except OSError:
        return []
    return sorted(entries, key=lambda path: (path.name.casefold(), path.name))[: max(1, limit)]


def script_directories(home: str | None = None, data_home: str | None = None) -> list[Path]:
    """Return Nautilus' user script directory and a conventional fallback."""

    root = Path(data_home or os.environ.get("XDG_DATA_HOME", Path(home or Path.home()) / ".local/share"))
    directories = [root / "nautilus" / "scripts"]
    fallback = Path(home or Path.home()) / "Scripts"
    if fallback not in directories:
        directories.append(fallback)
    return directories


def script_paths(home: str | None = None, data_home: str | None = None, limit: int = MAX_SCRIPTS) -> list[Path]:
    """Return runnable user scripts in stable display order."""

    found: dict[str, Path] = {}
    for directory in script_directories(home, data_home):
        try:
            entries = directory.iterdir()
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.name.startswith(".") or not entry.is_file() or not os.access(entry, os.X_OK):
                    continue
            except OSError:
                continue
            found.setdefault(entry.name.casefold(), entry)
    return sorted(found.values(), key=lambda path: (path.name.casefold(), path.name))[: max(1, limit)]

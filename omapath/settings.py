"""Validated, atomic settings and session persistence for Omapath."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


CONFIG_PATH = Path.home() / ".config/omapath/settings.json"
LEGACY_CONFIG_PATH = Path.home() / ".config/omarchy-files/settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 5,
    "default_view": "grid",
    "zoom_level": 100,
    "icon_zoom": 48,
    "icon_size": 84,
    "thumbnail_size": 96,
    "start_location": "last",
    "startup_location": "",
    "show_hidden": False,
    "folders_first": True,
    "natural_sort": True,
    "confirm_delete": True,
    "confirm_permanent_delete": True,
    "single_click_open": False,
    "auto_refresh": True,
    "show_thumbnails": True,
    "show_folder_sizes": False,
    "show_additional_info": True,
    "show_git_status": False,
    "show_groups": False,
    "group_criterion": "none",
    "show_selection_marker": False,
    "show_tooltips": True,
    "inline_rename": True,
    "show_status_bar": True,
    "show_previews": True,
    "archive_as_folder": False,
    "services_enabled": True,
    "new_tab_inherit_view": True,
    "confirm_empty_trash": True,
    "trash_auto_cleanup": False,
    "compact_mode": False,
    "show_sidebar": True,
    "show_folders_panel": False,
    "show_terminal_panel": False,
    "show_inspector": False,
    "remember_location": True,
    "remember_tabs": True,
    "remember_window_size": True,
    "window_width": 1320,
    "window_height": 820,
    "window_maximized": False,
    "split_view": False,
    "split_position": 0.5,
    "sidebar_width": 200,
    "folders_panel_width": 260,
    "info_panel_width": 280,
    "terminal_panel_height": 220,
    "follow_omarchy_theme": True,
    "animations": True,
    "quick_look_enabled": True,
    "image_preview": True,
    "text_preview": True,
    "media_preview": True,
    "preview_max_bytes": 262144,
    "thumbnail_cache_limit": 64,
    "sort_mode": "name",
    "sort_reverse": False,
    "search_recursive": False,
    "search_contents": False,
    "search_case_sensitive": False,
    "search_show_hidden": False,
    "conflict_behavior": "ask",
    "terminal_shell": "",
    "external_clipboard": True,
    "shortcuts": {},
    "folder_view_settings": {},
    "pinned_places": [],
    "starred_places": [],
    "recent_locations": [],
    "location_history": [],
    "last_location": "",
    "session_tabs": [],
    "active_tab": 0,
}

_ENUMS = {
    "default_view": {"grid", "icons", "compact", "details", "list"},
    "start_location": {"home", "last", "custom"},
    "sort_mode": {"name", "modified", "size", "type"},
    "conflict_behavior": {"ask", "replace", "skip", "rename"},
    "group_criterion": {"none", "name", "type", "date", "size"},
}
_INT_RANGES = {
    "zoom_level": (60, 180),
    "icon_zoom": (32, 96),
    "icon_size": (48, 180),
    "thumbnail_size": (48, 512),
    "preview_max_bytes": (4096, 16 * 1024 * 1024),
    "thumbnail_cache_limit": (8, 512),
    "window_width": (720, 8192),
    "window_height": (480, 8192),
    "sidebar_width": (160, 320),
    "folders_panel_width": (220, 440),
    "info_panel_width": (220, 440),
    "terminal_panel_height": (120, 480),
}
_FLOAT_RANGES = {"split_position": (0.25, 0.75)}
_BOOL_KEYS = {
    key for key, value in DEFAULT_SETTINGS.items() if isinstance(value, bool)
}


def _copy_defaults() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETTINGS)


def validate_settings(raw: Any) -> dict[str, Any]:
    """Return a complete safe settings object, ignoring unknown keys."""

    settings = _copy_defaults()
    if isinstance(raw, dict):
        for key in settings:
            if key not in raw:
                continue
            value = raw[key]
            if key in _BOOL_KEYS:
                if isinstance(value, bool):
                    settings[key] = value
                continue
            if key in _ENUMS:
                if value in _ENUMS[key]:
                    settings[key] = value
                continue
            if key in _INT_RANGES:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                low, high = _INT_RANGES[key]
                settings[key] = max(low, min(number, high))
                continue
            if key in _FLOAT_RANGES:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                low, high = _FLOAT_RANGES[key]
                settings[key] = max(low, min(number, high))
                continue
            if key == "shortcuts":
                if isinstance(value, dict):
                    clean_shortcuts: dict[str, str] = {}
                    for action_id, shortcut in value.items():
                        if (
                            isinstance(action_id, str)
                            and "." in action_id
                            and isinstance(shortcut, str)
                            and len(shortcut) <= 64
                        ):
                            clean_shortcuts[action_id.strip()] = shortcut.strip()
                    settings[key] = clean_shortcuts
                continue
            if key in {"pinned_places", "starred_places", "recent_locations", "location_history"}:
                if isinstance(value, list):
                    # Keep this layer display-server independent.  The GTK
                    # adapter performs existence checks before rendering.
                    limit = 64 if key == "starred_places" else 24
                    clean: list[str] = []
                    for entry in value:
                        if isinstance(entry, str) and entry and entry not in clean:
                            clean.append(entry)
                        if len(clean) >= limit:
                            break
                    settings[key] = clean
                continue
            if key == "folder_view_settings":
                if isinstance(value, dict):
                    clean: dict[str, dict[str, Any]] = {}
                    for location, options in value.items():
                        if not isinstance(location, str) or not isinstance(options, dict):
                            continue
                        item: dict[str, Any] = {}
                        if options.get("view_mode") in {"grid", "icons", "compact", "details", "list"}:
                            item["view_mode"] = options["view_mode"]
                        if options.get("sort_mode") in {"name", "modified", "size", "type"}:
                            item["sort_mode"] = options["sort_mode"]
                        try:
                            if "zoom_level" in options:
                                item["zoom_level"] = max(60, min(180, int(options["zoom_level"])))
                            if "icon_zoom" in options:
                                item["icon_zoom"] = max(32, min(96, int(options["icon_zoom"])))
                        except (TypeError, ValueError):
                            pass
                        if options.get("group_criterion") in {"none", "name", "type", "date", "size"}:
                            item["group_criterion"] = options["group_criterion"]
                        for option in ("sort_reverse", "folders_first", "show_hidden", "show_previews", "show_groups"):
                            if isinstance(options.get(option), bool):
                                item[option] = options[option]
                        if item:
                            clean[location] = item
                    settings[key] = clean
                continue
            if key == "session_tabs":
                if isinstance(value, list):
                    settings[key] = [entry for entry in value if isinstance(entry, dict)]
                continue
            if key == "active_tab":
                try:
                    settings[key] = max(0, int(value))
                except (TypeError, ValueError):
                    pass
                continue
            if key in {"last_location", "startup_location", "terminal_shell"} and isinstance(value, str):
                settings[key] = value
    settings["schema_version"] = 5
    return settings


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Load the new config, migrating the previous config on first use."""

    target = path or CONFIG_PATH
    loaded = _read_json(target)
    migrated = False
    if loaded is None and path is None:
        loaded = _read_json(LEGACY_CONFIG_PATH)
        migrated = loaded is not None
    settings = validate_settings(loaded or {})
    if migrated:
        save_settings(settings, target)
    return settings


def save_settings(settings: dict[str, Any], path: Path | None = None) -> bool:
    """Write settings atomically, keeping a failed write non-destructive."""

    target = path or CONFIG_PATH
    parent = target.parent
    temporary: Path | None = None
    descriptor = -1
    try:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=parent)
        temporary = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with stream:
            json.dump(validate_settings(settings), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        try:
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    # Some filesystems do not support syncing directory FDs;
                    # the file itself has already been flushed and replaced.
                    pass
            finally:
                os.close(directory_fd)
        return True
    except (OSError, TypeError, ValueError):
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def session_payload(settings: dict[str, Any], tabs: list[dict[str, Any]], active_tab: int) -> dict[str, Any]:
    """Return the minimal serializable session update."""

    result = dict(settings)
    result["session_tabs"] = tabs
    result["active_tab"] = max(0, int(active_tab))
    return result

"""Omarchy theme/font integration with event-driven reload support."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .diagnostics import LOGGER


THEME_STATE = Path.home() / ".local/state/omarchy/current"
FALLBACK_COLORS = {
    "accent": "#9ccfd8",
    "selection": "#3b4d52",
    "muted": "#68747b",
    "background": "#16191d",
    "dark_background": "#101215",
    "darker_background": "#0b0d0f",
    "lighter_background": "#232a2e",
    "foreground": "#e4e9eb",
    "dark_foreground": "#7f8b92",
    "light_foreground": "#b7c1c6",
    "bright_foreground": "#f2f5f6",
    "red": "#f07178",
    "yellow": "#f2c777",
    "green": "#a8cc8c",
    "cyan": "#82cfff",
    "blue": "#8cb6ff",
}
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6,8}")


def _run_omarchy(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["omarchy", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip().splitlines()
    return value[0].strip() if value else None


def active_theme() -> str:
    try:
        value = (THEME_STATE / "theme.name").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        value = ""
    if not value:
        value = _run_omarchy("theme", "current") or "default"
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower() or "default"


def active_font() -> str:
    for candidate in (THEME_STATE / "font.name", THEME_STATE / "font", Path.home() / ".config/omarchy/current/font"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            return value.strip("\"'")
    return (_run_omarchy("font", "current") or "monospace").strip("\"'")


def _color_candidates(theme: str) -> list[Path]:
    return [
        THEME_STATE / "theme/colors.toml",
        Path.home() / ".config/omarchy/themes" / theme / "colors.toml",
        Path("/usr/share/omarchy/themes") / theme / "colors.toml",
    ]


def _read_colors_source(theme: str) -> tuple[dict[str, str], Path | None]:
    colors = dict(FALLBACK_COLORS)
    for path in _color_candidates(theme):
        try:
            with path.open("rb") as handle:
                loaded = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            LOGGER.warning("theme colors invalid source=%s error=%s", path, error)
            return colors, None
        except (OSError, UnicodeError):
            continue
        if not isinstance(loaded, dict):
            LOGGER.warning("theme colors invalid source=%s reason=not-a-table", path)
            return colors, None
        for key in colors:
            value = loaded.get(key)
            if value is not None and (not isinstance(value, str) or not COLOR_RE.fullmatch(value)):
                LOGGER.warning("theme colors invalid source=%s token=%s", path, key)
                return colors, None
            if isinstance(value, str) and COLOR_RE.fullmatch(value):
                colors[key] = value
        return colors, path
    return colors, None


def read_colors(theme: str | None = None) -> dict[str, str]:
    colors, _source = _read_colors_source(theme or active_theme())
    return colors


@dataclass(frozen=True, slots=True)
class ThemeSnapshot:
    """One validated palette/font read used for an atomic live reload."""

    name: str
    colors: dict[str, str]
    font: str


def read_theme_snapshot() -> ThemeSnapshot | None:
    """Read a valid current theme, or return ``None`` during replacement/errors."""

    name = active_theme()
    colors, source = _read_colors_source(name)
    if source is None:
        LOGGER.debug("theme snapshot unavailable name=%s", name)
        return None
    return ThemeSnapshot(name=name, colors=colors, font=active_font())


def theme_watch_paths() -> list[Path]:
    config_root = Path.home() / ".config/omarchy"
    fontconfig = Path.home() / ".config/fontconfig/fonts.conf"
    return [
        THEME_STATE,
        THEME_STATE / "theme.name",
        THEME_STATE / "theme",
        THEME_STATE / "theme/colors.toml",
        THEME_STATE / "font.name",
        THEME_STATE / "font",
        config_root / "current",
        config_root / "current/font",
        fontconfig,
    ]


def rgba(value: str, opacity: float) -> str:
    raw = value.removeprefix("#")[:6]
    try:
        red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    except (ValueError, IndexError):
        return value
    return f"rgba({red}, {green}, {blue}, {opacity:.2f})"


class ThemeWatcher:
    """Robust Gio monitor for Omarchy's current-theme state.

    Parent directories are monitored alongside existing files so an atomic
    replace, symlink retarget, or temporary deletion can be followed by a
    monitor rebuild without losing the next theme event.
    """

    _RELEVANT_EVENTS = {
        "CHANGED",
        "CHANGES_DONE_HINT",
        "CREATED",
        "DELETED",
        "MOVED",
        "RENAMED",
        "ATTRIBUTE_CHANGED",
    }

    def __init__(self, on_change: Callable[..., None]) -> None:
        self.on_change = on_change
        self.monitors: list[object] = []
        self._gio: object | None = None
        self._glib: object | None = None
        self._monitor_rebuild_source = 0
        self._running = False

    def start(self) -> None:
        self.stop()
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            return
        self._gio = Gio
        self._glib = GLib
        self._running = True
        self._install_monitors()

    @staticmethod
    def _nearest_existing_directory(path: Path) -> Path | None:
        candidate = path
        while not (candidate.exists() and candidate.is_dir()):
            if candidate == candidate.parent:
                return None
            candidate = candidate.parent
        return candidate

    def _install_monitors(self) -> None:
        gio = self._gio
        if gio is None or not self._running:
            return
        self._cancel_monitors()
        watched_directories: set[str] = set()
        watched_files: set[str] = set()
        for source in theme_watch_paths():
            LOGGER.debug("theme monitor source=%s", source)
            parent = self._nearest_existing_directory(source.parent)
            if parent is not None and str(parent) not in watched_directories:
                self._watch_directory(gio, parent)
                watched_directories.add(str(parent))
            if source.exists() and source.is_dir():
                directory = self._nearest_existing_directory(source)
                if directory is not None and str(directory) not in watched_directories:
                    self._watch_directory(gio, directory)
                    watched_directories.add(str(directory))
            if (source.exists() or source.is_symlink()) and str(source) not in watched_files:
                self._watch_file(gio, source)
                watched_files.add(str(source))

    def _watch_directory(self, gio: object, path: Path) -> None:
        try:
            monitor = gio.File.new_for_path(str(path)).monitor_directory(  # type: ignore[attr-defined]
                gio.FileMonitorFlags.WATCH_MOVES,
                None,
            )
            monitor.connect("changed", self._on_changed)
            self.monitors.append(monitor)
        except Exception as error:
            LOGGER.debug("theme directory monitor unavailable source=%s error=%s", path, error)

    def _watch_file(self, gio: object, path: Path) -> None:
        try:
            monitor = gio.File.new_for_path(str(path)).monitor_file(  # type: ignore[attr-defined]
                gio.FileMonitorFlags.WATCH_MOVES,
                None,
            )
            monitor.connect("changed", self._on_changed)
            self.monitors.append(monitor)
        except Exception as error:
            LOGGER.debug("theme file monitor unavailable source=%s error=%s", path, error)

    @staticmethod
    def _event_name(event: object) -> str:
        value_nick = getattr(event, "value_nick", None)
        if value_nick:
            return str(value_nick).upper()
        text = str(event).upper()
        for name in ThemeWatcher._RELEVANT_EVENTS:
            if name in text:
                return name
        return text

    @staticmethod
    def _file_label(file: object | None) -> str:
        if file is None:
            return "unknown"
        try:
            return file.get_path() or file.get_uri()  # type: ignore[attr-defined]
        except Exception:
            return "unknown"

    @staticmethod
    def _is_relevant_source(file: object | None) -> bool:
        label = ThemeWatcher._file_label(file)
        if label == "unknown" or "://" in label:
            return True
        changed = Path(label)
        for source in theme_watch_paths():
            if changed == source:
                return True
            if changed == source.parent and not source.exists():
                return True
            if source.is_dir() and source in changed.parents:
                return True
        return False

    def _on_changed(self, _monitor: object, file: object, _other_file: object, event: object) -> None:
        if not self._running:
            return
        event_name = self._event_name(event)
        if event_name not in self._RELEVANT_EVENTS:
            return
        if not self._is_relevant_source(file):
            return
        source = self._file_label(file)
        LOGGER.debug("theme event=%s source=%s", event_name, source)
        if event_name in {"DELETED", "MOVED", "RENAMED", "CREATED"}:
            self._schedule_monitor_rebuild()
        try:
            self.on_change(event_name, source)
        except Exception as error:
            LOGGER.error("theme change callback failed event=%s source=%s error=%s", event_name, source, error)

    def _schedule_monitor_rebuild(self) -> None:
        glib = self._glib
        if glib is None or self._monitor_rebuild_source:
            return
        self._monitor_rebuild_source = glib.idle_add(self._rebuild_monitors)  # type: ignore[attr-defined]

    def _rebuild_monitors(self) -> bool:
        self._monitor_rebuild_source = 0
        if self._running:
            self._install_monitors()
        return False

    def _cancel_monitors(self) -> None:
        for monitor in self.monitors:
            try:
                monitor.cancel()  # type: ignore[attr-defined]
            except Exception:
                continue
        self.monitors.clear()

    def stop(self) -> None:
        self._running = False
        glib = self._glib
        if glib is not None and self._monitor_rebuild_source:
            try:
                glib.source_remove(self._monitor_rebuild_source)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._monitor_rebuild_source = 0
        self._cancel_monitors()
        self._gio = None
        self._glib = None

"""Debounced live monitoring for the currently visible directory.

The monitor is intentionally small and owns no GTK widgets.  It watches one
directory at a time, coalesces the burst of events produced by common file
operations, and drops callbacks that belong to an older directory or to a
stopped monitor.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib

from .diagnostics import LOGGER


DirectoryChanged = Callable[[str, str], None]


class DirectoryMonitor:
    """Watch one directory and emit one debounced callback for changes."""

    RELEVANT_EVENTS = frozenset(
        {
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.MOVED,
            Gio.FileMonitorEvent.RENAMED,
            Gio.FileMonitorEvent.ATTRIBUTE_CHANGED,
        }
    )

    def __init__(self, on_changed: DirectoryChanged, debounce_ms: int = 120) -> None:
        self._on_changed = on_changed
        self.debounce_ms = max(80, min(150, int(debounce_ms)))
        self.monitor: Gio.FileMonitor | None = None
        self.location: str | None = None
        self._debounce_source = 0
        self._generation = 0
        self._pending_event = "unknown"

    @property
    def is_running(self) -> bool:
        return self.monitor is not None and self.location is not None

    def start(self, folder: Gio.File | str, location: str | None = None) -> bool:
        """Start watching *folder*, returning false when the backend rejects it."""

        self.stop()
        self._generation += 1
        generation = self._generation
        file = self._as_file(folder)
        target = location or file.get_uri()
        try:
            monitor = file.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except (GLib.Error, TypeError, ValueError) as error:
            LOGGER.debug("filesystem monitor unavailable location=%s error=%s", target, error)
            return False

        self.monitor = monitor
        self.location = target
        monitor.connect("changed", self._on_monitor_changed)
        LOGGER.debug("filesystem monitor start location=%s debounce_ms=%d", target, self.debounce_ms)
        return generation == self._generation

    def stop(self) -> None:
        """Cancel the active monitor and any callback waiting in the debounce."""

        self._generation += 1
        if self._debounce_source:
            GLib.source_remove(self._debounce_source)
            self._debounce_source = 0
        if self.monitor is not None:
            try:
                self.monitor.cancel()
            except (GLib.Error, TypeError, ValueError) as error:
                LOGGER.debug("filesystem monitor stop failed: %s", error)
        if self.monitor is not None or self.location is not None:
            LOGGER.debug("filesystem monitor stopped location=%s", self.location or "unknown")
        self.monitor = None
        self.location = None
        self._pending_event = "unknown"

    def _on_monitor_changed(
        self,
        _monitor: Gio.FileMonitor,
        file: Gio.File,
        _other_file: Gio.File | None,
        event_type: Gio.FileMonitorEvent,
    ) -> None:
        if not self.is_running or event_type not in self.RELEVANT_EVENTS:
            return
        location = self.location
        if location is None:
            return
        self._pending_event = self._event_name(event_type)
        LOGGER.debug(
            "filesystem monitor event=%s location=%s changed=%s",
            self._pending_event,
            location,
            self._file_label(file),
        )
        if self._debounce_source:
            GLib.source_remove(self._debounce_source)
        generation = self._generation
        self._debounce_source = GLib.timeout_add(
            self.debounce_ms,
            self._flush,
            generation,
            location,
        )

    def _flush(self, generation: int, location: str) -> bool:
        self._debounce_source = 0
        if generation != self._generation or not self.is_running or self.location != location:
            return False
        event = self._pending_event
        LOGGER.debug("filesystem refresh scheduled location=%s event=%s", location, event)
        self._on_changed(location, event)
        return False

    @staticmethod
    def _as_file(folder: Gio.File | str) -> Gio.File:
        if isinstance(folder, Gio.File):
            return folder
        target = str(folder)
        return Gio.File.new_for_uri(target) if "://" in target else Gio.File.new_for_path(target)

    @staticmethod
    def _event_name(event_type: Gio.FileMonitorEvent) -> str:
        value_nick = getattr(event_type, "value_nick", None)
        if value_nick:
            return str(value_nick).upper().replace("-", "_")
        return str(event_type).rsplit(".", 1)[-1].upper().replace("-", "_")

    @staticmethod
    def _file_label(file: Gio.File | None) -> str:
        if file is None:
            return "unknown"
        try:
            return file.get_path() or file.get_uri()
        except (GLib.Error, TypeError, ValueError):
            return "unknown"

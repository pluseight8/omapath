from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gio", "2.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
    from omapath.directory_monitor import DirectoryMonitor
    from omapath.ui.simple_window import FileRow, OmapathApplication, SimpleFileView

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gdk = None  # type: ignore[assignment]
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]
    DirectoryMonitor = None  # type: ignore[assignment]
    FileRow = None  # type: ignore[assignment]
    OmapathApplication = None  # type: ignore[assignment]
    SimpleFileView = None  # type: ignore[assignment]
    GTK_IMPORT_ERROR = error


class DirectoryMonitorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GIO/GTK unavailable: {GTK_IMPORT_ERROR}")

    @staticmethod
    def pump_until(predicate: object, timeout: float = 3.0) -> bool:
        assert GLib is not None
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if callable(predicate) and predicate():
                return True
            time.sleep(0.01)
        return bool(predicate()) if callable(predicate) else False

    def test_current_directory_events_are_debounced_and_stop_is_quiet(self) -> None:
        assert DirectoryMonitor is not None
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            events: list[tuple[str, str]] = []
            monitor = DirectoryMonitor(lambda location, event: events.append((location, event)))
            self.assertTrue(monitor.start(str(root), str(root)))
            try:
                changed = root / "changed.txt"
                for value in ("one", "two", "three"):
                    changed.write_text(value, encoding="utf-8")
                self.assertTrue(self.pump_until(lambda: bool(events)))
                self.assertEqual(events[0][0], str(root))
                self.assertIn(events[0][1], {"CREATED", "CHANGED", "CHANGES_DONE_HINT"})
            finally:
                monitor.stop()

            count_after_stop = len(events)
            (root / "after-stop.txt").write_text("ignored", encoding="utf-8")
            self.pump_until(lambda: False, timeout=0.3)
            self.assertEqual(len(events), count_after_stop)

    def test_starting_a_new_location_invalidates_the_old_monitor(self) -> None:
        assert DirectoryMonitor is not None
        with tempfile.TemporaryDirectory() as first_text, tempfile.TemporaryDirectory() as second_text:
            first = Path(first_text)
            second = Path(second_text)
            events: list[tuple[str, str]] = []
            monitor = DirectoryMonitor(lambda location, event: events.append((location, event)))
            self.assertTrue(monitor.start(str(first), str(first)))
            self.assertTrue(monitor.start(str(second), str(second)))
            try:
                (first / "stale.txt").write_text("stale", encoding="utf-8")
                (second / "current.txt").write_text("current", encoding="utf-8")
                self.assertTrue(self.pump_until(lambda: bool(events)))
                self.assertTrue(all(location == str(second) for location, _event in events))
            finally:
                monitor.stop()


class LiveRefreshRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK/GLib unavailable: {GTK_IMPORT_ERROR}")

    def test_stale_directory_event_is_ignored_and_current_event_preserves_selection(self) -> None:
        assert OmapathApplication is not None
        application = OmapathApplication.__new__(OmapathApplication)
        application.shutting_down = False
        application.current_location = "/current"
        application.load_directory = Mock()

        application._on_directory_changed("/old", "CREATED")
        application._on_directory_changed("/current", "CHANGED")

        application.load_directory.assert_called_once_with("/current", preserve_selection=True)

    def test_selection_is_restored_by_uri_after_model_replacement(self) -> None:
        assert FileRow is not None and Gio is not None and Gtk is not None
        assert SimpleFileView is not None
        view = SimpleFileView.__new__(SimpleFileView)
        view.store = Gio.ListStore.new(FileRow)
        view.selection = Gtk.MultiSelection.new(view.store)

        old_rows = [
            FileRow(Gio.File.new_for_path("/tmp/omapath-live-a"), "a", "a", False, "text/plain"),
            FileRow(Gio.File.new_for_path("/tmp/omapath-live-b"), "b", "b", False, "text/plain"),
        ]
        for row in old_rows:
            view.store.append(row)
        view.selection.select_item(1, True)
        selected = view.selected_locations()

        view.store.remove_all()
        refreshed_rows = [
            FileRow(Gio.File.new_for_path("/tmp/omapath-live-b"), "b", "b", False, "text/plain"),
            FileRow(Gio.File.new_for_path("/tmp/omapath-live-c"), "c", "c", False, "text/plain"),
        ]
        for row in refreshed_rows:
            view.store.append(row)
        view.restore_selection(selected)

        self.assertEqual([row.name for row in view.selected_rows()], ["b"])


class LiveRefreshRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK/GLib unavailable: {GTK_IMPORT_ERROR}")
        if Gdk is None or Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no graphical display")

    def test_external_create_refreshes_the_active_view_and_keeps_selection(self) -> None:
        assert OmapathApplication is not None
        assert GLib is not None
        application = OmapathApplication()
        application.build_window()
        assert application.window is not None
        application.window.present()
        context = GLib.MainContext.default()

        def pump_until(predicate: object, timeout: float = 3.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if callable(predicate) and predicate():
                    return True
                time.sleep(0.01)
            return bool(predicate()) if callable(predicate) else False

        try:
            with tempfile.TemporaryDirectory() as root_text:
                root = Path(root_text)
                (root / "kept.txt").write_text("kept", encoding="utf-8")
                application.navigate(str(root), record_history=False)
                self.assertTrue(
                    pump_until(
                        lambda: application.file_view is not None
                        and application.file_view.store.get_n_items() == 1
                    )
                )
                assert application.file_view is not None
                view = application.file_view
                view.selection.select_item(0, True)
                selected_uri = view.store.get_item(0).location

                (root / "created.txt").write_text("created", encoding="utf-8")
                self.assertTrue(pump_until(lambda: view.store.get_n_items() == 2))
                self.assertEqual({row.location for row in view.selected_rows()}, {selected_uri})
        finally:
            application.do_shutdown()

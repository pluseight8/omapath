from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio
from omapath.tabs import TabState
from omapath.ui.simple_window import FileRow, OmapathApplication

try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gdk, GLib

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gdk = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    GTK_IMPORT_ERROR = error


class TabStateTests(unittest.TestCase):
    def test_clone_copies_histories_and_preferences_independently(self) -> None:
        original = TabState(
            "/tmp/a",
            back_history=["/tmp"],
            forward_history=["/tmp/b"],
            show_hidden=True,
            sort_key="modified",
            sort_descending=True,
            folders_first=False,
            icon_zoom=72,
            filter_query="*.md",
            filter_mode="glob",
            group_criterion="type",
            groups_enabled=True,
            show_previews=False,
            selected_locations=["file:///tmp/a"],
        )
        clone = original.clone()
        clone.back_history.append("/tmp/c")
        clone.sort_key = "name"
        clone.selected_locations.append("file:///tmp/b")
        self.assertEqual(original.back_history, ["/tmp"])
        self.assertEqual(original.sort_key, "modified")
        self.assertEqual(original.filter_query, "*.md")
        self.assertEqual(original.filter_mode, "glob")
        self.assertEqual(original.group_criterion, "type")
        self.assertTrue(original.groups_enabled)
        self.assertFalse(original.show_previews)
        self.assertEqual(original.selected_locations, ["file:///tmp/a"])


class HeadlessTabApplicationTests(unittest.TestCase):
    def test_move_left_and_right_keep_active_tab_identity(self) -> None:
        application = OmapathApplication.__new__(OmapathApplication)
        application.tabs = [TabState("/one"), TabState("/two"), TabState("/three")]
        application.active_tab_index = 1
        application.settings = {"session_tabs": [], "active_tab": 1}
        application._sync_active_tab_state = lambda: None
        application._render_tab_bar = lambda: None
        application.move_tab_left(1)
        self.assertEqual([tab.location for tab in application.tabs], ["/two", "/one", "/three"])
        self.assertEqual(application.active_tab_index, 0)
        application.move_tab_right(0)
        self.assertEqual([tab.location for tab in application.tabs], ["/one", "/two", "/three"])
        self.assertEqual(application.active_tab_index, 1)

    def test_folder_activation_stays_in_the_same_tab(self) -> None:
        application = OmapathApplication.__new__(OmapathApplication)
        application.navigate = Mock()
        row = FileRow(Gio.File.new_for_path("/tmp/folder"), "folder", "folder", True, "inode/directory")

        application.activate_row(row)

        application.navigate.assert_called_once_with(row.location)

    def test_tabs_keep_independent_history_and_preferences(self) -> None:
        application = OmapathApplication()
        try:
            with tempfile.TemporaryDirectory() as root_text:
                root = Path(root_text)
                documents = root / "Documents"
                downloads = root / "Downloads"
                documents.mkdir()
                downloads.mkdir()
                application.current_location = str(root)
                application.tabs[0].location = str(root)
                application.navigate(str(documents), record_history=True)
                application.new_tab()
                application.set_show_hidden(True, persist=False)
                application.set_sort_key("modified", persist=False)
                application.set_zoom(72, persist=False)
                application.navigate(str(downloads), record_history=True)

                self.assertEqual(len(application.tabs), 2)
                application.switch_tab(0)
                self.assertEqual(application.current_location, str(documents))
                self.assertEqual(application.back_history, [str(root)])
                self.assertFalse(application.show_hidden)
                self.assertEqual(application.sort_key, "name")
                self.assertEqual(application.icon_zoom, 48)

                application.switch_tab(1)
                self.assertEqual(application.current_location, str(downloads))
                self.assertTrue(application.show_hidden)
                self.assertEqual(application.sort_key, "modified")
                self.assertEqual(application.icon_zoom, 72)
                application.go_back()
                self.assertEqual(application.current_location, str(documents))
                self.assertEqual(application.back_history, [])
        finally:
            application.do_shutdown()

    def test_close_and_reopen_restores_the_closed_tab_state(self) -> None:
        application = OmapathApplication()
        try:
            application.new_tab()
            location = application.current_location
            application.set_show_hidden(True, persist=False)
            application.close_tab()
            self.assertEqual(len(application.tabs), 1)
            self.assertEqual(len(application.closed_tabs), 1)
            application.reopen_tab()
            self.assertEqual(len(application.tabs), 2)
            self.assertEqual(application.current_location, location)
            self.assertTrue(application.show_hidden)
        finally:
            application.do_shutdown()


class TabRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK unavailable: {GTK_IMPORT_ERROR}")
        if Gdk is None or Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no graphical display")

    def test_location_entry_and_visible_tab_strip_use_one_file_view(self) -> None:
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
                documents = root / "Documents"
                downloads = root / "Downloads"
                documents.mkdir()
                downloads.mkdir()
                application.navigate(str(root), record_history=False)
                self.assertTrue(
                    pump_until(
                        lambda: application.file_view is not None
                        and application.file_view.store.get_n_items() == 2
                    )
                )
                application.show_location_entry()
                assert application.location_stack is not None
                assert application.location_entry is not None
                self.assertIs(application.location_stack.get_visible_child(), application.location_entry)
                application.location_entry.set_text(str(documents))
                application.location_entry.emit("activate")
                self.assertTrue(pump_until(lambda: application.current_location == str(documents)))
                application.new_tab()
                self.assertEqual(len(application.tabs), 2)
                self.assertTrue(application.tab_bar is not None and application.tab_bar.get_visible())
                self.assertIsNotNone(application.file_view)
        finally:
            application.do_shutdown()

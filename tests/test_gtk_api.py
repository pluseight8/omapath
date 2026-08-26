from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk
    from omapath.compat import set_accessible_label
    from omapath.ui.file_view import FileRow, make_file_rows

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - depends on the host image
    Gdk = None  # type: ignore[assignment]
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]
    set_accessible_label = None  # type: ignore[assignment]
    FileRow = None  # type: ignore[assignment]
    make_file_rows = None  # type: ignore[assignment]
    GTK_IMPORT_ERROR = error


class GtkApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")

    def test_accessibility_uses_the_gtk4_binding_api(self) -> None:
        source_paths = list((PROJECT_ROOT / "omapath").rglob("*.py"))
        self.assertTrue(source_paths)
        self.assertFalse(any(".set_accessible_name(" in path.read_text(encoding="utf-8") for path in source_paths))
        self.assertIsNone(getattr(Gtk.Button, "set_accessible_name", None))
        self.assertIsNotNone(getattr(Gtk.Widget, "update_property", None))
        self.assertIsNotNone(getattr(Gtk.AccessibleProperty, "LABEL", None))

        class Recorder:
            def __init__(self) -> None:
                self.properties = None
                self.values = None

            def update_property(self, properties: object, values: object) -> None:
                self.properties = properties
                self.values = values

        recorder = Recorder()
        set_accessible_label(recorder, "Open folder")  # type: ignore[operator,arg-type]
        self.assertEqual(recorder.properties, [Gtk.AccessibleProperty.LABEL])
        self.assertEqual(recorder.values[0].get_string(), "Open folder")  # type: ignore[index]

    def test_used_gtk4_surfaces_exist_in_this_binding(self) -> None:
        expected = {
            Gtk.Widget: ("update_property", "get_clipboard"),
            Gtk.GridView: ("set_min_columns", "set_max_columns", "set_single_click_activate", "get_model"),
            Gtk.ListView: ("set_single_click_activate", "get_model"),
            Gtk.MultiSelection: ("get_model", "get_n_items"),
            Gtk.DropTarget: ("set_gtypes", "set_preload", "get_current_drop"),
            Gtk.Popover: ("set_parent", "set_pointing_to", "popup"),
            Gtk.GestureClick: ("set_propagation_phase", "set_state"),
            Gtk.EventControllerKey: ("connect",),
            Gdk.FileList: ("get_files",),
            Gdk.Clipboard: ("set_content", "read_text_async", "read_text_finish"),
            Gdk.ContentProvider: ("new_for_value", "new_for_bytes", "new_union"),
            Gio.File: ("new_for_path", "new_for_uri", "query_info", "query_file_type", "monitor_directory", "monitor_file"),
            Gio.FileInfo: ("get_file_type", "get_is_symlink", "get_attribute_boolean", "get_attribute_uint64"),
            Gio.FileMonitor: ("cancel",),
            Gio.FileEnumerator: ("next_files_async", "next_files_finish", "close"),
            Gio.SubprocessLauncher: ("new", "set_cwd", "spawnv"),
            Gio.Subprocess: ("communicate_utf8_async", "communicate_utf8_finish", "force_exit"),
            Gio.VolumeMonitor: ("get", "get_mounts", "get_volumes", "connect"),
            Gio.Mount: ("get_root", "unmount_with_operation", "unmount_with_operation_finish"),
            Gio.Volume: ("get_mount", "can_mount", "mount", "mount_finish"),
        }
        for owner, methods in expected.items():
            for method in methods:
                with self.subTest(owner=owner.__name__, method=method):
                    self.assertIsNotNone(getattr(owner, method, None))
        for owner, methods in {
            Gdk: ("Display", "Clipboard", "ContentProvider", "Texture", "keyval_to_lower"),
            Gio: ("AppInfo", "content_type_get_symbolic_icon"),
            GLib: ("idle_add", "timeout_add", "source_remove"),
        }.items():
            for method in methods:
                with self.subTest(owner=getattr(owner, "__name__", repr(owner)), method=method):
                    self.assertIsNotNone(getattr(owner, method, None))

    def test_gridview_spacing_is_not_called_as_a_missing_api(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in (PROJECT_ROOT / "omapath").rglob("*.py"))
        self.assertNotIn("set_row_spacing", source)
        self.assertNotIn("set_column_spacing", source)

    def test_file_view_store_and_selection_share_the_same_model(self) -> None:
        store = Gio.ListStore.new(FileRow)
        store.append(FileRow("first"))
        store.append(FileRow("second"))
        selection = Gtk.MultiSelection.new(store)

        self.assertEqual(store.get_n_items(), 2)
        self.assertIs(selection.get_model(), store)
        self.assertEqual(selection.get_n_items(), 2)

    def test_file_row_conversion_allocates_distinct_gobjects(self) -> None:
        rows = make_file_rows(["A", "B", "C", "D", "E"])  # type: ignore[operator]
        store = Gio.ListStore.new(FileRow)
        for row in rows:
            store.append(row)

        model_rows = [store.get_item(position) for position in range(store.get_n_items())]
        self.assertEqual(store.get_n_items(), 5)
        self.assertEqual(len({id(row) for row in model_rows}), 5)
        self.assertEqual(len({hash(row) for row in model_rows}), 5)


class GtkRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no graphical display")

    def test_builds_main_window_and_browses_a_folder_in_the_same_tab(self) -> None:
        from unittest.mock import patch

        from omapath.ui.window import OmapathApplication

        with patch("omapath.ui.window.save_settings", return_value=True):
            application = OmapathApplication()
            try:
                application.build_window()
                application.window.present()
                for attribute in ("window", "back_button", "grid", "listbox", "sidebar", "status_label"):
                    with self.subTest(attribute=attribute):
                        self.assertIsNotNone(getattr(application, attribute, None))
                self.assertIsNone(getattr(application, "inspector", None))

                context = GLib.MainContext.default()

                def pump() -> None:
                    for _ in range(80):
                        while context.pending():
                            context.iteration(False)
                        time.sleep(0.005)

                def assert_file_view_chain(expected: int, label: str) -> None:
                    active = application.active_file_view()
                    selection_model = active.selection.get_model()
                    view_model = active.view.get_model()
                    with self.subTest(label=label, chain="backend"):
                        self.assertEqual(len(application.all_items), expected)
                    with self.subTest(label=label, chain="stack"):
                        self.assertEqual(application.content_stack.get_visible_child_name(), application.view_mode)
                        expected_scroll = {
                            "grid": application.grid_scroll,
                            "compact": application.compact_scroll,
                            "details": application.list_scroll,
                        }[application.view_mode]
                        self.assertIs(application.content_stack.get_visible_child(), expected_scroll)
                    with self.subTest(label=label, chain="models"):
                        self.assertEqual(active.model.get_n_items(), expected)
                        self.assertEqual(selection_model.get_n_items(), expected)
                        self.assertEqual(view_model.get_n_items(), expected)
                        self.assertIs(application.content_stack.get_visible_child().get_child(), active.view)
                        model_rows = [active.model.get_item(position) for position in range(expected)]
                        self.assertEqual(len({id(row) for row in model_rows}), expected)
                        self.assertEqual(len({hash(row) for row in model_rows}), expected)
                    with self.subTest(label=label, chain="factory"):
                        self.assertGreater(active.factory_setup_count, 0)
                        self.assertGreater(active.factory_bind_count, 0)
                        self.assertGreater(active.factory_child_count, 0)
                        children = [state.root for state in active._item_widgets.values()]
                        self.assertEqual(len({id(child) for child in children}), len(children))
                        self.assertEqual(len({hash(child) for child in children}), len(children))
                    with self.subTest(label=label, chain="visibility"):
                        self.assertTrue(active.get_visible())
                        self.assertTrue(active.view.get_visible())
                        self.assertTrue(active.get_hexpand())
                        self.assertTrue(active.get_vexpand())

                with tempfile.TemporaryDirectory() as root:
                    home = Path(root)
                    folder_a = home / "FolderA"
                    folder_b = folder_a / "FolderB"
                    folder_b.mkdir(parents=True)
                    (folder_a / "note.txt").write_text("hello", encoding="utf-8")
                    (folder_b / "inside.txt").write_text("inside", encoding="utf-8")

                    application.navigate(str(home), record_history=False)
                    pump()
                    item_a = next(item for item in application.all_items if item.name == "FolderA")
                    tab_count = len(application.tabs)
                    assert_file_view_chain(1, "home")

                    active_view = application.active_file_view()
                    active_view.selection.select_item(0, False)
                    self.assertEqual(active_view.get_selected_item(), item_a)
                    active_view.selection.unselect_all()
                    application.on_virtual_context(active_view.view, item_a, 8, 8)
                    self.assertIsNotNone(application.context_popover)
                    if application.context_popover is not None:
                        application.context_popover.popdown()

                    # This is the same callback emitted by double-click/Enter.
                    item_a_position = next(
                        position
                        for position in range(active_view.model.get_n_items())
                        if active_view.model.get_item(position).item is item_a
                    )
                    active_view._on_activate(active_view.view, item_a_position)
                    pump()
                    self.assertEqual(application.current_location, str(folder_a))
                    self.assertEqual(len(application.tabs), tab_count)
                    self.assertEqual({item.name for item in application.all_items}, {"FolderB", "note.txt"})
                    assert_file_view_chain(2, "folder-a")

                    # Exercise the ListView path as well as the initial GridView.
                    application.set_view("details")
                    pump()
                    assert_file_view_chain(2, "folder-a-details")

                    item_b = next(item for item in application.all_items if item.name == "FolderB")
                    active_view = application.active_file_view()
                    item_b_position = next(
                        position
                        for position in range(active_view.model.get_n_items())
                        if active_view.model.get_item(position).item is item_b
                    )
                    active_view._on_activate(active_view.view, item_b_position)
                    pump()
                    self.assertEqual(application.current_location, str(folder_b))
                    self.assertEqual(len(application.tabs), tab_count)
                    assert_file_view_chain(1, "folder-b")

                    application.go_back()
                    pump()
                    self.assertEqual(application.current_location, str(folder_a))
                    application.go_forward()
                    pump()
                    self.assertEqual(application.current_location, str(folder_b))

                # These builders exercise the remaining accessibility-bearing
                # controls that are not constructed by the initial top bar.
                application.show_settings()
                application.show_command_palette()

                for _ in range(10):
                    if not context.pending():
                        break
                    context.iteration(False)
            finally:
                if getattr(application, "settings_window", None) is not None:
                    application.settings_window.close()
                if getattr(application, "palette_window", None) is not None:
                    application.palette_window.close()
                application.do_shutdown()

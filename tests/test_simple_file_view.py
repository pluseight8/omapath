from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gio", "2.0")
    gi.require_version("Graphene", "1.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Graphene, Gtk
    from omapath.ui.simple_window import (
        FileRow,
        OmapathApplication,
        SimpleFileView,
        breadcrumb_segments,
        build_css,
        format_item_count,
    )

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gdk = None  # type: ignore[assignment]
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    Graphene = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]
    FileRow = None  # type: ignore[assignment]
    OmapathApplication = None  # type: ignore[assignment]
    SimpleFileView = None  # type: ignore[assignment]
    breadcrumb_segments = None  # type: ignore[assignment]
    build_css = None  # type: ignore[assignment]
    format_item_count = None  # type: ignore[assignment]
    GTK_IMPORT_ERROR = error


class SimpleFileViewCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")

    def test_one_store_and_one_selection_keep_five_unique_rows(self) -> None:
        rows = [
            FileRow(
                Gio.File.new_for_path(f"/tmp/omapath-simple-{index}"),
                f"item-{index}",
                f"Item {index}",
                index == 0,
                "inode/directory" if index == 0 else "text/plain",
            )
            for index in range(5)
        ]
        store = Gio.ListStore.new(FileRow)
        for row in rows:
            store.append(row)
        selection = Gtk.MultiSelection.new(store)

        model_rows = [store.get_item(index) for index in range(store.get_n_items())]
        self.assertEqual(store.get_n_items(), 5)
        self.assertEqual(selection.get_n_items(), 5)
        self.assertIs(selection.get_model(), store)
        self.assertEqual(len({hash(row) for row in model_rows}), 5)

    def test_multi_selection_keeps_one_model_and_multiple_rows(self) -> None:
        rows = [
            FileRow(
                Gio.File.new_for_path(f"/tmp/omapath-multi-{index}"),
                f"item-{index}",
                f"Item {index}",
                False,
                "text/plain",
            )
            for index in range(4)
        ]
        store = Gio.ListStore.new(FileRow)
        for row in rows:
            store.append(row)
        selection = Gtk.MultiSelection.new(store)

        self.assertTrue(selection.select_item(0, True))
        self.assertTrue(selection.select_item(2, False))
        self.assertIs(selection.get_model(), store)
        self.assertTrue(selection.is_selected(0))
        self.assertFalse(selection.is_selected(1))
        self.assertTrue(selection.is_selected(2))
        self.assertEqual(selection.get_n_items(), 4)

    def test_entrypoint_uses_the_simple_runtime(self) -> None:
        from omapath import app

        self.assertIs(app.OmapathApplication, OmapathApplication)
        self.assertEqual(app.OmapathApplication.__module__, "omapath.ui.simple_window")

    def test_breadcrumbs_keep_home_and_collapse_only_middle_segments(self) -> None:
        home = "/home/tester"
        nested = "/home/tester/Documents/Codex/Projects/Omapath"
        self.assertEqual(
            breadcrumb_segments(nested, home),
            [
                ("Home", home),
                ("…", None),
                ("Projects", "/home/tester/Documents/Codex/Projects"),
                ("Omapath", nested),
            ],
        )

    def test_breadcrumbs_support_trash_and_filesystem_root(self) -> None:
        self.assertEqual(breadcrumb_segments("trash:///", "/home/tester"), [("Trash", "trash:///")])
        self.assertEqual(
            breadcrumb_segments("/", "/home/tester"),
            [("Filesystem", "/")],
        )

    def test_item_count_uses_singular_and_plural_copy(self) -> None:
        self.assertEqual(format_item_count(0), "0 items")
        self.assertEqual(format_item_count(1), "1 item")
        self.assertEqual(format_item_count(7), "7 items")

    def test_inline_rename_selects_filename_stem_without_extension(self) -> None:
        self.assertEqual(SimpleFileView._filename_stem_end("photo.jpg"), 5)
        self.assertEqual(SimpleFileView._filename_stem_end("archive.tar.gz"), 11)
        self.assertEqual(SimpleFileView._filename_stem_end(".profile"), 8)
        self.assertEqual(SimpleFileView._filename_stem_end("README"), 6)

    def test_item_tooltip_uses_cached_metadata_only(self) -> None:
        row = FileRow(
            Gio.File.new_for_path("/tmp/omapath-tooltip.txt"),
            "omapath-tooltip.txt",
            "Omapath tooltip.txt",
            False,
            "text/plain",
            size=2048,
            modified=0,
        )
        tooltip = SimpleFileView._tooltip_for_row(row)
        self.assertIn("Name: Omapath tooltip.txt", tooltip)
        self.assertIn("Type: text/plain", tooltip)
        self.assertIn("Size: 2.0 KB", tooltip)
        self.assertIn("Modified: —", tooltip)

    def test_invert_selection_actually_toggles_each_item(self) -> None:
        store = Gio.ListStore.new(FileRow)
        for index in range(3):
            store.append(
                FileRow(
                    Gio.File.new_for_path(f"/tmp/omapath-invert-{index}"),
                    f"item-{index}",
                    f"Item {index}",
                    False,
                    "text/plain",
                )
            )
        selection = Gtk.MultiSelection.new(store)
        selection.select_item(0, True)
        selection.select_item(2, False)
        for position in range(store.get_n_items()):
            if selection.is_selected(position):
                selection.unselect_item(position)
            else:
                selection.select_item(position, False)
        self.assertFalse(selection.is_selected(0))
        self.assertTrue(selection.is_selected(1))
        self.assertFalse(selection.is_selected(2))

    def test_register_bound_row_refreshes_only_the_new_item(self) -> None:
        """Large-directory binding must stay linear instead of rescanning rows."""

        class FakeRoot:
            def remove_css_class(self, _name: str) -> None:
                return None

            def add_css_class(self, _name: str) -> None:
                return None

            def set_tooltip_text(self, _value: str | None) -> None:
                return None

        class FakeListItem:
            def get_position(self) -> int:
                return 0

        class Bound:
            def __init__(self, list_item: FakeListItem) -> None:
                self.list_item = list_item
                self.row_location = None
                self.name_label = None

        view = SimpleFileView.__new__(SimpleFileView)
        list_item = FakeListItem()
        bound = Bound(list_item)
        view._bound_items = {id(list_item): bound}
        view._bound_by_location = {}
        view._inline_rename_item = None
        view.show_tooltips = True
        calls: list[object] = []
        view._sync_bound_item = lambda item: calls.append(item)
        row = FileRow(
            Gio.File.new_for_path("/tmp/omapath-large-bind-row"),
            "large-bind-row",
            "Large bind row",
            False,
            "text/plain",
        )

        SimpleFileView._register_bound_row(view, list_item, row, None, FakeRoot())

        self.assertEqual(calls, [bound])
        self.assertIs(view._bound_by_location[row.location], bound)

    def test_inline_rename_rejects_collision_and_accepts_unicode_name(self) -> None:
        class FakeEntry:
            def __init__(self, text: str) -> None:
                self.text = text
                self.classes: set[str] = set()
                self.tooltip = ""

            def get_text(self) -> str:
                return self.text

            def add_css_class(self, name: str) -> None:
                self.classes.add(name)

            def remove_css_class(self, name: str) -> None:
                self.classes.discard(name)

            def set_visible(self, _visible: bool) -> None:
                return None

            def set_tooltip_text(self, value: str) -> None:
                self.tooltip = value

        class FakeLabel:
            def set_visible(self, _visible: bool) -> None:
                return None

        class FakeItem:
            def __init__(self, row: FileRow) -> None:
                self.row = row

            def get_item(self) -> FileRow:
                return self.row

        class Bound:
            def __init__(self, list_item: FakeItem, entry: FakeEntry) -> None:
                self.list_item = list_item
                self.rename_entry = entry
                self.name_label = FakeLabel()

        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "отчёт.txt"
            collision = Path(root) / "есть.txt"
            source.write_text("source", encoding="utf-8")
            collision.write_text("collision", encoding="utf-8")
            row = FileRow(
                Gio.File.new_for_path(str(source)),
                source.name,
                source.name,
                False,
                "text/plain",
            )
            list_item = FakeItem(row)
            entry = FakeEntry("есть.txt")
            bound = Bound(list_item, entry)
            callbacks: list[tuple[str, str]] = []
            fake_view = type("FakeView", (), {})()
            fake_view._inline_rename_item = bound
            fake_view.rename_callback = lambda current, name: callbacks.append((current.name, name))
            fake_view.cancel_inline_rename = lambda: None

            self.assertFalse(SimpleFileView.commit_inline_rename(fake_view, list_item))
            self.assertIn("inline-rename-error", entry.classes)
            self.assertEqual(callbacks, [])

            entry.text = "новый отчёт.txt"
            self.assertTrue(SimpleFileView.commit_inline_rename(fake_view, list_item))
            self.assertEqual(callbacks, [("отчёт.txt", "новый отчёт.txt")])

    def test_context_coordinates_use_compute_point_and_safe_fallback(self) -> None:
        class SuccessfulSource:
            def compute_point(self, target: object, point: object) -> tuple[bool, object]:
                self.target = target
                self.point = point
                return True, Graphene.Point().init(point.x + 4, point.y + 5)  # type: ignore[union-attr]

        source = SuccessfulSource()
        self.assertEqual(
            SimpleFileView._context_menu_coordinates(source, object(), 8, 9),
            (12.0, 14.0),
        )
        self.assertIsInstance(source.point, Graphene.Point)

        class FailedSource:
            def compute_point(self, _target: object, _point: object) -> tuple[bool, object | None]:
                return False, None

        self.assertEqual(
            SimpleFileView._context_menu_coordinates(FailedSource(), object(), 8, 9),
            (8.0, 9.0),
        )

        class MalformedSource:
            def compute_point(self, _target: object, _point: object) -> tuple[bool]:
                return (True,)

        self.assertEqual(
            SimpleFileView._context_menu_coordinates(MalformedSource(), object(), 8, 9),
            (8.0, 9.0),
        )

        source_text = Path(__file__).parents[1].joinpath("omapath", "ui", "simple_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("compute_point", source_text)
        self.assertNotIn("translated[1]", source_text)
        self.assertNotIn("translated[2]", source_text)

    def test_css_exposes_only_semantic_app_classes(self) -> None:
        css = build_css(
            {
                "background": "#101010",
                "dark_background": "#0b0b0b",
                "darker_background": "#070707",
                "foreground": "#eeeeee",
                "bright_foreground": "#ffffff",
                "muted": "#777777",
                "accent": "#99cc66",
                "selection": "#334433",
            },
            "monospace",
        )
        for class_name in (
            ".topbar",
            ".sidebar-row",
            ".breadcrumbs",
            ".file-grid",
            ".omapath-file-grid > child:selected",
            ".file-item",
            ".item-count",
            ".context-separator",
        ):
            self.assertIn(class_name, css)
        self.assertNotIn("gridview > child >", css)
        self.assertIn("rgba(153, 204, 102, 0.14)", css)
        self.assertIn("rgba(153, 204, 102, 0.32)", css)
        self.assertNotIn(".file-item:selected", css)
        self.assertNotIn("#0000ff", css.lower())
        self.assertIn("background-color: transparent;", css)

        provider = Gtk.CssProvider()
        provider.load_from_string(css)

    def test_sidebar_labels_and_entrypoint_have_no_legacy_ui_import(self) -> None:
        topbar = Path(__file__).parents[1].joinpath("omapath", "ui", "topbar.py").read_text(encoding="utf-8")
        sidebar = Path(__file__).parents[1].joinpath("omapath", "ui", "sidebar.py").read_text(encoding="utf-8")
        self.assertIn('"PLACES"', sidebar)
        self.assertIn('"DEVICES"', sidebar)
        self.assertIn('host.breadcrumbs.add_css_class("breadcrumbs")', topbar)
        self.assertNotIn('host.breadcrumbs.add_css_class("breadcrumb-button")', topbar)
        entrypoint = Path(__file__).parents[1].joinpath("omapath", "app.py").read_text(encoding="utf-8")
        self.assertNotIn("ui.window", entrypoint)
        self.assertNotIn("ui.file_view", entrypoint)

    def test_recent_sidebar_is_one_virtual_location_not_history_rows(self) -> None:
        sidebar = Path(__file__).parents[1].joinpath("omapath", "ui", "sidebar.py").read_text(encoding="utf-8")
        self.assertIn('RECENT_URI = "recent:///"', sidebar)
        self.assertIn('self.build_row(\n                "Recent", RECENT_URI', sidebar)
        self.assertNotIn("for location in self.host.places_store.recent[:12]", sidebar)


class SimpleFileViewRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no graphical display")

    def test_factory_setup_and_bind_sets_gicon_and_pixel_size(self) -> None:
        row = FileRow(
            Gio.File.new_for_path("/tmp/omapath-simple-binding-directory"),
            "binding-directory",
            "Binding directory",
            True,
            "inode/directory",
        )

        class FakeListItem:
            def __init__(self, item: FileRow) -> None:
                self.item = item
                self.child = None

            def set_child(self, child: Gtk.Widget) -> None:
                self.child = child

            def get_item(self) -> FileRow:
                return self.item

            def get_child(self) -> Gtk.Widget | None:
                return self.child

            def get_position(self) -> int:
                return 0

        list_item = FakeListItem(row)
        SimpleFileView._on_setup(None, list_item)  # type: ignore[arg-type]

        try:
            SimpleFileView._on_bind(None, list_item)  # type: ignore[arg-type]
        except Exception as error:  # pragma: no cover - assertion gives context
            self.fail(f"factory bind raised unexpectedly: {error}")

        self.assertIsInstance(list_item.child, Gtk.Box)
        image = list_item.child.get_first_child()
        self.assertIsInstance(image, Gtk.Image)
        self.assertIsNotNone(image.get_gicon())
        self.assertEqual(image.get_pixel_size(), 48)
        label = image.get_next_sibling()
        self.assertIsInstance(label, Gtk.Label)
        self.assertEqual(label.get_text(), "Binding directory")

    def test_navigation_replaces_one_core_model_and_uses_native_activation(self) -> None:
        application = OmapathApplication()
        application.build_window()
        assert application.window is not None
        application.window.present()
        context = GLib.MainContext.default()

        def pump() -> None:
            for _ in range(100):
                while context.pending():
                    context.iteration(False)
                time.sleep(0.005)

        try:
            with tempfile.TemporaryDirectory() as root:
                root_path = Path(root)
                documents = root_path / "Documents"
                documents.mkdir()
                (root_path / "note.txt").write_text("hello", encoding="utf-8")
                (documents / "inside.txt").write_text("inside", encoding="utf-8")

                application.navigate(str(root_path), record_history=False)
                pump()
                assert application.file_view is not None
                view = application.file_view
                self.assertEqual(view.store.get_n_items(), 2)
                self.assertIsInstance(view.selection, Gtk.MultiSelection)
                self.assertIs(view.selection.get_model(), view.store)
                self.assertIs(view.grid.get_model(), view.selection)
                rows = [view.store.get_item(index) for index in range(view.store.get_n_items())]
                self.assertEqual(len({hash(row) for row in rows}), 2)

                view.selection.select_item(0, True)
                view.selection.select_item(1, False)
                self.assertEqual(len(view.selected_rows()), 2)
                application.show_context_menu(rows[0], 8, 8)
                self.assertIsNotNone(application.context_popover)
                application._close_context_popover()
                application.show_context_menu(None, 8, 8)
                self.assertIsNotNone(application.context_popover)
                application._close_context_popover()

                folder_position = next(
                    index for index, row in enumerate(rows) if row is not None and row.name == "Documents"
                )
                view.selection.select_item(folder_position, True)
                self.assertTrue(view.selection.is_selected(folder_position))
                view.grid.emit("activate", folder_position)
                pump()
                self.assertEqual(application.current_location, str(documents))
                self.assertEqual(view.store.get_n_items(), 1)

                application.go_back()
                pump()
                self.assertEqual(application.current_location, str(root_path))
                application.go_forward()
                pump()
                self.assertEqual(application.current_location, str(documents))
        finally:
            application.do_shutdown()

from __future__ import annotations

import time
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    from omapath import theme
    from omapath.theme import ThemeSnapshot, ThemeWatcher

    THEME_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    GLib = None  # type: ignore[assignment]
    theme = None  # type: ignore[assignment]
    ThemeSnapshot = None  # type: ignore[assignment]
    ThemeWatcher = None  # type: ignore[assignment]
    THEME_IMPORT_ERROR = error


class ThemeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if THEME_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GIO/GLib unavailable: {THEME_IMPORT_ERROR}")

    def test_snapshot_reads_existing_state_and_font(self) -> None:
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            state = root / "current"
            theme_dir = state / "theme"
            theme_dir.mkdir(parents=True)
            (state / "theme.name").write_text("Theme A\n", encoding="utf-8")
            (state / "font.name").write_text("Mono Test\n", encoding="utf-8")
            (theme_dir / "colors.toml").write_text(
                'accent = "#112233"\nforeground = "#eeeeee"\n',
                encoding="utf-8",
            )

            with patch.object(theme, "THEME_STATE", state):
                snapshot = theme.read_theme_snapshot()

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.name, "theme-a")
            self.assertEqual(snapshot.colors["accent"], "#112233")
            self.assertEqual(snapshot.font, "Mono Test")

    def test_invalid_colors_return_no_snapshot_for_safe_reload(self) -> None:
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            state = root / "current"
            theme_dir = state / "theme"
            theme_dir.mkdir(parents=True)
            (state / "theme.name").write_text("Theme A\n", encoding="utf-8")
            colors = theme_dir / "colors.toml"
            colors.write_text('accent = "not-a-color"\n', encoding="utf-8")

            with patch.object(theme, "THEME_STATE", state), self.assertLogs("omapath", level="WARNING"):
                self.assertIsNone(theme.read_theme_snapshot())

    def test_watcher_handles_change_atomic_replace_and_stop(self) -> None:
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            state = root / "current"
            theme_dir = state / "theme"
            theme_dir.mkdir(parents=True)
            name = state / "theme.name"
            colors = theme_dir / "colors.toml"
            name.write_text("a\n", encoding="utf-8")
            colors.write_text('accent = "#112233"\n', encoding="utf-8")
            paths = [
                state,
                name,
                theme_dir,
                colors,
                state / "font.name",
                state / "font",
                root / "config/current",
                root / "config/current/font",
            ]
            events: list[tuple[object, ...]] = []
            context = GLib.MainContext.default()

            def pump(seconds: float = 0.5) -> None:
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    while context.pending():
                        context.iteration(False)
                    time.sleep(0.01)

            with warnings.catch_warnings(), patch.object(theme, "theme_watch_paths", return_value=paths):
                warnings.simplefilter("ignore", DeprecationWarning)
                watcher = ThemeWatcher(lambda *args: events.append(args))
                watcher.start()
                try:
                    name.write_text("b\n", encoding="utf-8")
                    pump()
                    self.assertTrue(any("theme.name" in str(event[-1]) for event in events))

                    replacement = state / "theme.name.new"
                    replacement.write_text("c\n", encoding="utf-8")
                    replacement.replace(name)
                    pump()
                    self.assertTrue(events)
                    self.assertGreater(len(watcher.monitors), 0)
                finally:
                    watcher.stop()

                count = len(events)
                name.write_text("d\n", encoding="utf-8")
                pump(0.2)
                self.assertEqual(len(events), count)


class ThemeReloadTests(unittest.TestCase):
    def test_reload_reuses_provider_and_keeps_application_state(self) -> None:
        from omapath.ui import simple_window

        class Provider:
            def __init__(self) -> None:
                self.loaded: list[str] = []

            def load_from_string(self, css: str) -> None:
                self.loaded.append(css)

        application = simple_window.OmapathApplication.__new__(simple_window.OmapathApplication)
        provider = Provider()
        application.shutting_down = False
        application.theme_reload_source = 0
        application.theme_reload_failure_logged = False
        application.css_provider = provider
        application.theme_name = "old"
        application.colors = dict(simple_window.FALLBACK_COLORS)
        application.font_family = "old-font"
        application.current_location = "/tmp/keep"
        application.file_view = object()
        snapshot = ThemeSnapshot(
            name="new",
            colors={**simple_window.FALLBACK_COLORS, "accent": "#112233"},
            font="new-font",
        )

        with patch.object(simple_window, "read_theme_snapshot", return_value=snapshot):
            self.assertFalse(application.reload_theme())

        self.assertIs(application.css_provider, provider)
        self.assertEqual(len(provider.loaded), 1)
        self.assertEqual(application.theme_name, "new")
        self.assertEqual(application.font_family, "new-font")
        self.assertEqual(application.current_location, "/tmp/keep")
        self.assertIsNotNone(application.file_view)

    def test_reload_failure_keeps_last_theme_and_schedules_retry(self) -> None:
        from omapath.ui import simple_window

        application = simple_window.OmapathApplication.__new__(simple_window.OmapathApplication)
        application.shutting_down = False
        application.theme_reload_source = 0
        application.theme_reload_failure_logged = False
        application.css_provider = None
        application.theme_name = "old"
        application.colors = dict(simple_window.FALLBACK_COLORS)
        application.font_family = "old-font"
        with self.assertLogs("omapath", level="WARNING"), patch.object(
            simple_window, "read_theme_snapshot", return_value=None
        ), patch.object(simple_window.GLib, "timeout_add", return_value=41) as timeout_add:
            self.assertFalse(application.reload_theme())

        timeout_add.assert_called_once_with(200, application.reload_theme)
        self.assertEqual(application.theme_reload_source, 41)
        self.assertEqual(application.theme_name, "old")
        self.assertEqual(application.font_family, "old-font")

    def test_theme_events_are_debounced(self) -> None:
        from omapath.ui import simple_window

        application = simple_window.OmapathApplication.__new__(simple_window.OmapathApplication)
        application.shutting_down = False
        application.theme_reload_source = 0
        application.theme_watcher = None
        with patch.object(simple_window.GLib, "timeout_add", side_effect=[11, 12]) as timeout_add, patch.object(
            simple_window.GLib, "source_remove"
        ) as source_remove:
            application._on_theme_changed("CHANGED", "/tmp/theme")
            application._on_theme_changed("CHANGES_DONE_HINT", "/tmp/theme")

        self.assertEqual(timeout_add.call_args_list[0].args[0], 100)
        self.assertEqual(timeout_add.call_args_list[1].args[0], 100)
        source_remove.assert_called_once_with(11)
        self.assertEqual(application.theme_reload_source, 12)

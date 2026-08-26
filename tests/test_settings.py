from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omapath.settings import DEFAULT_SETTINGS, load_settings, save_settings, validate_settings


class SettingsTests(unittest.TestCase):
    def test_compact_ui_defaults_keep_secondary_panels_hidden(self) -> None:
        self.assertFalse(DEFAULT_SETTINGS["show_inspector"])
        self.assertFalse(DEFAULT_SETTINGS["show_terminal_panel"])
        self.assertEqual(DEFAULT_SETTINGS["sidebar_width"], 200)

    def test_corrupt_json_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual(settings["schema_version"], 5)
            self.assertEqual(settings["default_view"], "grid")

    def test_save_is_atomic_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings.json"
            self.assertTrue(save_settings({"default_view": "list", "preview_max_bytes": 999999999}, path))
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["default_view"], "list")
            self.assertEqual(loaded["preview_max_bytes"], 16 * 1024 * 1024)
            self.assertNotEqual(path.with_suffix(".json.tmp").exists(), True)

    def test_unknown_and_invalid_values_are_ignored(self) -> None:
        settings = validate_settings({"default_view": "unsupported", "sort_mode": "bad", "show_hidden": "yes", "unknown": 1})
        self.assertEqual(settings["default_view"], "grid")
        self.assertEqual(settings["sort_mode"], "name")
        self.assertFalse(settings["show_hidden"])
        self.assertEqual(validate_settings({"icon_zoom": 999})["icon_zoom"], 96)
        self.assertNotIn("unknown", settings)

    def test_resizable_panel_and_custom_startup_settings_are_validated(self) -> None:
        settings = validate_settings(
            {
                "start_location": "custom",
                "startup_location": "/tmp/Работа",
                "sidebar_width": 999,
                "info_panel_width": 1,
                "terminal_panel_height": 999,
            }
        )
        self.assertEqual(settings["start_location"], "custom")
        self.assertEqual(settings["startup_location"], "/tmp/Работа")
        self.assertEqual(settings["sidebar_width"], 320)
        self.assertEqual(settings["info_panel_width"], 220)
        self.assertEqual(settings["terminal_panel_height"], 480)

    def test_pinned_places_and_session_tabs_have_separate_shapes(self) -> None:
        settings = validate_settings(
            {
                "pinned_places": ["/tmp", {"location": "/bad"}, 42],
                "session_tabs": [{"location": "/tmp"}, "/bad", 42],
            }
        )
        self.assertEqual(settings["pinned_places"], ["/tmp"])
        self.assertEqual(settings["session_tabs"], [{"location": "/tmp"}])

    def test_dolphin_view_settings_are_validated(self) -> None:
        settings = validate_settings(
            {
                "zoom_level": 999,
                "folder_view_settings": {
                    "/tmp": {"view_mode": "compact", "sort_mode": "type", "sort_reverse": True},
                    "/bad": {"view_mode": "unsupported", "unknown": 1},
                    42: {"view_mode": "grid"},
                },
                "terminal_shell": "/bin/bash",
            }
        )
        self.assertEqual(settings["zoom_level"], 180)
        self.assertEqual(
            settings["folder_view_settings"],
            {"/tmp": {"view_mode": "compact", "sort_mode": "type", "sort_reverse": True}},
        )
        self.assertEqual(settings["terminal_shell"], "/bin/bash")

    def test_nautilus_location_lists_are_bounded_and_deduplicated(self) -> None:
        settings = validate_settings(
            {
                "starred_places": ["~/Work", "~/Work", 42, "sftp://host/share", *[f"/tmp/{i}" for i in range(80)]],
                "recent_locations": ["/tmp", "/tmp", "file:///tmp", None],
            }
        )
        self.assertEqual(settings["starred_places"][0], "~/Work")
        self.assertLessEqual(len(settings["starred_places"]), 64)
        self.assertEqual(settings["recent_locations"], ["/tmp", "file:///tmp"])

    def test_shortcut_overrides_are_bounded_and_namespaced(self) -> None:
        settings = validate_settings(
            {
                "shortcuts": {
                    "file.copy": "Ctrl+Shift+C",
                    "not-namespaced": "Ctrl+N",
                    "file.bad": "x" * 65,
                    "file.empty": "",
                    42: "Ctrl+X",
                }
            }
        )
        self.assertEqual(
            settings["shortcuts"],
            {"file.copy": "Ctrl+Shift+C", "file.empty": ""},
        )

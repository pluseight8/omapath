from __future__ import annotations

import re
import unittest
from pathlib import Path

from omapath.settings import DEFAULT_SETTINGS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_UI = (
    "simple_window.py",
    "context_menu.py",
    "dialogs.py",
    "sidebar.py",
    "tab_bar.py",
    "quick_look.py",
    "operation_popover.py",
    "panel_controls.py",
    "search_controls.py",
    "topbar.py",
)


class ArchitectureGuardTests(unittest.TestCase):
    def test_active_entrypoint_and_controllers_do_not_reenable_legacy_ui(self) -> None:
        app_source = (PROJECT_ROOT / "omapath" / "app.py").read_text(encoding="utf-8")
        self.assertIn("from .ui.simple_window", app_source)
        for filename in ACTIVE_UI:
            source = (PROJECT_ROOT / "omapath" / "ui" / filename).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"from\s+\.\s*(?:window|file_view)\b")
            self.assertNotRegex(source, r"(?:from|import)\s+omapath\.ui\.(?:window|file_view)\b")
            if filename != "simple_window.py":
                self.assertNotIn("from .simple_window", source)
                self.assertNotIn("from omapath.ui.simple_window", source)

    def test_settings_window_controls_reference_known_persisted_keys(self) -> None:
        source = (PROJECT_ROOT / "omapath" / "ui" / "settings_window.py").read_text(encoding="utf-8")
        keys = set(re.findall(r"self\._(?:switch|combo)\(page, \"([^\"]+)\"", source))
        self.assertTrue(keys)
        self.assertTrue(keys.issubset(DEFAULT_SETTINGS))
        self.assertIn("conflict_behavior", keys)

    def test_active_controllers_do_not_contain_old_widget_lifecycle_hacks(self) -> None:
        source = "\n".join(
            (PROJECT_ROOT / "omapath" / "ui" / filename).read_text(encoding="utf-8")
            for filename in ACTIVE_UI
        )
        self.assertNotIn(".unparent(", source)
        self.assertNotIn("set_child(None)", source)
        self.assertNotIn("set_from_gicon(gicon,", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system(", source)

    def test_active_window_uses_responsive_layout_without_legacy_window_minimum(self) -> None:
        source = (PROJECT_ROOT / "omapath" / "ui" / "simple_window.py").read_text(encoding="utf-8")
        self.assertIn("from ..responsive import", source)
        self.assertNotIn("set_size_request(720, 480)", source)
        self.assertIn("self.grid.set_min_columns(1)", source)
        self.assertIn("set_shrink_start_child", source)
        self.assertIn("set_shrink_end_child", source)
        self.assertIn("responsive-narrow", source)


if __name__ == "__main__":
    unittest.main()

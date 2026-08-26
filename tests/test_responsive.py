from __future__ import annotations

import unittest
from pathlib import Path

from omapath.responsive import (
    ResponsiveMode,
    detail_columns_for_mode,
    grid_inset_for_mode,
    mode_uses_sidebar_overlay,
    responsive_mode_for_width,
)


class ResponsivePolicyTests(unittest.TestCase):
    def test_reference_widths_use_discrete_modes(self) -> None:
        self.assertIs(responsive_mode_for_width(1400), ResponsiveMode.WIDE)
        self.assertIs(responsive_mode_for_width(1000), ResponsiveMode.MEDIUM)
        self.assertIs(responsive_mode_for_width(700), ResponsiveMode.NARROW)
        self.assertIs(responsive_mode_for_width(500), ResponsiveMode.NARROW)

    def test_breakpoints_have_hysteresis(self) -> None:
        self.assertIs(responsive_mode_for_width(1085, ResponsiveMode.WIDE), ResponsiveMode.WIDE)
        self.assertIs(responsive_mode_for_width(1079, ResponsiveMode.WIDE), ResponsiveMode.MEDIUM)
        self.assertIs(responsive_mode_for_width(805, ResponsiveMode.MEDIUM), ResponsiveMode.MEDIUM)
        self.assertIs(responsive_mode_for_width(779, ResponsiveMode.MEDIUM), ResponsiveMode.NARROW)
        self.assertIs(responsive_mode_for_width(600, ResponsiveMode.NARROW), ResponsiveMode.NARROW)
        self.assertIs(responsive_mode_for_width(579, ResponsiveMode.NARROW), ResponsiveMode.NARROW)
        self.assertIs(responsive_mode_for_width(619, ResponsiveMode.NARROW), ResponsiveMode.NARROW)
        self.assertIs(responsive_mode_for_width(820, ResponsiveMode.NARROW), ResponsiveMode.MEDIUM)

    def test_details_columns_are_temporary_presentation_policy(self) -> None:
        self.assertEqual(
            detail_columns_for_mode(ResponsiveMode.WIDE),
            ("name", "size", "type", "modified"),
        )
        self.assertEqual(detail_columns_for_mode(ResponsiveMode.MEDIUM), ("name", "size", "modified"))
        self.assertEqual(detail_columns_for_mode(ResponsiveMode.NARROW), ("name",))
        self.assertEqual(grid_inset_for_mode(ResponsiveMode.NARROW), (8, 9, 10))
        self.assertFalse(mode_uses_sidebar_overlay(ResponsiveMode.MEDIUM))
        self.assertTrue(mode_uses_sidebar_overlay(ResponsiveMode.NARROW))

    def test_sidebar_drawer_is_above_its_backdrop(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "omapath" / "ui" / "simple_window.py"
        source = source_path.read_text(encoding="utf-8")
        show_section = source.split("    def _show_responsive_drawer", 1)[1].split(
            "    def _close_responsive_drawer", 1
        )[0]
        self.assertLess(
            show_section.index("backdrop = self._ensure_responsive_backdrop()"),
            show_section.index("self.workspace_overlay.add_overlay(widget)"),
        )
        self.assertIn("self.workspace_overlay.remove_overlay(widget)", show_section)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from omapath.zoom import CELL_WIDTHS, DEFAULT_ZOOM, ZOOM_LEVELS, cell_width, normalize_zoom, zoom_in, zoom_out


class ZoomTests(unittest.TestCase):
    def test_levels_and_boundaries(self) -> None:
        self.assertEqual(normalize_zoom("bad"), DEFAULT_ZOOM)
        self.assertEqual(normalize_zoom(31), 32)
        self.assertEqual(normalize_zoom(100), 96)
        self.assertEqual(zoom_out(32), 32)
        self.assertEqual(zoom_in(96), 96)
        self.assertEqual(zoom_in(48), 56)
        self.assertEqual(zoom_out(48), 40)

    def test_each_icon_level_has_matching_cell_geometry(self) -> None:
        self.assertEqual(ZOOM_LEVELS, (32, 40, 48, 56, 64, 72, 96))
        for level, expected_width in CELL_WIDTHS.items():
            self.assertEqual(cell_width(level), expected_width)
            self.assertGreater(expected_width, level)

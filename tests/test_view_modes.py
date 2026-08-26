from __future__ import annotations

import unittest
from types import SimpleNamespace

from omapath.view_modes import ViewMode, detail_value, normalize_view_mode


class ViewModeTests(unittest.TestCase):
    def test_legacy_view_names_are_normalized_without_breaking_sessions(self) -> None:
        self.assertIs(normalize_view_mode("grid"), ViewMode.ICONS)
        self.assertIs(normalize_view_mode("list"), ViewMode.DETAILS)
        self.assertIs(normalize_view_mode("compact"), ViewMode.COMPACT)

    def test_detail_values_are_human_readable(self) -> None:
        row = SimpleNamespace(display_name="note.txt", size=2048, content_type="text/plain", modified=0)
        self.assertEqual(detail_value(row, "name"), "note.txt")
        self.assertEqual(detail_value(row, "size"), "2.0 KB")
        self.assertEqual(detail_value(row, "type"), "text/plain")
        self.assertEqual(detail_value(row, "modified"), "—")


if __name__ == "__main__":
    unittest.main()

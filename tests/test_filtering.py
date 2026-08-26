from __future__ import annotations

import unittest
from dataclasses import dataclass

from omapath.filtering import FilterMode, filter_entries


@dataclass
class Entry:
    name: str
    display_name: str


class FilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = [Entry("kde.txt", "kde.txt"), Entry("IMG_001.png", "IMG_001.png"), Entry("notes.md", "notes.md")]

    def test_plain_filter_is_current_directory_only(self) -> None:
        result = filter_entries(self.entries, "KDE")
        self.assertEqual([entry.name for entry in result.items], ["kde.txt"])

    def test_glob_and_regex_modes(self) -> None:
        self.assertEqual([entry.name for entry in filter_entries(self.entries, "*.png", FilterMode.GLOB).items], ["IMG_001.png"])
        self.assertEqual([entry.name for entry in filter_entries(self.entries, r"^IMG_\d+", FilterMode.REGEX).items], ["IMG_001.png"])

    def test_invalid_regex_returns_visible_error(self) -> None:
        result = filter_entries(self.entries, "[", FilterMode.REGEX)
        self.assertEqual(result.items, ())
        self.assertIn("Invalid regular expression", result.error or "")


if __name__ == "__main__":
    unittest.main()

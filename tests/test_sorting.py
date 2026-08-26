from __future__ import annotations

import unittest
from dataclasses import dataclass

from omapath.sorting import natural_sort_key, sort_entries


@dataclass
class Entry:
    name: str
    is_dir: bool = False
    size: int = 0
    content_type: str = "text/plain"
    modified: int | None = 0

    @property
    def display_name(self) -> str:
        return self.name


class SortingTests(unittest.TestCase):
    def test_natural_sort_handles_unicode_and_numbers(self) -> None:
        names = ["file10", "file2", "file1", "Альбом", "é", "ä", "日本語", "123"]
        ordered = sorted(names, key=natural_sort_key)
        self.assertEqual(ordered[0], "123")
        self.assertLess(ordered.index("file1"), ordered.index("file2"))
        self.assertLess(ordered.index("file2"), ordered.index("file10"))
        self.assertLess(ordered.index("ä"), ordered.index("é"))
        self.assertIn("日本語", ordered)

    def test_folders_first_is_independent_of_direction(self) -> None:
        entries = [Entry("file2", size=2), Entry("folder", is_dir=True), Entry("file10", size=10)]
        ascending = sort_entries(entries, "name", False, True)
        descending = sort_entries(entries, "name", True, True)
        self.assertTrue(ascending[0].is_dir)
        self.assertTrue(descending[0].is_dir)
        self.assertEqual([entry.name for entry in ascending[1:]], ["file2", "file10"])
        self.assertEqual([entry.name for entry in descending[1:]], ["file10", "file2"])

    def test_size_type_and_modified_are_supported_without_recursive_folder_size(self) -> None:
        entries = [
            Entry("large.txt", size=20, content_type="text/plain", modified=3),
            Entry("folder", is_dir=True, size=4096, content_type="inode/directory", modified=1),
            Entry("small.png", size=2, content_type="image/png", modified=None),
        ]
        self.assertEqual([entry.name for entry in sort_entries(entries, "size", False, False)], ["small.png", "large.txt", "folder"])
        self.assertEqual([entry.name for entry in sort_entries(entries, "type", False, False)], ["small.png", "folder", "large.txt"])
        modified = sort_entries(entries, "modified", False, False)
        self.assertEqual([entry.name for entry in modified], ["folder", "large.txt", "small.png"])

    def test_invalid_sort_key_defaults_to_name(self) -> None:
        entries = [Entry("file10"), Entry("file2")]
        self.assertEqual([entry.name for entry in sort_entries(entries, "invalid")], ["file2", "file10"])

    def test_sort_can_disable_natural_number_order(self) -> None:
        entries = [Entry("file10"), Entry("file2")]
        ordered = sort_entries(entries, natural=False, folders_first=False)
        self.assertEqual([entry.name for entry in ordered], ["file10", "file2"])

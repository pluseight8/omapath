from __future__ import annotations

import unittest

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from omapath.trash import TrashItem, attribute_text, purge_older_than


class TrashTests(unittest.TestCase):
    def test_attribute_text_reads_string_and_byte_string_without_wrong_getter(self) -> None:
        string_info = Gio.FileInfo()
        string_info.set_attribute_string("trash::orig-path", "/tmp/string")
        self.assertEqual(attribute_text(string_info, "trash::orig-path"), "/tmp/string")

        byte_info = Gio.FileInfo()
        byte_info.set_attribute_byte_string("trash::orig-path", "/tmp/byte")
        self.assertEqual(attribute_text(byte_info, "trash::orig-path"), "/tmp/byte")

    def test_attribute_text_returns_none_for_missing_optional_attribute(self) -> None:
        info = Gio.FileInfo()
        self.assertIsNone(attribute_text(info, "trash::orig-path"))

    def test_trash_item_is_explicit_about_original_path(self) -> None:
        item = TrashItem("trash:///item", "item", "/tmp/item", 10, 100)
        self.assertEqual(item.original_path, "/tmp/item")

    def test_negative_cleanup_age_is_rejected_before_io(self) -> None:
        with self.assertRaises(ValueError):
            purge_older_than(-1)


if __name__ == "__main__":
    unittest.main()

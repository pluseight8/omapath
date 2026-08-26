from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from omapath.filesystem import (
    directory_size,
    filter_items,
    natural_sort_key,
    sort_items,
    unique_destination,
    validate_transfer,
    valid_entry_name,
    BoundedCache,
)
from omapath.model import FileItem


class FakeFile:
    def __init__(self, path: str) -> None:
        self._path = path

    def get_path(self) -> str:
        return self._path

    def get_uri(self) -> str:
        return Path(self._path).as_uri()


def item(name: str, *, folder: bool = False, size: int = 0, modified: int = 0) -> FileItem:
    return FileItem(FakeFile("/tmp/" + name), name, name, folder, size, modified, "inode/directory" if folder else "text/plain", name.startswith("."), False)


class FilesystemTests(unittest.TestCase):
    def test_natural_sort(self) -> None:
        self.assertEqual(sorted(["file10", "file2", "file9"], key=natural_sort_key), ["file2", "file9", "file10"])
        self.assertEqual(sorted(["alpha", "10-items", "2-items"], key=natural_sort_key), ["2-items", "10-items", "alpha"])

    def test_folders_first_even_when_descending(self) -> None:
        entries = [item("file2"), item("folder", folder=True), item("file10")]
        result = sort_items(entries, reverse=True)
        self.assertEqual([entry.name for entry in result], ["folder", "file10", "file2"])

    def test_filtering(self) -> None:
        entries = [item("notes.md", size=10), item("image.png", size=20), item("src", folder=True)]
        self.assertEqual([entry.name for entry in filter_items(entries, extension="md")], ["notes.md"])
        self.assertEqual([entry.name for entry in filter_items(entries, folders_only=True)], ["src"])
        self.assertEqual([entry.name for entry in filter_items(entries, min_size=15)], ["image.png"])

    def test_safe_names(self) -> None:
        self.assertTrue(valid_entry_name("тест 日本語 😎.txt"))
        self.assertTrue(valid_entry_name("a'b;$.txt"))
        self.assertFalse(valid_entry_name("../escape"))
        self.assertFalse(valid_entry_name(""))

    def test_cache_is_bounded_and_lru(self) -> None:
        cache = BoundedCache(limit=2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)
        cache.put("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(len(cache), 2)
        cache.set_limit(1)
        self.assertEqual(len(cache), 1)

    def test_transfer_rejects_folder_into_itself_and_symlink_size(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            child = source / "child"
            child.mkdir(parents=True)
            (source / "hello world.txt").write_text("hello", encoding="utf-8")
            outside = base / "outside.txt"
            outside.write_bytes(b"x" * 17)
            (source / "link").symlink_to(outside)
            allowed, reason = validate_transfer(str(source), str(child))
            self.assertFalse(allowed)
            self.assertIn("itself", reason or "")
            self.assertEqual(directory_size(str(source)), 5 + os.lstat(source / "link").st_size)

    def test_unique_destination_handles_quotes_and_existing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / 'a"b.txt').write_text("x", encoding="utf-8")
            self.assertEqual(unique_destination(str(base), 'a"b.txt'), str(base / 'a"b (copy 2).txt'))

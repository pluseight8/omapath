from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from omapath.operations import ConflictPolicy, OperationKind, OperationManager


class GioOperationTests(unittest.TestCase):
    def test_copy_and_unique_name_use_gio_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "тестовый файл.txt"
            destination = base / "destination"
            source.write_text("payload", encoding="utf-8")
            destination.mkdir()
            manager = OperationManager(scheduler=None)
            try:
                source_file = Gio.File.new_for_path(str(source))
                destination_file = Gio.File.new_for_path(str(destination))
                first = manager.submit_gio(
                    OperationKind.COPY,
                    [source_file],
                    destination_file,
                ).future.result(timeout=5)
                second = manager.submit_gio(
                    OperationKind.COPY,
                    [source_file],
                    destination_file,
                    policy=ConflictPolicy.RENAME,
                ).future.result(timeout=5)
                self.assertEqual(first.completed, 1)
                self.assertEqual(second.completed, 1)
                self.assertTrue((destination / source.name).exists())
                self.assertTrue((destination / "тестовый файл (copy 2).txt").exists())
            finally:
                manager.shutdown()

    def test_gio_create_rename_and_delete_are_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            folder = base / "Новая папка"
            file_path = folder / "файл.txt"
            renamed = folder / "переименован.txt"
            manager = OperationManager(scheduler=None)
            try:
                parent = Gio.File.new_for_path(str(base))
                created_folder = manager.submit_gio(
                    OperationKind.CREATE_FOLDER,
                    [parent.get_child(folder.name)],
                ).future.result(timeout=5)
                created_file = manager.submit_gio(
                    OperationKind.CREATE_FILE,
                    [Gio.File.new_for_path(str(file_path))],
                ).future.result(timeout=5)
                renamed_result = manager.submit_gio(
                    OperationKind.RENAME,
                    [Gio.File.new_for_path(str(file_path))],
                    renamed.name,
                ).future.result(timeout=5)
                deleted = manager.submit_gio(
                    OperationKind.DELETE,
                    [Gio.File.new_for_path(str(renamed))],
                ).future.result(timeout=5)
                self.assertEqual(created_folder.completed, 1)
                self.assertEqual(created_file.completed, 1)
                self.assertEqual(renamed_result.completed, 1)
                self.assertEqual(deleted.completed, 1)
                self.assertFalse(renamed.exists())
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()

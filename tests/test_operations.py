from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omapath.operations import ConflictPolicy, OperationKind, OperationManager
from omapath.rename import BatchRenameSpec


class OperationTests(unittest.TestCase):
    def test_link_creates_symlink_and_undo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source.txt"
            destination = base / "destination"
            source.write_text("payload", encoding="utf-8")
            destination.mkdir()
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(OperationKind.LINK, [str(source)], str(destination)).future.result(timeout=5)
                link = destination / "source.txt"
                self.assertEqual(result.completed, 1)
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), source)
                self.assertTrue(result.undo and result.undo.created_paths)
            finally:
                manager.shutdown()

    def test_copy_move_conflict_and_undo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            destination = base / "destination"
            source.mkdir()
            destination.mkdir()
            filename = "тест a'b $dollar.txt"
            (source / filename).write_text("payload", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                copied = manager.submit(OperationKind.COPY, [str(source / filename)], str(destination))
                result = copied.future.result(timeout=5)
                self.assertEqual(result.completed, 1)
                target = destination / filename
                self.assertTrue(target.exists())
                (source / filename).write_text("new", encoding="utf-8")
                copied_again = manager.submit(OperationKind.COPY, [str(source / filename)], str(destination), policy=ConflictPolicy.RENAME)
                copied_again.future.result(timeout=5)
                self.assertTrue((destination / "тест a'b $dollar (copy 2).txt").exists())
                manager.submit(OperationKind.MOVE, [str(source / filename)], str(destination)).future.result(timeout=5)
                self.assertFalse((source / filename).exists())
                self.assertGreaterEqual(len(manager.undo), 1)
            finally:
                manager.shutdown()

    def test_directory_merge_preserves_existing_children_and_is_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "incoming"
            destination = base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "same.txt").write_text("incoming", encoding="utf-8")
            (source / "new.txt").write_text("new", encoding="utf-8")
            (source / "nested").mkdir()
            (source / "nested" / "deep.txt").write_text("deep", encoding="utf-8")
            (destination / "incoming").mkdir()
            (destination / "incoming" / "same.txt").write_text("existing", encoding="utf-8")
            (destination / "incoming" / "nested").mkdir()
            (destination / "incoming" / "nested" / "old.txt").write_text("old", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(
                    OperationKind.COPY,
                    [str(source)],
                    str(destination),
                    policy=ConflictPolicy.MERGE,
                ).future.result(timeout=5)
                target = destination / "incoming"
                self.assertEqual(result.completed, 1)
                self.assertEqual((target / "same.txt").read_text(encoding="utf-8"), "existing")
                self.assertEqual((target / "old.txt").exists(), False)
                self.assertTrue((target / "new.txt").exists())
                self.assertTrue((target / "nested" / "old.txt").exists())
                self.assertTrue((target / "nested" / "deep.txt").exists())
                self.assertTrue(any(path.startswith(str(target)) for path in result.undo.created_paths))
            finally:
                manager.shutdown()

    def test_directory_merge_move_keeps_conflicts_and_restores_source_parent_on_undo(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "incoming"
            destination = base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "move.txt").write_text("payload", encoding="utf-8")
            (destination / "incoming").mkdir()
            (destination / "incoming" / "keep.txt").write_text("keep", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(
                    OperationKind.MOVE,
                    [str(source)],
                    str(destination),
                    policy=ConflictPolicy.MERGE,
                ).future.result(timeout=5)
                self.assertEqual(result.completed, 1)
                self.assertFalse((source / "move.txt").exists())
                self.assertTrue((destination / "incoming" / "move.txt").exists())
                self.assertTrue((destination / "incoming" / "keep.txt").exists())
                undone = manager.undo_last()
                self.assertFalse(undone.errors)
                self.assertTrue((source / "move.txt").exists())
                self.assertTrue((destination / "incoming" / "keep.txt").exists())
            finally:
                manager.shutdown()

    def test_cancelled_large_copy_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source.bin"
            destination = base / "out"
            source.write_bytes(b"x" * 4096)
            manager = OperationManager(scheduler=None)
            try:
                handle = manager.submit(OperationKind.COPY, [str(source)], str(destination))
                handle.cancel()
                result = handle.future.result(timeout=5)
                self.assertTrue(result.cancelled or result.completed in {0, 1})
            finally:
                manager.shutdown()

    def test_nested_unicode_transfer_and_copy_undo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            destination = base / "destination"
            (source / "nested").mkdir(parents=True)
            destination.mkdir()
            filenames = [
                "hello world.txt",
                "тестовый файл.txt",
                "日本語.txt",
                "한글.txt",
                "😎🔥.txt",
                "a'b.txt",
                'a"b.txt',
                "$dollar.txt",
                "back`tick.txt",
                "semi;colon.txt",
                "[brackets].txt",
                "--option.txt",
                "file",
                "file.",
                ".hidden",
                "line\nname.txt",
            ]
            for index, name in enumerate(filenames):
                (source / "nested" / name).write_text(str(index), encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(OperationKind.COPY, [str(source)], str(destination)).future.result(timeout=5)
                self.assertEqual(result.completed, 1)
                self.assertEqual((destination / "source" / "nested" / "тестовый файл.txt").read_text(encoding="utf-8"), "1")
                self.assertTrue((destination / "source" / "nested" / "line\nname.txt").exists())
                self.assertEqual(result.undo.created_paths, [str(destination / "source")])
            finally:
                manager.shutdown()

    def test_rename_conflict_and_same_path_replace_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source.txt"
            target = base / "target.txt"
            source.write_text("keep source", encoding="utf-8")
            target.write_text("keep target", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                conflict = manager.submit(OperationKind.RENAME, [str(source)], "target.txt").future.result(timeout=5)
                self.assertTrue(conflict.errors)
                self.assertEqual(source.read_text(encoding="utf-8"), "keep source")
                self.assertEqual(target.read_text(encoding="utf-8"), "keep target")

                same = manager.submit(
                    OperationKind.COPY,
                    [str(source)],
                    str(base),
                    policy=ConflictPolicy.REPLACE,
                ).future.result(timeout=5)
                self.assertTrue(same.errors)
                self.assertEqual(source.read_text(encoding="utf-8"), "keep source")
            finally:
                manager.shutdown()

    def test_create_and_rename_unicode_names(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            folder = base / "Новая папка с пробелом"
            file_path = folder / "файл с пробелом.txt"
            renamed = folder / "переименованный файл.txt"
            manager = OperationManager(scheduler=None)
            try:
                created_folder = manager.submit(OperationKind.CREATE_FOLDER, [str(folder)]).future.result(timeout=5)
                self.assertEqual(created_folder.completed, 1)
                self.assertTrue(folder.is_dir())

                created_file = manager.submit(OperationKind.CREATE_FILE, [str(file_path)]).future.result(timeout=5)
                self.assertEqual(created_file.completed, 1)
                self.assertTrue(file_path.is_file())

                renamed_result = manager.submit(
                    OperationKind.RENAME,
                    [str(file_path)],
                    renamed.name,
                ).future.result(timeout=5)
                self.assertEqual(renamed_result.completed, 1)
                self.assertFalse(file_path.exists())
                self.assertTrue(renamed.is_file())
            finally:
                manager.shutdown()

    def test_disappeared_source_is_reported_without_creating_a_target(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            missing = base / "исчезнувший файл.txt"
            destination = base / "destination"
            destination.mkdir()
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(OperationKind.COPY, [str(missing)], str(destination)).future.result(timeout=5)
                self.assertEqual(result.completed, 0)
                self.assertTrue(result.errors)
                self.assertEqual(list(destination.iterdir()), [])
            finally:
                manager.shutdown()

    def test_read_only_create_reports_a_safe_error(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "read-only folder" / "файл.txt"
            manager = OperationManager(scheduler=None)
            try:
                with patch("omapath.operations.os.mkdir", side_effect=PermissionError("read-only")):
                    result = manager.submit(OperationKind.CREATE_FOLDER, [str(target.parent)]).future.result(timeout=5)
                self.assertEqual(result.completed, 0)
                self.assertTrue(result.errors)
                self.assertFalse(target.parent.exists())
            finally:
                manager.shutdown()

    def test_broken_symlink_and_symlink_loop_are_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            destination = base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "broken").symlink_to("missing-target")
            (source / "loop").symlink_to(source, target_is_directory=True)
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(OperationKind.COPY, [str(source)], str(destination)).future.result(timeout=5)
                self.assertEqual(result.completed, 1)
                self.assertTrue((destination / "source" / "broken").is_symlink())
                self.assertTrue((destination / "source" / "loop").is_symlink())
            finally:
                manager.shutdown()

    def test_replace_move_restores_existing_target_when_move_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source.txt"
            target = base / "target.txt"
            source.write_text("source", encoding="utf-8")
            target.write_text("original target", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                with patch("omapath.operations.shutil.move", side_effect=OSError("simulated move failure")):
                    result = manager.submit(
                        OperationKind.MOVE,
                        [str(source)],
                        str(base),
                        policy=ConflictPolicy.REPLACE,
                    ).future.result(timeout=5)
                self.assertTrue(result.errors)
                self.assertEqual(source.read_text(encoding="utf-8"), "source")
                self.assertEqual(target.read_text(encoding="utf-8"), "original target")
                self.assertEqual(list(base.glob(".omapath-replace-*.tmp")), [])
            finally:
                manager.shutdown()

    def test_replace_copy_restores_existing_target_when_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source.txt"
            target = base / "target.txt"
            source.write_text("new source", encoding="utf-8")
            target.write_text("original target", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                real_replace = __import__("os").replace

                def replace_with_one_commit_failure(old: str, new: str) -> None:
                    if ".omapath-copy-" in old:
                        raise OSError("simulated commit failure")
                    real_replace(old, new)

                with patch("omapath.operations.os.replace", side_effect=replace_with_one_commit_failure):
                    result = manager.submit(
                        OperationKind.COPY,
                        [str(source)],
                        str(base),
                        policy=ConflictPolicy.REPLACE,
                    ).future.result(timeout=5)
                self.assertTrue(result.errors)
                self.assertEqual(source.read_text(encoding="utf-8"), "new source")
                self.assertEqual(target.read_text(encoding="utf-8"), "original target")
                self.assertEqual(list(base.glob(".omapath-*")), [])
            finally:
                manager.shutdown()

    def test_shutdown_drops_callbacks_already_queued_by_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "created.txt"
            queued: list[tuple[object, tuple[object, ...]]] = []
            completed = []

            def scheduler(callback: object, *args: object) -> None:
                queued.append((callback, args))

            manager = OperationManager(scheduler=scheduler)
            try:
                manager.submit(
                    OperationKind.CREATE_FILE,
                    [str(path)],
                    on_complete=completed.append,
                ).future.result(timeout=5)
                deadline = time.monotonic() + 5
                while not queued and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(queued)
                manager.shutdown()
                while queued:
                    callback, args = queued.pop(0)
                    callback(*args)  # type: ignore[misc]
                self.assertEqual(completed, [])
            finally:
                manager.shutdown()

    def test_rename_undo_and_redo_are_real_inverse_moves(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "before.txt"
            target = base / "after.txt"
            source.write_text("payload", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(OperationKind.RENAME, [str(source)], target.name).future.result(timeout=5)
                self.assertEqual(result.completed, 1)
                self.assertTrue(target.exists())
                undone = manager.undo_last()
                self.assertFalse(undone.errors)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                self.assertEqual(manager.undo.redo_count, 1)
                redone = manager.redo_last()
                self.assertFalse(redone.errors)
                self.assertTrue(target.exists())
                self.assertFalse(source.exists())
            finally:
                manager.shutdown()

    def test_batch_rename_preflights_and_is_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            first = base / "one.txt"
            second = base / "two.txt"
            first.write_text("1", encoding="utf-8")
            second.write_text("2", encoding="utf-8")
            manager = OperationManager(scheduler=None)
            try:
                result = manager.submit(
                    OperationKind.BATCH_RENAME,
                    [str(first), str(second)],
                    BatchRenameSpec(prefix="renamed-"),
                ).future.result(timeout=5)
                self.assertEqual(result.completed, 2)
                self.assertTrue((base / "renamed-one.txt").exists())
                self.assertTrue((base / "renamed-two.txt").exists())
                undone = manager.undo_last()
                self.assertFalse(undone.errors)
                self.assertTrue(first.exists())
                self.assertTrue(second.exists())
            finally:
                manager.shutdown()

    def test_archive_operations_run_in_manager_and_undo_created_targets(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            source.mkdir()
            (source / "тест.txt").write_text("payload", encoding="utf-8")
            archive = base / "bundle.zip"
            extracted = base / "extracted"
            manager = OperationManager(scheduler=None)
            try:
                created = manager.submit(OperationKind.CREATE_ARCHIVE, [str(source)], str(archive)).future.result(timeout=5)
                self.assertEqual(created.completed, 1)
                self.assertTrue(archive.exists())
                extracted_result = manager.submit(
                    OperationKind.EXTRACT_ARCHIVE,
                    [str(archive)],
                    str(extracted),
                ).future.result(timeout=5)
                self.assertEqual(extracted_result.completed, 1)
                self.assertEqual((extracted / "source" / "тест.txt").read_text(encoding="utf-8"), "payload")
                self.assertFalse(manager.undo_last().errors)
                self.assertFalse(extracted.exists())
            finally:
                manager.shutdown()

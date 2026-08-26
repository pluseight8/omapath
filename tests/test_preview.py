from __future__ import annotations

import tempfile
import threading
import time
import zipfile
import unittest
from pathlib import Path

from omapath.preview import PreviewManager


class PreviewTests(unittest.TestCase):
    def test_text_preview_is_bounded_and_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "a'b.txt"
            path.write_bytes("тест\n".encode("utf-8") * 100)
            manager = PreviewManager(max_bytes=32, workers=1)
            event = threading.Event()
            results = []
            try:
                manager.request(str(path), lambda result: (results.append(result), event.set()))
                self.assertTrue(event.wait(5))
                self.assertEqual(results[0].kind, "text")
                self.assertIn("preview truncated", results[0].content)
                self.assertTrue(results[0].truncated)
            finally:
                manager.shutdown()

    def test_folder_and_archive_previews_are_bounded_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "one.txt").write_text("one", encoding="utf-8")
            archive = base / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("one.txt", "one")
                source.writestr("nested/two.txt", "two")
            manager = PreviewManager(workers=1)
            results = []
            try:
                folder_done = threading.Event()
                manager.request(str(base), lambda result: (results.append(result), folder_done.set()))
                self.assertTrue(folder_done.wait(5))
                archive_done = threading.Event()
                manager.request(str(archive), lambda result: (results.append(result), archive_done.set()))
                self.assertTrue(archive_done.wait(5))
                self.assertEqual(results[0].kind, "folder")
                self.assertIn("items", results[0].content)
                self.assertEqual(results[1].kind, "archive")
                self.assertIn("nested/two.txt", results[1].content)
            finally:
                manager.shutdown()

    def test_modified_file_does_not_use_stale_cached_preview(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "changing.txt"
            path.write_text("first", encoding="utf-8")
            manager = PreviewManager(max_bytes=64, workers=1)
            first_done = threading.Event()
            second_done = threading.Event()
            results = []
            try:
                manager.request(str(path), lambda result: (results.append(result), first_done.set()))
                self.assertTrue(first_done.wait(5))
                path.write_text("second version", encoding="utf-8")
                manager.request(str(path), lambda result: (results.append(result), second_done.set()))
                self.assertTrue(second_done.wait(5))
                self.assertEqual(results[-1].content, "second version")
            finally:
                manager.shutdown()

    def test_cancelled_preview_drops_callback_already_queued_by_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "queued.txt"
            path.write_text("queued", encoding="utf-8")
            manager = PreviewManager(max_bytes=64, workers=1)
            queued: list[tuple[object, tuple[object, ...]]] = []
            captured = []

            def scheduler(callback: object, *args: object) -> None:
                queued.append((callback, args))

            try:
                manager.request(str(path), captured.append, scheduler=scheduler)
                deadline = time.monotonic() + 5
                while not queued and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(queued)
                manager.cancel()
                callback, args = queued.pop()
                callback(*args)  # type: ignore[misc]
                self.assertEqual(captured, [])
            finally:
                manager.shutdown()

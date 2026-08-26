from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from omapath.search import SearchQuery, SearchService, parse_search_query


class SearchTests(unittest.TestCase):
    def test_metadata_predicates_are_extracted_from_visible_query(self) -> None:
        text, tags, rating = parse_search_query("tag:work tag:Linux rating:>=4 report")
        self.assertEqual(text, "report")
        self.assertEqual(tags, ("work", "Linux"))
        self.assertEqual(rating, 4)

    def test_recursive_search_handles_unicode_and_hidden_policy(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "nested").mkdir()
            (base / "nested" / "hello world.txt").write_text("x", encoding="utf-8")
            (base / "nested" / "日本語.txt").write_text("x", encoding="utf-8")
            (base / "nested" / ".hidden.txt").write_text("x", encoding="utf-8")
            service = SearchService()
            event = threading.Event()
            captured: list[str] = []
            try:
                service.search(SearchQuery(str(base), "日本語", recursive=True), lambda paths: (captured.extend(paths), event.set()))
                self.assertTrue(event.wait(5))
                self.assertEqual([Path(path).name for path in captured], ["日本語.txt"])
            finally:
                service.shutdown()

    def test_cancelled_search_does_not_dispatch_stale_result(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            service = SearchService()
            event = threading.Event()

            def slow_run(query: SearchQuery, cancel: threading.Event) -> list[str]:
                time.sleep(0.05)
                return []

            try:
                with patch.object(SearchService, "_run", side_effect=slow_run):
                    service.search(SearchQuery(root, "old", recursive=True), lambda _paths: event.set())
                    service.cancel()
                    self.assertFalse(event.wait(0.2))
            finally:
                service.shutdown()

    def test_cancelled_search_drops_callback_already_queued_by_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            service = SearchService()
            queued: list[tuple[object, tuple[object, ...]]] = []
            captured: list[list[str]] = []

            def scheduler(callback: object, *args: object) -> None:
                queued.append((callback, args))

            try:
                service.search(SearchQuery(root, "old"), captured.append, scheduler=scheduler)
                deadline = time.monotonic() + 5
                while not queued and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(queued)
                service.cancel()
                callback, args = queued.pop()
                callback(*args)  # type: ignore[misc]
                self.assertEqual(captured, [])
            finally:
                service.shutdown()

    def test_content_search_is_bounded_and_optional(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "notes.txt").write_text("needle in content", encoding="utf-8")
            (base / "other.txt").write_text("different", encoding="utf-8")
            service = SearchService(workers=1)
            try:
                captured: list[str] = []
                done = threading.Event()
                service.search(
                    SearchQuery(str(base), "needle", search_contents=True),
                    lambda paths: (captured.extend(paths), done.set()),
                )
                self.assertTrue(done.wait(5))
                self.assertEqual(captured, [str(base / "notes.txt")])
            finally:
                service.shutdown()

    def test_path_and_content_type_filters_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            nested = base / "nested"
            nested.mkdir()
            (nested / "photo.png").write_bytes(b"png")
            (nested / "note.txt").write_text("note", encoding="utf-8")
            service = SearchService()
            done = threading.Event()
            captured: list[str] = []
            try:
                service.search(
                    SearchQuery(str(base), "nested/photo", recursive=True, path_text=True, content_type="image/"),
                    lambda paths: (captured.extend(paths), done.set()),
                )
                self.assertTrue(done.wait(5))
                self.assertEqual(captured, [str(nested / "photo.png")])
            finally:
                service.shutdown()

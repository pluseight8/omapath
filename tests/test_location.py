from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omapath.location import (
    completion_location_parts,
    filter_completion_names,
    local_location_error,
    location_display_text,
    parse_location,
    record_location_history,
)


class LocationTests(unittest.TestCase):
    def test_tilde_and_uri_inputs_resolve_to_real_locations(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            documents = root / "Documents"
            documents.mkdir()
            self.assertEqual(parse_location("~", str(root)), str(root))
            self.assertEqual(parse_location("~/Documents", str(root)), str(documents))
            self.assertEqual(parse_location(documents.as_uri(), str(root)), str(documents))
            self.assertEqual(location_display_text(str(documents), str(root)), str(documents))

    def test_invalid_and_empty_inputs_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            self.assertIsNone(parse_location("", root_text))
            self.assertIsNotNone(parse_location("/path/that/does/not/exist", root_text))

    def test_relative_paths_use_the_active_folder(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            current = root / "Documents"
            pictures = root / "Pictures"
            nested = current / "subfolder"
            current.mkdir()
            pictures.mkdir()
            nested.mkdir()

            self.assertEqual(parse_location(".", str(root), str(current)), str(current))
            self.assertEqual(parse_location("..", str(root), str(current)), str(root))
            self.assertEqual(parse_location("../Pictures", str(root), str(current)), str(pictures))
            self.assertEqual(parse_location("./subfolder", str(root), str(current)), str(nested))
            self.assertEqual(parse_location("/", str(root), str(current)), os.path.sep)

    def test_unicode_spaces_and_supported_gio_uris_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            folder = root / "Работа с пробелами"
            folder.mkdir()
            self.assertEqual(parse_location(f"  {folder}  ", str(root)), str(folder))
            self.assertEqual(parse_location(folder.as_uri(), str(root)), str(folder))
            for scheme in ("sftp", "smb", "dav", "davs", "ftp"):
                uri = f"{scheme}://example.test/share/Работа"
                self.assertEqual(parse_location(uri, str(root)), uri)

    def test_local_validation_distinguishes_missing_file_and_permission(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            folder = root / "folder"
            file_path = root / "note.txt"
            folder.mkdir()
            file_path.write_text("note", encoding="utf-8")
            self.assertEqual(local_location_error(str(root / "missing")), "Location does not exist")
            self.assertEqual(local_location_error(str(file_path)), "Not a folder")
            with patch("omapath.location.os.access", return_value=False):
                self.assertEqual(local_location_error(str(folder)), "Permission denied")
            self.assertIsNone(local_location_error(str(folder)))

    def test_completion_is_bounded_local_directory_only_and_unicode_safe(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            (root / "Documents").mkdir()
            (root / "Downloads").mkdir()
            (root / "Документы").mkdir()
            (root / ".hidden-folder").mkdir()

            self.assertEqual(
                completion_location_parts("Do", str(root), str(root)),
                (str(root), "Do"),
            )
            self.assertEqual(
                completion_location_parts("Documents/", str(root), str(root)),
                (str(root / "Documents"), ""),
            )
            self.assertIsNone(completion_location_parts("sftp://example.test/share/Do", str(root), str(root)))
            self.assertEqual(
                filter_completion_names(
                    ["Downloads", "Documents", "Документы", ".hidden-folder"],
                    "d",
                ),
                ["Documents", "Downloads"],
            )
            self.assertEqual(
                filter_completion_names([".hidden-folder", "Documents"], "."),
                [".hidden-folder"],
            )

    def test_manual_location_history_is_deduplicated_and_bounded(self) -> None:
        old = [f"/tmp/location-{index}" for index in range(40)]
        history = record_location_history(old, "/tmp/Работа с пробелами")
        self.assertEqual(len(history), 24)
        self.assertEqual(history[0], "/tmp/Работа с пробелами")
        self.assertEqual(
            record_location_history(history, history[5])[0],
            history[5],
        )
        self.assertEqual(record_location_history(history, "  "), history)

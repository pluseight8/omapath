from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.capabilities import item_capabilities, location_capabilities
from omapath.dnd import locations_from_clipboard_text, locations_from_uri_list, uri_list


class CapabilityTests(unittest.TestCase):
    def test_local_location_and_item_capabilities_follow_filesystem_state(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            folder = root / "folder"
            folder.mkdir()
            item = folder / "тестовый файл.txt"
            item.write_text("data", encoding="utf-8")
            location = location_capabilities(str(folder))
            item_state = item_capabilities(str(item))
            self.assertTrue(location.is_local)
            self.assertTrue(location.is_directory)
            self.assertTrue(location.can_create)
            self.assertTrue(location.can_drop)
            self.assertTrue(item_state.can_open)
            self.assertTrue(item_state.can_copy)
            self.assertTrue(item_state.can_rename)

    def test_unavailable_remote_provider_is_conservative(self) -> None:
        state = location_capabilities("sftp://invalid.example.invalid/home", query_remote=True)
        self.assertFalse(state.can_create)
        self.assertFalse(state.can_paste)
        self.assertFalse(state.can_drop)

    def test_uri_payload_keeps_remote_locations_and_cut_state(self) -> None:
        remote = "sftp://example.test/home/file%20name.txt"
        locations = locations_from_uri_list(uri_list([remote]))
        self.assertEqual(locations, [remote])
        parsed, cut = locations_from_clipboard_text("cut\n" + uri_list([remote]))
        self.assertEqual(parsed, [remote])
        self.assertTrue(cut)


if __name__ == "__main__":
    unittest.main()

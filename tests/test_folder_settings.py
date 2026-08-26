from __future__ import annotations

import unittest

from omapath.folder_settings import FolderViewProperties, FolderViewSettings


class FolderSettingsTests(unittest.TestCase):
    def test_settings_are_keyed_by_canonical_location(self) -> None:
        settings = FolderViewSettings()
        settings.set("/tmp/../tmp", FolderViewProperties(view_mode="details", icon_zoom=72))
        self.assertEqual(settings.get("/tmp").view_mode, "details")
        self.assertEqual(settings.get("/tmp").icon_zoom, 72)

    def test_all_and_subfolder_inheritance_are_explicit(self) -> None:
        settings = FolderViewSettings()
        settings.apply_to_all(FolderViewProperties(sort_mode="modified"))
        settings.apply_to_subfolders("/home/user", FolderViewProperties(show_hidden=True))
        resolved = settings.resolve("/home/user/Documents")
        self.assertEqual(resolved.sort_mode, "modified")
        self.assertTrue(resolved.show_hidden)

    def test_invalid_values_are_discarded(self) -> None:
        settings = FolderViewSettings({"/tmp": {"view_mode": "bad", "icon_zoom": 999, "unknown": 1}})
        self.assertEqual(settings.get("/tmp").view_mode, "grid")
        self.assertEqual(settings.get("/tmp").icon_zoom, 48)
        self.assertNotIn("unknown", settings.to_dict()["/tmp"])


if __name__ == "__main__":
    unittest.main()

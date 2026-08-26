from __future__ import annotations

import unittest

from omapath.places import PlacesStore


class PlacesTests(unittest.TestCase):
    def test_pinned_labels_and_order_are_persistent_data(self) -> None:
        places = PlacesStore()
        self.assertTrue(places.add_pinned("/tmp", "Scratch", "folder-symbolic"))
        self.assertFalse(places.add_pinned("/tmp", "Duplicate"))
        self.assertTrue(places.rename_pinned("/tmp", "Work"))
        self.assertEqual(places.to_dict()["pinned_places"][0]["label"], "Work")

    def test_recent_and_starred_are_bounded_and_deduplicated(self) -> None:
        places = PlacesStore()
        for index in range(70):
            places.add_starred(f"/tmp/{index}")
            places.add_recent(f"/tmp/{index}")
        places.add_starred("/tmp/69")
        self.assertEqual(places.starred[0], "/tmp/69")
        self.assertLessEqual(len(places.starred), 64)
        self.assertLessEqual(len(places.recent), 24)


if __name__ == "__main__":
    unittest.main()

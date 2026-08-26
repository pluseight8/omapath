from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.metadata_store import MetadataStore, checksum


class MetadataStoreTests(unittest.TestCase):
    def test_tags_rating_and_comments_persist_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "metadata.json"
            store = MetadataStore(path)
            store.set("/tmp/file", tags=["work", "work"], rating=9, comment="hello")
            self.assertTrue(store.save())
            restored = MetadataStore(path)
            self.assertEqual(restored.get("/tmp/file").tags, ["work"])
            self.assertEqual(restored.get("/tmp/file").rating, 5)
            self.assertEqual(restored.search_tag("WORK"), ["/tmp/file"])
            self.assertEqual(restored.tag_counts(), {"work": 1})

    def test_checksum_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "file"
            path.write_bytes(b"hello")
            self.assertEqual(checksum(str(path)), "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")


if __name__ == "__main__":
    unittest.main()

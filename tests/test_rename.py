from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.rename import BatchRenameSpec, RenamePlanError, apply_rename_plan, build_rename_plan


class BatchRenameTests(unittest.TestCase):
    def test_preview_preserves_extensions_and_supports_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            first = base / "IMG_a.png"
            second = base / "IMG_b.png"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            plan = build_rename_plan(
                [str(first), str(second)],
                BatchRenameSpec(find="IMG_", replace="photo_", numbering=True, padding=2),
            )
            self.assertEqual([Path(pair.target).name for pair in plan], ["photo_a 01.png", "photo_b 02.png"])
            apply_rename_plan(plan)
            self.assertTrue((base / "photo_a 01.png").exists())

    def test_collision_is_rejected_before_any_rename(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            first = base / "a.txt"
            second = base / "b.txt"
            existing = base / "target.txt"
            first.write_text("a", encoding="utf-8")
            second.write_text("b", encoding="utf-8")
            existing.write_text("keep", encoding="utf-8")
            with self.assertRaises(RenamePlanError):
                build_rename_plan([str(first), str(second)], BatchRenameSpec(find="a", replace="target"))
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_regex_validation_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "a.txt"
            path.write_text("a", encoding="utf-8")
            with self.assertRaises(RenamePlanError):
                build_rename_plan([str(path)], BatchRenameSpec(find="[", replace="x", regex=True))


if __name__ == "__main__":
    unittest.main()

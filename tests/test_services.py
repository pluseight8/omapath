from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.compare import text_diff, validate_compare_paths
from omapath.services import ServiceAction, expand_argv, load_service


class ServiceTests(unittest.TestCase):
    def test_placeholders_remain_argv_tokens_for_hostile_names(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "semi;colon 'quote'.txt"
            second = Path(root) / "два файла.txt"
            argv = expand_argv(["tool", "--file", "{file}", "{files}", "--dir", "{directory}"], [first, second])
            self.assertEqual(argv[0:3], ["tool", "--file", str(first)])
            self.assertEqual(argv[3:5], [str(first), str(second)])
            self.assertNotIn(";", argv[0])

    def test_service_loader_rejects_missing_command_and_accepts_json(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "service.json"
            path.write_text('{"name":"Echo","argv":["printf","{file}"],"applies_to":"files"}', encoding="utf-8")
            action = load_service(path)
            self.assertIsInstance(action, ServiceAction)
            self.assertIsNone(load_service(Path(root) / "missing.json"))

    def test_compare_validation_and_internal_diff(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            left = Path(root) / "left.txt"
            right = Path(root) / "right.txt"
            left.write_text("one\n", encoding="utf-8")
            right.write_text("two\n", encoding="utf-8")
            self.assertIn("-one", text_diff(str(left), str(right)))
            self.assertEqual(validate_compare_paths([str(left), str(right)]), (str(left), str(right)))
            with self.assertRaises(ValueError):
                validate_compare_paths([str(left)])


if __name__ == "__main__":
    unittest.main()

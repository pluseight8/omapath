from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.ui.dnd import _drop_signal, _enter_signal, _leave_signal, _motion_signal
from omapath.dnd import (
    choose_drop_action,
    drop_action,
    paths_from_clipboard_text,
    paths_from_uri_list,
    uri_list,
    validate_drop,
)


class DndTests(unittest.TestCase):
    def test_gtk4_signal_adapters_match_pygobject_argument_order(self) -> None:
        seen: list[tuple[str, object]] = []
        target = object()

        enter_result = _enter_signal(
            target, 1.5, 2.5, lambda current, x, y: seen.append(("enter", (current, x, y))) or "copy"
        )
        motion_result = _motion_signal(
            target, 3.5, 4.5, lambda current, x, y: seen.append(("motion", (current, x, y))) or "move"
        )
        _leave_signal(target, lambda current: seen.append(("leave", current)))
        drop_result = _drop_signal(
            target,
            "payload",
            5.5,
            6.5,
            lambda value, current: seen.append(("drop", (current, value))) or True,
            "test-target",
        )

        self.assertEqual(str(enter_result), "copy")
        self.assertEqual(str(motion_result), "move")
        self.assertTrue(drop_result)
        self.assertEqual([event[0] for event in seen], ["enter", "motion", "leave", "drop"])
        self.assertEqual(seen[0][1][1:], (1.5, 2.5))
        self.assertEqual(seen[1][1][1:], (3.5, 4.5))
        self.assertEqual(seen[3][1][1], "payload")

    def test_uri_list_round_trip_handles_spaces_and_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            paths = [str(Path(root) / "file with spaces.txt"), str(Path(root) / "тест.txt")]
            self.assertEqual(paths_from_uri_list(uri_list(paths)), paths)

    def test_drop_action_modifier_policy(self) -> None:
        self.assertEqual(drop_action(), "copy")
        self.assertEqual(drop_action(move=True), "move")
        self.assertEqual(drop_action(move=True, link=True), "link")

    def test_gnome_clipboard_payload_preserves_cut_and_unicode_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            paths = [str(Path(root) / "hello world.txt"), str(Path(root) / "日本語.txt")]
            payload = "cut\n" + uri_list(paths)
            parsed, cut = paths_from_clipboard_text(payload)
            self.assertEqual(parsed, paths)
            self.assertTrue(cut)

    def test_plain_uri_clipboard_defaults_to_copy(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "external.txt")
            parsed, cut = paths_from_clipboard_text(uri_list([path]))
            self.assertEqual(parsed, [path])
            self.assertFalse(cut)

    def test_drop_policy_uses_modifiers_and_same_device_default(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.txt"
            destination = Path(root) / "destination"
            source.write_text("data", encoding="utf-8")
            destination.mkdir()
            self.assertEqual(choose_drop_action([str(source)], str(destination)), "move")
            self.assertEqual(
                choose_drop_action([str(source)], str(destination), copy_modifier=True),
                "copy",
            )
            self.assertEqual(
                choose_drop_action([str(source)], str(destination), link_modifier=True),
                "link",
            )

    def test_validate_drop_rejects_folder_into_itself_and_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "folder"
            child = source / "child"
            source.mkdir()
            child.mkdir()
            valid, message = validate_drop([str(source)], str(child))
            self.assertFalse(valid)
            self.assertIn("itself", message or "")
            valid, message = validate_drop([str(source / "missing")], root)
            self.assertFalse(valid)
            self.assertIn("no longer exists", message or "")


if __name__ == "__main__":
    unittest.main()

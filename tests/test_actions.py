from __future__ import annotations

import unittest

from omapath.actions import (
    ActionRegistry,
    ShortcutConflict,
    normalize_shortcut,
    shortcut_from_key_event,
)


class ActionRegistryTests(unittest.TestCase):
    def test_actions_are_namespaced_and_executable(self) -> None:
        calls: list[str] = []
        registry = ActionRegistry()
        registry.register("file.copy", "Copy", lambda: calls.append("copy"), shortcut="Ctrl+C", category="File")
        registry.register("view.icons", "Icons", lambda: calls.append("icons"), shortcut="Ctrl+1", category="View")

        registry.execute("file.copy")
        self.assertEqual(calls, ["copy"])
        self.assertEqual(registry.find_shortcut("ctrl+c").action_id, "file.copy")  # type: ignore[union-attr]
        self.assertEqual(registry.by_category()["View"][0].action_id, "view.icons")

    def test_disabled_action_is_not_executed_and_predicate_errors_are_safe(self) -> None:
        calls: list[str] = []
        registry = ActionRegistry()
        registry.register("file.open", "Open", lambda: calls.append("open"), enabled=lambda: False)
        registry.register("file.bad", "Bad", lambda: calls.append("bad"), enabled=lambda: 1 / 0)

        registry.execute("file.open")
        registry.execute("file.bad")
        self.assertEqual(calls, [])
        self.assertEqual([action.action_id for action in registry.all(include_disabled=False)], [])

    def test_shortcut_conflicts_are_reported(self) -> None:
        registry = ActionRegistry()
        registry.register("file.copy", "Copy", lambda: None, shortcut="Ctrl+C")
        registry.register("selection.copy", "Copy Selection", lambda: None, shortcut="ctrl+c")
        self.assertEqual(registry.shortcut_conflicts(), {"ctrl+c": ["file.copy", "selection.copy"]})

    def test_invalid_ids_and_duplicates_are_rejected(self) -> None:
        registry = ActionRegistry()
        with self.assertRaises(ValueError):
            registry.register("copy", "Copy", lambda: None)
        registry.register("file.copy", "Copy", lambda: None)
        with self.assertRaises(ValueError):
            registry.register("file.copy", "Copy", lambda: None)

    def test_shortcuts_have_one_canonical_representation(self) -> None:
        self.assertEqual(normalize_shortcut("control+shift+t"), "Ctrl+Shift+T")
        self.assertEqual(normalize_shortcut("Ctrl++"), "Ctrl++")
        self.assertEqual(normalize_shortcut("Ctrl+-"), "Ctrl+-")
        self.assertEqual(shortcut_from_key_event("Return", ["Ctrl"]), "Ctrl+Enter")

    def test_user_override_can_clear_and_replace_after_explicit_conflict(self) -> None:
        registry = ActionRegistry()
        registry.register("file.copy", "Copy", lambda: None, shortcut="Ctrl+C")
        registry.register("tab.new", "New Tab", lambda: None, shortcut="Ctrl+T")
        with self.assertRaises(ShortcutConflict):
            registry.assign_shortcut("tab.new", "Ctrl+C")
        registry.assign_shortcut("tab.new", "Ctrl+C", allow_replace=True)
        self.assertEqual(registry.require("tab.new").shortcut, "Ctrl+C")
        self.assertEqual(registry.require("file.copy").shortcut, "")
        registry.load_shortcuts({"file.copy": "", "tab.new": "Ctrl+Shift+T"})
        self.assertEqual(registry.require("file.copy").shortcut, "")
        self.assertEqual(registry.require("tab.new").shortcut, "Ctrl+Shift+T")
        self.assertEqual(registry.user_shortcuts(), {"file.copy": "", "tab.new": "Ctrl+Shift+T"})

    def test_apply_shortcuts_validates_before_changing_active_bindings(self) -> None:
        registry = ActionRegistry()
        registry.register("file.copy", "Copy", lambda: None, shortcut="Ctrl+C")
        registry.register("tab.new", "New Tab", lambda: None, shortcut="Ctrl+T")
        with self.assertRaises(ShortcutConflict):
            registry.apply_shortcuts({"file.copy": "Ctrl+K", "tab.new": "Ctrl+K"})
        self.assertEqual(registry.require("file.copy").shortcut, "Ctrl+C")
        self.assertEqual(registry.require("tab.new").shortcut, "Ctrl+T")


if __name__ == "__main__":
    unittest.main()

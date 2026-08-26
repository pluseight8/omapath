from __future__ import annotations

import unittest
from dataclasses import dataclass

from omapath.grouping import GroupCriterion, group_entries


@dataclass
class Entry:
    name: str
    display_name: str
    content_type: str
    modified: int
    size: int


class GroupingTests(unittest.TestCase):
    def test_group_criterion_is_independent_from_input_order(self) -> None:
        entries = [
            Entry("b.txt", "b.txt", "text/plain", 2, 1),
            Entry("a.png", "a.png", "image/png", 1, 10),
        ]
        groups = group_entries(entries, GroupCriterion.TYPE)
        self.assertEqual([group.key for group in groups], ["image/png", "text/plain"])
        self.assertEqual([entry.name for entry in groups[0].items], ["a.png"])

    def test_none_keeps_one_group_and_empty_is_empty(self) -> None:
        self.assertEqual(group_entries([], "none"), [])
        self.assertEqual(group_entries([Entry("a", "a", "x", 0, 0)], "none")[0].label, "All items")


if __name__ == "__main__":
    unittest.main()

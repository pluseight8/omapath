from __future__ import annotations

import unittest

from omapath.split import PaneState, SplitState


class SplitTests(unittest.TestCase):
    def test_panes_keep_independent_histories_and_selection(self) -> None:
        state = SplitState([PaneState("/left"), PaneState("/right")])
        state.panes[0].navigate("/left/child")
        state.panes[0].selected_uris.add("file:///left/child/item")
        self.assertEqual(state.panes[1].location, "/right")
        self.assertEqual(state.panes[0].back_history, ["/left"])
        self.assertEqual(state.panes[1].selected_uris, set())

    def test_switch_is_explicit(self) -> None:
        state = SplitState([PaneState("/left"), PaneState("/right")])
        self.assertEqual(state.switch(), 1)
        self.assertEqual(state.current.location, "/right")


if __name__ == "__main__":
    unittest.main()

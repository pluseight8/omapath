import unittest
from pathlib import Path
import tempfile

from omapath.model import TabState
from omapath.navigation import NavigationHistory
from omapath.nautilus_features import push_recent


class NavigationTests(unittest.TestCase):
    def test_back_forward_round_trip(self) -> None:
        history = NavigationHistory()
        history.visit("/a")
        history.visit("/b")
        history.visit("/c")
        self.assertEqual(history.go_back(), "/b")
        self.assertEqual(history.go_back(), "/a")
        self.assertEqual(history.go_forward(), "/b")
        history.visit("/d")
        self.assertIsNone(history.go_forward())

    def test_each_tab_keeps_an_independent_history(self) -> None:
        first_tab = NavigationHistory()
        second_tab = NavigationHistory()

        first_tab.visit("/home")
        first_tab.visit("/home/Projects")
        second_tab.visit("/home")
        second_tab.visit("/home/Downloads")

        self.assertEqual(first_tab.go_back(), "/home")
        self.assertEqual(second_tab.go_back(), "/home")
        self.assertEqual(first_tab.current, "/home")
        self.assertEqual(second_tab.current, "/home")
        self.assertEqual(first_tab.forward, ["/home/Projects"])
        self.assertEqual(second_tab.forward, ["/home/Downloads"])

    def test_tab_state_keeps_ui_state_with_the_tab(self) -> None:
        tab = TabState(
            "/home/Projects/Omapath",
            back_history=["/home"],
            forward_history=["/home/Projects"],
            view_mode="details",
            selected_uris=["file:///home/Projects/Omapath/README.md"],
            scroll_value=184.5,
            zoom_level=120,
            sort_mode="modified",
            sort_reverse=True,
            show_hidden=True,
        )

        self.assertEqual(tab.location, "/home/Projects/Omapath")
        self.assertEqual(tab.selected_uris, ["file:///home/Projects/Omapath/README.md"])
        self.assertEqual(tab.scroll_value, 184.5)
        self.assertEqual((tab.view_mode, tab.zoom_level), ("details", 120))
        self.assertEqual((tab.sort_mode, tab.sort_reverse, tab.show_hidden), ("modified", True, True))

    def test_recent_navigation_does_not_change_stable_places(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            stable_places = [str(home)]
            initial_sidebar_entries = list(stable_places)
            recent: list[str] = []
            for index in range(100):
                name = f"folder-{index:03d}"
                location = home / name
                location.mkdir()
                recent = push_recent(recent, str(location))

            self.assertEqual(stable_places, initial_sidebar_entries)
            self.assertEqual(len(stable_places), 1)
            self.assertEqual(len(recent), 24)
            self.assertEqual(Path(recent[0]).name, "folder-099")

from __future__ import annotations

import unittest

from omapath.panel_layout import (
    PANEL_DEFINITIONS,
    PanelControlPresentation,
    PanelId,
    panel_control_presentation,
)
from omapath.responsive import ResponsiveMode


class PanelLayoutTests(unittest.TestCase):
    def test_panel_vocabulary_keeps_dolphin_shortcuts_and_action_ids_stable(self) -> None:
        self.assertEqual(
            [(definition.panel_id, definition.action_id, definition.shortcut) for definition in PANEL_DEFINITIONS],
            [
                (PanelId.SIDEBAR, "panel.sidebar", "F9"),
                (PanelId.FOLDERS, "panel.folders", "F7"),
                (PanelId.INFORMATION, "panel.info", "F11"),
                (PanelId.TERMINAL, "panel.terminal", "F4"),
            ],
        )

    def test_one_panels_button_is_used_at_every_breakpoint(self) -> None:
        for mode in (
            ResponsiveMode.WIDE,
            ResponsiveMode.MEDIUM,
            ResponsiveMode.NARROW,
        ):
            with self.subTest(mode=mode):
                self.assertIs(
                    panel_control_presentation(mode),
                    PanelControlPresentation.COLLAPSED,
                )


try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk
    from omapath.ui.panel_controls import PanelControlsController

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gdk = None  # type: ignore[assignment]
    PanelControlsController = None  # type: ignore[assignment,misc]
    GTK_IMPORT_ERROR = error


class PanelControlsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")
        if Gdk.Display.get_default() is None:  # type: ignore[union-attr]
            raise unittest.SkipTest("no graphical display")

    def test_surfaces_switch_at_breakpoint_and_reflect_live_panel_state(self) -> None:
        class FakeAction:
            def is_enabled(self) -> bool:
                return True

        class FakeActions:
            def get(self, _action_id: str) -> FakeAction:
                return FakeAction()

        class FakeHost:
            responsive_mode = ResponsiveMode.WIDE

            def __init__(self) -> None:
                self.actions = FakeActions()
                self.state = {definition.panel_id.value: False for definition in PANEL_DEFINITIONS}

            def panel_terminal_available(self) -> bool:
                return True

            def panel_control_visible(self, panel_id: str) -> bool:
                return self.state[panel_id]

            def _execute_action(self, action_id: str) -> bool:
                definition = next(item for item in PANEL_DEFINITIONS if item.action_id == action_id)
                self.state[definition.panel_id.value] = not self.state[definition.panel_id.value]
                return True

        host = FakeHost()
        controller = PanelControlsController(host)  # type: ignore[misc,operator]
        controller.build()

        self.assertFalse(controller.expanded_box.get_visible())  # type: ignore[union-attr]
        self.assertTrue(controller.collapsed_button.get_visible())  # type: ignore[union-attr]

        host._execute_action("panel.info")
        controller.sync()
        self.assertTrue(controller.menu_checks[PanelId.INFORMATION].get_active())

        host.responsive_mode = ResponsiveMode.NARROW
        controller.sync()
        self.assertFalse(controller.expanded_box.get_visible())  # type: ignore[union-attr]
        self.assertTrue(controller.collapsed_button.get_visible())  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()

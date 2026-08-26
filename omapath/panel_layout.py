"""Toolkit-neutral policy and vocabulary for Omapath panel controls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .responsive import ResponsiveMode


class PanelId(StrEnum):
    """Stable identifiers shared by shortcuts, settings and panel controls."""

    SIDEBAR = "sidebar"
    FOLDERS = "folders"
    INFORMATION = "information"
    TERMINAL = "terminal"


class PanelControlPresentation(StrEnum):
    """How much panel-control chrome fits in the topbar."""

    EXPANDED = "expanded"
    COLLAPSED = "collapsed"


@dataclass(frozen=True, slots=True)
class PanelDefinition:
    """User-facing panel metadata; GTK widgets consume this description."""

    panel_id: PanelId
    action_id: str
    label: str
    icon_name: str
    shortcut: str


PANEL_DEFINITIONS: tuple[PanelDefinition, ...] = (
    PanelDefinition(PanelId.SIDEBAR, "panel.sidebar", "Sidebar", "view-sidebar-symbolic", "F9"),
    PanelDefinition(PanelId.FOLDERS, "panel.folders", "Folders", "folder-open-symbolic", "F7"),
    PanelDefinition(
        PanelId.INFORMATION,
        "panel.info",
        "Information",
        "dialog-information-symbolic",
        "F11",
    ),
    PanelDefinition(
        PanelId.TERMINAL,
        "panel.terminal",
        "Terminal",
        "utilities-terminal-symbolic",
        "F4",
    ),
)


def panel_control_presentation(
    mode: ResponsiveMode | str | None,
) -> PanelControlPresentation:
    """Keep one discoverable Panels control at every breakpoint."""

    return PanelControlPresentation.COLLAPSED


__all__ = [
    "PANEL_DEFINITIONS",
    "PanelControlPresentation",
    "PanelDefinition",
    "PanelId",
    "panel_control_presentation",
]

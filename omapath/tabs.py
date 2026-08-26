"""Lightweight per-tab navigation and presentation state."""

from __future__ import annotations

from dataclasses import dataclass, field

from .zoom import normalize_zoom


@dataclass
class TabState:
    location: str
    back_history: list[str] = field(default_factory=list)
    forward_history: list[str] = field(default_factory=list)
    show_hidden: bool = False
    sort_key: str = "name"
    sort_descending: bool = False
    folders_first: bool = True
    icon_zoom: int = 48
    view_mode: str = "grid"
    scroll_position: float = 0.0
    filter_query: str = ""
    filter_mode: str = "plain"
    group_criterion: str = "none"
    groups_enabled: bool = False
    collapsed_groups: list[str] = field(default_factory=list)
    show_previews: bool = True
    selected_locations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.icon_zoom = normalize_zoom(self.icon_zoom)

    def clone(self) -> TabState:
        return TabState(
            location=self.location,
            back_history=list(self.back_history),
            forward_history=list(self.forward_history),
            show_hidden=self.show_hidden,
            sort_key=self.sort_key,
            sort_descending=self.sort_descending,
            folders_first=self.folders_first,
            icon_zoom=self.icon_zoom,
            view_mode=self.view_mode,
            scroll_position=self.scroll_position,
            filter_query=self.filter_query,
            filter_mode=self.filter_mode,
            group_criterion=self.group_criterion,
            groups_enabled=self.groups_enabled,
            collapsed_groups=list(self.collapsed_groups),
            show_previews=self.show_previews,
            selected_locations=list(self.selected_locations),
        )

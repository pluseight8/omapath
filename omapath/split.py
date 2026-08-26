"""Independent state contract for the optional two-pane layout."""

from __future__ import annotations

from dataclasses import dataclass, field

from .view_modes import normalize_view_mode
from .zoom import normalize_zoom


@dataclass(slots=True)
class PaneState:
    location: str
    back_history: list[str] = field(default_factory=list)
    forward_history: list[str] = field(default_factory=list)
    selected_uris: set[str] = field(default_factory=set)
    view_mode: str = "icons"
    zoom: int = 48
    show_hidden: bool = False
    sort_key: str = "name"
    sort_descending: bool = False
    folders_first: bool = True
    filter_query: str = ""
    filter_mode: str = "plain"
    groups_enabled: bool = False
    group_criterion: str = "none"
    collapsed_groups: set[str] = field(default_factory=set)
    show_previews: bool = True
    search_query: str = ""
    scroll_position: float = 0.0

    def __post_init__(self) -> None:
        self.view_mode = normalize_view_mode(self.view_mode).value
        self.zoom = normalize_zoom(self.zoom)

    def navigate(self, location: str, *, record_history: bool = True) -> None:
        if location == self.location:
            return
        if record_history:
            self.back_history.append(self.location)
            self.forward_history.clear()
        self.location = location
        self.scroll_position = 0.0

    def back(self) -> bool:
        if not self.back_history:
            return False
        self.forward_history.append(self.location)
        self.location = self.back_history.pop()
        return True

    def forward(self) -> bool:
        if not self.forward_history:
            return False
        self.back_history.append(self.location)
        self.location = self.forward_history.pop()
        return True


@dataclass(slots=True)
class SplitState:
    panes: list[PaneState]
    active: int = 0

    def __post_init__(self) -> None:
        if len(self.panes) != 2:
            raise ValueError("split state requires exactly two panes")
        self.active = 0 if self.active not in {0, 1} else self.active

    @property
    def current(self) -> PaneState:
        return self.panes[self.active]

    def switch(self) -> int:
        self.active = 1 - self.active
        return self.active


__all__ = ["PaneState", "SplitState"]

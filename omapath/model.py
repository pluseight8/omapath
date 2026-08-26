"""Small, toolkit-agnostic data contracts shared by Omapath."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FileItem:
    """A directory entry snapshot.

    ``file`` is intentionally typed as ``Any`` so the model remains usable by
    headless tests without importing GTK/GIO.  The GTK adapter supplies a
    ``Gio.File`` instance at runtime.
    """

    file: Any
    name: str
    display_name: str
    is_dir: bool
    size: int
    modified: int
    content_type: str
    hidden: bool
    is_symlink: bool
    trash_orig_path: str | None = None
    folder_size: int | None = None
    created: int | None = None
    accessed: int | None = None
    group: str = ""
    git_status: str = ""

    @property
    def path(self) -> str | None:
        getter = getattr(self.file, "get_path", None)
        return getter() if getter is not None else None

    @property
    def uri(self) -> str:
        getter = getattr(self.file, "get_uri", None)
        if getter is not None:
            return str(getter())
        return self.path or self.name


@dataclass(slots=True)
class TabState:
    location: str
    back_history: list[str] = field(default_factory=list)
    forward_history: list[str] = field(default_factory=list)
    view_mode: str = "grid"
    title: str = "Home"
    selected_uris: list[str] = field(default_factory=list)
    scroll_value: float = 0.0
    zoom_level: int = 100
    sort_mode: str = "name"
    sort_reverse: bool = False
    show_hidden: bool = False

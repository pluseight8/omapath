#!/usr/bin/env python3
"""Omapath - the native file manager for Omarchy.

The app intentionally stays dependency-light: it uses the GTK4 and GIO
bindings already shipped with Omarchy and opens files with the desktop's
existing default applications.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from ..filesystem import directory_size as core_directory_size
from ..filesystem import format_date as core_format_date
from ..filesystem import format_size as core_format_size
from ..filesystem import BoundedCache, metadata_for_path, sort_items as core_sort_items
from ..filesystem import unique_destination
from ..archive import ArchiveSafetyError, archive_kind as core_archive_kind, create_archive, extract_archive
from ..diagnostics import LOGGER
from ..git_status import status_for_location
from ..model import FileItem as CoreFileItem
from ..model import TabState as CoreTabState
from ..nautilus_features import MAX_RECENT_LOCATIONS, MAX_STARRED_LOCATIONS
from ..nautilus_features import canonical_location, push_recent, remove_location
from ..nautilus_features import script_paths as discover_scripts
from ..nautilus_features import template_paths as discover_templates
from ..settings import DEFAULT_SETTINGS as CORE_DEFAULT_SETTINGS
from ..settings import load_settings as core_load_settings
from ..settings import save_settings as core_save_settings
from ..settings import validate_settings
from ..theme import active_font as core_active_font
from ..theme import active_theme as core_active_theme
from ..theme import ThemeWatcher, read_colors as core_read_colors
from ..compat import set_accessible_label
from ..operations import ConflictPolicy, OperationHandle, OperationKind, OperationManager
from ..preview import PreviewManager, PreviewResult, ThumbnailManager
from ..search import SearchQuery, SearchService
from ..trash import attribute_text
from .file_view import VirtualFileView
from .palette import CommandPalette, PaletteAction


APP_ID = "dev.omarchy.Omapath"
TRASH_URI = "trash:///"
DEFAULT_SETTINGS: dict[str, Any] = CORE_DEFAULT_SETTINGS

FILE_ATTRIBUTES = (
    "standard::name,standard::display-name,standard::type,standard::size,"
    "standard::content-type,standard::is-hidden,standard::is-symlink,time::modified,"
    "time::created,time::access,"
    "trash::orig-path"
)
ICON_NAME_CACHE = BoundedCache(128)


# Keep the GTK adapter at its boundary: shared contracts, settings and theme
# parsing come from the toolkit-agnostic Omapath modules.
FileItem = CoreFileItem
TabState = CoreTabState
active_omarchy_theme = core_active_theme
active_omarchy_font = core_active_font
read_omarchy_colors = core_read_colors


load_settings = core_load_settings
save_settings = core_save_settings
directory_size = core_directory_size


def rgba(hex_color: str, opacity: float) -> str:
    raw = hex_color.removeprefix("#")
    if len(raw) == 8:
        raw = raw[:6]
    try:
        red, green, blue = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
        return f"rgba({red}, {green}, {blue}, {opacity:.2f})"
    except (ValueError, IndexError):
        return hex_color


def build_css(colors: dict[str, str], font_family: str = "monospace") -> str:
    font_stack = ", ".join(
        f'"{family}"' for family in (font_family, "JetBrainsMono Nerd Font", "monospace") if family
    )
    def luminance(hex_color: str) -> float:
        raw = hex_color.removeprefix("#")[:6]
        try:
            red, green, blue = (int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue
        except (ValueError, IndexError):
            return 0.0

    tokens = {
        "__font__": font_stack,
        "__accent__": colors["accent"],
        "__accent_contrast__": "#111111" if luminance(colors["accent"]) > 0.55 else "#ffffff",
        "__selection__": colors["selection"],
        "__muted__": colors["muted"],
        "__line__": colors["muted"],
        "__line_soft__": rgba(colors["muted"], 0.45),
        "__line_strong__": rgba(colors["muted"], 0.8),
        "__background__": colors["background"],
        "__dark_background__": colors["dark_background"],
        "__darker_background__": colors["darker_background"],
        "__lighter_background__": colors["lighter_background"],
        "__panel__": colors["dark_background"],
        "__panel_2__": colors["background"],
        "__code_bg__": colors["darker_background"],
        "__foreground__": colors["foreground"],
        "__dark_foreground__": colors["dark_foreground"],
        "__light_foreground__": colors["light_foreground"],
        "__red__": colors["red"],
        "__yellow__": colors["yellow"],
        "__green__": colors["green"],
        "__cyan__": colors["cyan"],
        "__accent_12__": rgba(colors["accent"], 0.12),
        "__accent_20__": rgba(colors["accent"], 0.20),
        "__accent_30__": rgba(colors["accent"], 0.30),
        "__foreground_65__": rgba(colors["foreground"], 0.65),
        "__foreground_45__": rgba(colors["foreground"], 0.45),
    }
    css = """
    * {
        font-family: __font__;
        color: __foreground__;
    }

    window,
    .app-shell {
        background: __background__;
        color: __foreground__;
    }

    .topbar {
        background: __darker_background__;
        border-bottom: 1px solid __line__;
        min-height: 38px;
        padding: 0 10px;
    }

    .tab-bar {
        background: __dark_background__;
        border-bottom: 1px solid __line__;
        min-height: 30px;
        padding: 2px 8px 0;
    }

    .tab-item {
        background: transparent;
        border: 0;
        border-bottom: 1px solid transparent;
        min-width: 96px;
    }

    .tab-item.active {
        background: __panel_2__;
        border-bottom-color: __accent__;
    }

    .tab-button,
    .tab-close {
        background: transparent;
        border: 0;
        border-radius: 0;
        box-shadow: none;
    }

    .tab-button {
        color: __light_foreground__;
        font-size: 11px;
        padding: 4px 8px;
    }

    .tab-button.active {
        color: __foreground__;
        font-weight: 800;
    }

    .tab-button:hover,
    .tab-close:hover {
        background: __panel_2__;
    }

    .tab-close {
        min-width: 25px;
        padding: 4px;
        color: __light_foreground__;
    }

    .eyebrow,
    .section-label {
        color: __light_foreground__;
        font-size: 10px;
        font-weight: 800;
    }

    .section-label {
        color: __accent__;
    }

    .icon-button,
    .sidebar-item,
    .crumb-button,
    .action-button {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 0;
        box-shadow: none;
    }

    .icon-button {
        padding: 6px;
    }

    .icon-button:hover,
    .action-button:hover,
    .crumb-button:hover {
        background: __panel_2__;
        border-color: __line_strong__;
    }

    .icon-button:focus,
    .sidebar-item:focus,
    .crumb-button:focus,
    .action-button:focus {
        outline: 1px solid __accent__;
        outline-offset: 1px;
    }

    .icon-button:disabled {
        color: __dark_foreground__;
    }

    .icon-button.active {
        background: __accent__;
        border-color: __accent__;
        color: __accent_contrast__;
    }

    .location-entry,
    searchentry {
        background: __code_bg__;
        border: 1px solid __line__;
        border-radius: 0;
        min-height: 28px;
        padding: 3px 9px;
        caret-color: __accent__;
    }

    .location-entry:focus,
    searchentry:focus-within {
        border-color: __accent__;
        box-shadow: 0 0 0 1px __accent_12__;
    }

    .sidebar {
        background: __dark_background__;
        padding: 8px;
    }

    .sidebar-item {
        padding: 6px 10px;
        min-height: 32px;
        border-left: 1px solid transparent;
    }

    .sidebar-item:hover {
        background: __panel_2__;
        border-color: transparent;
    }

    .sidebar-item.active {
        background: __accent__;
        border-color: __accent__;
        color: __accent_contrast__;
    }

    .sidebar-item.active label,
    .sidebar-item.active image {
        color: __accent_contrast__;
    }

    .sidebar-name {
        font-size: 12px;
    }

    .pinned-section {
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid __line_soft__;
    }

    .pinned-hint {
        color: __foreground_65__;
        font-size: 10px;
        padding: 4px 10px 2px;
    }

    .storage-bar trough,
    progressbar trough {
        min-height: 3px;
        border-radius: 0;
        background: __line__;
    }

    .storage-bar progress,
    progressbar progress {
        min-height: 3px;
        border-radius: 0;
        background: __accent__;
    }

    .main-pane {
        background: __background__;
        padding: 10px 12px 6px;
    }

    .main-pane.compact-layout {
        padding: 8px 10px 4px;
    }

    .breadcrumb-row {
        min-height: 24px;
    }

    .crumb-button {
        padding: 2px 6px;
        color: __light_foreground__;
        font-size: 11px;
    }

    .crumb-button:last-child {
        color: __accent__;
    }

    .path-title {
        color: __foreground__;
        font-size: 13px;
        font-weight: 700;
    }

    .path-meta,
    .file-meta,
    .status-text,
    .hint-text {
        color: __foreground_65__;
        font-size: 11px;
        font-variant-numeric: tabular-nums;
    }

    .view-toolbar {
        background: transparent;
        border: 1px solid __line__;
        border-radius: 0;
        padding: 2px;
    }

    .view-toolbar button {
        padding: 5px 7px;
    }

    .file-tile {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 2px;
        min-width: 84px;
        min-height: 82px;
        padding: 5px 5px 4px;
        box-shadow: none;
    }

    .file-tile:hover {
        background: __panel_2__;
    }

    row:selected .file-tile,
    gridview > child:selected .file-tile {
        background: __accent_12__;
        border-color: __accent_30__;
    }

    .compact-layout .file-tile {
        min-height: 72px;
        min-width: 76px;
        padding: 4px;
    }

    .file-tile-icon {
        min-height: 50px;
    }

    .compact-layout .file-tile-icon {
        min-height: 42px;
    }

    .file-tile-name {
        color: __foreground__;
        font-size: 12px;
        font-weight: 600;
    }

    .file-tile-meta {
        color: __foreground_65__;
        font-size: 11px;
    }

    row:focus-visible {
        outline: 1px solid __accent__;
        outline-offset: 1px;
    }

    .list-row {
        background: transparent;
        border-radius: 0;
        border-bottom: 1px solid __line_soft__;
        padding: 6px 8px;
    }

    .compact-row {
        background: transparent;
        border-bottom: 1px solid __line_soft__;
        min-height: 34px;
        padding: 4px 10px;
    }

    .compact-row:hover {
        background: __panel_2__;
    }

    row:selected .compact-row {
        background: __accent_12__;
        border-bottom-color: __accent__;
    }

    .compact-name {
        font-size: 12px;
    }

    .group-label {
        color: __accent__;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.06em;
        padding: 6px 8px 2px;
    }

    .list-row:hover {
        background: __panel_2__;
    }

    row:selected .list-row {
        background: __accent_12__;
        border-bottom-color: __accent__;
    }

    .list-name {
        font-size: 12px;
        font-weight: 700;
    }

    .preview-image {
        outline: 1px solid rgba(255, 255, 255, 0.10);
        outline-offset: -1px;
    }

    .folders-panel {
        background: __code_bg__;
        border-top: 1px solid __line_soft__;
        border-bottom: 1px solid __line_soft__;
        margin: 8px -14px 0;
        padding: 10px 14px;
    }

    .folder-tree-item {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 0;
        padding: 4px 6px;
    }

    .folder-tree-item:hover,
    .folder-tree-item:focus {
        background: __panel_2__;
        border-color: __line_strong__;
        outline: none;
    }

    .terminal-panel {
        background: __darker_background__;
        border: 1px solid __line__;
        min-height: 150px;
    }

    .terminal-output {
        background: __code_bg__;
        color: __foreground__;
        font-family: monospace;
        font-size: 11px;
        padding: 10px;
    }

    .terminal-entry {
        background: __code_bg__;
        border: 0;
        border-top: 1px solid __line__;
        border-radius: 0;
        padding: 7px 9px;
    }

    .action-button {
        background: __panel_2__;
        border-color: __line__;
        padding: 8px 10px;
    }

    .action-button.solid {
        background: __accent__;
        border-color: __accent__;
        color: __accent_contrast__;
        font-weight: 800;
    }

    popover {
        background: __dark_background__;
        border: 1px solid __line_strong__;
        border-radius: 0;
        box-shadow: none;
    }

    .context-menu {
        background: __dark_background__;
        min-width: 218px;
        padding: 5px;
    }

    .context-menu-header {
        color: __light_foreground__;
        font-size: 10px;
        font-weight: 800;
        padding: 7px 9px 5px;
    }

    .context-menu-item {
        background: transparent;
        border: 1px solid transparent;
        border-radius: 0;
        box-shadow: none;
        padding: 7px 9px;
    }

    .context-menu-item:hover,
    .context-menu-item:focus {
        background: __panel_2__;
        border-color: __line_strong__;
        outline: none;
    }

    .context-menu-item.destructive {
        color: __red__;
    }

    .menu-separator {
        background: __line__;
        min-height: 1px;
        margin: 5px 2px;
    }

    .statusbar {
        border-top: 1px solid __line__;
        padding: 8px 2px 1px;
    }

    .empty-state {
        color: __foreground_65__;
        font-size: 13px;
    }

    .dialog-shell {
        background: __dark_background__;
        padding: 20px;
    }

    .settings-shell {
        background: __dark_background__;
    }

    .settings-header {
        background: __darker_background__;
        border-bottom: 1px solid __line__;
        padding: 22px 24px 18px;
    }

    .settings-content {
        padding: 22px 24px;
    }

    .settings-section {
        background: __code_bg__;
        border: 1px solid __line__;
        padding: 14px 16px 16px;
    }

    .settings-row {
        min-height: 48px;
        padding: 7px 0;
    }

    .settings-name {
        color: __foreground__;
        font-size: 12px;
        font-weight: 700;
    }

    .settings-description {
        color: __light_foreground__;
        font-size: 11px;
    }

    .settings-footer {
        background: __darker_background__;
        border-top: 1px solid __line__;
        padding: 12px 18px;
    }

    .loading-overlay {
        background: __accent_12__;
        border: 0;
        padding: 12px 16px;
    }

    .quick-preview {
        background: __darker_background__;
    }

    .preview-header {
        background: __dark_background__;
        border-bottom: 1px solid __line__;
        padding: 12px 16px;
    }

    .preview-title {
        color: __foreground__;
        font-size: 14px;
        font-weight: 800;
    }

    .preview-content {
        background: __code_bg__;
        padding: 14px;
    }

    .preview-text {
        background: __code_bg__;
        color: __foreground__;
        font-size: 12px;
        padding: 18px;
    }

    .preview-fallback {
        color: __light_foreground__;
    }

    .preview-footer {
        background: __dark_background__;
        border-top: 1px solid __line__;
        color: __light_foreground__;
        font-size: 10px;
        padding: 8px 16px;
    }

    .dialog-title {
        font-size: 16px;
        font-weight: 800;
    }

    .dialog-entry {
        background: __code_bg__;
        border: 1px solid __line__;
        border-radius: 0;
        padding: 8px 10px;
    }

    .dialog-entry:focus {
        border-color: __accent__;
    }

    scrollbar slider {
        background: __line_strong__;
        min-width: 5px;
        min-height: 5px;
        border-radius: 0;
    }

    gridview > child,
    listview > row,
    .file-tile,
    .list-row {
        transition: background 90ms ease, border-color 90ms ease;
    }

    gridview > child:focus-visible,
    listview > row:focus-visible,
    .file-tile:focus-visible,
    button:focus-visible,
    entry:focus-visible,
    searchentry:focus-visible {
        outline: 2px solid __accent__;
        outline-offset: 1px;
    }

    gridview > child:selected,
    listview > row:selected {
        background: __selection__;
        box-shadow: inset 0 0 0 1px __accent_30__;
    }

    .drop-target {
        background: __accent_12__;
        box-shadow: inset 0 0 0 1px __accent_30__;
    }

    .split-panel {
        border-left: 1px solid __line__;
        padding: 0 10px;
    }

    .palette-shell {
        background: __dark_background__;
        border: 1px solid __line_strong__;
        padding: 14px;
    }

    .palette-shell listbox {
        background: transparent;
    }

    .palette-shell listbox row {
        padding: 10px 12px;
        border-bottom: 1px solid __line__;
    }

    .palette-shell listbox row:selected {
        background: __selection__;
    }

    .shortcut-hint {
        color: __muted__;
        font-size: 11px;
    }

    @media (prefers-reduced-motion: reduce) {
        * {
            transition: none;
        }
    }
    """
    for token, value in tokens.items():
        css = css.replace(token, value)
    return css


def icon_for_item(item: FileItem) -> str:
    if item.is_dir:
        return "folder-symbolic"
    content_type = item.content_type or ""
    cached = ICON_NAME_CACHE.get(content_type)
    if isinstance(cached, str):
        return cached
    if content_type.startswith("image/"):
        icon_name = "image-x-generic-symbolic"
        ICON_NAME_CACHE.put(content_type, icon_name)
        return icon_name
    if content_type.startswith("video/"):
        icon_name = "video-x-generic-symbolic"
        ICON_NAME_CACHE.put(content_type, icon_name)
        return icon_name
    if content_type.startswith("audio/"):
        icon_name = "audio-x-generic-symbolic"
        ICON_NAME_CACHE.put(content_type, icon_name)
        return icon_name
    if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
        icon_name = "text-x-generic-symbolic"
        ICON_NAME_CACHE.put(content_type, icon_name)
        return icon_name
    if content_type in {"application/zip", "application/x-tar", "application/gzip", "application/x-7z-compressed"}:
        icon_name = "package-x-generic-symbolic"
        ICON_NAME_CACHE.put(content_type, icon_name)
        return icon_name
    try:
        themed = Gio.content_type_get_symbolic_icon(content_type)
        names = themed.get_names()
        if names:
            ICON_NAME_CACHE.put(content_type, names[0])
            return names[0]
    except (AttributeError, GLib.Error):
        pass
    ICON_NAME_CACHE.put(content_type, "text-x-generic-symbolic")
    return "text-x-generic-symbolic"


def is_image(item: FileItem) -> bool:
    return not item.is_dir and (item.content_type or "").startswith("image/") and bool(item.path)


def clear_box(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        child.unparent()
        child = next_child


format_size = core_format_size
format_date = core_format_date


class OmapathApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS | Gio.ApplicationFlags.HANDLES_OPEN)
        self.shutting_down = False
        self.window: Gtk.ApplicationWindow | None = None
        self.settings = load_settings()
        self.current_location: str | None = None
        self.back_history: list[str] = []
        self.forward_history: list[str] = []
        self.all_items: list[FileItem] = []
        self.selected_item: FileItem | None = None
        self.clipboard_items: list[FileItem] = []
        self.clipboard_mode = "copy"
        self.view_mode = str(self.settings["default_view"])
        if self.view_mode == "list":
            self.view_mode = "details"
        self.zoom_level = int(self.settings.get("zoom_level", 100))
        self.show_hidden = bool(self.settings["show_hidden"])
        self.theme_name = active_omarchy_theme()
        self.font_family = active_omarchy_font()
        self.css_provider: Gtk.CssProvider | None = None
        self.theme_watcher: ThemeWatcher | None = None
        self.theme_reload_source = 0
        self.folder_monitor: Gio.FileMonitor | None = None
        self.folder_reload_source = 0
        self.load_generation = 0
        self.load_cancellable: Gio.Cancellable | None = None
        self.loading_enumerator: Gio.FileEnumerator | None = None
        self.loading_items: list[FileItem] = []
        self.loading_location: str | None = None
        self.loading_folder: Gio.File | None = None
        self.load_error: str | None = None
        self.render_generation = 0
        self.render_source = 0
        self.pending_render_items: list[FileItem] = []
        self.size_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omapath-size")
        self.folder_size_jobs: dict[str, Future[int]] = {}
        self.git_status_future: Future[dict[str, str]] | None = None
        self.git_status_generation = 0
        self.operation_manager = OperationManager(
            workers=2,
            scheduler=lambda callback, *args: GLib.idle_add(lambda: bool(callback(*args) or False)),
        )
        self.preview_manager = PreviewManager(
            max_bytes=int(self.settings.get("preview_max_bytes", 262144)),
            cache_limit=int(self.settings.get("thumbnail_cache_limit", 32)),
        )
        self.thumbnail_manager = ThumbnailManager(limit=int(self.settings.get("thumbnail_cache_limit", 64)))
        self.thumbnail_targets: dict[int, str] = {}
        self.search_service = SearchService()
        self.search_debounce_source = 0
        self.search_generation = 0
        self.suppress_search_change = False
        self.search_result_paths: list[str] = []
        self.search_result_items: list[FileItem] = []
        self.search_result_index = 0
        self.recently_closed_tabs: list[TabState] = []
        self.split_enabled = False
        self.start_with_split = bool(self.settings.get("split_view", False))
        self.split_focus = 0
        self.operations: dict[str, OperationHandle] = {}
        self.palette_window: CommandPalette | None = None
        self.preview_window: Gtk.Window | None = None
        self.preview_items: list[FileItem] = []
        self.preview_item_index = 0
        self.terminal_process: Gio.Subprocess | None = None
        self.archive_future: Future[Any] | None = None
        self.terminal_panel_visible = bool(self.settings.get("show_terminal_panel", False))
        self.folders_panel_visible = bool(self.settings.get("show_folders_panel", False))
        self.pending_reveal_uri: str | None = None
        self.folder_view_settings: dict[str, dict[str, Any]] = dict(self.settings.get("folder_view_settings", {}))
        self.place_buttons: dict[str, Gtk.Button] = {}
        self.colors = read_omarchy_colors()

        self.home = str(Path.home())
        self.places = self.build_places()
        self.tabs: list[TabState] = [TabState(self.home, title="Home")]
        self.active_tab_index = 0
        self.tab_bar: Gtk.Box | None = None
        self.sidebar_user_hidden = False
        self.context_popover: Gtk.Popover | None = None
        self.current_context_item: FileItem | None = None
        self.pending_selection_uris: set[str] = set()
        self.split_loading_items: list[FileItem] = []
        self.sort_mode = str(self.settings["sort_mode"])
        self.sort_reverse = bool(self.settings["sort_reverse"])
        self.pinned_places: list[str] = [
            path for path in self.settings["pinned_places"]
            if path != TRASH_URI and os.path.isdir(os.path.expanduser(path))
        ]
        self.pinned_button_locations: set[str] = set()
        self.starred_places: list[str] = [
            canonical_location(location)
            for location in self.settings.get("starred_places", [])
            if isinstance(location, str) and canonical_location(location)
        ]
        self.recent_locations: list[str] = [
            canonical_location(location)
            for location in self.settings.get("recent_locations", [])
            if isinstance(location, str) and canonical_location(location)
        ]
        self.starred_button_locations: set[str] = set()
        self.recent_button_locations: set[str] = set()
        self.volume_monitor = Gio.VolumeMonitor.get()
        self.volume_signal_ids: list[int] = []
        self.device_buttons: dict[str, Gtk.Button] = {}
        self.sidebar_user_hidden = not bool(self.settings["show_sidebar"])
        self.show_additional_info = bool(self.settings.get("show_additional_info", True))
        self.show_groups = bool(self.settings.get("show_groups", False))
        self.batch_rename_queue: list[tuple[str, str]] = []
        self.batch_rename_total = 0
        self.batch_rename_active = False

    def build_places(self) -> list[tuple[str, str, str]]:
        special_dirs = {
            "Desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
            "Documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
            "Downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
            "Music": GLib.UserDirectory.DIRECTORY_MUSIC,
            "Pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
            "Videos": GLib.UserDirectory.DIRECTORY_VIDEOS,
        }
        icon_names = {
            "Home": "user-home-symbolic",
            "Desktop": "user-desktop-symbolic",
            "Documents": "folder-documents-symbolic",
            "Downloads": "folder-download-symbolic",
            "Music": "folder-music-symbolic",
            "Pictures": "folder-pictures-symbolic",
            "Videos": "folder-videos-symbolic",
        }
        places = [("Home", icon_names["Home"], self.home)]
        for name, directory in special_dirs.items():
            path = GLib.get_user_special_dir(directory)
            if path and os.path.isdir(path) and os.path.abspath(path) != os.path.abspath(self.home):
                places.append((name, icon_names[name], path))
        return places

    def do_activate(self) -> None:
        if self.window is None:
            self.build_window()
        assert self.window is not None
        self.window.present()

    def do_open(self, files: list[Gio.File], _n_files: int, _hint: str) -> None:
        self.do_activate()
        if files:
            target = files[0]
            self.navigate(target.get_path() or target.get_uri())

    def do_shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        self.load_generation += 1
        self.render_generation += 1
        self.search_generation += 1
        self.git_status_generation += 1
        if hasattr(self, "split_generation"):
            self.split_generation += 1
        if self.current_location and self.settings.get("remember_location"):
            self.settings["last_location"] = self.current_location
        if self.window is not None and self.settings.get("remember_window_size", True):
            self.settings["window_width"] = max(720, self.window.get_width())
            self.settings["window_height"] = max(480, self.window.get_height())
            self.settings["window_maximized"] = self.window.is_maximized()
        if hasattr(self, "split_pane"):
            self.settings["split_position"] = max(0.25, min(0.75, self.split_pane.get_position() / max(1, self.window.get_width() if self.window else 1)))
        if hasattr(self, "sidebar"):
            self.settings["sidebar_width"] = max(180, min(280, self.sidebar.get_width()))
        self.save_active_tab()
        if self.settings.get("remember_tabs", True):
            self.settings["session_tabs"] = [
                {
                    "location": tab.location,
                    "back_history": list(tab.back_history),
                    "forward_history": list(tab.forward_history),
                    "view_mode": tab.view_mode,
                    "title": tab.title,
                    "selected_uris": list(tab.selected_uris),
                    "scroll_value": tab.scroll_value,
                    "zoom_level": tab.zoom_level,
                    "sort_mode": tab.sort_mode,
                    "sort_reverse": tab.sort_reverse,
                    "show_hidden": tab.show_hidden,
                }
                for tab in self.tabs
            ]
            self.settings["active_tab"] = self.active_tab_index
        self.settings["pinned_places"] = list(self.pinned_places)
        self.settings["starred_places"] = list(self.starred_places)
        self.settings["recent_locations"] = list(self.recent_locations)
        self.settings["folder_view_settings"] = dict(self.folder_view_settings)
        self.settings["show_terminal_panel"] = self.terminal_panel_visible
        self.settings["show_folders_panel"] = self.folders_panel_visible
        save_settings(self.settings)
        if self.load_cancellable is not None:
            self.load_cancellable.cancel()
        self._close_loading_enumerator()
        if self.render_source:
            GLib.source_remove(self.render_source)
            self.render_source = 0
        if self.folder_reload_source:
            GLib.source_remove(self.folder_reload_source)
            self.folder_reload_source = 0
        if self.search_debounce_source:
            GLib.source_remove(self.search_debounce_source)
            self.search_debounce_source = 0
        if self.theme_reload_source:
            GLib.source_remove(self.theme_reload_source)
            self.theme_reload_source = 0
        if self.folder_monitor is not None:
            self.folder_monitor.cancel()
        if self.theme_watcher is not None:
            self.theme_watcher.stop()
        for handler_id in self.volume_signal_ids:
            try:
                self.volume_monitor.disconnect(handler_id)
            except (TypeError, ValueError):
                pass
        self.volume_signal_ids.clear()
        if getattr(self, "split_cancellable", None) is not None:
            self.split_cancellable.cancel()
        if getattr(self, "split_enumerator", None) is not None:
            try:
                self.split_enumerator.close(None)
            except GLib.Error:
                pass
            self.split_enumerator = None
        if self.preview_window is not None:
            self.preview_window.close()
        if self.terminal_process is not None:
            try:
                self.terminal_process.force_exit()
            except GLib.Error:
                pass
            self.terminal_process = None
        if self.archive_future is not None:
            self.archive_future.cancel()
            self.archive_future = None
        if self.git_status_future is not None:
            self.git_status_future.cancel()
            self.git_status_future = None
        self.preview_manager.cancel()
        self.size_executor.shutdown(wait=False, cancel_futures=True)
        self.search_service.shutdown()
        self.preview_manager.shutdown()
        self.thumbnail_manager.shutdown()
        self.operation_manager.shutdown()
        Gio.Application.do_shutdown(self)

    def build_window(self) -> None:
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title("Omapath")
        self.window.set_default_size(
            int(self.settings.get("window_width", 1320)),
            int(self.settings.get("window_height", 820)),
        )
        self.window.set_size_request(720, 480)
        self.window.connect("notify::width", self.on_window_width_changed)
        self.window.connect("notify::height", self.on_window_width_changed)

        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            provider.load_from_data(build_css(self.colors, self.font_family).encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            self.css_provider = provider

        self.setup_volume_monitor()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("app-shell")
        root.append(self.build_topbar())
        root.append(self.build_tabbar())

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        root.append(body)

        self.body = body
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_min_content_width(220)
        sidebar_scroll.set_hexpand(False)
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_child(self.build_sidebar())
        self.sidebar_scroll = sidebar_scroll
        body.append(sidebar_scroll)
        self.sidebar_separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.sidebar_separator)
        primary = self.build_main_pane()
        self.split_pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.split_pane.set_hexpand(True)
        self.split_pane.set_vexpand(True)
        self.split_pane.set_start_child(primary)
        self.split_panel = self.build_split_panel()
        self.split_pane.set_end_child(self.split_panel)
        self.split_pane.set_position(max(1, int(self.settings.get("split_position", 0.5) * self.settings.get("window_width", 1320))))
        self.split_pane.set_visible(False)
        body.append(self.split_pane)

        self.window.set_child(root)
        if self.settings.get("window_maximized", False):
            self.window.maximize()

        self.set_view(self.view_mode)
        self.watch_active_theme()

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.window.add_controller(key_controller)

        initial_location = self.home
        last_location = self.settings.get("last_location")
        if self.settings.get("remember_location") and self.settings.get("start_location") == "last" and isinstance(last_location, str):
            expanded = os.path.expanduser(last_location)
            if expanded == TRASH_URI or os.path.isdir(expanded):
                initial_location = expanded
        self.tabs[0].location = initial_location
        self.navigate(initial_location, record_history=False)
        self.restore_session_tabs()
        if self.start_with_split:
            self.toggle_split_view()
        self.sync_responsive_layout()

    def build_topbar(self) -> Gtk.Widget:
        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        topbar.add_css_class("topbar")

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.back_button = self.icon_button("go-previous-symbolic", "Back", self.go_back)
        self.forward_button = self.icon_button("go-next-symbolic", "Forward", self.go_forward)
        self.up_button = self.icon_button("go-up-symbolic", "Up", self.go_up)
        nav.append(self.back_button)
        nav.append(self.forward_button)
        nav.append(self.up_button)
        topbar.append(nav)

        self.location_stack = Gtk.Stack()
        self.location_stack.set_hexpand(True)
        self.location_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.breadcrumb_box.add_css_class("breadcrumb-row")
        breadcrumb_scroll = Gtk.ScrolledWindow()
        breadcrumb_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        breadcrumb_scroll.set_min_content_width(120)
        breadcrumb_scroll.set_child(self.breadcrumb_box)
        self.location_stack.add_named(breadcrumb_scroll, "breadcrumbs")

        self.location_entry = Gtk.Entry()
        self.location_entry.set_hexpand(True)
        self.location_entry.set_placeholder_text("Location")
        self.location_entry.add_css_class("location-entry")
        self.location_entry.connect("activate", self.on_location_activate)
        self.location_stack.add_named(self.location_entry, "location")
        self.location_stack.set_visible_child_name("breadcrumbs")
        topbar.append(self.location_stack)

        self.search_stack = Gtk.Stack()
        self.search_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.search_button = self.icon_button(
            "system-search-symbolic",
            "Search this folder (Ctrl+F)",
            self.show_search_entry,
        )
        self.search_stack.add_named(self.search_button, "button")
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search")
        self.search_entry.set_width_chars(14)
        self.search_entry.set_tooltip_text("Filter this folder (Ctrl+F)")
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_stack.add_named(self.search_entry, "entry")
        self.search_stack.set_visible_child_name("button")
        topbar.append(self.search_stack)

        self.grid_button = self.icon_button("view-grid-symbolic", "Icons view (Ctrl+1)", lambda *_: self.set_view("grid"))
        self.compact_button = self.icon_button("view-list-symbolic", "Compact view (Ctrl+2)", lambda *_: self.set_view("compact"))
        self.list_button = self.icon_button("view-list-bulleted-symbolic", "Details view (Ctrl+3)", lambda *_: self.set_view("details"))
        view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        view_box.add_css_class("view-toolbar")
        view_box.append(self.grid_button)
        view_box.append(self.list_button)
        topbar.append(view_box)

        self.overflow_button = self.icon_button("open-menu-symbolic", "More actions", self.show_overflow_menu)
        topbar.append(self.overflow_button)
        return topbar

    def show_overflow_menu(self, button: Gtk.Button, *_args) -> None:
        popover = Gtk.Popover()
        popover.set_parent(button)
        popover.set_has_arrow(False)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.add_css_class("context-menu")
        header = Gtk.Label(label="MENU")
        header.set_halign(Gtk.Align.START)
        header.add_css_class("context-menu-header")
        menu.append(header)
        self.menu_button(menu, popover, "NEW WINDOW", self.new_window)
        self.menu_button(menu, popover, "NEW FOLDER", self.new_folder, sensitive=self.can_write_location())
        self.menu_button(menu, popover, "COMMAND PALETTE", self.show_command_palette)
        self.menu_button(menu, popover, "CONNECT TO SERVER", self.connect_to_server)
        self.menu_button(menu, popover, "COMPACT VIEW", lambda: self.set_view("compact"), sensitive=self.view_mode != "compact")
        self.menu_separator(menu)
        self.menu_button(menu, popover, "TERMINAL PANEL", self.toggle_terminal_panel)
        self.menu_button(menu, popover, "FOLDERS PANEL", self.toggle_folders_panel)
        self.menu_button(menu, popover, "TOGGLE SIDEBAR", self.toggle_sidebar)
        self.menu_button(menu, popover, "SETTINGS", self.show_settings)
        popover.connect("closed", lambda *_args: popover.unparent() if popover.get_parent() is not None else None)
        popover.set_child(menu)
        popover.popup()

    def show_search_entry(self, *_args) -> None:
        self.search_stack.set_visible_child_name("entry")
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def hide_search_entry(self, clear: bool = True) -> None:
        if clear:
            self.suppress_search_change = True
            self.search_entry.set_text("")
            self.suppress_search_change = False
        self.search_stack.set_visible_child_name("button")

    def build_tabbar(self) -> Gtk.Widget:
        tabbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tabbar.add_css_class("tab-bar")
        self.tab_bar = tabbar
        self.tab_scroll = Gtk.ScrolledWindow()
        self.tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.tab_scroll.set_child(tabbar)
        self.tab_scroll.set_visible(len(self.tabs) > 1)
        self.update_tabs()
        return self.tab_scroll

    def tab_label(self, tab: TabState) -> str:
        if tab.location == TRASH_URI:
            return "Trash"
        if "://" in tab.location:
            return tab.location.split("://", 1)[-1].rstrip("/").split("/")[-1] or tab.location
        if os.path.abspath(tab.location) == os.path.abspath(self.home):
            return "Home"
        return os.path.basename(os.path.abspath(tab.location)) or "/"

    def restore_session_tabs(self) -> None:
        if not self.settings.get("remember_tabs", True):
            return
        restored: list[TabState] = []
        for raw in self.settings.get("session_tabs", []):
            if not isinstance(raw, dict) or not isinstance(raw.get("location"), str):
                continue
            location = self.normalize_location(raw["location"])
            if location != TRASH_URI and not os.path.isdir(location):
                continue
            mode = raw.get("view_mode") if raw.get("view_mode") in {"grid", "compact", "details", "list"} else "grid"
            if mode == "list":
                mode = "details"
            selected = raw.get("selected_uris", [])
            selected_uris = [uri for uri in selected if isinstance(uri, str)] if isinstance(selected, list) else []
            try:
                scroll_value = max(0.0, float(raw.get("scroll_value", 0.0)))
            except (TypeError, ValueError):
                scroll_value = 0.0
            restored.append(
                TabState(
                    location,
                    [path for path in raw.get("back_history", []) if isinstance(path, str)],
                    [path for path in raw.get("forward_history", []) if isinstance(path, str)],
                    mode,
                    str(raw.get("title") or self.tab_label(TabState(location))),
                    selected_uris,
                    scroll_value,
                    max(60, min(180, int(raw.get("zoom_level", self.settings.get("zoom_level", 100))))),
                    str(raw.get("sort_mode") if raw.get("sort_mode") in {"name", "modified", "size", "type"} else self.sort_mode),
                    bool(raw.get("sort_reverse", self.sort_reverse)),
                    bool(raw.get("show_hidden", self.show_hidden)),
                )
            )
        if not restored:
            return
        self.tabs = restored
        self.active_tab_index = min(int(self.settings.get("active_tab", 0)), len(self.tabs) - 1)
        active = self.tabs[self.active_tab_index]
        self.current_location = None
        self.back_history = list(active.back_history)
        self.forward_history = list(active.forward_history)
        self.view_mode = active.view_mode
        self.zoom_level = active.zoom_level
        self.sort_mode = active.sort_mode
        self.sort_reverse = active.sort_reverse
        self.show_hidden = active.show_hidden
        self.update_tabs()
        self.navigate(active.location, record_history=False, preserve_selection=list(active.selected_uris))

    def save_active_tab(self) -> None:
        if not self.tabs or self.current_location is None:
            return
        tab = self.tabs[self.active_tab_index]
        tab.location = self.current_location
        tab.back_history = list(self.back_history)
        tab.forward_history = list(self.forward_history)
        tab.view_mode = self.view_mode
        tab.title = self.tab_label(tab)
        tab.zoom_level = self.zoom_level
        tab.sort_mode = self.sort_mode
        tab.sort_reverse = self.sort_reverse
        tab.show_hidden = self.show_hidden
        if hasattr(self, "grid_scroll"):
            tab.scroll_value = self.active_scroll_adjustment().get_value()
        if hasattr(self, "grid"):
            tab.selected_uris = [item.uri for item in self.get_selected_items()]

    def update_tabs(self) -> None:
        if self.tab_bar is None:
            return
        clear_box(self.tab_bar)
        for index, tab in enumerate(self.tabs):
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            item.add_css_class("tab-item")
            if index == self.active_tab_index:
                item.add_css_class("active")

            select = Gtk.Button(label=self.tab_label(tab))
            select.set_hexpand(True)
            select.set_halign(Gtk.Align.FILL)
            select.set_tooltip_text(tab.location)
            select.add_css_class("tab-button")
            if index == self.active_tab_index:
                select.add_css_class("active")
            select.connect("clicked", lambda _button, tab_index=index: self.switch_tab(tab_index))
            middle = Gtk.GestureClick()
            middle.set_button(2)
            middle.connect("pressed", lambda _gesture, _count, _x, _y, tab_index=index: self.close_tab(tab_index))
            select.add_controller(middle)
            context = Gtk.GestureClick()
            context.set_button(3)
            context.connect("pressed", lambda gesture, _count, x, y, tab_index=index: self.show_tab_context(gesture, select, tab_index, x, y))
            select.add_controller(context)
            item.append(select)

            close = self.icon_button("window-close-symbolic", "Close tab", lambda _button, tab_index=index: self.close_tab(tab_index))
            close.add_css_class("tab-close")
            item.append(close)
            self.tab_bar.append(item)

        if hasattr(self, "tab_scroll"):
            self.tab_scroll.set_visible(len(self.tabs) > 1)

    def new_tab(self, *_args, location: str | None = None) -> None:
        self.save_active_tab()
        target = self.normalize_location(location or self.home)
        self.tabs.append(TabState(target, title=self.tab_label(TabState(target))))
        self.active_tab_index = len(self.tabs) - 1
        self.current_location = None
        self.back_history = []
        self.forward_history = []
        self.view_mode = str(self.settings.get("default_view", "grid"))
        if self.view_mode == "list":
            self.view_mode = "details"
        self.zoom_level = int(self.settings.get("zoom_level", 100))
        self.sort_mode = str(self.settings.get("sort_mode", "name"))
        self.sort_reverse = bool(self.settings.get("sort_reverse", False))
        self.show_hidden = bool(self.settings.get("show_hidden", False))
        self.update_tabs()
        self.navigate(target, record_history=False)

    def new_window(self, *_args) -> None:
        """Start an independent Omapath process for Dolphin-style windows."""

        try:
            Gio.Subprocess.new(
                [sys.executable, "-m", "omapath"],
                Gio.SubprocessFlags.NONE,
            )
            self.toast("New window started")
        except (GLib.Error, OSError) as error:
            self.toast(f"Could not open a new window: {error}")

    def duplicate_tab(self, index: int | None = None) -> None:
        source = self.tabs[index if index is not None else self.active_tab_index]
        self.tabs.append(
            TabState(
                location=source.location,
                back_history=list(source.back_history),
                forward_history=list(source.forward_history),
                view_mode=source.view_mode,
                title=source.title,
                selected_uris=list(source.selected_uris),
                scroll_value=source.scroll_value,
                zoom_level=source.zoom_level,
                sort_mode=source.sort_mode,
                sort_reverse=source.sort_reverse,
                show_hidden=source.show_hidden,
            )
        )
        self.active_tab_index = len(self.tabs) - 1
        self.current_location = None
        self.back_history = list(source.back_history)
        self.forward_history = list(source.forward_history)
        self.view_mode = source.view_mode
        self.zoom_level = source.zoom_level
        self.sort_mode = source.sort_mode
        self.sort_reverse = source.sort_reverse
        self.show_hidden = source.show_hidden
        self.update_tabs()
        self.navigate(source.location, record_history=False, preserve_selection=list(source.selected_uris))

    def reopen_closed_tab(self, *_args) -> None:
        if not self.recently_closed_tabs:
            self.toast("No closed tab to reopen")
            return
        tab = self.recently_closed_tabs.pop()
        self.tabs.append(tab)
        self.active_tab_index = len(self.tabs) - 1
        self.current_location = None
        self.back_history = list(tab.back_history)
        self.forward_history = list(tab.forward_history)
        self.view_mode = tab.view_mode
        self.zoom_level = tab.zoom_level
        self.sort_mode = tab.sort_mode
        self.sort_reverse = tab.sort_reverse
        self.show_hidden = tab.show_hidden
        self.update_tabs()
        self.navigate(tab.location, record_history=False, preserve_selection=list(tab.selected_uris))

    def show_tab_context(self, gesture: Gtk.GestureClick, parent: Gtk.Widget, index: int, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.Popover()
        popover.set_parent(parent)
        popover.set_has_arrow(False)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.add_css_class("context-menu")
        self.menu_button(menu, popover, "DUPLICATE TAB", lambda: self.duplicate_tab(index))
        self.menu_button(menu, popover, "CLOSE TAB", lambda: self.close_tab(index), destructive=True)
        popover.connect("closed", lambda *_args: popover.unparent() if popover.get_parent() is not None else None)
        popover.set_child(menu)
        popover.popup()

    def switch_tab(self, index: int) -> None:
        if index == self.active_tab_index or index < 0 or index >= len(self.tabs):
            return
        self.save_active_tab()
        self.active_tab_index = index
        tab = self.tabs[index]
        self.current_location = None
        self.back_history = list(tab.back_history)
        self.forward_history = list(tab.forward_history)
        self.view_mode = tab.view_mode
        self.zoom_level = tab.zoom_level
        self.sort_mode = tab.sort_mode
        self.sort_reverse = tab.sort_reverse
        self.show_hidden = tab.show_hidden
        self.update_tabs()
        self.navigate(tab.location, record_history=False, preserve_selection=list(tab.selected_uris))

    def close_tab(self, index: int) -> None:
        if len(self.tabs) == 1:
            self.toast("Keep at least one tab open")
            return
        self.save_active_tab()
        was_active = index == self.active_tab_index
        self.recently_closed_tabs.append(self.tabs[index])
        del self.recently_closed_tabs[:-32]
        del self.tabs[index]
        if index < self.active_tab_index:
            self.active_tab_index -= 1
        elif was_active:
            self.active_tab_index = min(self.active_tab_index, len(self.tabs) - 1)
        if was_active:
            tab = self.tabs[self.active_tab_index]
            self.current_location = None
            self.back_history = list(tab.back_history)
            self.forward_history = list(tab.forward_history)
            self.view_mode = tab.view_mode
            self.zoom_level = tab.zoom_level
            self.sort_mode = tab.sort_mode
            self.sort_reverse = tab.sort_reverse
            self.show_hidden = tab.show_hidden
            self.update_tabs()
            self.navigate(tab.location, record_history=False, preserve_selection=list(tab.selected_uris))
        else:
            self.update_tabs()

    def toggle_sidebar(self, *_args) -> None:
        self.sidebar_user_hidden = not self.sidebar_user_hidden
        self.settings["show_sidebar"] = not self.sidebar_user_hidden
        save_settings(self.settings)
        self.sync_responsive_layout()

    def settings_switch_row(self, parent: Gtk.Box, title: str, description: str, active: bool) -> Gtk.Switch:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.add_css_class("settings-row")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.set_hexpand(True)
        name = Gtk.Label(label=title)
        name.set_halign(Gtk.Align.START)
        name.add_css_class("settings-name")
        labels.append(name)
        hint = Gtk.Label(label=description)
        hint.set_halign(Gtk.Align.START)
        hint.set_wrap(True)
        hint.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        hint.add_css_class("settings-description")
        labels.append(hint)
        row.append(labels)
        switch = Gtk.Switch()
        set_accessible_label(switch, title)
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(active)
        row.append(switch)
        parent.append(row)
        return switch

    def settings_combo_row(
        self,
        parent: Gtk.Box,
        title: str,
        description: str,
        values: list[tuple[str, str]],
        active_id: str,
    ) -> Gtk.ComboBoxText:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.add_css_class("settings-row")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.set_hexpand(True)
        name = Gtk.Label(label=title)
        name.set_halign(Gtk.Align.START)
        name.add_css_class("settings-name")
        labels.append(name)
        hint = Gtk.Label(label=description)
        hint.set_halign(Gtk.Align.START)
        hint.set_wrap(True)
        hint.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        hint.add_css_class("settings-description")
        labels.append(hint)
        row.append(labels)
        combo = Gtk.ComboBoxText()
        for identifier, label in values:
            combo.append(identifier, label)
        combo.set_active_id(active_id)
        combo.set_valign(Gtk.Align.CENTER)
        row.append(combo)
        parent.append(row)
        return combo

    def show_settings(self, *_args) -> None:
        if getattr(self, "settings_window", None) is not None:
            self.settings_window.present()
            return
        dialog = Gtk.Window(title="Omapath · Settings", transient_for=self.window, modal=True)
        dialog.set_default_size(680, 700)
        dialog.set_resizable(True)
        self.settings_window = dialog

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        shell.add_css_class("settings-shell")
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.add_css_class("settings-header")
        title = Gtk.Label(label="WORKSPACE SETTINGS")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("dialog-title")
        header.append(title)
        subtitle = Gtk.Label(label="Tune navigation, rendering and preview behavior for this workspace.")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.add_css_class("path-meta")
        header.append(subtitle)
        shell.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.add_css_class("settings-content")

        appearance = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        appearance.add_css_class("settings-section")
        appearance_label = Gtk.Label(label="APPEARANCE")
        appearance_label.set_halign(Gtk.Align.START)
        appearance_label.add_css_class("section-label")
        appearance.append(appearance_label)
        view_combo = self.settings_combo_row(
            appearance,
            "Default view",
            "Choose the Dolphin-style layout used when a new tab opens.",
            [("grid", "Icons / grid"), ("compact", "Compact"), ("details", "Details")],
            self.normalize_view_mode(str(self.settings["default_view"])),
        )
        start_combo = self.settings_combo_row(
            appearance,
            "Startup location",
            "Open Home or restore the last location when the app starts.",
            [("home", "Home"), ("last", "Last location")],
            str(self.settings["start_location"]),
        )
        sort_combo = self.settings_combo_row(
            appearance,
            "Sort folders by",
            "Set the default ordering for every location.",
            [("name", "Name"), ("modified", "Modified"), ("size", "Size"), ("type", "Type")],
            str(self.settings["sort_mode"]),
        )
        sort_reverse_switch = self.settings_switch_row(
            appearance,
            "Descending order",
            "Reverse the selected sorting rule.",
            bool(self.settings["sort_reverse"]),
        )
        compact_switch = self.settings_switch_row(
            appearance,
            "Compact density",
            "Use tighter cards and smaller spacing for large folders.",
            bool(self.settings["compact_mode"]),
        )
        sidebar_switch = self.settings_switch_row(
            appearance,
            "Show Places sidebar",
            "Keep Places and pinned locations visible when the window allows it.",
            not self.sidebar_user_hidden,
        )
        hidden_switch = self.settings_switch_row(
            appearance,
            "Show hidden files",
            "Include dotfiles in every folder listing.",
            self.show_hidden,
        )
        additional_info_switch = self.settings_switch_row(
            appearance,
            "Additional details column",
            "Show permissions and creation time in Details view.",
            bool(self.settings.get("show_additional_info", True)),
        )
        groups_switch = self.settings_switch_row(
            appearance,
            "Group entries by type",
            "Keep a grouping preference ready for views that support section headers.",
            bool(self.settings.get("show_groups", False)),
        )
        folders_first_switch = self.settings_switch_row(
            appearance,
            "Folders first",
            "Keep folders before files regardless of the selected sort direction.",
            bool(self.settings.get("folders_first", True)),
        )
        natural_sort_switch = self.settings_switch_row(
            appearance,
            "Natural sorting",
            "Sort file2 before file10 instead of using byte order.",
            bool(self.settings.get("natural_sort", True)),
        )
        folders_panel_switch = self.settings_switch_row(
            appearance,
            "Folders panel",
            "Show a lightweight expandable folder tree in Places.",
            bool(self.settings.get("show_folders_panel", False)),
        )
        terminal_panel_switch = self.settings_switch_row(
            appearance,
            "Terminal panel",
            "Restore the embedded terminal panel below the file view.",
            bool(self.settings.get("show_terminal_panel", False)),
        )
        split_switch = self.settings_switch_row(
            appearance,
            "Restore split view",
            "Restore the two-panel workspace on the next launch.",
            bool(self.settings.get("split_view", False)),
        )
        theme_switch = self.settings_switch_row(
            appearance,
            "Follow Omarchy theme",
            "Reload palette and font when the active Omarchy theme changes.",
            bool(self.settings.get("follow_omarchy_theme", True)),
        )
        zoom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        zoom_row.add_css_class("settings-row")
        zoom_labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        zoom_labels.set_hexpand(True)
        zoom_title = Gtk.Label(label="Default zoom")
        zoom_title.set_halign(Gtk.Align.START)
        zoom_title.add_css_class("settings-name")
        zoom_labels.append(zoom_title)
        zoom_hint = Gtk.Label(label="Use Ctrl+Plus, Ctrl+Minus or Ctrl+0 while browsing.")
        zoom_hint.set_halign(Gtk.Align.START)
        zoom_hint.add_css_class("settings-description")
        zoom_labels.append(zoom_hint)
        zoom_row.append(zoom_labels)
        zoom_spin = Gtk.SpinButton.new_with_range(60, 180, 10)
        zoom_spin.set_value(float(self.settings.get("zoom_level", 100)))
        zoom_spin.set_numeric(True)
        zoom_spin.set_valign(Gtk.Align.CENTER)
        zoom_row.append(zoom_spin)
        appearance.append(zoom_row)
        content.append(appearance)

        behavior = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        behavior.add_css_class("settings-section")
        behavior_label = Gtk.Label(label="BEHAVIOR")
        behavior_label.set_halign(Gtk.Align.START)
        behavior_label.add_css_class("section-label")
        behavior.append(behavior_label)
        confirm_switch = self.settings_switch_row(
            behavior,
            "Confirm destructive actions",
            "Ask before moving items to Trash or deleting them permanently.",
            bool(self.settings["confirm_delete"]),
        )
        refresh_switch = self.settings_switch_row(
            behavior,
            "Live folder updates",
            "Refresh the current folder when files change outside the app.",
            bool(self.settings["auto_refresh"]),
        )
        remember_switch = self.settings_switch_row(
            behavior,
            "Remember last location",
            "Restore the last active location at the next launch.",
            bool(self.settings["remember_location"]),
        )
        recursive_search_switch = self.settings_switch_row(
            behavior,
            "Search recursively",
            "Search descendants in the background instead of only this folder.",
            bool(self.settings.get("search_recursive", False)),
        )
        case_search_switch = self.settings_switch_row(
            behavior,
            "Case-sensitive search",
            "Keep letter case significant in recursive searches.",
            bool(self.settings.get("search_case_sensitive", False)),
        )
        content_search_switch = self.settings_switch_row(
            behavior,
            "Search inside text files",
            "Search the first bounded chunk of text files when the name does not match.",
            bool(self.settings.get("search_contents", False)),
        )
        conflict_combo = self.settings_combo_row(
            behavior,
            "File conflict behavior",
            "Ask before the first conflict, or choose a default policy for queued operations.",
            [("ask", "Ask before operation"), ("rename", "Keep both / rename"), ("skip", "Skip"), ("replace", "Replace")],
            str(self.settings.get("conflict_behavior", "ask")),
        )
        content.append(behavior)

        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview.add_css_class("settings-section")
        preview_label = Gtk.Label(label="PREVIEW & METADATA")
        preview_label.set_halign(Gtk.Align.START)
        preview_label.add_css_class("section-label")
        preview.append(preview_label)
        thumbnails_switch = self.settings_switch_row(
            preview,
            "Image thumbnails",
            "Use cached image previews in file tiles and Quick Look.",
            bool(self.settings["show_thumbnails"]),
        )
        folder_size_switch = self.settings_switch_row(
            preview,
            "Calculate folder sizes on selection",
            "Compute directory sizes in a background worker; large folders may take time.",
            bool(self.settings["show_folder_sizes"]),
        )
        git_status_switch = self.settings_switch_row(
            preview,
            "Git status in Details",
            "Run a bounded Git status lookup only when this optional decoration is enabled.",
            bool(self.settings.get("show_git_status", False)),
        )
        quicklook_switch = self.settings_switch_row(
            preview,
            "Quick Look enabled",
            "Use Space for an asynchronous preview window.",
            bool(self.settings.get("quick_look_enabled", True)),
        )
        image_preview_switch = self.settings_switch_row(
            preview,
            "Image preview",
            "Decode image previews lazily in the preview worker.",
            bool(self.settings.get("image_preview", True)),
        )
        text_preview_switch = self.settings_switch_row(
            preview,
            "Text preview",
            "Read text only up to the configured byte limit.",
            bool(self.settings.get("text_preview", True)),
        )
        bytes_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bytes_row.add_css_class("settings-row")
        bytes_labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        bytes_labels.set_hexpand(True)
        bytes_title = Gtk.Label(label="Text preview limit")
        bytes_title.set_halign(Gtk.Align.START)
        bytes_title.add_css_class("settings-name")
        bytes_labels.append(bytes_title)
        bytes_hint = Gtk.Label(label="Maximum bytes read when previewing text with Space.")
        bytes_hint.set_halign(Gtk.Align.START)
        bytes_hint.add_css_class("settings-description")
        bytes_labels.append(bytes_hint)
        bytes_row.append(bytes_labels)
        bytes_spin = Gtk.SpinButton.new_with_range(4096, 4 * 1024 * 1024, 4096)
        bytes_spin.set_value(float(self.settings["preview_max_bytes"]))
        bytes_spin.set_numeric(True)
        bytes_spin.set_valign(Gtk.Align.CENTER)
        bytes_row.append(bytes_spin)
        preview.append(bytes_row)
        clear_cache = Gtk.Button(label="CLEAR PREVIEW CACHE")
        clear_cache.set_halign(Gtk.Align.START)
        clear_cache.add_css_class("action-button")
        clear_cache.connect("clicked", lambda *_args: self.clear_preview_cache())
        preview.append(clear_cache)
        content.append(preview)

        scroll.set_child(content)
        shell.append(scroll)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("settings-footer")
        cancel = Gtk.Button(label="CANCEL")
        cancel.add_css_class("action-button")
        cancel.connect("clicked", lambda *_args: dialog.close())
        footer.append(cancel)
        reset = Gtk.Button(label="RESET")
        reset.add_css_class("action-button")
        reset.connect("clicked", lambda *_args: self.reset_settings_dialog(dialog))
        footer.append(reset)
        save = Gtk.Button(label="SAVE SETTINGS")
        save.add_css_class("action-button")
        save.add_css_class("solid")
        save.set_hexpand(True)
        save.connect(
            "clicked",
            lambda *_args: self.apply_settings_from_dialog(
                dialog,
                view_combo,
                start_combo,
                sort_combo,
                sort_reverse_switch,
                compact_switch,
                sidebar_switch,
                hidden_switch,
                split_switch,
                theme_switch,
                confirm_switch,
                refresh_switch,
                remember_switch,
                recursive_search_switch,
                case_search_switch,
                content_search_switch,
                conflict_combo,
                thumbnails_switch,
                folder_size_switch,
                git_status_switch,
                quicklook_switch,
                image_preview_switch,
                text_preview_switch,
                bytes_spin,
                additional_info_switch,
                groups_switch,
                folders_first_switch,
                natural_sort_switch,
                folders_panel_switch,
                terminal_panel_switch,
                zoom_spin,
            ),
        )
        footer.append(save)
        shell.append(footer)
        dialog.connect("close-request", lambda *_args: self.clear_settings_window(dialog))
        dialog.set_child(shell)
        dialog.present()

    def clear_settings_window(self, dialog: Gtk.Window) -> bool:
        if getattr(self, "settings_window", None) is dialog:
            self.settings_window = None
        return False

    def show_command_palette(self, *_args) -> None:
        if self.palette_window is not None:
            self.palette_window.present()
            self.palette_window.entry.grab_focus()
            return
        actions = self.palette_actions()
        palette = CommandPalette(self.window, actions)
        self.palette_window = palette
        palette.connect("close-request", lambda *_close: self.clear_palette(palette))
        palette.present()
        palette.entry.grab_focus()

    def clear_palette(self, palette: CommandPalette) -> None:
        if self.palette_window is palette:
            self.palette_window = None

    def palette_actions(self) -> list[PaletteAction]:
        item = self.selected_item or self.get_selected_item()
        items = self.get_selected_items()
        local_items = bool(items) and all(entry.path for entry in items)
        deletable_items = bool(items) and (self.current_location == TRASH_URI or local_items)
        local_folder = bool(self.current_location and self.current_location != TRASH_URI and "://" not in self.current_location)
        writable_folder = local_folder and self.can_write_location()
        pinned = bool(item and item.is_dir and item.path and item.path in self.pinned_places)
        starred = bool(item and self.is_starred(item.uri))
        return [
            PaletteAction("terminal", "Open terminal here", self.open_terminal_here, enabled=local_folder),
            PaletteAction("new-folder", "Create folder", self.new_folder, "Ctrl+Shift+N", writable_folder),
            PaletteAction("new-file", "Create file", self.new_file, enabled=writable_folder),
            PaletteAction("template", "New from template", self.show_template_picker, enabled=writable_folder and bool(discover_templates())),
            PaletteAction("server", "Connect to server", self.connect_to_server),
            PaletteAction("rename", "Rename", self.rename_selected, "F2", bool(item and item.path)),
            PaletteAction("copy", "Copy", self.copy_selected, "Ctrl+C", local_items),
            PaletteAction("cut", "Cut", self.cut_selected, "Ctrl+X", local_items),
            PaletteAction("paste", "Paste", self.paste_items, "Ctrl+V", bool(self.clipboard_items and writable_folder)),
            PaletteAction("duplicate", "Duplicate", self.duplicate_selected, "", local_items),
            PaletteAction("link", "Create symbolic link here", self.link_selected_here, "", local_items and writable_folder),
            PaletteAction("copy-path", "Copy path", self.copy_selected_path, "", bool(item and item.path)),
            PaletteAction("copy-uri", "Copy URI", self.copy_selected_uri, "", bool(item)),
            PaletteAction("open-with", "Open With", lambda: self.open_with(item), "", bool(item and item.path)),
            PaletteAction("new-tab", "Open in New Tab", lambda: self.open_item_new_tab(item), "", bool(item and item.is_dir)),
            PaletteAction("split-tab", "Open in Split", lambda: self.navigate_split(item.path or item.uri), "", bool(item and item.is_dir and item.path)),
            PaletteAction("trash", "Move to Trash", self.delete_selected, "Delete", deletable_items),
            PaletteAction("permanent-delete", "Delete permanently", self.permanent_delete_selected, "Shift+Delete", deletable_items),
            PaletteAction("pin", "Unpin from sidebar" if pinned else "Pin to sidebar", lambda: self.unpin_path(item.path) if pinned and item and item.path else self.pin_item(item) if item else None, "", bool(item and item.is_dir and item.path)),
            PaletteAction("star", "Unstar" if starred else "Star", lambda: self.unstar_item(item) if starred else self.star_item(item), "", bool(item)),
            PaletteAction("script", "Run script", lambda: self.show_script_picker(items), "", bool(discover_scripts()) and bool(items) and local_items),
            PaletteAction("hidden", "Toggle hidden files", self.toggle_hidden, "Ctrl+H"),
            PaletteAction("split", "Toggle split view", self.toggle_split_view, "F3"),
            PaletteAction("icons", "Switch to icons view", lambda: self.set_view("grid"), "Ctrl+1", self.view_mode != "grid"),
            PaletteAction("compact", "Switch to compact view", lambda: self.set_view("compact"), "Ctrl+2", self.view_mode != "compact"),
            PaletteAction("details", "Switch to details view", lambda: self.set_view("details"), "Ctrl+3", self.view_mode != "details"),
            PaletteAction("sort-name", "Sort by name", lambda: self.set_sort_order("name"), ""),
            PaletteAction("sort-modified", "Sort by modified", lambda: self.set_sort_order("modified"), ""),
            PaletteAction("sort-size", "Sort by size", lambda: self.set_sort_order("size"), ""),
            PaletteAction("sort-type", "Sort by type", lambda: self.set_sort_order("type"), ""),
            PaletteAction("folders-first", "Toggle folders first", self.toggle_folders_first),
            PaletteAction("natural-sort", "Toggle natural sorting", self.toggle_natural_sort),
            PaletteAction("groups", "Toggle groups", self.toggle_groups),
            PaletteAction("folders-panel", "Toggle folders panel", self.toggle_folders_panel, "F7"),
            PaletteAction("terminal-panel", "Toggle terminal panel", self.toggle_terminal_panel, "F4"),
            PaletteAction("invert-selection", "Invert selection", self.invert_selection_current, "Ctrl+Shift+A"),
            PaletteAction("zoom-in", "Zoom in", lambda: self.change_zoom(10), "Ctrl++"),
            PaletteAction("zoom-out", "Zoom out", lambda: self.change_zoom(-10), "Ctrl+-"),
            PaletteAction("zoom-reset", "Reset zoom", lambda: self.set_zoom(100), "Ctrl+0", self.zoom_level != 100),
            PaletteAction("new-window", "New window", self.new_window, "Ctrl+N"),
            PaletteAction("show-target", "Show target", lambda: self.show_target(item), "", bool(item and item.path)),
            PaletteAction("compare", "Compare selected files", self.compare_selected, "", len(items) == 2),
            PaletteAction("batch-rename", "Batch rename selection", self.batch_rename_selected, "", len(items) > 1),
            PaletteAction("compress", "Compress selection", self.compress_selected, "", bool(items) and local_items and writable_folder),
            PaletteAction("extract", "Extract archive", self.extract_selected, "", len(items) == 1 and self.archive_kind(item) is not None),
            PaletteAction("properties", "Properties", lambda: self.show_properties_for_selection(item), "", bool(item or items)),
            PaletteAction("settings", "Settings", self.show_settings),
            PaletteAction("refresh", "Refresh", self.refresh, "F5"),
            PaletteAction("undo", "Undo last safe operation", self.undo_last, "Ctrl+Z", len(self.operation_manager.undo) > 0),
        ]

    def copy_selected_path(self) -> None:
        item = self.selected_item or self.get_selected_item()
        if item is not None:
            self.copy_path(item)

    def copy_selected_uri(self) -> None:
        item = self.selected_item or self.get_selected_item()
        if item is not None:
            self.copy_uri(item)

    def copy_path(self, item: FileItem) -> None:
        if item.path and self.window is not None:
            self.window.get_clipboard().set_text(item.path)
            self.toast("Copied path")

    def set_external_clipboard(self, items: list[FileItem], cut: bool = False) -> None:
        """Publish a standards-compatible URI list for other file managers."""

        if not self.settings.get("external_clipboard", True) or self.window is None:
            return
        uris = [item.uri for item in items if item.path]
        if not uris:
            return
        uri_text = "\r\n".join(uris) + "\r\n"
        try:
            uri_provider = Gdk.ContentProvider.new_for_bytes(
                "text/uri-list",
                GLib.Bytes.new(uri_text.encode("utf-8")),
            )
            copied_text = ("cut" if cut else "copy") + "\n" + "\n".join(uris) + "\n"
            copied_provider = Gdk.ContentProvider.new_for_bytes(
                "x-special/gnome-copied-files",
                GLib.Bytes.new(copied_text.encode("utf-8")),
            )
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_value("\n".join(uris))
            text_provider = Gdk.ContentProvider.new_for_value(value)
            provider = Gdk.ContentProvider.new_union([uri_provider, copied_provider, text_provider])
            self.window.get_clipboard().set_content(provider)
        except (GLib.Error, TypeError, ValueError):
            # The internal clipboard remains available even on older GDK builds.
            return

    def read_external_clipboard(self) -> None:
        if self.window is None:
            return
        clipboard = self.window.get_clipboard()

        def received(source: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
            try:
                text = source.read_text_finish(result)
            except GLib.Error:
                text = None
            paths = self.dropped_paths(text or "")
            if not paths:
                self.toast("Clipboard contains no local files")
                return
            self.paste_external_paths(paths)

        clipboard.read_text_async(None, received)

    def paste_external_paths(self, paths: list[str]) -> None:
        destination_dir = self.current_location or self.home
        if not self.can_write_location(destination_dir):
            self.toast("Paste is available for writable local folders only")
            return
        self.start_operation(OperationKind.COPY, paths, destination_dir)

    def copy_uri(self, item: FileItem) -> None:
        if self.window is not None:
            self.window.get_clipboard().set_text(item.uri)
            self.toast("Copied URI")

    def select_all_current(self) -> None:
        self.active_file_view().select_all()

    def invert_selection_current(self) -> None:
        self.active_file_view().invert_selection()
        self.update_inspector(self.get_selected_item())
        self.save_active_tab()

    def permanent_delete_items(self, items: list[FileItem]) -> None:
        paths = self.operation_paths(items)
        if paths:
            self.confirm(
                "Delete permanently",
                f"Delete {len(paths)} item{'s' if len(paths) != 1 else ''} permanently?",
                "DELETE PERMANENTLY",
                lambda: self.start_operation(OperationKind.DELETE, paths),
            )
        elif items:
            self.toast("Permanent delete is unavailable for this location")

    def show_directory_properties(self) -> None:
        if self.current_location and self.current_location != TRASH_URI and "://" not in self.current_location:
            self.show_properties(
                FileItem(
                    file=Gio.File.new_for_path(self.current_location),
                    name=os.path.basename(self.current_location) or "/",
                    display_name=os.path.basename(self.current_location) or "/",
                    is_dir=True,
                    size=0,
                    modified=0,
                    content_type="inode/directory",
                    hidden=False,
                    is_symlink=False,
                )
            )

    def duplicate_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            items = [item] if item is not None else []
        paths = [item.path for item in items if item.path]
        if paths:
            self.start_operation(OperationKind.DUPLICATE, paths, self.current_location)
        elif items:
            self.toast("Duplicate is available for local items only")

    def link_selected_here(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            items = [item] if item is not None else []
        paths = [item.path for item in items if item.path]
        if not paths:
            self.toast("Link is available for local items only")
            return
        if not self.can_write_location():
            self.toast("The current folder is not writable")
            return
        self.start_operation(OperationKind.LINK, paths, self.current_location)

    def operation_paths(self, items: list[FileItem]) -> list[str]:
        values = [item.uri if self.current_location == TRASH_URI else item.path for item in items]
        return [value for value in values if value]

    def permanent_delete_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            items = [item] if item is not None else []
        paths = self.operation_paths(items) if items else []
        if paths:
            self.confirm(
                "Delete permanently",
                f"Delete {len(paths)} selected item{'s' if len(paths) != 1 else ''} permanently?",
                "DELETE PERMANENTLY",
                lambda: self.start_operation(OperationKind.DELETE, paths),
            )
        elif items:
            self.toast("Permanent delete is unavailable for this location")

    def open_terminal_here(self, item: FileItem | None = None, *_args) -> None:
        location = item.path if item is not None and item.is_dir and item.path else self.current_location
        location = location if location != TRASH_URI else self.home
        if not location:
            return
        if "://" in location:
            self.toast("Terminal integration is available for local folders only")
            return
        configured = os.environ.get("OMAPATH_TERMINAL") or os.environ.get("TERMINAL")
        if not configured:
            self.terminal_panel_visible = False
            self.toggle_terminal_panel()
            self.terminal_entry.grab_focus()
            self.toast("Embedded terminal panel opened")
            return
        import shlex

        argv = shlex.split(configured)
        if not argv:
            self.toast("Terminal configuration is empty")
            return
        executable = os.path.basename(argv[0])
        if executable in {"foot", "footclient"}:
            argv += ["--working-directory", location]
        elif executable in {"kitty", "alacritty"}:
            argv += ["--directory", location] if executable == "kitty" else ["--working-directory", location]
        elif executable == "wezterm":
            argv += ["start", "--cwd", location]
        elif executable in {"gnome-terminal", "kgx"}:
            argv += [f"--working-directory={location}"]
        else:
            self.toast("This terminal does not expose a safe working-directory option")
            return
        try:
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.toast(f"Could not open terminal: {error.message}")

    def open_with(self, item: FileItem | None) -> None:
        if item is None or not item.path:
            return
        try:
            info = item.file.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
            content_type = info.get_content_type() or "application/octet-stream"
            applications = Gio.AppInfo.get_all_for_type(content_type)
        except GLib.Error as error:
            self.toast(f"Could not find compatible applications: {error.message}")
            return
        if not applications:
            self.toast("No compatible application is installed")
            return
        dialog = Gtk.Window(title=f"Open {item.display_name} with", transient_for=self.window, modal=True)
        dialog.set_default_size(420, 320)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        title = Gtk.Label(label="OPEN WITH")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("eyebrow")
        box.append(title)
        for application in applications:
            button = Gtk.Button(label=application.get_display_name())
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, app=application: self.launch_with(app, item, dialog))
            box.append(button)
        dialog.set_child(box)
        dialog.present()

    def launch_with(self, application: Gio.AppInfo, item: FileItem, dialog: Gtk.Window) -> None:
        try:
            application.launch([item.file], None)
            dialog.close()
        except GLib.Error as error:
            self.toast(f"Could not open item: {error.message}")


    def reset_settings_dialog(self, dialog: Gtk.Window) -> None:
        self.settings_window = None
        dialog.close()
        self.settings = DEFAULT_SETTINGS.copy()
        self.show_hidden = False
        self.sort_mode = "name"
        self.sort_reverse = False
        self.sidebar_user_hidden = False
        self.apply_settings({**DEFAULT_SETTINGS, "pinned_places": list(self.pinned_places)})
        self.show_settings()

    def apply_settings_from_dialog(self, dialog: Gtk.Window, view_combo: Gtk.ComboBoxText, start_combo: Gtk.ComboBoxText, sort_combo: Gtk.ComboBoxText, sort_reverse_switch: Gtk.Switch, compact_switch: Gtk.Switch, sidebar_switch: Gtk.Switch, hidden_switch: Gtk.Switch, split_switch: Gtk.Switch, theme_switch: Gtk.Switch, confirm_switch: Gtk.Switch, refresh_switch: Gtk.Switch, remember_switch: Gtk.Switch, recursive_search_switch: Gtk.Switch, case_search_switch: Gtk.Switch, content_search_switch: Gtk.Switch, conflict_combo: Gtk.ComboBoxText, thumbnails_switch: Gtk.Switch, folder_size_switch: Gtk.Switch, git_status_switch: Gtk.Switch, quicklook_switch: Gtk.Switch, image_preview_switch: Gtk.Switch, text_preview_switch: Gtk.Switch, bytes_spin: Gtk.SpinButton, additional_info_switch: Gtk.Switch, groups_switch: Gtk.Switch, folders_first_switch: Gtk.Switch, natural_sort_switch: Gtk.Switch, folders_panel_switch: Gtk.Switch, terminal_panel_switch: Gtk.Switch, zoom_spin: Gtk.SpinButton) -> None:
        settings = dict(self.settings)
        settings.update(
            {
                "default_view": view_combo.get_active_id() or "grid",
                "start_location": start_combo.get_active_id() or "last",
                "sort_mode": sort_combo.get_active_id() or "name",
                "sort_reverse": sort_reverse_switch.get_active(),
                "compact_mode": compact_switch.get_active(),
                "show_sidebar": sidebar_switch.get_active(),
                "split_view": split_switch.get_active(),
                "follow_omarchy_theme": theme_switch.get_active(),
                "show_hidden": hidden_switch.get_active(),
                "confirm_delete": confirm_switch.get_active(),
                "auto_refresh": refresh_switch.get_active(),
                "remember_location": remember_switch.get_active(),
                "search_recursive": recursive_search_switch.get_active(),
                "search_case_sensitive": case_search_switch.get_active(),
                "search_contents": content_search_switch.get_active(),
                "conflict_behavior": conflict_combo.get_active_id() or "ask",
                "show_thumbnails": thumbnails_switch.get_active(),
                "show_folder_sizes": folder_size_switch.get_active(),
                "show_git_status": git_status_switch.get_active(),
                "quick_look_enabled": quicklook_switch.get_active(),
                "image_preview": image_preview_switch.get_active(),
                "text_preview": text_preview_switch.get_active(),
                "preview_max_bytes": int(bytes_spin.get_value()),
                "show_additional_info": additional_info_switch.get_active(),
                "show_groups": groups_switch.get_active(),
                "folders_first": folders_first_switch.get_active(),
                "natural_sort": natural_sort_switch.get_active(),
                "show_folders_panel": folders_panel_switch.get_active(),
                "show_terminal_panel": terminal_panel_switch.get_active(),
                "zoom_level": int(zoom_spin.get_value()),
            }
        )
        dialog.close()
        self.apply_settings(settings)

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.settings = validate_settings(settings)
        settings = self.settings
        self.show_hidden = bool(settings.get("show_hidden", False))
        self.sort_mode = str(settings.get("sort_mode", "name"))
        self.sort_reverse = bool(settings.get("sort_reverse", False))
        self.folder_view_settings = dict(settings.get("folder_view_settings", {}))
        self.starred_places = [
            canonical_location(location)
            for location in settings.get("starred_places", [])
            if isinstance(location, str) and canonical_location(location)
        ]
        self.recent_locations = [
            canonical_location(location)
            for location in settings.get("recent_locations", [])
            if isinstance(location, str) and canonical_location(location)
        ]
        self.sidebar_user_hidden = not bool(settings.get("show_sidebar", True))
        if not settings.get("auto_refresh", True) and self.folder_monitor is not None:
            self.folder_monitor.cancel()
            self.folder_monitor = None
        elif settings.get("auto_refresh", True) and self.current_location:
            self.watch_current_folder()
        self.preview_manager.clear()
        self.thumbnail_manager.clear()
        self.preview_manager.max_bytes = int(settings.get("preview_max_bytes", 262144))
        self.preview_manager.cache.set_limit(int(settings.get("thumbnail_cache_limit", 32)))
        self.thumbnail_manager.cache.set_limit(int(settings.get("thumbnail_cache_limit", 64)))
        save_settings(self.settings)
        self.watch_active_theme()
        if hasattr(self, "grid"):
            self.grid.set_single_click_activate(False)
            self.compact_view.set_single_click_activate(False)
            self.listbox.set_single_click_activate(False)
            self.zoom_level = int(settings.get("zoom_level", self.zoom_level))
            self.show_additional_info = bool(settings.get("show_additional_info", True))
            self.show_groups = bool(settings.get("show_groups", False))
            self.set_view(str(settings.get("default_view", self.view_mode)))
            self.folders_panel_visible = bool(settings.get("show_folders_panel", False))
            self.terminal_panel_visible = bool(settings.get("show_terminal_panel", False))
            self.folders_section.set_visible(self.folders_panel_visible)
            self.terminal_panel.set_visible(self.terminal_panel_visible)
            self.update_starred_places()
            self.update_recent_places()
            self.update_folder_panel()
            if bool(settings.get("split_view", False)) != self.split_enabled:
                self.toggle_split_view()
            self.sync_responsive_layout()
            self.load_items()
        self.toast("Settings applied")

    def clear_preview_cache(self) -> None:
        self.preview_manager.clear()
        self.thumbnail_manager.clear()
        self.toast("Preview cache cleared")

    def watch_active_theme(self) -> None:
        """Reload the app palette when `omarchy theme set` updates its state."""

        if self.theme_watcher is not None:
            self.theme_watcher.stop()
            self.theme_watcher = None
        if not self.settings.get("follow_omarchy_theme", True):
            return
        self.theme_watcher = ThemeWatcher(self.on_theme_state_changed)
        self.theme_watcher.start()

    def on_theme_state_changed(self, *_args) -> None:
        if self.shutting_down:
            return
        if self.theme_reload_source:
            GLib.source_remove(self.theme_reload_source)
        self.theme_reload_source = GLib.timeout_add(300, self.reload_active_theme)

    def reload_active_theme(self) -> bool:
        self.theme_reload_source = 0
        if self.shutting_down:
            return False
        self.theme_name = active_omarchy_theme()
        self.font_family = active_omarchy_font()
        self.colors = read_omarchy_colors()
        if self.css_provider is not None:
            self.css_provider.load_from_data(build_css(self.colors, self.font_family).encode("utf-8"))
        self.toast(f"Theme synced: {self.theme_name}")
        return False

    def icon_button(self, icon_name: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name(icon_name)
        button.set_tooltip_text(tooltip)
        set_accessible_label(button, tooltip)
        button.add_css_class("icon-button")
        button.connect("clicked", callback)
        return button

    def drag_items(self, item: FileItem) -> list[FileItem]:
        selected = self.get_selected_items()
        return selected if item in selected else [item]

    def drag_paths(self, item: FileItem) -> list[str]:
        return [entry.path for entry in self.drag_items(item) if entry.path]

    def split_drag_paths(self, item: FileItem) -> list[str]:
        selected = self.split_view.get_selected_items() if hasattr(self, "split_view") else []
        entries = selected if item in selected else [item]
        return [entry.path for entry in entries if entry.path]

    def on_virtual_context(self, widget: Gtk.Widget, item: FileItem | None, x: float, y: float) -> None:
        if item is not None:
            self.update_inspector(item)
        self.show_context_menu(widget, x, y, item)

    def on_virtual_drop(self, value: Any, destination: str | None, move: bool, link: bool = False) -> bool:
        target_path = destination or self.current_location
        if not target_path or target_path == TRASH_URI:
            self.toast("Drop into Trash is disabled; use Move to Trash")
            return False
        paths = self.dropped_paths(value)
        if not paths:
            self.toast("No local files found in the drop")
            return False
        return self.import_paths(paths, target_path, move, link)

    def start_operation(self, kind: OperationKind, paths: list[str], destination: str | None = None, policy_override: ConflictPolicy | None = None) -> bool:
        clean_paths = [path for path in paths if path]
        if not clean_paths:
            return False
        configured_policy = str(self.settings.get("conflict_behavior", "ask"))
        if policy_override is None and configured_policy == "ask" and destination and kind in {OperationKind.COPY, OperationKind.MOVE, OperationKind.DUPLICATE}:
            conflict = next(
                (
                    (source, os.path.join(destination, os.path.basename(os.path.normpath(source))))
                    for source in clean_paths
                    if os.path.lexists(os.path.join(destination, os.path.basename(os.path.normpath(source))))
                ),
                None,
            )
            if conflict is not None:
                self.show_conflict_dialog(kind, clean_paths, destination, conflict[0], conflict[1])
                return True
        policy = policy_override or ConflictPolicy("rename" if configured_policy == "ask" else configured_policy)
        try:
            handle = self.operation_manager.submit(
                kind,
                clean_paths,
                destination,
                policy=policy,
                on_progress=self.on_operation_progress,
                on_complete=self.on_operation_complete,
            )
        except (RuntimeError, OSError, ValueError) as error:
            self.toast(f"Could not start operation: {error}")
            return False
        self.operations[handle.operation_id] = handle
        self.operation_progress.set_visible(True)
        self.cancel_operation_button.set_visible(True)
        self.operation_progress.set_fraction(0.0)
        self.status_label.set_text(f"{kind.value.title()} queued · {len(clean_paths)} item{'s' if len(clean_paths) != 1 else ''}")
        return True

    def show_conflict_dialog(self, kind: OperationKind, paths: list[str], destination: str, source: str, target: str) -> None:
        dialog = Gtk.Window(title="File conflict", transient_for=self.window, modal=True)
        dialog.set_default_size(560, 330)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.set_margin_top(16)
        shell.set_margin_bottom(16)
        shell.set_margin_start(16)
        shell.set_margin_end(16)
        shell.add_css_class("dialog-shell")
        title = Gtk.Label(label="A file with this name already exists")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("dialog-title")
        shell.append(title)
        try:
            source_info = os.stat(source, follow_symlinks=False)
            target_info = os.stat(target, follow_symlinks=False)
            details_text = (
                f"SOURCE\n{source}\n{format_size(source_info.st_size)} · {format_date(source_info.st_mtime)}\n\n"
                f"DESTINATION\n{target}\n{format_size(target_info.st_size)} · {format_date(target_info.st_mtime)}"
            )
        except OSError:
            details_text = f"SOURCE\n{source}\n\nDESTINATION\n{target}"
        details = Gtk.Label(label=details_text)
        details.set_selectable(True)
        details.set_halign(Gtk.Align.START)
        details.set_wrap(True)
        details.add_css_class("path-meta")
        shell.append(details)
        apply_all = Gtk.CheckButton(label="Apply this choice to all conflicts")
        shell.append(apply_all)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)

        def choose(policy: ConflictPolicy) -> None:
            if apply_all.get_active():
                self.settings["conflict_behavior"] = policy.value
                save_settings(self.settings)
            dialog.close()
            self.start_operation(kind, paths, destination, policy)

        skip = Gtk.Button(label="SKIP")
        skip.connect("clicked", lambda *_args: choose(ConflictPolicy.SKIP))
        buttons.append(skip)
        keep = Gtk.Button(label="KEEP BOTH")
        keep.add_css_class("action-button")
        keep.connect("clicked", lambda *_args: choose(ConflictPolicy.RENAME))
        buttons.append(keep)
        replace = Gtk.Button(label="REPLACE")
        replace.add_css_class("action-button")
        replace.add_css_class("destructive")
        replace.connect("clicked", lambda *_args: choose(ConflictPolicy.REPLACE))
        buttons.append(replace)
        shell.append(buttons)
        dialog.set_child(shell)
        dialog.present()

    def on_operation_progress(self, progress: Any) -> None:
        if self.shutting_down or not hasattr(self, "operation_progress"):
            return
        self.operation_progress.set_visible(True)
        self.operation_progress.set_fraction(progress.fraction)
        current = f" · {progress.current_name}" if progress.current_name else ""
        self.status_label.set_text(f"{progress.kind.value.title()} · {progress.completed}/{progress.total}{current}")

    def cancel_operations(self, *_args) -> None:
        if not self.operations:
            return
        for handle in list(self.operations.values()):
            handle.cancel()
        self.status_label.set_text("Cancelling file operations…")

    def on_operation_complete(self, result: Any) -> None:
        if self.shutting_down:
            return
        self.operations.pop(result.operation_id, None)
        if not self.operations:
            self.operation_progress.set_visible(False)
            self.cancel_operation_button.set_visible(False)
        if result.cancelled:
            self.toast(f"{result.kind.value.title()} cancelled")
        elif result.errors:
            self.toast(f"{result.kind.value.title()}: {result.errors[0]}")
        else:
            self.toast(f"{result.kind.value.title()} complete · {result.completed} item{'s' if result.completed != 1 else ''}")
        if result.kind == OperationKind.RENAME and self.batch_rename_active:
            if result.errors or result.cancelled:
                self.batch_rename_queue.clear()
                self.batch_rename_total = 0
                self.batch_rename_active = False
                self.toast("Batch rename stopped")
            else:
                self.run_next_batch_rename()
        if result.kind == OperationKind.MOVE and self.clipboard_mode == "cut" and not result.errors and not result.cancelled:
            self.clipboard_items = []
        if result.undo is not None and result.undo.created_paths:
            self.tabs[self.active_tab_index].selected_uris = [Gio.File.new_for_path(path).get_uri() for path in result.undo.created_paths]
        elif result.undo is not None and result.undo.undo_paths:
            self.tabs[self.active_tab_index].selected_uris = [Gio.File.new_for_path(current).get_uri() for current, _original in result.undo.undo_paths]
        self.refresh()
        if self.split_enabled:
            self.load_split_items()

    def undo_last(self, *_args) -> None:
        record = self.operation_manager.undo.pop()
        if record is None:
            self.toast("Nothing to undo")
            return
        failures = 0
        for current, original in reversed(record.undo_paths):
            try:
                if current.startswith("trash://"):
                    Gio.File.new_for_uri(current).move(
                        Gio.File.new_for_path(original),
                        Gio.FileCopyFlags.NONE,
                        None,
                    )
                elif os.path.lexists(current):
                    os.rename(current, original)
            except (GLib.Error, OSError):
                failures += 1
        for current, original in reversed(record.trash_paths):
            try:
                Gio.File.new_for_uri(current).move(
                    Gio.File.new_for_path(original),
                    Gio.FileCopyFlags.NONE,
                    None,
                )
            except (GLib.Error, OSError):
                failures += 1
        for path in reversed(record.created_paths):
            try:
                if os.path.lexists(path):
                    OperationManager._remove_path(path)
            except OSError:
                failures += 1
        self.refresh()
        self.toast("Undo complete" if failures == 0 else f"Undo incomplete · {failures} failed")

    def add_drop_target(self, widget: Gtk.Widget, destination: str | None = None) -> None:
        actions = Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, actions)
        target.set_preload(True)
        target.connect("drop", lambda active_target, value, _x, _y: self.on_drop(active_target, value, destination))
        widget.add_controller(target)
        file_target = Gtk.DropTarget.new(Gdk.FileList, actions)
        file_target.set_preload(True)
        file_target.connect("drop", lambda active_target, value, _x, _y: self.on_drop(active_target, value, destination))
        widget.add_controller(file_target)

    def drop_action_is_move(self, target: Gtk.DropTarget) -> bool:
        drop = target.get_current_drop()
        if drop is None or drop.get_drag() is None:
            return False
        return drop.get_drag().get_selected_action() == Gdk.DragAction.MOVE

    def drop_action_is_link(self, target: Gtk.DropTarget) -> bool:
        drop = target.get_current_drop()
        if drop is None or drop.get_drag() is None:
            return False
        return drop.get_drag().get_selected_action() == Gdk.DragAction.LINK

    def dropped_paths(self, value: Any) -> list[str]:
        if isinstance(value, Gdk.FileList):
            return [file.get_path() for file in value.get_files() if file.get_path()]
        if isinstance(value, GObject.Value):
            value = value.get_string()
        if not isinstance(value, str):
            return []
        paths: list[str] = []
        for raw in value.replace("\r", "").split("\n"):
            entry = raw.strip()
            if not entry or entry.startswith("#"):
                continue
            file = Gio.File.new_for_uri(entry) if "://" in entry else Gio.File.new_for_path(os.path.expanduser(entry))
            path = file.get_path()
            if path:
                paths.append(path)
        return paths

    def on_drop(self, target: Gtk.DropTarget, value: Any, destination: str | None) -> bool:
        target_path = destination or self.current_location
        if not target_path or target_path == TRASH_URI:
            self.toast("Drop into Trash is disabled; use Move to Trash")
            return False
        paths = self.dropped_paths(value)
        if not paths:
            self.toast("No local files found in the drop")
            return False
        return self.import_paths(paths, target_path, self.drop_action_is_move(target), self.drop_action_is_link(target))

    def import_paths(self, paths: list[str], destination: str, move: bool = False, link: bool = False) -> bool:
        if destination == TRASH_URI or not os.path.isdir(destination):
            self.toast("Drop target is not a writable local folder")
            return False
        if not self.can_write_location(destination):
            self.toast("You don't have permission to write to this folder")
            return False
        kind = OperationKind.LINK if link else OperationKind.MOVE if move else OperationKind.COPY
        return self.start_operation(kind, paths, destination)

    def can_write_location(self, location: str | None = None) -> bool:
        target = location or self.current_location
        if not target or target == TRASH_URI:
            return False
        if "://" in target:
            # The operation queue intentionally uses the local filesystem
            # backend.  GVfs locations remain browse/open capable, but must
            # not expose create/paste/drop controls that cannot complete.
            return False
        return os.path.isdir(target) and os.access(target, os.W_OK)

    def open_pinned_path(self, path: str) -> None:
        if path == TRASH_URI or os.path.isdir(path):
            self.navigate(path)
        elif os.path.isfile(path):
            self.open_uri(Gio.File.new_for_path(path).get_uri())
        else:
            self.toast("Pinned item is no longer available")

    def location_identity(self, location: str) -> str:
        """Use one identity for a local path and its file:// URI."""

        normalized = canonical_location(location)
        if not normalized:
            return ""
        if normalized == TRASH_URI or "://" in normalized:
            return normalized
        try:
            return Gio.File.new_for_path(normalized).get_uri()
        except (GLib.Error, ValueError):
            return normalized

    def location_label(self, location: str) -> str:
        if location == TRASH_URI:
            return "Trash"
        normalized = canonical_location(location)
        if "://" in normalized:
            return normalized.rstrip("/").rsplit("/", 1)[-1] or normalized
        try:
            file = Gio.File.new_for_path(normalized)
            info = file.query_info("standard::display-name", Gio.FileQueryInfoFlags.NONE, None)
            label = info.get_display_name()
            if label:
                return label
        except GLib.Error:
            pass
        return os.path.basename(normalized) or normalized

    def local_path_for_location(self, location: str) -> str | None:
        normalized = canonical_location(location)
        if not normalized or normalized == TRASH_URI or "://" in normalized:
            return None
        return normalized

    def location_available(self, location: str) -> bool:
        normalized = canonical_location(location)
        if not normalized:
            return False
        if normalized == TRASH_URI or "://" in normalized:
            # Do not block the GTK thread with a network metadata request just
            # to paint the sidebar.  GIO performs the real check on activation.
            return True
        return os.path.lexists(normalized)

    def open_location_reference(self, location: str) -> None:
        normalized = canonical_location(location)
        if not normalized:
            return
        if normalized == TRASH_URI:
            self.navigate(normalized)
            return
        if "://" not in normalized:
            if os.path.isdir(normalized):
                self.navigate(normalized)
            elif os.path.isfile(normalized):
                self.open_uri(Gio.File.new_for_path(normalized).get_uri())
            else:
                self.toast("Location is no longer available")
            return
        file = Gio.File.new_for_uri(normalized)
        try:
            if file.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY:
                self.navigate(normalized)
            else:
                self.open_uri(normalized)
        except GLib.Error:
            self.navigate(normalized)

    def is_starred(self, location: str) -> bool:
        identity = self.location_identity(location)
        return bool(identity) and any(self.location_identity(entry) == identity for entry in self.starred_places)

    def star_location(self, location: str) -> None:
        normalized = canonical_location(location)
        if not normalized or normalized == TRASH_URI:
            self.toast("Trash cannot be starred")
            return
        if "://" not in normalized and not os.path.lexists(normalized):
            self.toast("Location is no longer available")
            return
        identity = self.location_identity(normalized)
        if self.is_starred(identity):
            self.toast("Already starred")
            return
        self.starred_places.insert(0, identity)
        self.starred_places = self.starred_places[:MAX_STARRED_LOCATIONS]
        self.settings["starred_places"] = list(self.starred_places)
        save_settings(self.settings)
        if hasattr(self, "starred_button"):
            self.starred_button.set_tooltip_text(f"Starred · {len(self.starred_places)} locations")
        if hasattr(self, "starred_box"):
            self.update_starred_places()
        self.toast(f"Starred {self.location_label(normalized)}")

    def star_item(self, item: FileItem | None) -> None:
        if item is None:
            return
        self.star_location(item.uri)

    def unstar_location(self, location: str) -> None:
        identity = self.location_identity(location)
        if not identity:
            return
        updated = [entry for entry in self.starred_places if self.location_identity(entry) != identity]
        if len(updated) == len(self.starred_places):
            return
        self.starred_places = updated
        self.settings["starred_places"] = list(updated)
        save_settings(self.settings)
        if hasattr(self, "starred_button"):
            self.starred_button.set_tooltip_text(f"Starred · {len(self.starred_places)} locations")
        if hasattr(self, "starred_box"):
            self.update_starred_places()

    def unstar_item(self, item: FileItem | None) -> None:
        if item is not None:
            self.unstar_location(item.uri)

    def add_starred_drop_target(self, widget: Gtk.Widget) -> None:
        actions = Gdk.DragAction.COPY
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, actions)
        target.set_preload(True)
        target.connect("drop", self.on_starred_drop)
        widget.add_controller(target)
        file_target = Gtk.DropTarget.new(Gdk.FileList, actions)
        file_target.set_preload(True)
        file_target.connect("drop", self.on_starred_drop)
        widget.add_controller(file_target)

    def on_starred_drop(self, _target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
        locations = self.dropped_paths(value)
        added = 0
        for path in locations:
            before = len(self.starred_places)
            self.star_location(path)
            added += int(len(self.starred_places) > before)
        return added > 0

    def update_starred_places(self) -> None:
        if hasattr(self, "starred_button"):
            self.starred_button.set_tooltip_text(f"Starred · {len(self.starred_places)} locations")
        if not hasattr(self, "starred_box"):
            return
        clear_box(self.starred_box)
        self.starred_button_locations = set()
        visible = [entry for entry in self.starred_places if self.location_available(entry)]
        self.starred_hint.set_visible(not visible)
        for location in visible:
            button = Gtk.Button()
            button.add_css_class("sidebar-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon_name = "folder-symbolic" if self.local_path_for_location(location) and os.path.isdir(self.local_path_for_location(location) or "") else "starred-symbolic"
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(17)
            row.append(icon)
            label = Gtk.Label(label=self.location_label(location))
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("sidebar-name")
            row.append(label)
            button.set_child(row)
            button.set_tooltip_text(location)
            button.connect("clicked", lambda _button, target=location: self.open_location_reference(target))
            self.add_sidebar_context_gesture(button, location, self.on_starred_context)
            self.starred_box.append(button)
            self.starred_button_locations.add(location)

    def on_starred_context(self, gesture: Gtk.GestureClick, widget: Gtk.Widget, location: str, x: float, y: float) -> None:
        self.on_sidebar_context(
            gesture,
            widget,
            location,
            x,
            y,
            heading="STARRED",
            remove_label="UNSTAR",
            open_callback=lambda: self.open_location_reference(location),
            remove_callback=lambda: self.unstar_location(location),
        )

    def record_recent_location(self, location: str) -> None:
        normalized = canonical_location(location)
        if not normalized or normalized == TRASH_URI:
            return
        self.recent_locations = push_recent(self.recent_locations, normalized)
        self.settings["recent_locations"] = list(self.recent_locations)
        if hasattr(self, "recent_button"):
            self.recent_button.set_tooltip_text(f"Recent · {len(self.recent_locations)} locations")
        if hasattr(self, "recent_box"):
            self.update_recent_places()

    def remove_recent_location(self, location: str) -> None:
        self.recent_locations = remove_location(self.recent_locations, location)
        self.settings["recent_locations"] = list(self.recent_locations)
        if hasattr(self, "recent_button"):
            self.recent_button.set_tooltip_text(f"Recent · {len(self.recent_locations)} locations")
        if hasattr(self, "recent_box"):
            self.update_recent_places()

    def update_recent_places(self) -> None:
        if hasattr(self, "recent_button"):
            self.recent_button.set_tooltip_text(f"Recent · {len(self.recent_locations)} locations")
        if not hasattr(self, "recent_box"):
            return
        clear_box(self.recent_box)
        self.recent_button_locations = set()
        visible = [entry for entry in self.recent_locations if self.location_available(entry)]
        self.recent_hint.set_visible(not visible)
        for location in visible[: max(1, MAX_RECENT_LOCATIONS // 2)]:
            button = Gtk.Button()
            button.add_css_class("sidebar-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            path = self.local_path_for_location(location)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic" if path and os.path.isdir(path) else "document-open-recent-symbolic")
            icon.set_pixel_size(17)
            row.append(icon)
            label = Gtk.Label(label=self.location_label(location))
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("sidebar-name")
            row.append(label)
            button.set_child(row)
            button.set_tooltip_text(location)
            button.connect("clicked", lambda _button, target=location: self.open_location_reference(target))
            self.add_sidebar_context_gesture(button, location, self.on_recent_context)
            self.recent_box.append(button)
            self.recent_button_locations.add(location)

    def on_recent_context(self, gesture: Gtk.GestureClick, widget: Gtk.Widget, location: str, x: float, y: float) -> None:
        self.on_sidebar_context(
            gesture,
            widget,
            location,
            x,
            y,
            heading="RECENT",
            remove_label="REMOVE FROM RECENT",
            open_callback=lambda: self.open_location_reference(location),
            remove_callback=lambda: self.remove_recent_location(location),
        )

    def show_recent_menu(self, button: Gtk.Button, *_args) -> None:
        self.show_virtual_location_menu(button, "RECENT", self.recent_locations)

    def show_starred_menu(self, button: Gtk.Button, *_args) -> None:
        self.show_virtual_location_menu(button, "STARRED", self.starred_places)

    def show_virtual_location_menu(self, parent: Gtk.Widget, title: str, locations: list[str]) -> None:
        popover = Gtk.Popover()
        popover.set_parent(parent)
        popover.set_has_arrow(False)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.add_css_class("context-menu")
        header = Gtk.Label(label=title)
        header.set_halign(Gtk.Align.START)
        header.add_css_class("context-menu-header")
        menu.append(header)
        visible = [location for location in locations if self.location_available(location)]
        if not visible:
            empty = Gtk.Label(label="No locations")
            empty.set_halign(Gtk.Align.START)
            empty.add_css_class("pinned-hint")
            menu.append(empty)
        for location in visible[:12]:
            self.menu_button(
                menu,
                popover,
                self.location_label(location),
                lambda target=location: self.open_location_reference(target),
            )
        popover.connect("closed", lambda *_args: popover.unparent() if popover.get_parent() is not None else None)
        popover.set_child(menu)
        popover.popup()

    def add_location_drop_target(self, widget: Gtk.Widget, location: str) -> None:
        self.add_drop_target(widget, location)

    def add_pinned_drop_target(self, widget: Gtk.Widget) -> None:
        actions = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, actions)
        target.set_preload(True)
        target.connect("drop", self.on_pinned_drop)
        widget.add_controller(target)
        file_target = Gtk.DropTarget.new(Gdk.FileList, actions)
        file_target.set_preload(True)
        file_target.connect("drop", self.on_pinned_drop)
        widget.add_controller(file_target)

    def on_pinned_drop(self, target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
        paths = self.dropped_paths(value)
        added = 0
        for path in paths:
            if self.pin_path(path):
                added += 1
        if added:
            self.toast(f"Pinned {added} item{'s' if added != 1 else ''}")
        return added > 0

    def pin_path(self, path: str) -> bool:
        path = os.path.abspath(os.path.expanduser(path))
        fixed_paths = {location for _name, _icon, location in self.places if location != TRASH_URI}
        fixed_paths.add(os.path.sep)
        fixed_paths.add(TRASH_URI)
        if not os.path.isdir(path) or path in fixed_paths or path in self.pinned_places:
            return False
        self.pinned_places.append(path)
        self.settings["pinned_places"] = list(self.pinned_places)
        save_settings(self.settings)
        if hasattr(self, "pinned_box"):
            self.update_pinned_places()
        return True

    def pin_item(self, item: FileItem) -> None:
        if not item.path or not item.is_dir:
            self.toast("Only local folders can be pinned")
            return
        if self.pin_path(item.path):
            self.toast(f"Pinned {item.display_name}")
        else:
            self.toast("Item is already pinned")

    def unpin_path(self, path: str) -> None:
        if path in self.pinned_places:
            self.pinned_places.remove(path)
            self.settings["pinned_places"] = list(self.pinned_places)
            save_settings(self.settings)
            self.update_pinned_places()

    def update_pinned_places(self) -> None:
        if not hasattr(self, "pinned_box"):
            return
        for old_path in self.pinned_button_locations - set(self.pinned_places):
            self.place_buttons.pop(old_path, None)
        self.pinned_button_locations = set()
        clear_box(self.pinned_box)
        self.pinned_hint.set_visible(not self.pinned_places)
        for path in self.pinned_places:
            label = "Trash" if path == TRASH_URI else os.path.basename(path) or path
            button = Gtk.Button()
            button.add_css_class("sidebar-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon_name = "user-trash-symbolic" if path == TRASH_URI else "folder-symbolic"
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(17)
            row.append(icon)
            text = Gtk.Label(label=label)
            text.set_halign(Gtk.Align.START)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_hexpand(True)
            text.add_css_class("sidebar-name")
            row.append(text)
            button.set_child(row)
            button.set_tooltip_text(path)
            button.connect("clicked", lambda _button, target=path: self.open_pinned_path(target))
            self.add_sidebar_context_gesture(button, path)
            self.add_location_drop_target(button, path)
            self.pinned_box.append(button)
            self.place_buttons[path] = button
            self.pinned_button_locations.add(path)

    def add_sidebar_context_gesture(
        self,
        widget: Gtk.Widget,
        path: str,
        handler: Callable[[Gtk.GestureClick, Gtk.Widget, str, float, float], None] | None = None,
    ) -> None:
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        callback = handler or self.on_sidebar_context
        gesture.connect("pressed", lambda active, _n_press, x, y: callback(active, widget, path, x, y))
        widget.add_controller(gesture)

    def on_sidebar_context(
        self,
        gesture: Gtk.GestureClick,
        widget: Gtk.Widget,
        path: str,
        x: float,
        y: float,
        heading: str = "PINNED LOCATION",
        remove_label: str = "UNPIN FROM SIDEBAR",
        open_callback: Callable[[], None] | None = None,
        remove_callback: Callable[[], None] | None = None,
    ) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.Popover()
        popover.set_parent(widget)
        popover.set_has_arrow(False)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.add_css_class("context-menu")
        header = Gtk.Label(label=heading)
        header.set_halign(Gtk.Align.START)
        header.add_css_class("context-menu-header")
        menu.append(header)
        self.menu_button(menu, popover, "OPEN", open_callback or (lambda: self.open_pinned_path(path)))
        self.menu_button(
            menu,
            popover,
            remove_label,
            remove_callback or (lambda: self.unpin_path(path)),
            destructive=True,
        )
        popover.connect("closed", lambda *_args: popover.unparent() if popover.get_parent() is not None else None)
        popover.set_child(menu)
        popover.popup()

    def build_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar.set_size_request(220, -1)
        sidebar.set_hexpand(False)
        sidebar.add_css_class("sidebar")
        self.sidebar = sidebar

        places_label = Gtk.Label(label="PLACES")
        places_label.set_halign(Gtk.Align.START)
        places_label.add_css_class("section-label")
        sidebar.append(places_label)

        for name, icon_name, location in self.places:
            button = Gtk.Button()
            button.set_halign(Gtk.Align.FILL)
            button.add_css_class("sidebar-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(17)
            row.append(icon)
            text = Gtk.Label(label=name)
            text.set_halign(Gtk.Align.START)
            text.set_hexpand(True)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.add_css_class("sidebar-name")
            row.append(text)
            button.set_child(row)
            button.connect("clicked", lambda _button, target=location: self.navigate(target))
            self.add_location_drop_target(button, location)
            sidebar.append(button)
            self.place_buttons[location] = button

        self.pinned_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.pinned_section.add_css_class("pinned-section")
        pinned_label = Gtk.Label(label="PINNED")
        pinned_label.set_halign(Gtk.Align.START)
        pinned_label.add_css_class("section-label")
        self.pinned_section.append(pinned_label)
        self.pinned_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.pinned_section.append(self.pinned_box)
        self.pinned_hint = Gtk.Label(label="Drop files here to pin")
        self.pinned_hint.set_halign(Gtk.Align.START)
        self.pinned_hint.add_css_class("pinned-hint")
        self.pinned_section.append(self.pinned_hint)
        self.add_pinned_drop_target(self.pinned_box)
        sidebar.append(self.pinned_section)
        self.update_pinned_places()

        virtual_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        virtual_section.add_css_class("pinned-section")
        virtual_label = Gtk.Label(label="LIBRARY")
        virtual_label.set_halign(Gtk.Align.START)
        virtual_label.add_css_class("section-label")
        virtual_section.append(virtual_label)

        self.recent_button = Gtk.Button()
        self.recent_button.add_css_class("sidebar-item")
        recent_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        recent_row.append(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
        recent_text = Gtk.Label(label="Recent")
        recent_text.set_halign(Gtk.Align.START)
        recent_text.set_hexpand(True)
        recent_text.set_ellipsize(Pango.EllipsizeMode.END)
        recent_text.add_css_class("sidebar-name")
        recent_row.append(recent_text)
        self.recent_button.set_child(recent_row)
        self.recent_button.set_tooltip_text(f"Recent · {len(self.recent_locations)} locations")
        self.recent_button.connect("clicked", self.show_recent_menu)
        virtual_section.append(self.recent_button)

        self.starred_button = Gtk.Button()
        self.starred_button.add_css_class("sidebar-item")
        starred_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        starred_row.append(Gtk.Image.new_from_icon_name("starred-symbolic"))
        starred_text = Gtk.Label(label="Starred")
        starred_text.set_halign(Gtk.Align.START)
        starred_text.set_hexpand(True)
        starred_text.set_ellipsize(Pango.EllipsizeMode.END)
        starred_text.add_css_class("sidebar-name")
        starred_row.append(starred_text)
        self.starred_button.set_child(starred_row)
        self.starred_button.set_tooltip_text(f"Starred · {len(self.starred_places)} locations")
        self.starred_button.connect("clicked", self.show_starred_menu)
        self.add_starred_drop_target(self.starred_button)
        virtual_section.append(self.starred_button)
        sidebar.append(virtual_section)

        self.folders_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.folders_section.add_css_class("folders-panel")
        folders_label = Gtk.Label(label="FOLDERS")
        folders_label.set_halign(Gtk.Align.START)
        folders_label.add_css_class("section-label")
        self.folders_section.append(folders_label)
        self.folders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.folders_section.append(self.folders_box)
        sidebar.append(self.folders_section)
        self.folders_section.set_visible(self.folders_panel_visible)

        device_label = Gtk.Label(label="DEVICES")
        device_label.set_halign(Gtk.Align.START)
        device_label.add_css_class("section-label")
        sidebar.append(device_label)
        self.device_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        sidebar.append(self.device_box)
        root_button = Gtk.Button()
        root_button.add_css_class("sidebar-item")
        root_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        root_icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        root_icon.set_pixel_size(17)
        root_row.append(root_icon)
        root_label = Gtk.Label(label="Filesystem")
        root_label.set_halign(Gtk.Align.START)
        root_label.set_hexpand(True)
        root_label.set_ellipsize(Pango.EllipsizeMode.END)
        root_label.add_css_class("sidebar-name")
        root_row.append(root_label)
        root_button.set_child(root_row)
        root_button.connect("clicked", lambda *_: self.navigate(os.path.sep))
        self.add_location_drop_target(root_button, os.path.sep)
        sidebar.append(root_button)
        self.place_buttons[os.path.sep] = root_button
        self.update_devices()

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        bottom.add_css_class("pinned-section")
        trash_button = Gtk.Button()
        trash_button.add_css_class("sidebar-item")
        trash_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        trash_row.append(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        trash_label = Gtk.Label(label="Trash")
        trash_label.set_halign(Gtk.Align.START)
        trash_label.set_hexpand(True)
        trash_label.set_ellipsize(Pango.EllipsizeMode.END)
        trash_label.add_css_class("sidebar-name")
        trash_row.append(trash_label)
        trash_button.set_child(trash_row)
        trash_button.connect("clicked", lambda *_args: self.navigate(TRASH_URI))
        self.place_buttons[TRASH_URI] = trash_button
        bottom.append(trash_button)
        sidebar.append(bottom)
        return sidebar

    def setup_volume_monitor(self) -> None:
        if self.volume_signal_ids:
            return
        for signal in ("mount-added", "mount-removed", "volume-added", "volume-removed", "drive-connected", "drive-disconnected"):
            try:
                self.volume_signal_ids.append(self.volume_monitor.connect(signal, self.on_volume_event))
            except (TypeError, ValueError):
                continue

    def on_volume_event(self, *_args) -> None:
        if self.shutting_down:
            return
        if hasattr(self, "device_box"):
            GLib.idle_add(self.update_devices)

    def update_devices(self) -> bool:
        if not hasattr(self, "device_box"):
            return False
        clear_box(self.device_box)
        self.device_buttons.clear()
        try:
            mounts = self.volume_monitor.get_mounts()
        except GLib.Error:
            mounts = []
        for mount in mounts:
            try:
                root = mount.get_root()
                location = root.get_path() or root.get_uri()
                name = mount.get_name() or location
            except (GLib.Error, AttributeError):
                continue
            if location == os.path.sep:
                # The fixed Filesystem entry already represents the root mount.
                continue
            button = Gtk.Button()
            button.add_css_class("sidebar-item")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image.new_from_icon_name("drive-removable-media-symbolic")
            icon.set_pixel_size(17)
            row.append(icon)
            label = Gtk.Label(label=name)
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("sidebar-name")
            row.append(label)
            eject = self.icon_button("media-eject-symbolic", f"Unmount {name}", lambda *_args, active_mount=mount: self.unmount_device(active_mount))
            eject.add_css_class("tab-close")
            row.append(eject)
            button.set_child(row)
            button.set_tooltip_text(location)
            button.connect("clicked", lambda _button, target=location: self.navigate(target))
            self.add_location_drop_target(button, location)
            self.device_box.append(button)
            self.device_buttons[location] = button
        return False

    def mount_device(self, volume: Gio.Volume) -> None:
        try:
            volume.mount(
                Gio.MountMountFlags.NONE,
                None,
                None,
                lambda active_volume, result: self.finish_mount(active_volume, result),
            )
        except GLib.Error as error:
            self.toast(f"Could not mount device: {error.message}")

    def finish_mount(self, volume: Gio.Volume, result: Gio.AsyncResult) -> None:
        try:
            volume.mount_finish(result)
            self.toast("Device mounted")
            self.update_devices()
        except GLib.Error as error:
            self.toast(f"Could not mount device: {error.message}")

    def unmount_device(self, mount: Gio.Mount) -> None:
        try:
            mount.unmount_with_operation(
                Gio.MountUnmountFlags.NONE,
                None,
                None,
                lambda active_mount, result: self.finish_unmount(active_mount, result),
            )
        except GLib.Error as error:
            self.toast(f"Could not unmount device: {error.message}")

    def finish_unmount(self, mount: Gio.Mount, result: Gio.AsyncResult) -> None:
        try:
            mount.unmount_with_operation_finish(result)
            self.toast("Device unmounted")
        except GLib.Error as error:
            self.toast(f"Could not unmount device: {error.message}")

    def update_folder_panel(self) -> None:
        if not hasattr(self, "folders_box"):
            return
        clear_box(self.folders_box)
        if not self.folders_panel_visible:
            return
        location = self.current_location
        if not location or location == TRASH_URI or "://" in location or not os.path.isdir(location):
            label = Gtk.Label(label="Local folders only")
            label.set_halign(Gtk.Align.START)
            label.add_css_class("pinned-hint")
            self.folders_box.append(label)
            return

        def append_folder(path: str, depth: int, expand: bool) -> None:
            name = os.path.basename(path) or os.path.sep
            button = Gtk.Button()
            button.set_halign(Gtk.Align.FILL)
            button.add_css_class("folder-tree-item")
            button.set_margin_start(depth * 12)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic")
            icon.set_pixel_size(14)
            row.append(icon)
            text = Gtk.Label(label=name)
            text.set_halign(Gtk.Align.START)
            text.set_hexpand(True)
            text.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            text.add_css_class("sidebar-name")
            row.append(text)
            button.set_child(row)
            button.set_tooltip_text(path)
            button.connect("clicked", lambda *_args, target=path: self.navigate(target))
            self.add_location_drop_target(button, path)
            self.folders_box.append(button)
            if not expand or depth >= 1:
                return
            try:
                children = sorted(
                    entry.path
                    for entry in os.scandir(path)
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
                )[:24]
            except OSError:
                children = []
            for child in children:
                append_folder(child, depth + 1, False)

        append_folder(location, 0, True)

    def toggle_folders_panel(self, *_args) -> None:
        self.folders_panel_visible = not self.folders_panel_visible
        self.settings["show_folders_panel"] = self.folders_panel_visible
        if hasattr(self, "folders_section"):
            self.folders_section.set_visible(self.folders_panel_visible)
        self.update_folder_panel()
        save_settings(self.settings)
        self.toast("Folders panel shown" if self.folders_panel_visible else "Folders panel hidden")

    def build_main_pane(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        main.set_hexpand(True)
        main.set_vexpand(True)
        main.add_css_class("main-pane")
        self.main_pane = main

        self.content_stack = Gtk.Stack()
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.NONE)

        self.grid = VirtualFileView(
            "grid",
            activate=self.activate_item,
            context=self.on_virtual_context,
            selection_changed=self.on_grid_selection_changed,
            dropped=self.on_virtual_drop,
            drag_paths=self.drag_paths,
            open_new_tab=self.open_item_new_tab,
            icon_name=icon_for_item,
            compact=lambda: bool(self.settings.get("compact_mode")),
            zoom=lambda: self.zoom_level,
            thumbnail=self.request_thumbnail,
            thumbnail_clear=self.clear_thumbnail_target,
        )
        self.grid.set_single_click_activate(False)
        self.grid.set_max_children_per_line(10)
        self.grid.set_min_children_per_line(2)
        self.grid_scroll = Gtk.ScrolledWindow()
        self.grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.grid_scroll.set_child(self.grid.take_view())
        self.content_stack.add_named(self.grid_scroll, "grid")

        self.compact_view = VirtualFileView(
            "compact",
            activate=self.activate_item,
            context=self.on_virtual_context,
            selection_changed=self.on_list_selection_changed,
            dropped=self.on_virtual_drop,
            drag_paths=self.drag_paths,
            open_new_tab=self.open_item_new_tab,
            icon_name=icon_for_item,
            compact=lambda: bool(self.settings.get("compact_mode")),
            zoom=lambda: self.zoom_level,
            thumbnail=self.request_thumbnail,
            thumbnail_clear=self.clear_thumbnail_target,
        )
        self.compact_view.set_single_click_activate(False)
        self.compact_scroll = Gtk.ScrolledWindow()
        self.compact_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.compact_scroll.set_child(self.compact_view.take_view())
        self.content_stack.add_named(self.compact_scroll, "compact")

        self.listbox = VirtualFileView(
            "details",
            activate=self.activate_item,
            context=self.on_virtual_context,
            selection_changed=self.on_list_selection_changed,
            dropped=self.on_virtual_drop,
            drag_paths=self.drag_paths,
            open_new_tab=self.open_item_new_tab,
            icon_name=icon_for_item,
            compact=lambda: bool(self.settings.get("compact_mode")),
            zoom=lambda: self.zoom_level,
            additional_info=lambda item: self.additional_info_for_item(item) if self.show_additional_info else "",
            thumbnail=self.request_thumbnail,
            thumbnail_clear=self.clear_thumbnail_target,
        )
        self.listbox.set_single_click_activate(False)
        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroll.set_child(self.listbox.take_view())
        self.content_stack.add_named(self.list_scroll, "details")

        overlay = Gtk.Overlay()
        overlay.set_child(self.content_stack)
        self.empty_label = Gtk.Label(label="This folder is empty")
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        self.empty_label.set_can_target(False)
        self.empty_label.add_css_class("empty-state")
        overlay.add_overlay(self.empty_label)
        self.loading_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.loading_overlay.set_halign(Gtk.Align.CENTER)
        self.loading_overlay.set_valign(Gtk.Align.CENTER)
        self.loading_overlay.set_can_target(False)
        self.loading_overlay.add_css_class("loading-overlay")
        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_halign(Gtk.Align.CENTER)
        self.loading_overlay.append(self.loading_spinner)
        loading_label = Gtk.Label(label="Loading…")
        loading_label.set_can_target(False)
        loading_label.add_css_class("eyebrow")
        self.loading_overlay.append(loading_label)
        self.loading_overlay.set_visible(False)
        overlay.add_overlay(self.loading_overlay)
        main.append(overlay)

        self.terminal_panel = self.build_terminal_panel()
        self.terminal_panel.set_visible(self.terminal_panel_visible)
        main.append(self.terminal_panel)

        statusbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        statusbar.add_css_class("statusbar")
        self.path_meta = Gtk.Label(label="")
        self.path_meta.set_halign(Gtk.Align.START)
        self.path_meta.set_hexpand(True)
        self.path_meta.set_ellipsize(Pango.EllipsizeMode.END)
        self.path_meta.add_css_class("path-meta")
        statusbar.append(self.path_meta)
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_halign(Gtk.Align.END)
        self.status_label.add_css_class("status-text")
        statusbar.append(self.status_label)
        self.operation_progress = Gtk.ProgressBar()
        self.operation_progress.set_size_request(150, -1)
        self.operation_progress.set_visible(False)
        self.operation_progress.set_tooltip_text("File operation progress")
        statusbar.append(self.operation_progress)
        self.cancel_operation_button = self.icon_button(
            "process-stop-symbolic",
            "Cancel active file operations",
            self.cancel_operations,
        )
        self.cancel_operation_button.set_visible(False)
        statusbar.append(self.cancel_operation_button)
        self.hint_label = Gtk.Label(label="Enter open · Space preview · F2 rename · Ctrl+A select")
        self.hint_label.set_halign(Gtk.Align.END)
        self.hint_label.add_css_class("hint-text")
        statusbar.append(self.hint_label)
        main.append(statusbar)
        self.log_file_view_state("build-main-pane")
        return main

    def log_file_view_state(self, event: str) -> None:
        """Trace the backend-to-GTK file view chain in debug sessions."""

        if not hasattr(self, "content_stack") or not hasattr(self, "grid"):
            return
        active = self.active_file_view()
        selection_model = getattr(active.selection, "get_model", lambda: None)()
        view_model = getattr(active.view, "get_model", lambda: None)()
        try:
            store_items = active.model.get_n_items()
        except (GLib.Error, TypeError, ValueError):
            store_items = None
        try:
            selection_items = selection_model.get_n_items() if selection_model is not None else None
        except (GLib.Error, TypeError, ValueError, AttributeError):
            selection_items = None
        try:
            view_items = view_model.get_n_items() if view_model is not None else None
        except (GLib.Error, TypeError, ValueError, AttributeError):
            view_items = None
        stack_page = self.content_stack.get_visible_child_name()
        stack_child = self.content_stack.get_visible_child()
        scroll = {
            "grid": self.grid_scroll,
            "compact": self.compact_scroll,
            "details": self.list_scroll,
        }.get(self.view_mode)
        LOGGER.debug("file view audit event=%s", event)
        LOGGER.debug("backend items=%d", len(self.all_items))
        LOGGER.debug("gtk store items=%s", store_items)
        LOGGER.debug("selection model items=%s", selection_items)
        LOGGER.debug("active view=%s", self.view_mode)
        LOGGER.debug(
            "gtk model links selection_model=%s view_model=%s selection_wraps_store=%s view_uses_selection=%s",
            type(selection_model).__name__ if selection_model is not None else None,
            type(view_model).__name__ if view_model is not None else None,
            selection_model is active.model,
            view_model is active.selection,
        )
        LOGGER.debug(
            "gtk stack visible page=%s expected page=%s child=%s child_visible=%s",
            stack_page,
            self.view_mode,
            type(stack_child).__name__ if stack_child is not None else None,
            stack_child.get_visible() if stack_child is not None else None,
        )
        LOGGER.debug(
            "scrolled window mode=%s child=%s child_is_active=%s visible=%s",
            self.view_mode,
            type(scroll.get_child()).__name__ if scroll is not None and scroll.get_child() is not None else None,
            scroll is not None and scroll.get_child() is active.view,
            scroll.get_visible() if scroll is not None else None,
        )
        LOGGER.debug(
            "file view visible=%s hexpand=%s vexpand=%s container_visible=%s container_hexpand=%s container_vexpand=%s",
            active.view.get_visible(),
            active.view.get_hexpand(),
            active.view.get_vexpand(),
            active.get_visible(),
            active.get_hexpand(),
            active.get_vexpand(),
        )
        LOGGER.debug(
            "overlay state loading=%s empty=%s error=%s",
            self.loading_overlay.get_visible(),
            self.empty_label.get_visible(),
            self.load_error,
        )

    def ensure_active_file_page(self, reason: str) -> None:
        if not hasattr(self, "content_stack"):
            return
        mode = self.normalize_view_mode(self.view_mode)
        self.view_mode = mode
        self.content_stack.set_visible_child_name(mode)
        actual = self.content_stack.get_visible_child_name()
        if actual != mode:
            LOGGER.error("gtk stack page mismatch expected=%s actual=%s reason=%s", mode, actual, reason)
        self.log_file_view_state(reason)

    def build_terminal_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.add_css_class("terminal-panel")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(10)
        header.set_margin_end(6)
        header.set_margin_top(5)
        header.set_margin_bottom(5)
        title = Gtk.Label(label="TERMINAL")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        title.add_css_class("section-label")
        header.append(title)
        close = self.icon_button("window-close-symbolic", "Close terminal panel (F4)", self.toggle_terminal_panel)
        header.append(close)
        panel.append(header)

        output_scroll = Gtk.ScrolledWindow()
        output_scroll.set_min_content_height(120)
        output_scroll.set_vexpand(True)
        self.terminal_output = Gtk.TextView()
        self.terminal_output.set_editable(False)
        self.terminal_output.set_cursor_visible(False)
        self.terminal_output.set_monospace(True)
        self.terminal_output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.terminal_output.add_css_class("terminal-output")
        output_scroll.set_child(self.terminal_output)
        panel.append(output_scroll)

        self.terminal_entry = Gtk.Entry()
        self.terminal_entry.set_placeholder_text("Run a command in the current folder…")
        self.terminal_entry.add_css_class("terminal-entry")
        self.terminal_entry.connect("activate", lambda entry: self.run_terminal_command(entry.get_text()))
        panel.append(self.terminal_entry)
        return panel

    def append_terminal_output(self, text: str) -> None:
        if not text:
            return
        buffer = self.terminal_output.get_buffer()
        end = buffer.get_end_iter()
        buffer.insert(end, text if text.endswith("\n") else text + "\n")
        self.terminal_output.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 1.0)

    def run_terminal_command(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        location = self.current_location
        if not location or location == TRASH_URI or "://" in location or not os.path.isdir(location):
            self.toast("Embedded terminal is available for local folders only")
            return
        shell = str(self.settings.get("terminal_shell", "") or os.environ.get("SHELL", "/bin/sh"))
        if not os.path.isabs(shell) or not os.access(shell, os.X_OK):
            shell = "/bin/sh"
        self.append_terminal_output(f"$ {command}")
        self.terminal_entry.set_text("")
        if self.terminal_process is not None:
            try:
                self.terminal_process.force_exit()
            except GLib.Error:
                pass
        try:
            launcher = Gio.SubprocessLauncher.new(
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            )
            launcher.set_cwd(location)
            process = launcher.spawnv([shell, "-lc", command])
            self.terminal_process = process
        except (GLib.Error, OSError) as error:
            self.append_terminal_output(f"omapath: {error}")
            return

        def completed(process_result: Gio.Subprocess, result: Gio.AsyncResult) -> None:
            if self.shutting_down:
                return
            try:
                _success, stdout, stderr = process_result.communicate_utf8_finish(result)
            except GLib.Error as error:
                output = f"omapath: {error}"
            else:
                output = "\n".join(part for part in (stdout or "", stderr or "") if part)
            if output:
                self.append_terminal_output(output)
            self.terminal_process = None

        process.communicate_utf8_async(None, None, completed)

    def toggle_terminal_panel(self, *_args) -> None:
        self.terminal_panel_visible = not self.terminal_panel_visible
        self.settings["show_terminal_panel"] = self.terminal_panel_visible
        if hasattr(self, "terminal_panel"):
            self.terminal_panel.set_visible(self.terminal_panel_visible)
        save_settings(self.settings)
        self.toast("Terminal panel shown" if self.terminal_panel_visible else "Terminal panel hidden")

    def build_split_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("split-panel")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.split_location_label = Gtk.Label(label="split")
        self.split_location_label.set_halign(Gtk.Align.START)
        self.split_location_label.set_hexpand(True)
        self.split_location_label.add_css_class("path-title")
        header.append(self.split_location_label)
        up = self.icon_button("go-up-symbolic", "Up in split panel", lambda *_args: self.go_up_split())
        header.append(up)
        panel.append(header)

        self.split_view = VirtualFileView(
            "list",
            activate=self.activate_split_item,
            context=self.on_virtual_context,
            selection_changed=self.on_split_selection_changed,
            dropped=self.on_split_drop,
            drag_paths=self.split_drag_paths,
            open_new_tab=self.open_item_new_tab,
            icon_name=icon_for_item,
            compact=lambda: bool(self.settings.get("compact_mode")),
            thumbnail=self.request_thumbnail,
            thumbnail_clear=self.clear_thumbnail_target,
        )
        self.split_view.set_single_click_activate(False)
        split_scroll = Gtk.ScrolledWindow()
        split_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        split_scroll.set_child(self.split_view.take_view())
        panel.append(split_scroll)
        self.split_location = self.current_location or self.home
        self.split_generation = 0
        self.split_cancellable: Gio.Cancellable | None = None
        self.split_enumerator: Gio.FileEnumerator | None = None
        self.split_folder: Gio.File | None = None
        self.split_loading_items = []
        return panel

    def toggle_split_view(self, *_args) -> None:
        self.split_enabled = not self.split_enabled
        self.settings["split_view"] = self.split_enabled
        save_settings(self.settings)
        if self.split_enabled:
            self.split_location = self.split_location if os.path.isdir(self.split_location or "") else (self.current_location or self.home)
            self.split_pane.set_visible(True)
            self.load_split_items()
            self.toast("Split view enabled · F3 to toggle")
        else:
            if self.split_cancellable is not None:
                self.split_cancellable.cancel()
            self.split_pane.set_visible(False)
            self.toast("Split view disabled")
        self.sync_responsive_layout()

    def on_split_selection_changed(self, *_args) -> None:
        if self.split_enabled:
            count = len(self.split_view.get_selected_items())
            self.status_label.set_text(f"Split panel · {count} selected" if count else "Split panel ready")

    def activate_split_item(self, item: FileItem | None) -> None:
        if item is None:
            return
        if item.is_dir:
            self.navigate_split(item.path or item.uri)
        else:
            self.open_uri(item.uri)

    def navigate_split(self, location: str) -> None:
        target = self.normalize_location(location)
        if target == TRASH_URI or not os.path.isdir(target):
            self.toast("Split location is not a readable local folder")
            return
        self.split_location = target
        self.split_location_label.set_text(self.display_location(target))
        self.load_split_items()

    def go_up_split(self, *_args) -> None:
        if self.split_location and os.path.abspath(self.split_location) != os.path.sep:
            self.navigate_split(os.path.dirname(os.path.abspath(self.split_location)))

    def on_split_drop(self, value: Any, _destination: str | None, move: bool, link: bool = False) -> bool:
        return self.on_virtual_drop(value, self.split_location, move, link)

    def load_split_items(self) -> None:
        if self.shutting_down:
            return
        self.split_generation += 1
        generation = self.split_generation
        if self.split_cancellable is not None:
            self.split_cancellable.cancel()
        if self.split_enumerator is not None:
            try:
                self.split_enumerator.close(None)
            except GLib.Error:
                pass
            self.split_enumerator = None
        self.split_cancellable = Gio.Cancellable()
        self.split_loading_items = []
        self.split_view.set_items([])
        self.split_folder = Gio.File.new_for_path(self.split_location)
        try:
            self.split_folder.enumerate_children_async(
                FILE_ATTRIBUTES,
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.split_cancellable,
                lambda source, result: self.on_split_enumerator(source, result, generation),
            )
        except GLib.Error as error:
            self.toast(f"Unable to read split folder: {error.message}")

    def on_split_enumerator(self, source: Gio.File, result: Gio.AsyncResult, generation: int) -> None:
        if self.shutting_down or generation != self.split_generation or self.split_cancellable is None:
            try:
                stale_enumerator = source.enumerate_children_finish(result)
                stale_enumerator.close(None)
            except GLib.Error:
                pass
            return
        try:
            self.split_enumerator = source.enumerate_children_finish(result)
            self.split_enumerator.next_files_async(
                128,
                GLib.PRIORITY_DEFAULT,
                self.split_cancellable,
                lambda enumerator, chunk: self.on_split_chunk(enumerator, chunk, generation),
            )
        except GLib.Error as error:
            self.toast(f"Unable to read split folder: {error.message}")

    def on_split_chunk(self, source: Gio.FileEnumerator, result: Gio.AsyncResult, generation: int) -> None:
        if self.shutting_down or generation != self.split_generation or self.split_cancellable is None:
            try:
                source.close(None)
            except GLib.Error:
                pass
            return
        try:
            infos = source.next_files_finish(result)
        except GLib.Error as error:
            if not self.split_cancellable.is_cancelled():
                self.toast(f"Unable to read split folder: {error.message}")
            return
        items: list[FileItem] = []
        if self.split_folder is not None:
            for info in infos:
                name = info.get_name()
                hidden = name.startswith(".") or info.get_attribute_boolean("standard::is-hidden")
                if hidden and not self.show_hidden:
                    continue
                items.append(
                    FileItem(
                        file=self.split_folder.get_child(name),
                        name=name,
                        display_name=info.get_display_name() or name,
                        is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
                        size=int(info.get_size()),
                        modified=int(info.get_attribute_uint64("time::modified")),
                        content_type=info.get_content_type() or "",
                        hidden=hidden,
                        is_symlink=info.get_is_symlink(),
                        created=int(info.get_attribute_uint64("time::created")),
                        accessed=int(info.get_attribute_uint64("time::access")),
                    )
                )
        self.split_loading_items.extend(items)
        if len(infos) >= 128:
            source.next_files_async(
                128,
                GLib.PRIORITY_DEFAULT,
                self.split_cancellable,
                lambda enumerator, chunk: self.on_split_chunk(enumerator, chunk, generation),
            )
        else:
            try:
                source.close(None)
            except GLib.Error:
                pass
            self.split_enumerator = None
            self.split_view.set_items(self.sort_items(self.split_loading_items))

    def normalize_location(self, location: str) -> str:
        location = location.strip()
        if location in {"trash:", "trash://", TRASH_URI}:
            return TRASH_URI
        if location.startswith("file://"):
            return Gio.File.new_for_uri(location).get_path() or self.home
        if "://" in location:
            return location
        if location.startswith("~/") or location == "~":
            location = os.path.expanduser(location)
        return os.path.abspath(location or self.home)

    def display_location(self, location: str) -> str:
        if location == TRASH_URI:
            return "TRASH"
        if "://" in location:
            return location
        if os.path.abspath(location) == os.path.abspath(self.home):
            return "~"
        home_prefix = os.path.abspath(self.home) + os.path.sep
        if os.path.abspath(location).startswith(home_prefix):
            return "~" + os.path.abspath(location)[len(self.home) :]
        return os.path.abspath(location)

    def folder_settings_key(self, location: str | None = None) -> str:
        target = location or self.current_location or self.home
        if target == TRASH_URI or "://" in target:
            return target
        return os.path.abspath(target)

    def apply_folder_view_settings(self, location: str) -> None:
        options = self.folder_view_settings.get(self.folder_settings_key(location), {})
        if not isinstance(options, dict):
            return
        mode = options.get("view_mode")
        if mode in {"grid", "compact", "details", "list"}:
            self.view_mode = self.normalize_view_mode(mode)
        if options.get("sort_mode") in {"name", "modified", "size", "type"}:
            self.sort_mode = options["sort_mode"]
        if isinstance(options.get("sort_reverse"), bool):
            self.sort_reverse = options["sort_reverse"]
        if isinstance(options.get("show_hidden"), bool):
            self.show_hidden = options["show_hidden"]
        if "zoom_level" in options:
            try:
                self.zoom_level = max(60, min(180, int(options["zoom_level"])))
            except (TypeError, ValueError):
                pass
        self.update_view_buttons()
        if hasattr(self, "content_stack"):
            self.content_stack.set_visible_child_name(self.view_mode)

    def persist_folder_view_settings(self) -> None:
        if not self.current_location:
            return
        self.folder_view_settings[self.folder_settings_key()] = {
            "view_mode": self.view_mode,
            "sort_mode": self.sort_mode,
            "sort_reverse": self.sort_reverse,
            "show_hidden": self.show_hidden,
            "zoom_level": self.zoom_level,
        }
        self.settings["folder_view_settings"] = dict(self.folder_view_settings)
        save_settings(self.settings)

    def current_file(self) -> Gio.File:
        if self.current_location == TRASH_URI:
            return Gio.File.new_for_uri(TRASH_URI)
        if self.current_location and "://" in self.current_location:
            return Gio.File.new_for_uri(self.current_location)
        return Gio.File.new_for_path(self.current_location or self.home)

    def watch_current_folder(self) -> None:
        if self.folder_monitor is not None:
            self.folder_monitor.cancel()
            self.folder_monitor = None
        if self.folder_reload_source:
            GLib.source_remove(self.folder_reload_source)
            self.folder_reload_source = 0
        try:
            self.folder_monitor = self.current_file().monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.folder_monitor.connect("changed", self.on_folder_changed)
        except GLib.Error:
            self.folder_monitor = None

    def on_folder_changed(self, *_args) -> None:
        if self.shutting_down:
            return
        if self.folder_reload_source:
            GLib.source_remove(self.folder_reload_source)
        self.folder_reload_source = GLib.timeout_add(250, self.reload_after_folder_change)

    def reload_after_folder_change(self) -> bool:
        self.folder_reload_source = 0
        if not self.shutting_down and self.current_location:
            self.preview_manager.clear()
            self.thumbnail_manager.clear()
            self.load_items()
        return False

    def navigate(
        self,
        location: str,
        record_history: bool = True,
        preserve_selection: list[str] | None = None,
    ) -> None:
        LOGGER.debug(
            "navigate requested location=%s current=%s tab=%d record_history=%s",
            location,
            self.current_location,
            self.active_tab_index,
            record_history,
        )
        target = self.normalize_location(location)
        remote = target != TRASH_URI and "://" in target
        remote_directory = False
        if remote:
            try:
                remote_directory = self.current_file_for(target).query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY
            except GLib.Error:
                remote_directory = False
        if target != TRASH_URI and not remote and not os.path.isdir(target):
            if os.path.isfile(target):
                self.open_uri(Gio.File.new_for_path(target).get_uri())
            else:
                self.toast(f"Folder not found: {target}")
                if self.current_location:
                    self.location_entry.set_text(self.display_location(self.current_location))
            return
        if remote and not remote_directory:
            self.toast(f"Location is not a readable folder: {target}")
            return
        self.record_recent_location(target)
        if target == self.current_location:
            self.refresh()
            return
        if self.current_location:
            self.save_active_tab()
            if record_history:
                self.back_history.append(self.current_location)
                self.forward_history.clear()
        self.search_generation += 1
        self.search_service.cancel()
        if self.search_debounce_source:
            GLib.source_remove(self.search_debounce_source)
            self.search_debounce_source = 0
        if self.preview_window is not None:
            self.preview_window.close()
        self.current_location = target
        self.tabs[self.active_tab_index].selected_uris = list(preserve_selection or [])
        self.hide_search_entry()
        self.location_entry.set_text(self.display_location(target))
        self.update_breadcrumbs()
        self.view_mode = self.normalize_view_mode(self.view_mode)
        self.update_view_buttons()
        if self.settings.get("auto_refresh", True):
            self.watch_current_folder()
        elif self.folder_monitor is not None:
            self.folder_monitor.cancel()
            self.folder_monitor = None
        self.apply_folder_view_settings(target)
        self.ensure_active_file_page("navigate")
        self.update_folder_panel()
        LOGGER.debug("navigation started target=%s tab=%d", target, self.active_tab_index)
        self.load_items()
        self.update_sidebar_active()
        self.update_nav_buttons()

    def current_file_for(self, location: str) -> Gio.File:
        return Gio.File.new_for_uri(location) if "://" in location else Gio.File.new_for_path(location)

    def load_items(self) -> None:
        if self.shutting_down:
            return
        self.load_generation += 1
        generation = self.load_generation
        if self.render_source:
            GLib.source_remove(self.render_source)
            self.render_source = 0
        self.pending_render_items = []
        if self.load_cancellable is not None:
            self.load_cancellable.cancel()
        self._close_loading_enumerator()
        self.load_cancellable = Gio.Cancellable()
        self.loading_items = []
        self.loading_location = self.current_location
        self.loading_folder = self.current_file()
        self.load_error = None
        self.all_items = []
        self.grid.set_items([])
        self.compact_view.set_items([])
        self.listbox.set_items([])
        self.selected_item = None
        self.update_inspector(None)
        self.set_loading(True)
        self.log_file_view_state("load-cleared")
        try:
            self.loading_folder.enumerate_children_async(
                FILE_ATTRIBUTES,
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.load_cancellable,
                lambda source, result: self.on_enumerator_ready(source, result, generation),
            )
        except GLib.Error as error:
            self.finish_loading(generation, f"Unable to read folder: {error.message}")

    def _close_loading_enumerator(self) -> None:
        if self.loading_enumerator is None:
            return
        try:
            self.loading_enumerator.close(None)
        except GLib.Error:
            pass
        self.loading_enumerator = None

    def set_loading(self, loading: bool) -> None:
        if not hasattr(self, "loading_overlay"):
            return
        self.loading_overlay.set_visible(loading)
        self.empty_label.set_visible(False if loading else (self.load_error is not None or not self.all_items))
        if self.load_error:
            self.empty_label.set_text(self.load_error)
        if loading:
            self.loading_spinner.start()
        else:
            self.loading_spinner.stop()
            self.status_label.set_text("Unable to read folder" if self.load_error else f"{len(self.all_items)} items")
        LOGGER.debug(
            "stack loading transition loading=%s overlay=%s empty=%s backend_items=%d error=%s",
            loading,
            self.loading_overlay.get_visible(),
            self.empty_label.get_visible(),
            len(self.all_items),
            self.load_error,
        )

    def on_enumerator_ready(
        self,
        source: Gio.File,
        result: Gio.AsyncResult,
        generation: int,
    ) -> None:
        if self.shutting_down or generation != self.load_generation or self.load_cancellable is None:
            try:
                stale_enumerator = source.enumerate_children_finish(result)
                stale_enumerator.close(None)
            except GLib.Error:
                pass
            return
        try:
            self.loading_enumerator = source.enumerate_children_finish(result)
            self.load_next_chunk(generation)
        except GLib.Error as error:
            if not self.load_cancellable.is_cancelled():
                self.finish_loading(generation, f"Unable to read folder: {error.message}")
            else:
                self.finish_loading(generation)

    def load_next_chunk(self, generation: int) -> None:
        if self.shutting_down or generation != self.load_generation or self.loading_enumerator is None or self.load_cancellable is None:
            return
        self.loading_enumerator.next_files_async(
            128,
            GLib.PRIORITY_DEFAULT,
            self.load_cancellable,
            lambda source, result: self.on_items_chunk(source, result, generation),
        )

    def on_items_chunk(
        self,
        source: Gio.FileEnumerator,
        result: Gio.AsyncResult,
        generation: int,
    ) -> None:
        if self.shutting_down or generation != self.load_generation or self.load_cancellable is None:
            try:
                source.close(None)
            except GLib.Error:
                pass
            return
        try:
            infos = source.next_files_finish(result)
        except GLib.Error as error:
            if not self.load_cancellable.is_cancelled():
                self.finish_loading(generation, f"Unable to read folder: {error.message}")
            else:
                self.finish_loading(generation)
            return

        folder = self.loading_folder
        if folder is not None:
            for info in infos:
                name = info.get_name()
                hidden = name.startswith(".") or info.get_attribute_boolean("standard::is-hidden")
                if hidden and not self.show_hidden:
                    continue
                file_type = info.get_file_type()
                self.loading_items.append(
                    FileItem(
                        file=folder.get_child(name),
                        name=name,
                        display_name=info.get_display_name() or name,
                        is_dir=file_type == Gio.FileType.DIRECTORY,
                        size=int(info.get_size()),
                        modified=int(info.get_attribute_uint64("time::modified")),
                        content_type=info.get_content_type() or "",
                        hidden=hidden,
                        is_symlink=info.get_is_symlink(),
                        created=int(info.get_attribute_uint64("time::created")),
                        accessed=int(info.get_attribute_uint64("time::access")),
                        trash_orig_path=attribute_text(info, "trash::orig-path") if self.loading_location == TRASH_URI else None,
                    )
                )

        if len(infos) < 128:
            self.finish_loading(generation)
        else:
            self.load_next_chunk(generation)

    def finish_loading(self, generation: int, error: str | None = None) -> None:
        if self.shutting_down or generation != self.load_generation:
            return
        self._close_loading_enumerator()
        self.load_error = error
        self.all_items = [] if error else self.sort_items(self.loading_items)
        LOGGER.debug(
            "enumeration count location=%s count=%d error=%s",
            self.loading_location,
            len(self.all_items),
            error,
        )
        LOGGER.debug("backend items=%d", len(self.all_items))
        self.set_loading(False)
        if error:
            self.empty_label.set_text(error)
            self.empty_label.set_visible(True)
        else:
            self.render_items()
            LOGGER.debug(
                "navigation committed location=%s tab=%d model_items=%d",
                self.current_location,
                self.active_tab_index,
                len(self.all_items),
            )
            self.log_file_view_state("navigation-committed-before-render-callback")
        if self.tabs and self.current_location:
            tab = self.tabs[self.active_tab_index]
            tab.location = self.current_location
            tab.back_history = list(self.back_history)
            tab.forward_history = list(self.forward_history)
            tab.view_mode = self.view_mode
            tab.title = self.tab_label(tab)
            self.update_tabs()
        self.refresh_git_status(generation)

    def sort_items(self, items: list[FileItem]) -> list[FileItem]:
        return core_sort_items(
            items,
            mode=self.sort_mode,
            reverse=self.sort_reverse,
            folders_first=bool(self.settings.get("folders_first", True)),
            natural=bool(self.settings.get("natural_sort", True)),
        )

    def refresh_git_status(self, generation: int) -> None:
        self.git_status_generation += 1
        status_generation = self.git_status_generation
        if self.git_status_future is not None:
            self.git_status_future.cancel()
            self.git_status_future = None
        if not self.settings.get("show_git_status", False):
            for item in self.all_items:
                item.git_status = ""
            return
        location = self.current_location
        if not location or location == TRASH_URI or "://" in location or not os.path.isdir(location):
            return
        self.git_status_future = self.size_executor.submit(status_for_location, location)
        self.git_status_future.add_done_callback(
            lambda future, target=location, load_generation=generation, token=status_generation: GLib.idle_add(
                self.apply_git_status,
                target,
                load_generation,
                token,
                future,
            )
        )

    def apply_git_status(
        self,
        location: str,
        generation: int,
        status_generation: int,
        future: Future[dict[str, str]],
    ) -> bool:
        if self.shutting_down or generation != self.load_generation or status_generation != self.git_status_generation:
            return False
        self.git_status_future = None
        try:
            statuses = future.result()
        except Exception:
            statuses = {}
        if location != self.current_location:
            return False
        for item in self.all_items:
            item.git_status = statuses.get(item.path or "", "")
        self.render_items()
        return False

    def prepare_groups(self, items: list[FileItem]) -> None:
        last_group = ""
        for item in items:
            if not self.show_groups:
                item.group = ""
                continue
            if item.is_dir:
                group = "Folders"
            elif item.content_type:
                group = item.content_type.split("/", 1)[-1].replace("-", " ").title()
            else:
                group = "Files"
            item.group = group if group != last_group else ""
            last_group = group

    def render_items(self) -> None:
        if self.shutting_down:
            return
        query = self.search_entry.get_text().strip()
        query_folded = query.casefold()
        visible = [item for item in self.all_items if not query_folded or query_folded in item.display_name.casefold()]
        self.ensure_active_file_page("render-start")
        LOGGER.debug("render queue backend_items=%d visible_items=%d", len(self.all_items), len(visible))
        self.prepare_groups(visible)
        if self.tabs and self.current_location == self.tabs[self.active_tab_index].location:
            self.pending_selection_uris = set(self.tabs[self.active_tab_index].selected_uris)
        else:
            self.pending_selection_uris = {item.uri for item in self.get_selected_items()}
        self.render_generation += 1
        generation = self.render_generation
        if self.render_source:
            GLib.source_remove(self.render_source)
            self.render_source = 0
        self.pending_render_items = visible
        self.grid.set_items([])
        self.compact_view.set_items([])
        self.listbox.set_items([])
        self.selected_item = None

        self.empty_label.set_visible(not visible and not self.loading_overlay.get_visible())
        if query and not visible:
            self.empty_label.set_text(f'No matches for “{query}”')
        else:
            self.empty_label.set_text("This folder is empty")
        if query:
            self.path_meta.set_text(f"{len(visible)} matches · {len(self.all_items)} items")
        else:
            direction = "descending" if self.sort_reverse else "ascending"
            self.path_meta.set_text(f"{len(visible)} items · sorted by {self.sort_mode} · {direction}")
        self.status_label.set_text(f"{len(visible)} items · {format_size(sum(item.size for item in visible if not item.is_dir))} files")
        self.update_inspector(None)
        self.update_breadcrumbs()
        if visible:
            self.render_source = GLib.timeout_add(1, self.render_next_batch, generation)
        else:
            self.pending_selection_uris.clear()
            self.restore_active_scroll()

    def render_next_batch(self, generation: int) -> bool:
        if self.shutting_down or generation != self.render_generation:
            return False
        self.ensure_active_file_page("render-batch")
        batch_size = 64 if self.settings.get("compact_mode") else 48
        batch = self.pending_render_items[:batch_size]
        del self.pending_render_items[:batch_size]
        LOGGER.debug(
            "render batch active_view=%s batch_items=%d remaining_items=%d",
            self.view_mode,
            len(batch),
            len(self.pending_render_items),
        )
        self.active_file_view().append_items(batch)
        if self.pending_render_items:
            return True
        active_view = self.active_file_view()
        active_view.select_uris(self.pending_selection_uris)
        self.pending_selection_uris.clear()
        self.restore_active_scroll()
        self.render_source = 0
        self.log_file_view_state("render-complete")
        return False

    def menu_button(
        self,
        box: Gtk.Box,
        popover: Gtk.Popover,
        label: str,
        callback: Callable[[], None],
        sensitive: bool = True,
        destructive: bool = False,
    ) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.set_halign(Gtk.Align.FILL)
        button.set_sensitive(sensitive)
        button.add_css_class("context-menu-item")
        if destructive:
            button.add_css_class("destructive")

        def activate(*_args) -> None:
            popover.popdown()
            callback()

        button.connect("clicked", activate)
        box.append(button)
        return button

    def menu_separator(self, box: Gtk.Box) -> None:
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.add_css_class("menu-separator")
        box.append(separator)

    def show_context_menu(
        self,
        parent: Gtk.Widget,
        x: float,
        y: float,
        item: FileItem | None,
    ) -> None:
        if self.context_popover is not None:
            self.context_popover.popdown()

        popover = Gtk.Popover()
        popover.set_parent(parent)
        popover.set_has_arrow(False)
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        popover.connect("closed", lambda *_args: self.clear_context_popover(popover))
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.add_css_class("context-menu")
        self.current_context_item = item

        if item is not None:
            selected_context_items = self.get_selected_items()
            operation_items = selected_context_items if item in selected_context_items else [item]
            local_operation_items = bool(operation_items) and all(entry.path for entry in operation_items)
            header = Gtk.Label(label=item.display_name)
            header.set_halign(Gtk.Align.START)
            header.set_ellipsize(Pango.EllipsizeMode.END)
            header.add_css_class("context-menu-header")
            menu.append(header)
            self.menu_button(menu, popover, "OPEN", lambda: self.activate_item(item))
            self.menu_button(menu, popover, "OPEN WITH", lambda: self.open_with(item), sensitive=bool(item.path))
            if item.is_dir:
                self.menu_button(menu, popover, "OPEN IN NEW TAB", lambda: self.open_item_new_tab(item))
                self.menu_button(menu, popover, "OPEN IN SPLIT", lambda: self.navigate_split(item.path or item.uri), sensitive=bool(item.path))
                self.menu_button(menu, popover, "OPEN TERMINAL HERE", lambda: self.open_terminal_here(item), sensitive=bool(item.path))
            self.menu_separator(menu)
            self.menu_button(menu, popover, "COPY", lambda: self.copy_items(operation_items), sensitive=local_operation_items)
            self.menu_button(menu, popover, "CUT", lambda: self.cut_items(operation_items), sensitive=local_operation_items)
            duplicate_paths = [entry.path for entry in operation_items if entry.path]
            self.menu_button(menu, popover, "DUPLICATE", lambda: self.start_operation(OperationKind.DUPLICATE, duplicate_paths, self.current_location), sensitive=local_operation_items)
            self.menu_button(menu, popover, "CREATE SYMBOLIC LINK", self.link_selected_here, sensitive=local_operation_items and self.can_write_location())
            self.menu_button(menu, popover, "COMPRESS SELECTION", self.compress_selected, sensitive=local_operation_items and self.can_write_location())
            self.menu_button(menu, popover, "EXTRACT ARCHIVE", self.extract_selected, sensitive=len(operation_items) == 1 and self.archive_kind(item) is not None)
            self.menu_button(menu, popover, "COPY PATH", lambda: self.copy_path(item), sensitive=bool(item.path))
            self.menu_button(menu, popover, "COPY URI", lambda: self.copy_uri(item))
            self.menu_button(menu, popover, "RENAME", lambda: self.rename_item(item), sensitive=bool(item.path))
            self.menu_button(menu, popover, "PROPERTIES", lambda: self.show_properties_for_selection(item))
            self.menu_button(menu, popover, "SHOW TARGET", lambda: self.show_target(item), sensitive=bool(item.is_symlink and item.path))
            self.menu_button(menu, popover, "COMPARE SELECTED", self.compare_selected, sensitive=len(operation_items) == 2 and all(entry.path and not entry.is_dir for entry in operation_items))
            self.menu_button(menu, popover, "BATCH RENAME", self.batch_rename_selected, sensitive=len(operation_items) > 1 and local_operation_items)
            if item.path and item.is_dir:
                if item.path in self.pinned_places:
                    self.menu_button(menu, popover, "UNPIN FROM SIDEBAR", lambda: self.unpin_path(item.path or ""))
                else:
                    self.menu_button(menu, popover, "PIN TO SIDEBAR", lambda: self.pin_item(item))
            if self.is_starred(item.uri):
                self.menu_button(menu, popover, "UNSTAR", lambda: self.unstar_item(item))
            else:
                self.menu_button(menu, popover, "STAR", lambda: self.star_item(item))
            scripts = discover_scripts()
            self.menu_button(
                menu,
                popover,
                "RUN SCRIPT…",
                lambda: self.show_script_picker(operation_items),
                sensitive=bool(scripts and local_operation_items),
            )
            if self.current_location == TRASH_URI and item.trash_orig_path:
                self.menu_button(menu, popover, "RESTORE", lambda: self.restore_item(item))
            self.menu_separator(menu)
            self.menu_button(
                menu,
                popover,
                "DELETE PERMANENTLY" if self.current_location == TRASH_URI else "MOVE TO TRASH",
                lambda: self.delete_items(operation_items),
                sensitive=local_operation_items or self.current_location == TRASH_URI,
                destructive=True,
            )
            if self.current_location != TRASH_URI:
                self.menu_button(menu, popover, "DELETE PERMANENTLY", lambda: self.permanent_delete_items(operation_items), sensitive=local_operation_items, destructive=True)
        else:
            location = self.display_location(self.current_location or self.home)
            writable = self.can_write_location()
            header = Gtk.Label(label=f"CURRENT · {location}")
            header.set_halign(Gtk.Align.START)
            header.add_css_class("context-menu-header")
            menu.append(header)
            self.menu_button(menu, popover, "NEW FOLDER", self.new_folder, sensitive=writable)
            self.menu_button(menu, popover, "NEW FILE", self.new_file, sensitive=writable)
            templates = discover_templates()
            self.menu_button(
                menu,
                popover,
                "NEW FROM TEMPLATE…",
                self.show_template_picker,
                sensitive=bool(templates and writable),
            )
            self.menu_button(menu, popover, "PASTE", self.paste_items, sensitive=bool(self.clipboard_items and writable))
            self.menu_button(
                menu,
                popover,
                "RUN SCRIPT…",
                lambda: self.show_script_picker([]),
                sensitive=bool(discover_scripts() and writable),
            )
            self.menu_button(
                menu,
                popover,
                "STAR CURRENT LOCATION",
                lambda: self.star_location(self.current_location or ""),
                sensitive=bool(self.current_location and self.current_location != TRASH_URI and not self.is_starred(self.current_location)),
            )
            if self.current_location and self.is_starred(self.current_location):
                self.menu_button(menu, popover, "UNSTAR CURRENT LOCATION", lambda: self.unstar_location(self.current_location or ""))
            self.menu_button(menu, popover, "CONNECT TO SERVER…", self.connect_to_server)
            self.menu_button(menu, popover, "OPEN TERMINAL HERE", self.open_terminal_here, sensitive=self.current_location != TRASH_URI and "://" not in (self.current_location or ""))
            self.menu_button(menu, popover, "SELECT ALL", self.select_all_current)
            self.menu_button(menu, popover, "PROPERTIES", lambda: self.show_directory_properties(), sensitive=bool(self.current_location and "://" not in self.current_location and self.current_location != TRASH_URI))
            self.menu_separator(menu)
            self.menu_button(menu, popover, "REFRESH", self.refresh)
            self.menu_button(menu, popover, "SHOW HIDDEN" if not self.show_hidden else "HIDE HIDDEN", self.toggle_hidden)
            self.menu_button(menu, popover, f"SORT: {self.sort_mode.upper()}{' ↓' if self.sort_reverse else ' ↑'}", self.cycle_sort)
            self.menu_button(menu, popover, "SORT BY NAME", lambda: self.set_sort_order("name"), sensitive=self.sort_mode != "name")
            self.menu_button(menu, popover, "SORT BY MODIFIED", lambda: self.set_sort_order("modified"), sensitive=self.sort_mode != "modified")
            self.menu_button(menu, popover, "SORT BY SIZE", lambda: self.set_sort_order("size"), sensitive=self.sort_mode != "size")
            self.menu_button(menu, popover, "SORT BY TYPE", lambda: self.set_sort_order("type"), sensitive=self.sort_mode != "type")
            self.menu_button(menu, popover, "FOLDERS FIRST", self.toggle_folders_first)
            self.menu_button(menu, popover, "GROUP BY TYPE", self.toggle_groups)
            self.menu_button(menu, popover, "ICONS VIEW", lambda: self.set_view("grid"), sensitive=self.view_mode != "grid")
            self.menu_button(menu, popover, "COMPACT VIEW", lambda: self.set_view("compact"), sensitive=self.view_mode != "compact")
            self.menu_button(menu, popover, "DETAILS VIEW", lambda: self.set_view("details"), sensitive=self.view_mode != "details")
            self.menu_button(menu, popover, "FOLDERS PANEL", self.toggle_folders_panel)
            self.menu_button(menu, popover, "TERMINAL PANEL", self.toggle_terminal_panel)
            self.menu_button(menu, popover, "TOGGLE SPLIT VIEW", self.toggle_split_view)
            if self.current_location == TRASH_URI:
                self.menu_separator(menu)
                self.menu_button(menu, popover, "EMPTY TRASH", self.empty_trash, destructive=True)

        popover.set_child(menu)
        self.context_popover = popover
        popover.popup()

    def clear_context_popover(self, popover: Gtk.Popover) -> None:
        if self.context_popover is popover:
            self.context_popover = None
            self.current_context_item = None
        if popover.get_parent() is not None:
            popover.unparent()

    def make_item_icon(self, item: FileItem, size: int) -> Gtk.Image:
        image = Gtk.Image.new_from_icon_name(icon_for_item(item))
        image.set_pixel_size(size)
        return image

    def request_thumbnail(self, item: FileItem, image: Gtk.Image, size: int) -> None:
        """Request a bounded thumbnail for a virtualized file-row image."""
        if not self.settings.get("show_thumbnails", True) or not is_image(item) or not item.path:
            return
        uri = item.uri
        self.thumbnail_targets[id(image)] = uri

        self.thumbnail_manager.request(
            item.path,
            max(24, int(size)),
            lambda pixbuf, target=image, target_uri=uri: self.apply_thumbnail(target, target_uri, pixbuf),
            scheduler=lambda callback, *args: GLib.idle_add(lambda: bool(callback(*args) or False)),
        )

    def clear_thumbnail_target(self, image: Gtk.Image) -> None:
        """Drop the pending target when a virtualized row is unbound."""

        self.thumbnail_targets.pop(id(image), None)

    def apply_thumbnail(self, image: Gtk.Image, uri: str, pixbuf: object | None) -> None:
        image_id = id(image)
        if self.thumbnail_targets.get(image_id) != uri:
            return
        self.thumbnail_targets.pop(image_id, None)
        if pixbuf is None:
            return
        try:
            image.set_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))  # type: ignore[arg-type]
            image.add_css_class("thumbnail-image")
        except (GLib.Error, RuntimeError, TypeError):
            # A stale virtualized row or a decoder failure must not affect browsing.
            return

    def update_breadcrumbs(self) -> None:
        clear_box(self.breadcrumb_box)
        location = self.current_location or self.home
        if location == TRASH_URI:
            crumb = Gtk.Button(label="TRASH")
            crumb.add_css_class("crumb-button")
            crumb.set_sensitive(False)
            self.breadcrumb_box.append(crumb)
            return
        if "://" in location:
            crumb = Gtk.Button(label=self.display_location(location))
            crumb.add_css_class("crumb-button")
            crumb.set_sensitive(False)
            self.breadcrumb_box.append(crumb)
            return

        path = os.path.abspath(location)
        parts: list[tuple[str, str]] = []
        if path == os.path.abspath(self.home) or path.startswith(os.path.abspath(self.home) + os.path.sep):
            parts.append(("Home", self.home))
            relative = os.path.relpath(path, self.home)
            if relative != ".":
                built = self.home
                for part in Path(relative).parts:
                    built = os.path.join(built, part)
                    parts.append((part, built))
        elif path == os.path.sep:
            parts.append(("Filesystem", os.path.sep))
        else:
            parts.append(("Filesystem", os.path.sep))
            built = os.path.sep
            for part in Path(path).parts:
                if part == os.path.sep:
                    continue
                built = os.path.join(built, part)
                parts.append((part, built))

        for index, (name, target) in enumerate(parts):
            if index:
                separator = Gtk.Label(label="›")
                separator.add_css_class("path-meta")
                self.breadcrumb_box.append(separator)
            crumb = Gtk.Button(label=name)
            crumb.add_css_class("crumb-button")
            crumb.connect("clicked", lambda _button, target=target: self.navigate(target))
            self.add_location_drop_target(crumb, target)
            self.breadcrumb_box.append(crumb)

    def update_sidebar_active(self) -> None:
        current = self.current_location
        for location, button in self.place_buttons.items():
            active = location == current
            if location != TRASH_URI and current != TRASH_URI and "://" not in (current or ""):
                active = os.path.abspath(location) == os.path.abspath(current or "")
            button.set_css_classes(["sidebar-item", "active"] if active else ["sidebar-item"])

    def update_nav_buttons(self) -> None:
        self.back_button.set_sensitive(bool(self.back_history))
        self.forward_button.set_sensitive(bool(self.forward_history))
        location = self.current_location
        if location and "://" in location:
            self.up_button.set_sensitive(Gio.File.new_for_uri(location).get_parent() is not None)
        else:
            self.up_button.set_sensitive(bool(location and location != TRASH_URI and os.path.abspath(location) != os.path.sep))

    def on_window_width_changed(self, *_args) -> None:
        if self.shutting_down:
            return
        GLib.idle_add(self.sync_responsive_layout)

    def sync_responsive_layout(self) -> bool:
        if self.shutting_down or self.window is None:
            return False
        width = self.window.get_width()
        height = self.window.get_height()
        if width <= 0:
            return False
        compact = width < 1180
        narrow = width < 920
        short = height > 0 and height < 650
        if hasattr(self, "sidebar"):
            sidebar_visible = not narrow and not self.sidebar_user_hidden
            self.sidebar.set_visible(sidebar_visible)
            if hasattr(self, "sidebar_scroll"):
                self.sidebar_scroll.set_visible(sidebar_visible)
            if hasattr(self, "sidebar_separator"):
                self.sidebar_separator.set_visible(sidebar_visible)
            configured_sidebar_width = int(self.settings.get("sidebar_width", 220))
            self.sidebar.set_size_request(
                max(180, min(280, configured_sidebar_width if not compact else min(configured_sidebar_width, 200))),
                -1,
            )
        if hasattr(self, "hint_label"):
            self.hint_label.set_visible(width >= 1050 and not short)
        if hasattr(self, "grid"):
            self.grid.set_max_children_per_line(3 if narrow else (7 if compact else 10))
        if hasattr(self, "main_pane"):
            if self.settings.get("compact_mode") or short:
                self.main_pane.add_css_class("compact-layout")
            else:
                self.main_pane.remove_css_class("compact-layout")
        return False

    def update_inspector(self, item: FileItem | None) -> None:
        """Keep selection state without reintroducing a permanent inspector."""

        self.selected_item = item

    def size_detail(self, size: int) -> str:
        return f"{format_size(size)} · {size:,} B"

    def normalize_view_mode(self, mode: str) -> str:
        return "details" if mode == "list" else mode if mode in {"grid", "compact", "details"} else "grid"

    def update_view_buttons(self) -> None:
        if not hasattr(self, "grid_button"):
            return
        self.grid_button.set_css_classes(["icon-button", "active"] if self.view_mode == "grid" else ["icon-button"])
        self.compact_button.set_css_classes(["icon-button", "active"] if self.view_mode == "compact" else ["icon-button"])
        self.list_button.set_css_classes(["icon-button", "active"] if self.view_mode == "details" else ["icon-button"])

    def active_file_view(self) -> VirtualFileView:
        if self.view_mode == "grid":
            return self.grid
        if self.view_mode == "compact":
            return self.compact_view
        return self.listbox

    def active_scroll_adjustment(self) -> Gtk.Adjustment:
        if self.view_mode == "grid":
            return self.grid_scroll.get_vadjustment()
        if self.view_mode == "compact":
            return self.compact_scroll.get_vadjustment()
        return self.list_scroll.get_vadjustment()

    def restore_active_scroll(self) -> None:
        if not self.tabs:
            return
        adjustment = self.active_scroll_adjustment()
        maximum = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        value = max(0.0, min(float(self.tabs[self.active_tab_index].scroll_value), maximum))
        adjustment.set_value(value)

    def permissions_for_item(self, item: FileItem) -> str:
        if not item.path:
            return "Remote / virtual"
        try:
            mode = os.stat(item.path, follow_symlinks=False).st_mode
            return stat.filemode(mode)
        except OSError:
            return "Unavailable"

    def additional_info_for_item(self, item: FileItem) -> str:
        """Short, stable details column used by the Dolphin-style Details view."""

        permissions = self.permissions_for_item(item)
        if item.git_status:
            permissions = f"{item.git_status} · {permissions}"
        if item.created:
            return f"{permissions} · {format_date(item.created)}"
        return permissions

    def calculate_folder_size(self, item: FileItem) -> None:
        if not item.path or item.uri in self.folder_size_jobs:
            return
        future = self.size_executor.submit(directory_size, item.path)
        self.folder_size_jobs[item.uri] = future
        future.add_done_callback(
            lambda completed, selected=item, uri=item.uri: GLib.idle_add(
                self.apply_folder_size,
                selected,
                uri,
                completed,
            )
        )

    def apply_folder_size(self, item: FileItem, uri: str, future: Future[int]) -> bool:
        self.folder_size_jobs.pop(uri, None)
        if self.shutting_down:
            return False
        try:
            item.folder_size = future.result()
        except Exception:
            item.folder_size = None
        if self.selected_item is item:
            # Folder size is also shown in grid/list metadata.  Rebind the
            # virtualized view only after the worker has committed its value;
            # selection restoration keeps this cheap and non-destructive.
            self.render_items()
        return False

    def get_selected_items(self) -> list[FileItem]:
        return list(self.active_file_view().get_selected_items())

    def get_selected_item(self) -> FileItem | None:
        selected = self.get_selected_items()
        return selected[0] if selected else None

    def on_grid_selection_changed(self, *_args) -> None:
        self.update_inspector(self.get_selected_item())
        self.save_active_tab()

    def on_list_selection_changed(self, *_args) -> None:
        self.update_inspector(self.get_selected_item())
        self.save_active_tab()

    def activate_item(self, item: FileItem | None) -> None:
        if item is None:
            return
        if item.is_dir:
            self.navigate(item.path or item.uri)
        else:
            self.open_uri(item.uri)

    def open_item_new_tab(self, item: FileItem) -> None:
        if item.is_dir:
            self.new_tab(location=item.path or item.uri)
        else:
            self.open_uri(item.uri)

    def show_target(self, item: FileItem | None) -> None:
        if item is None or not item.path:
            self.toast("Show Target is available for local items only")
            return
        target = item.path
        if item.is_symlink:
            try:
                link_target = os.readlink(item.path)
                target = link_target if os.path.isabs(link_target) else os.path.join(os.path.dirname(item.path), link_target)
            except OSError as error:
                self.toast(f"Could not read link target: {error}")
                return
        target = os.path.abspath(target)
        parent = os.path.dirname(target)
        if not parent or not os.path.isdir(parent):
            self.toast("Target location is no longer available")
            return
        try:
            uri = Gio.File.new_for_path(target).get_uri()
        except (GLib.Error, ValueError):
            uri = ""
        if parent == self.current_location:
            self.pending_selection_uris = {uri} if uri else set()
            self.tabs[self.active_tab_index].selected_uris = list(self.pending_selection_uris)
            self.render_items()
        else:
            self.navigate(parent, preserve_selection=[uri] if uri else None)

    def compare_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if len(items) != 2 or any(not item.path or item.is_dir for item in items):
            self.toast("Select exactly two local files to compare")
            return
        candidates = ("meld", "kompare", "kdiff3", "diffuse")
        executable = next((shutil.which(name) for name in candidates if shutil.which(name)), None)
        if executable is None:
            self.toast("No supported comparison tool is installed")
            return
        argv = [executable, *(item.path or "" for item in items)]
        if os.path.basename(executable) == "kdiff3":
            argv = [executable, "--", *(item.path or "" for item in items)]
        try:
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
            self.toast("Comparison tool started")
        except (GLib.Error, OSError) as error:
            self.toast(f"Could not start comparison tool: {error}")

    def batch_rename_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if len(items) < 2:
            self.toast("Select at least two local items")
            return
        local_items = [item for item in items if item.path]
        if len(local_items) != len(items):
            self.toast("Batch rename is available for local items only")
            return
        self.prompt(
            "Batch rename",
            "Use %n for the sequence number and %name% for the original stem.",
            "item-%n",
            "RENAME ALL",
            lambda pattern: self.finish_batch_rename(local_items, pattern),
        )

    def finish_batch_rename(self, items: list[FileItem], pattern: str) -> None:
        clean_pattern = pattern.strip()
        if not clean_pattern:
            self.toast("Choose a rename pattern")
            return
        queue: list[tuple[str, str]] = []
        for index, item in enumerate(items, start=1):
            if not item.path:
                continue
            stem, suffix = os.path.splitext(item.name)
            name = clean_pattern.replace("%n", str(index)).replace("%name%", stem).replace("%ext%", suffix.lstrip("."))
            if "%n" not in clean_pattern and "%name%" not in clean_pattern and len(items) > 1:
                name = f"{name}-{index}{suffix if not os.path.splitext(name)[1] else ''}"
            if not name or name in {".", ".."} or os.path.basename(name) != name or os.path.sep in name:
                self.toast(f"Invalid generated name for {item.display_name}")
                return
            queue.append((item.path, name))
        self.batch_rename_queue = queue
        self.batch_rename_total = len(queue)
        self.batch_rename_active = bool(queue)
        self.run_next_batch_rename()

    def run_next_batch_rename(self) -> None:
        if not self.batch_rename_queue:
            if self.batch_rename_active and self.batch_rename_total:
                self.toast(f"Batch rename complete · {self.batch_rename_total} items")
            self.batch_rename_total = 0
            self.batch_rename_active = False
            return
        source, target = self.batch_rename_queue.pop(0)
        self.start_operation(OperationKind.RENAME, [source], target)

    def archive_kind(self, item: FileItem | None) -> str | None:
        return core_archive_kind(item.path) if item is not None and not item.is_dir and item.path else None

    def compress_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            self.toast("Select files or folders to compress")
            return
        if any(not item.path for item in items) or not self.can_write_location():
            self.toast("Compression is available for writable local folders only")
            return
        self.prompt(
            "Create archive",
            "The selected entries will be added to a ZIP archive.",
            "archive.zip",
            "COMPRESS",
            lambda name: self.finish_compress(items, name),
        )

    def finish_compress(self, items: list[FileItem], name: str) -> None:
        location = self.current_location
        if not location or "://" in location or not os.path.isdir(location):
            self.toast("Compression is available for local folders only")
            return
        clean_name = name.strip()
        if not clean_name or os.path.basename(clean_name) != clean_name or os.path.sep in clean_name:
            self.toast("Choose a valid archive name")
            return
        kind = "tar" if clean_name.casefold().endswith(".tar") else "zip"
        if os.path.splitext(clean_name)[1].casefold() not in {".zip", ".tar"}:
            clean_name += ".tar" if kind == "tar" else ".zip"
        target = unique_destination(location, clean_name)
        paths = [item.path for item in items if item.path]
        self.run_archive_job(
            lambda: create_archive(paths, target, kind),
            f"Created {os.path.basename(target)}",
        )

    def extract_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if len(items) != 1:
            self.toast("Select one archive to extract")
            return
        item = items[0]
        kind = self.archive_kind(item)
        if kind is None or not item.path or not self.current_location or "://" in self.current_location:
            self.toast("Select a local ZIP or tar archive")
            return
        stem = item.name
        for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".zip", ".tar"):
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        destination = unique_destination(self.current_location, stem or "extracted")
        self.run_archive_job(
            lambda: extract_archive(item.path or "", destination),
            f"Extracted to {os.path.basename(destination)}",
        )

    def run_archive_job(self, job: Callable[[], Any], success_message: str) -> None:
        if self.archive_future is not None:
            self.toast("An archive operation is already running")
            return

        self.archive_future = self.size_executor.submit(job)

        def completed(future: Future[Any]) -> None:
            GLib.idle_add(self.finish_archive_job, future, success_message)

        self.archive_future.add_done_callback(completed)

    def finish_archive_job(self, future: Future[Any], success_message: str) -> bool:
        if self.shutting_down:
            return False
        try:
            future.result()
        except (ArchiveSafetyError, OSError, ValueError) as error:
            self.archive_future = None
            self.toast(f"Archive operation failed: {error}")
            return False
        except Exception as error:
            self.archive_future = None
            self.toast(f"Archive operation failed: {error}")
            return False
        self.archive_future = None
        if not self.shutting_down:
            self.toast(success_message)
            self.refresh()
        return False

    def show_properties_for_selection(self, item: FileItem | None = None) -> None:
        items = self.get_selected_items()
        if item is not None and item not in items:
            items = [item]
        if not items:
            self.toast("Select an item first")
            return
        if len(items) == 1:
            self.show_properties(items[0])
            return
        total_bytes = sum(item.size for item in items if not item.is_dir)
        folders = sum(1 for item in items if item.is_dir)
        files = len(items) - folders
        local_paths = [item.path for item in items if item.path]
        details = [
            "SELECTION PROPERTIES",
            f"ITEMS       {len(items)}",
            f"FILES       {files}",
            f"FOLDERS     {folders}",
            f"SIZE        {self.size_detail(total_bytes)}",
            f"LOCATION    {self.display_location(self.current_location or self.home)}",
            "",
            "ITEMS",
        ]
        details.extend(f"{index:02d}  {entry.display_name}  ·  {entry.uri}" for index, entry in enumerate(items, start=1))
        if len(local_paths) != len(items):
            details.append("\nSome entries are remote or virtual and do not expose local metadata.")
        self.show_properties_dialog("Selection properties", details)

    def show_properties(self, item: FileItem) -> None:
        if item.is_dir and self.settings.get("show_folder_sizes") and item.folder_size is None:
            self.calculate_folder_size(item)
        size_text = (
            self.size_detail(item.folder_size)
            if item.is_dir and item.folder_size is not None
            else ("Calculating…" if item.is_dir and self.settings.get("show_folder_sizes") else "Folder")
            if item.is_dir
            else self.size_detail(item.size)
        )
        details = [
            f"NAME        {item.display_name}",
            f"TYPE        {'Folder' if item.is_dir else (item.content_type or 'File')}",
            f"SIZE        {size_text}",
            f"MODIFIED    {format_date(item.modified)}",
            f"PERMISSIONS {self.permissions_for_item(item)}",
            f"LOCATION    {self.display_location(self.current_location or self.home)}",
            f"PATH        {item.path or 'remote / virtual location'}",
            f"URI         {item.uri}",
            f"HIDDEN      {'yes' if item.hidden else 'no'}",
            f"SYMLINK     {'yes' if item.is_symlink else 'no'}",
        ]
        if item.path:
            try:
                metadata = metadata_for_path(item.path, item.uri)
                details.extend(
                    [
                        f"MIME        {metadata.mime}",
                        f"CREATED     {format_date(metadata.created)}",
                        f"ACCESSED    {format_date(metadata.accessed)}",
                        f"OWNER       {metadata.owner}",
                        f"GROUP       {metadata.group}",
                        f"EXECUTABLE  {'yes' if metadata.executable else 'no'}",
                        f"SYMLINK TO  {metadata.symlink_target or '—'}",
                        f"FILESYSTEM  {metadata.filesystem}",
                        f"MOUNT       {metadata.mount}",
                        f"FREE SPACE  {format_size(metadata.free_space) if metadata.free_space is not None else '—'}",
                    ]
                )
            except OSError:
                details.append("METADATA    unavailable")
        dialog = Gtk.Window(title=f"Properties · {item.display_name}", transient_for=self.window, modal=True)
        dialog.set_default_size(560, 360)
        dialog.set_resizable(True)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.add_css_class("dialog-shell")
        title = Gtk.Label(label="ITEM PROPERTIES")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("dialog-title")
        shell.append(title)
        text = Gtk.Label(label="\n".join(details))
        text.set_halign(Gtk.Align.START)
        text.set_valign(Gtk.Align.START)
        text.set_selectable(True)
        text.set_wrap(True)
        text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.add_css_class("path-meta")
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(text)
        shell.append(scroll)
        close = Gtk.Button(label="CLOSE")
        close.set_halign(Gtk.Align.END)
        close.add_css_class("action-button")
        close.connect("clicked", lambda *_args: dialog.close())
        shell.append(close)
        dialog.set_child(shell)
        dialog.present()

    def show_properties_dialog(self, title: str, details: list[str]) -> None:
        dialog = Gtk.Window(title=f"Properties · {title}", transient_for=self.window, modal=True)
        dialog.set_default_size(620, 420)
        dialog.set_resizable(True)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.add_css_class("dialog-shell")
        title_label = Gtk.Label(label=title.upper())
        title_label.set_halign(Gtk.Align.START)
        title_label.add_css_class("dialog-title")
        shell.append(title_label)
        text = Gtk.Label(label="\n".join(details))
        text.set_halign(Gtk.Align.START)
        text.set_valign(Gtk.Align.START)
        text.set_selectable(True)
        text.set_wrap(True)
        text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.add_css_class("path-meta")
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(text)
        shell.append(scroll)
        close = Gtk.Button(label="CLOSE")
        close.set_halign(Gtk.Align.END)
        close.add_css_class("action-button")
        close.connect("clicked", lambda *_args: dialog.close())
        shell.append(close)
        dialog.set_child(shell)
        dialog.present()

    def show_quick_preview(self, item: FileItem | None) -> None:
        if item is None:
            self.toast("Select an item first")
            return
        if not self.settings.get("quick_look_enabled", True):
            self.toast("Quick Look is disabled in settings")
            return
        if self.preview_window is not None:
            self.preview_window.close()
            self.preview_window = None

        self.preview_items = list(self.all_items)
        self.preview_item_index = self.preview_items.index(item) if item in self.preview_items else 0

        dialog = Gtk.Window(title=f"Preview · {item.display_name}", transient_for=self.window, modal=False)
        dialog.set_default_size(960, 680)
        dialog.set_resizable(True)
        self.preview_window = dialog
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        shell.add_css_class("quick-preview")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("preview-header")
        icon = self.make_item_icon(item, 28)
        header.append(icon)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        name = Gtk.Label(label=item.display_name)
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.add_css_class("preview-title")
        titles.append(name)
        metadata = Gtk.Label(label=f"{item.content_type or ('Folder' if item.is_dir else 'File')} · {self.size_detail(item.size) if not item.is_dir else 'Folder'}")
        metadata.set_halign(Gtk.Align.START)
        metadata.add_css_class("path-meta")
        titles.append(metadata)
        header.append(titles)
        close = self.icon_button("window-close-symbolic", "Close preview (Space / Esc)", lambda *_: dialog.close())
        close.add_css_class("icon-button")
        header.append(close)
        shell.append(header)

        stack = Gtk.Stack()
        stack.set_hexpand(True)
        stack.set_vexpand(True)
        stack.add_css_class("preview-content")
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.start()
        loading.append(spinner)
        loading_label = Gtk.Label(label="Preparing preview…")
        loading_label.add_css_class("path-meta")
        loading.append(loading_label)
        stack.add_named(loading, "loading")
        stack.set_visible_child(loading)
        shell.append(stack)

        footer = Gtk.Label(label="SPACE / ESC close · ← / → change file · ENTER open")
        footer.set_halign(Gtk.Align.START)
        footer.add_css_class("preview-footer")
        shell.append(footer)
        dialog.connect("close-request", lambda *_args: self.clear_preview_window(dialog))
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", lambda _controller, keyval, _keycode, _state: self.preview_key(dialog, keyval))
        dialog.add_controller(key_controller)
        dialog.set_child(shell)
        dialog.present()
        if item.path:
            self.preview_manager.max_bytes = int(self.settings.get("preview_max_bytes", 262144))
            self.preview_manager.request(
                item.path,
                lambda result, active_stack=stack, active_item=item, active_dialog=dialog: self.apply_preview_result(
                    active_stack,
                    active_item,
                    active_dialog,
                    result,
                ),
                scheduler=lambda callback, *args: GLib.idle_add(lambda: bool(callback(*args) or False)),
            )
        else:
            self.apply_preview_result(
                stack,
                item,
                dialog,
                PreviewResult(
                    item.uri,
                    "folder" if item.is_dir else "fallback",
                    None,
                    item.content_type or "application/octet-stream",
                    int(item.size),
                    int(item.modified),
                    "Remote or virtual locations do not provide an inline preview",
                ),
            )

    def apply_preview_result(self, stack: Gtk.Stack, item: FileItem, dialog: Gtk.Window, result: Any) -> None:
        if self.preview_window is not dialog:
            return
        clear_box(stack)
        if result.kind == "image" and result.content is not None and self.settings.get("image_preview", True):
            image_view = Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(result.content))
            image_view.set_halign(Gtk.Align.CENTER)
            image_view.set_valign(Gtk.Align.CENTER)
            image_view.set_hexpand(True)
            image_view.set_vexpand(True)
            image_view.add_css_class("preview-image")
            stack.add_named(image_view, "image")
        elif result.kind == "text" and isinstance(result.content, str) and self.settings.get("text_preview", True):
            text_view = Gtk.TextView()
            text_view.set_editable(False)
            text_view.set_cursor_visible(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.NONE)
            text_view.add_css_class("preview-text")
            text_view.get_buffer().set_text(result.content)
            text_scroll = Gtk.ScrolledWindow()
            text_scroll.set_child(text_view)
            stack.add_named(text_scroll, "text")
        else:
            fallback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            fallback.set_halign(Gtk.Align.CENTER)
            fallback.set_valign(Gtk.Align.CENTER)
            fallback.add_css_class("preview-fallback")
            fallback_icon = self.make_item_icon(item, 72)
            fallback_icon.set_opacity(0.85)
            fallback.append(fallback_icon)
            message = Gtk.Label(label=result.error or "No inline preview available")
            message.add_css_class("path-meta")
            message.set_wrap(True)
            message.set_max_width_chars(52)
            fallback.append(message)
            details = Gtk.Label(label=f"{result.mime} · {self.size_detail(result.size)}")
            details.add_css_class("path-meta")
            fallback.append(details)
            stack.add_named(fallback, "fallback")
        child = stack.get_first_child()
        if child is not None:
            stack.set_visible_child(child)

    def clear_preview_window(self, dialog: Gtk.Window) -> bool:
        if self.preview_window is dialog:
            self.preview_window = None
            self.preview_manager.cancel()
        return False

    def preview_key(self, dialog: Gtk.Window, keyval: int) -> bool:
        if keyval in {Gdk.KEY_Escape, Gdk.KEY_space}:
            dialog.close()
            return True
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            if self.preview_items:
                self.activate_item(self.preview_items[self.preview_item_index])
            return True
        if keyval in {Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_Right, Gdk.KEY_Down}:
            if not self.preview_items:
                return True
            delta = -1 if keyval in {Gdk.KEY_Left, Gdk.KEY_Up} else 1
            self.preview_item_index = (self.preview_item_index + delta) % len(self.preview_items)
            self.show_quick_preview(self.preview_items[self.preview_item_index])
            return True
        return False

    def open_uri(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            self.record_recent_location(uri)
            self.toast("Opened in default application")
        except GLib.Error as error:
            self.toast(f"Could not open item: {error.message}")

    def refresh(self) -> None:
        if self.current_location:
            self.preview_manager.clear()
            self.thumbnail_manager.clear()
            self.load_items()
            self.update_nav_buttons()

    def go_back(self, *_args) -> None:
        if not self.back_history:
            return
        if self.current_location:
            self.forward_history.append(self.current_location)
        self.navigate(self.back_history.pop(), record_history=False)

    def go_forward(self, *_args) -> None:
        if not self.forward_history:
            return
        if self.current_location:
            self.back_history.append(self.current_location)
        self.navigate(self.forward_history.pop(), record_history=False)

    def go_up(self, *_args) -> None:
        if not self.current_location or self.current_location == TRASH_URI:
            return
        if "://" in self.current_location:
            parent = Gio.File.new_for_uri(self.current_location).get_parent()
            if parent is not None:
                self.navigate(parent.get_uri())
            return
        parent = os.path.dirname(os.path.abspath(self.current_location))
        self.navigate(parent)

    def set_view(self, mode: str) -> None:
        mode = self.normalize_view_mode(mode)
        if mode not in {"grid", "compact", "details"}:
            return
        self.view_mode = mode
        self.ensure_active_file_page("set-view")
        self.update_view_buttons()
        self.render_items()
        self.update_inspector(self.get_selected_item())
        self.persist_folder_view_settings()
        self.save_active_tab()
        self.update_tabs()

    def set_zoom(self, level: int) -> None:
        self.zoom_level = max(60, min(180, int(level)))
        self.settings["zoom_level"] = self.zoom_level
        if self.current_location:
            self.persist_folder_view_settings()
        if hasattr(self, "grid"):
            self.render_items()
        self.save_active_tab()
        self.toast(f"Zoom {self.zoom_level}%")

    def change_zoom(self, delta: int) -> None:
        self.set_zoom(self.zoom_level + int(delta))

    def on_search_changed(self, *_args) -> None:
        if self.suppress_search_change:
            return
        if self.search_debounce_source:
            GLib.source_remove(self.search_debounce_source)
            self.search_debounce_source = 0
        query = self.search_entry.get_text().strip()
        self.search_generation += 1
        self.search_service.cancel()
        if not query:
            if self.current_location:
                self.load_items()
            return
        if self.current_location:
            self.search_debounce_source = GLib.timeout_add(160, self.start_search)

    def start_search(self) -> bool:
        self.search_debounce_source = 0
        self.search_generation += 1
        generation = self.search_generation
        query = self.search_entry.get_text().strip()
        if not query:
            self.search_service.cancel()
            self.load_items()
            return False
        if (not self.settings.get("search_recursive", False) and not self.settings.get("search_contents", False)) or self.current_location == TRASH_URI:
            self.search_service.cancel()
            self.render_items()
            return False
        self.search_service.search(
            SearchQuery(
                root=self.current_location or self.home,
                text=query,
                recursive=bool(self.settings.get("search_recursive", False)),
                case_sensitive=bool(self.settings.get("search_case_sensitive", False)),
                include_hidden=bool(self.settings.get("search_show_hidden", False)),
                search_contents=bool(self.settings.get("search_contents", False)),
            ),
            lambda paths: self.apply_recursive_search(paths, generation),
            lambda error: self.toast(f"Search failed: {error}"),
            scheduler=lambda callback, *args: GLib.idle_add(lambda: bool(callback(*args) or False)),
        )
        self.status_label.set_text("Searching…")
        return False

    def apply_recursive_search(self, paths: list[str], generation: int) -> None:
        if self.shutting_down or generation != self.search_generation:
            return
        self.search_result_paths = paths[:5000]
        self.search_result_items = []
        self.search_result_index = 0
        self.all_items = []
        self.render_items()
        self.load_recursive_search_batch(generation)

    def load_recursive_search_batch(self, generation: int) -> bool:
        if self.shutting_down or generation != self.search_generation:
            return False
        start = self.search_result_index
        end = min(start + 64, len(self.search_result_paths))
        for path in self.search_result_paths[start:end]:
            try:
                info = Gio.File.new_for_path(path).query_info(FILE_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
            except GLib.Error:
                continue
            self.search_result_items.append(
                FileItem(
                    file=Gio.File.new_for_path(path),
                    name=info.get_name(),
                    display_name=info.get_display_name() or info.get_name(),
                    is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
                    size=int(info.get_size()),
                    modified=int(info.get_attribute_uint64("time::modified")),
                    content_type=info.get_content_type() or "",
                    hidden=info.get_attribute_boolean("standard::is-hidden"),
                    is_symlink=info.get_is_symlink(),
                    created=int(info.get_attribute_uint64("time::created")),
                    accessed=int(info.get_attribute_uint64("time::access")),
                )
            )
        self.search_result_index = end
        if end < len(self.search_result_paths):
            GLib.idle_add(self.load_recursive_search_batch, generation)
            return False
        self.all_items = self.sort_items(self.search_result_items)
        self.render_items()
        self.status_label.set_text(f"{len(self.all_items)} search results")
        return False

    def on_location_activate(self, entry: Gtk.Entry) -> None:
        target = entry.get_text().strip()
        if target:
            self.navigate(target)
        else:
            entry.set_text(self.display_location(self.current_location or self.home))
        if hasattr(self, "location_stack"):
            self.location_stack.set_visible_child_name("breadcrumbs")

    def toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.settings["show_hidden"] = self.show_hidden
        self.persist_folder_view_settings()
        save_settings(self.settings)
        self.load_items()
        self.toast("Hidden files shown" if self.show_hidden else "Hidden files hidden")

    def cycle_sort(self) -> None:
        modes = ("name", "modified", "size", "type")
        current_index = modes.index(self.sort_mode)
        next_index = current_index + 1
        if next_index >= len(modes):
            next_index = 0
            self.sort_reverse = not self.sort_reverse
        self.sort_mode = modes[next_index]
        self.settings["sort_mode"] = self.sort_mode
        self.settings["sort_reverse"] = self.sort_reverse
        self.persist_folder_view_settings()
        save_settings(self.settings)
        self.load_items()
        self.toast(f"Sorted by {self.sort_mode}{' descending' if self.sort_reverse else ''}")

    def set_sort_order(self, mode: str, reverse: bool | None = None) -> None:
        if mode not in {"name", "modified", "size", "type"}:
            return
        self.sort_mode = mode
        if reverse is not None:
            self.sort_reverse = reverse
        self.settings["sort_mode"] = self.sort_mode
        self.settings["sort_reverse"] = self.sort_reverse
        self.persist_folder_view_settings()
        self.load_items()
        self.toast(f"Sorted by {mode}{' descending' if self.sort_reverse else ''}")

    def toggle_folders_first(self, *_args) -> None:
        self.settings["folders_first"] = not bool(self.settings.get("folders_first", True))
        save_settings(self.settings)
        self.load_items()
        self.toast("Folders first enabled" if self.settings["folders_first"] else "Folders first disabled")

    def toggle_natural_sort(self, *_args) -> None:
        self.settings["natural_sort"] = not bool(self.settings.get("natural_sort", True))
        save_settings(self.settings)
        self.load_items()
        self.toast("Natural sorting enabled" if self.settings["natural_sort"] else "Natural sorting disabled")

    def toggle_groups(self, *_args) -> None:
        self.show_groups = not self.show_groups
        self.settings["show_groups"] = self.show_groups
        save_settings(self.settings)
        self.render_items()
        self.toast("Groups shown" if self.show_groups else "Groups hidden")

    def on_key_pressed(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        keyval = Gdk.keyval_to_lower(keyval)
        focus = self.window.get_focus() if self.window is not None else None
        editing = isinstance(focus, Gtk.Editable)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)

        if editing and keyval == Gdk.KEY_Escape and focus is self.location_entry:
            self.location_entry.set_text(self.display_location(self.current_location or self.home))
            self.location_stack.set_visible_child_name("breadcrumbs")
            return True
        if editing and keyval == Gdk.KEY_Escape and focus is self.search_entry:
            self.hide_search_entry()
            return True
        if editing and not (ctrl and keyval in {Gdk.KEY_f, Gdk.KEY_l, Gdk.KEY_h}):
            return False
        if ctrl and keyval == Gdk.KEY_f:
            self.show_search_entry()
            return True
        if ctrl and keyval == Gdk.KEY_k:
            self.show_command_palette()
            return True
        if ctrl and keyval == Gdk.KEY_l:
            self.location_stack.set_visible_child_name("location")
            self.location_entry.set_text(self.display_location(self.current_location or self.home))
            self.location_entry.grab_focus()
            self.location_entry.select_region(0, -1)
            return True
        if ctrl and keyval == Gdk.KEY_h:
            self.toggle_hidden()
            return True
        if ctrl and keyval == Gdk.KEY_n and not shift:
            self.new_window()
            return True
        if ctrl and keyval == Gdk.KEY_q:
            self.quit()
            return True
        if ctrl and shift and keyval == Gdk.KEY_t:
            self.reopen_closed_tab()
            return True
        if ctrl and keyval == Gdk.KEY_t:
            self.new_tab()
            return True
        if ctrl and keyval == Gdk.KEY_w:
            self.close_tab(self.active_tab_index)
            return True
        if ctrl and keyval in {Gdk.KEY_Page_Down, Gdk.KEY_Tab} and not shift:
            self.switch_tab((self.active_tab_index + 1) % len(self.tabs))
            return True
        if (ctrl and keyval == Gdk.KEY_Page_Up) or (ctrl and shift and keyval == Gdk.KEY_Tab):
            self.switch_tab((self.active_tab_index - 1) % len(self.tabs))
            return True
        if ctrl and shift and keyval == Gdk.KEY_a:
            self.invert_selection_current()
            return True
        if ctrl and keyval == Gdk.KEY_a:
            self.select_all_current()
            return True
        if ctrl and shift and keyval == Gdk.KEY_n:
            self.new_folder()
            return True
        if ctrl and keyval == Gdk.KEY_c:
            self.copy_selected()
            return True
        if ctrl and keyval == Gdk.KEY_x:
            self.cut_selected()
            return True
        if ctrl and keyval == Gdk.KEY_v:
            self.paste_items()
            return True
        if ctrl and keyval == Gdk.KEY_z:
            self.undo_last()
            return True
        if ctrl and keyval == Gdk.KEY_d:
            self.duplicate_selected()
            return True
        if ctrl and keyval == Gdk.KEY_1:
            self.set_view("grid")
            return True
        if ctrl and keyval == Gdk.KEY_2:
            self.set_view("compact")
            return True
        if ctrl and keyval == Gdk.KEY_3:
            self.set_view("details")
            return True
        if ctrl and keyval in {Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add}:
            self.change_zoom(10)
            return True
        if ctrl and keyval in {Gdk.KEY_minus, Gdk.KEY_KP_Subtract}:
            self.change_zoom(-10)
            return True
        if ctrl and keyval in {Gdk.KEY_0, Gdk.KEY_KP_0}:
            self.set_zoom(100)
            return True
        if keyval == Gdk.KEY_F3:
            self.toggle_split_view()
            return True
        if keyval == Gdk.KEY_F4:
            self.toggle_terminal_panel()
            return True
        if keyval == Gdk.KEY_F7:
            self.toggle_folders_panel()
            return True
        if keyval == Gdk.KEY_F5:
            self.refresh()
            return True
        if alt and keyval == Gdk.KEY_Left:
            self.go_back()
            return True
        if alt and keyval == Gdk.KEY_Right:
            self.go_forward()
            return True
        if alt and keyval == Gdk.KEY_Up:
            self.go_up()
            return True
        if keyval == Gdk.KEY_space:
            self.show_quick_preview(self.selected_item or self.get_selected_item())
            return True
        if keyval == Gdk.KEY_F2:
            self.rename_selected()
            return True
        if alt and keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            self.show_properties_for_selection()
            return True
        if keyval in {Gdk.KEY_Delete, Gdk.KEY_KP_Delete}:
            self.permanent_delete_selected() if shift else self.delete_selected()
            return True
        if keyval == Gdk.KEY_BackSpace:
            self.go_up()
            return True
        if keyval == Gdk.KEY_Escape:
            self.grid.unselect_all()
            self.compact_view.unselect_all()
            self.listbox.unselect_all()
            self.search_entry.set_text("")
            return True
        return False

    def new_folder(self, *_args) -> None:
        if self.current_location == TRASH_URI:
            self.toast("You cannot create a folder in Trash")
            return
        if not self.can_write_location():
            self.toast("You don't have permission to write to this folder")
            return
        self.prompt("New folder", "Create a folder in the current location", "Untitled folder", "CREATE", self.finish_new_folder)

    def finish_new_folder(self, name: str) -> None:
        clean_name = name.strip()
        if not clean_name or clean_name in {".", ".."} or os.path.sep in clean_name:
            self.toast("Choose a valid folder name")
            return
        target = os.path.join(self.current_location or self.home, clean_name)
        self.start_operation(OperationKind.CREATE_FOLDER, [target])

    def new_file(self, *_args) -> None:
        if self.current_location == TRASH_URI:
            self.toast("You cannot create a file in Trash")
            return
        if not self.can_write_location():
            self.toast("You don't have permission to write to this folder")
            return
        self.prompt("New file", "Create an empty file in the current location", "New file.txt", "CREATE", self.finish_new_file)

    def finish_new_file(self, name: str) -> None:
        clean_name = name.strip()
        if not clean_name or clean_name in {".", ".."} or os.path.sep in clean_name:
            self.toast("Choose a valid file name")
            return
        target = os.path.join(self.current_location or self.home, clean_name)
        self.start_operation(OperationKind.CREATE_FILE, [target])

    def show_template_picker(self, *_args) -> None:
        if not self.can_write_location():
            self.toast("Templates are available for writable local folders only")
            return
        templates = discover_templates()
        if not templates:
            self.toast("No templates found in ~/Templates")
            return
        dialog = Gtk.Window(title="Omapath · New from Template", transient_for=self.window, modal=True)
        dialog.set_default_size(460, min(620, 180 + len(templates) * 42))
        dialog.set_resizable(True)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        shell.set_margin_top(16)
        shell.set_margin_bottom(16)
        shell.set_margin_start(16)
        shell.set_margin_end(16)
        shell.add_css_class("dialog-shell")
        title = Gtk.Label(label="NEW FROM TEMPLATE")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("dialog-title")
        shell.append(title)
        subtitle = Gtk.Label(label="Choose a file from ~/Templates. A copy is created in the current folder.")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.set_wrap(True)
        subtitle.add_css_class("path-meta")
        shell.append(subtitle)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("template-list")
        for template in templates:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            button = Gtk.Button(label=template.name)
            button.set_halign(Gtk.Align.FILL)
            button.add_css_class("context-menu-item")
            button.connect(
                "clicked",
                lambda _button, target=template, active_dialog=dialog: self.copy_template(target, active_dialog),
            )
            row.set_child(button)
            listbox.append(row)
        scroll.set_child(listbox)
        shell.append(scroll)
        cancel = Gtk.Button(label="CANCEL")
        cancel.set_halign(Gtk.Align.END)
        cancel.add_css_class("action-button")
        cancel.connect("clicked", lambda *_args: dialog.close())
        shell.append(cancel)
        dialog.set_child(shell)
        dialog.present()

    def copy_template(self, template: Path, dialog: Gtk.Window | None = None) -> None:
        if dialog is not None:
            dialog.close()
        if not self.can_write_location() or not template.is_file():
            self.toast("Template or destination is no longer available")
            return
        destination = self.current_location or self.home
        self.start_operation(
            OperationKind.COPY,
            [str(template)],
            destination,
            policy_override=ConflictPolicy.RENAME,
        )

    def show_script_picker(self, items: list[FileItem] | None = None, *_args) -> None:
        if not self.current_location or self.current_location == TRASH_URI or "://" in self.current_location or not os.path.isdir(self.current_location):
            self.toast("Scripts require a local folder")
            return
        scripts = discover_scripts()
        if not scripts:
            self.toast("No executable scripts found in ~/.local/share/nautilus/scripts")
            return
        selected = list(items or self.get_selected_items())
        if any(not item.path for item in selected):
            self.toast("Scripts can receive local files only")
            return
        dialog = Gtk.Window(title="Omapath · Scripts", transient_for=self.window, modal=True)
        dialog.set_default_size(460, min(620, 180 + len(scripts) * 42))
        dialog.set_resizable(True)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        shell.set_margin_top(16)
        shell.set_margin_bottom(16)
        shell.set_margin_start(16)
        shell.set_margin_end(16)
        shell.add_css_class("dialog-shell")
        title = Gtk.Label(label="SCRIPTS")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("dialog-title")
        shell.append(title)
        subtitle = Gtk.Label(label="Scripts receive selected local paths as argv and run in the current folder.")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.set_wrap(True)
        subtitle.add_css_class("path-meta")
        shell.append(subtitle)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for script in scripts:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            button = Gtk.Button(label=script.name)
            button.set_halign(Gtk.Align.FILL)
            button.add_css_class("context-menu-item")
            button.connect(
                "clicked",
                lambda _button, target=script, active_dialog=dialog, active_items=selected: self.run_user_script(target, active_items, active_dialog),
            )
            row.set_child(button)
            listbox.append(row)
        scroll.set_child(listbox)
        shell.append(scroll)
        cancel = Gtk.Button(label="CANCEL")
        cancel.set_halign(Gtk.Align.END)
        cancel.add_css_class("action-button")
        cancel.connect("clicked", lambda *_args: dialog.close())
        shell.append(cancel)
        dialog.set_child(shell)
        dialog.present()

    def run_user_script(self, script: Path, items: list[FileItem], dialog: Gtk.Window | None = None) -> None:
        if dialog is not None:
            dialog.close()
        location = self.current_location
        if not location or location == TRASH_URI or "://" in location or not os.path.isdir(location):
            self.toast("Scripts require a local folder")
            return
        if not script.is_file() or not os.access(script, os.X_OK):
            self.toast("Script is no longer executable")
            return
        argv = [str(script), *(item.path or "" for item in items if item.path)]
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
            launcher.set_cwd(location)
            launcher.spawnv(argv)
            self.toast(f"Started {script.name}")
        except (GLib.Error, OSError) as error:
            self.toast(f"Could not run script: {error}")

    def connect_to_server(self, *_args) -> None:
        self.prompt(
            "Connect to Server",
            "Enter a GIO URI such as sftp://host/path, smb://server/share or davs://host/path.",
            "sftp://",
            "CONNECT",
            self.finish_connect_to_server,
        )

    def finish_connect_to_server(self, address: str) -> None:
        target = address.strip()
        if not target:
            self.toast("Enter a server address")
            return
        if "://" not in target:
            target = "sftp://" + target
        scheme = target.split("://", 1)[0].casefold()
        if scheme not in {"sftp", "ssh", "smb", "ftp", "dav", "davs", "afp", "nfs"}:
            self.toast("Unsupported server URI scheme")
            return
        self.navigate(target)

    def rename_selected(self, *_args) -> None:
        item = self.selected_item or self.get_selected_item()
        if item is None:
            self.toast("Select an item first")
            return
        self.rename_item(item)

    def rename_item(self, item: FileItem) -> None:
        if not item.path:
            self.toast("Rename is available for local items only")
            return
        self.prompt("Rename item", "Give this item a new name", item.display_name, "RENAME", lambda name: self.finish_rename(item, name))

    def finish_rename(self, item: FileItem, name: str) -> None:
        clean_name = name.strip()
        if not clean_name or clean_name in {".", ".."} or os.path.sep in clean_name:
            self.toast("Choose a valid name")
            return
        if not item.path:
            self.toast("This item cannot be renamed here")
            return
        self.start_operation(OperationKind.RENAME, [item.path], clean_name)

    def restore_item(self, item: FileItem) -> None:
        if self.current_location != TRASH_URI or not item.trash_orig_path:
            self.toast("Original location is unavailable")
            return
        if not item.uri.startswith("trash://") and not item.path:
            self.toast("The trashed item is unavailable")
            return
        destination = item.trash_orig_path
        if os.path.lexists(destination):
            self.toast("The original location already exists")
            return
        self.start_operation(OperationKind.RESTORE, [item.uri], destination)

    def empty_trash(self, *_args) -> None:
        if self.current_location != TRASH_URI:
            return
        if not self.all_items:
            self.toast("Trash is already empty")
            return
        if self.settings.get("confirm_delete", True):
            self.confirm(
                "Empty Trash",
                f"Delete {len(self.all_items)} item{'s' if len(self.all_items) != 1 else ''} permanently?",
                "EMPTY TRASH",
                self.finish_empty_trash,
            )
        else:
            self.finish_empty_trash()

    def finish_empty_trash(self) -> None:
        paths = [item.uri if self.current_location == TRASH_URI else item.path for item in self.all_items]
        paths = [path for path in paths if path]
        self.start_operation(OperationKind.DELETE, paths)

    def delete_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            if item is not None:
                items = [item]
        if not items:
            self.toast("Select an item first")
            return
        self.delete_items(items)

    def delete_items(self, items: list[FileItem]) -> None:
        if not items:
            return
        paths = self.operation_paths(items)
        if not paths:
            self.toast("File operations are available for local items only")
            return
        permanent = self.current_location == TRASH_URI
        if len(items) == 1:
            message = (
                f"Delete “{items[0].display_name}” permanently?"
                if permanent
                else f"Move “{items[0].display_name}” to the Trash?"
            )
        else:
            message = (
                f"Delete {len(items)} selected items permanently?"
                if permanent
                else f"Move {len(items)} selected items to the Trash?"
            )
        action = "DELETE PERMANENTLY" if permanent else "MOVE TO TRASH"
        if self.settings.get("confirm_delete", True):
            self.confirm(
                "Delete items" if permanent else "Move to Trash",
                message,
                action,
                lambda: self.finish_delete(items),
            )
        else:
            self.finish_delete(items)

    def finish_delete(self, items: list[FileItem]) -> None:
        paths = [item.uri if self.current_location == TRASH_URI else item.path for item in items]
        paths = [path for path in paths if path]
        kind = OperationKind.DELETE if self.current_location == TRASH_URI else OperationKind.TRASH
        self.start_operation(kind, paths)

    def copy_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            if item is not None:
                items = [item]
        if not items:
            self.toast("Select an item first")
            return
        self.copy_items(items)

    def copy_items(self, items: list[FileItem]) -> None:
        local_items = [item for item in items if item.path]
        if not local_items:
            self.toast("Copy is available for local items only")
            return
        self.clipboard_items = local_items
        self.clipboard_mode = "copy"
        self.set_external_clipboard(local_items, cut=False)
        self.toast(f"Copied {len(local_items)} item{'s' if len(local_items) != 1 else ''}")

    def cut_selected(self, *_args) -> None:
        items = self.get_selected_items()
        if not items:
            item = self.selected_item or self.get_selected_item()
            if item is not None:
                items = [item]
        if not items:
            self.toast("Select an item first")
            return
        self.cut_items(items)

    def cut_items(self, items: list[FileItem]) -> None:
        local_items = [item for item in items if item.path]
        if not local_items:
            self.toast("Cut is available for local items only")
            return
        self.clipboard_items = local_items
        self.clipboard_mode = "cut"
        self.set_external_clipboard(local_items, cut=True)
        self.toast(f"Cut {len(local_items)} item{'s' if len(local_items) != 1 else ''}")

    def paste_items(self, *_args) -> None:
        if not self.clipboard_items:
            self.read_external_clipboard()
            return
        if self.current_location == TRASH_URI:
            self.toast("You cannot paste into Trash")
            return
        destination_dir = self.current_location or self.home
        if not self.can_write_location(destination_dir):
            self.toast("Paste is available for writable local folders only")
            return
        paths = [item.path for item in self.clipboard_items if item.path]
        if not paths:
            self.toast("Clipboard contains no local items")
            return
        self.start_operation(
            OperationKind.MOVE if self.clipboard_mode == "cut" else OperationKind.COPY,
            paths,
            destination_dir,
        )

    def prompt(
        self,
        title: str,
        subtitle: str,
        initial: str,
        confirm_label: str,
        callback: Callable[[str], None],
    ) -> None:
        dialog = Gtk.Window(title=title, transient_for=self.window, modal=True)
        dialog.set_default_size(430, 190)
        dialog.set_resizable(False)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.add_css_class("dialog-shell")
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.add_css_class("dialog-title")
        shell.append(title_label)
        subtitle_label = Gtk.Label(label=subtitle)
        subtitle_label.set_halign(Gtk.Align.START)
        subtitle_label.add_css_class("path-meta")
        shell.append(subtitle_label)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.select_region(0, -1)
        entry.add_css_class("dialog-entry")
        shell.append(entry)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="CANCEL")
        cancel.add_css_class("action-button")
        cancel.connect("clicked", lambda *_: dialog.close())
        buttons.append(cancel)
        accept = Gtk.Button(label=confirm_label)
        accept.add_css_class("action-button")
        accept.add_css_class("solid")

        def accept_dialog(*_args) -> None:
            value = entry.get_text()
            dialog.close()
            callback(value)

        accept.connect("clicked", accept_dialog)
        entry.connect("activate", accept_dialog)
        buttons.append(accept)
        shell.append(buttons)
        dialog.set_child(shell)
        dialog.present()
        entry.grab_focus()

    def confirm(self, title: str, message: str, confirm_label: str, callback: Callable[[], None]) -> None:
        dialog = Gtk.Window(title=title, transient_for=self.window, modal=True)
        dialog.set_default_size(430, 180)
        dialog.set_resizable(False)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.add_css_class("dialog-shell")
        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.START)
        title_label.add_css_class("dialog-title")
        shell.append(title_label)
        message_label = Gtk.Label(label=message)
        message_label.set_halign(Gtk.Align.START)
        message_label.set_wrap(True)
        message_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        message_label.add_css_class("path-meta")
        shell.append(message_label)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="CANCEL")
        cancel.add_css_class("action-button")
        cancel.connect("clicked", lambda *_: dialog.close())
        buttons.append(cancel)
        accept = Gtk.Button(label=confirm_label)
        accept.add_css_class("action-button")
        accept.add_css_class("solid")

        def accept_dialog(*_args) -> None:
            dialog.close()
            callback()

        accept.connect("clicked", accept_dialog)
        buttons.append(accept)
        shell.append(buttons)
        dialog.set_child(shell)
        dialog.present()

    def toast(self, message: str) -> None:
        if hasattr(self, "status_label"):
            self.status_label.set_text(message)


def main() -> int:
    app = OmapathApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

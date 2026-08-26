"""Active GTK4 Omapath runtime and stable SimpleFileView model boundary.

The production entry point stays on this module.  Presentation controllers
are extracted incrementally, while SimpleFileView remains the sole owner of
the Gio.ListStore → Gtk.MultiSelection → virtualized GTK view pipeline.
Legacy GTK adapters are intentionally not imported here.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from ..actions import ActionRegistry, shortcut_from_key_event
from ..archive import archive_kind
from ..capabilities import item_capabilities, location_capabilities
from ..diagnostics import LOGGER, trace_items_enabled
from ..dnd import (
    choose_drop_action,
    locations_from_uri_list,
    locations_from_clipboard_text,
    uri_list,
    validate_drop,
)
from ..devices import (
    DeviceMonitor,
    device_info,
    eject as eject_device,
    eject_drive,
    stop_drive,
    unmount as unmount_device,
)
from ..directory_monitor import DirectoryMonitor
from ..compare import detect_compare_tool, validate_compare_paths
from ..filtering import FilterMode, filter_entries, normalize_filter_mode
from ..folder_settings import FolderViewProperties, FolderViewSettings
from ..filesystem import format_date, format_size, metadata_for_path, valid_entry_name
from ..grouping import GroupCriterion, group_entries, normalize_group_criterion
from ..location import (
    completion_location_parts,
    filter_completion_names,
    local_location_error,
    location_display_text,
    parse_location,
    record_location_history,
)
from ..metadata_store import MetadataStore
from ..navigation import NavigationHistory
from ..operations import ConflictPolicy, OperationKind, OperationManager, OperationResult
from ..places import PlacesStore
from ..plugins import PluginLoadReport, PluginRegistry, load_plugins
from ..preview import PreviewManager, PreviewResult, ThumbnailManager
from ..rename import BatchRenameSpec, RenamePlanError, apply_rename_plan, build_rename_plan
from ..responsive import (
    ResponsiveMode,
    detail_columns_for_mode,
    mode_uses_sidebar_overlay,
    responsive_mode_for_width,
)
from ..panel_layout import PanelId
from ..search import SearchQuery, SearchService, parse_search_query
from ..services import ServiceAction, discover_services, run_service
from ..split import PaneState
from ..vcs import GitAdapter
from ..settings import load_settings, save_settings
from ..sorting import SORT_KEYS, SORT_LABELS, normalize_sort_key, sort_entries
from ..tabs import TabState
from ..terminal import configured_terminal, terminal_argv
from ..trash import attribute_text
from ..view_modes import detail_value, normalize_view_mode, ViewMode
from ..zoom import cell_width, label_width_chars, normalize_zoom, zoom_in, zoom_out
from ..theme import (
    FALLBACK_COLORS,
    ThemeWatcher,
    active_font,
    active_theme,
    read_colors,
    read_theme_snapshot,
    rgba,
)
from ..compat import set_accessible_label
from .palette import CommandPalette, PaletteAction
from .context_menu import ContextMenuController
from .dialogs import DialogController
from .dnd import attach_drop_target, file_content_provider, set_drop_highlight
from .folders import FoldersPanel
from .settings_window import SettingsWindow
from .sidebar import RECENT_URI, SidebarController
from .tab_bar import TabBarController
from .quick_look import QuickLookController
from .operation_popover import OperationController
from .panel_controls import PanelControlsController
from .search_controls import SearchControls
from .topbar import TopbarController
from .terminal_panel import TerminalPanel


APP_ID = "dev.omarchy.Omapath"
TRASH_URI = "trash:///"
FILE_ATTRIBUTES = (
    "standard::name,standard::display-name,standard::type,standard::content-type,"
    "standard::is-hidden,standard::size,time::modified,trash::orig-path"
)


def format_item_count(count: int) -> str:
    """Return the small, neutral status text used by the topbar."""

    return f"{count} item" if count == 1 else f"{count} items"


def _all_breadcrumb_segments(location: str, home: str) -> list[tuple[str, str | None]]:
    """Build the complete breadcrumb path before width-based truncation."""

    if location == TRASH_URI:
        return [("Trash", TRASH_URI)]
    if location == RECENT_URI:
        return [("Recent", RECENT_URI)]

    if "://" in location and not location.startswith("file://"):
        return [("Location", location)]

    if location.startswith("file://"):
        path = Gio.File.new_for_uri(location).get_path() or os.path.abspath(home)
    else:
        path = os.path.abspath(os.path.expanduser(location or home))
    home_path = os.path.abspath(os.path.expanduser(home))

    try:
        inside_home = os.path.commonpath((path, home_path)) == home_path
    except ValueError:
        inside_home = False

    segments: list[tuple[str, str | None]] = [("Home", home_path)]
    if path != home_path and inside_home:
        current = home_path
        relative_parts = Path(path).relative_to(home_path).parts
        for part in relative_parts:
            current = os.path.join(current, part)
            segments.append((part, current))
    elif path != home_path:
        parts = Path(path).parts
        segments = [("Filesystem", os.path.sep)]
        current = os.path.sep
        for part in parts:
            if part == os.path.sep:
                continue
            current = os.path.join(current, part)
            segments.append((part, current))

    return segments


def breadcrumb_segments(
    location: str,
    home: str,
    available_width: int | None = None,
) -> list[tuple[str, str | None]]:
    """Build clickable breadcrumbs, collapsing only when the slot needs it.

    ``available_width=None`` preserves the compact, toolkit-neutral policy
    used by existing callers and tests.  The GTK renderer passes the actual
    breadcrumb slot width, so resizing changes only the visible path, not the
    underlying location or navigation model.
    """

    segments = _all_breadcrumb_segments(location, home)
    if len(segments) <= 4:
        return segments
    if available_width is None or available_width <= 0:
        return [segments[0], ("…", None), *segments[-2:]]

    def estimate(label: str) -> int:
        return max(42, min(180, len(label) * 8 + 24))

    def required(candidate: list[tuple[str, str | None]]) -> int:
        return sum(estimate(label) for label, _target in candidate) + max(0, len(candidate) - 1) * 12

    if required(segments) <= available_width:
        return segments

    for tail_count in range(len(segments) - 1, 0, -1):
        candidate = [segments[0], ("…", None), *segments[-tail_count:]]
        if required(candidate) <= available_width:
            return candidate

    if available_width >= 100:
        # Keep the current location clickable while retaining an explicit,
        # discoverable affordance for the hidden parent path.
        return [("…", None), segments[-1]]
    return [segments[-1]]


class FileRow(GObject.Object):
    """One immutable filesystem entry represented by one GObject instance."""

    def __init__(
        self,
        file: Gio.File | None,
        name: str,
        display_name: str,
        is_dir: bool,
        content_type: str,
        hidden: bool = False,
        size: int = 0,
        modified: int | None = None,
        original_path: str | None = None,
        *,
        group_header: bool = False,
        group_key: str = "",
        group_label: str = "",
        group_count: int = 0,
        group_collapsed: bool = False,
        tags: tuple[str, ...] = (),
        rating: int = 0,
        git_status: str = "",
    ) -> None:
        super().__init__()
        self.file = file
        self.name = name
        self.display_name = display_name
        self.is_dir = is_dir
        self.content_type = content_type
        self.hidden = hidden
        self.size = size
        self.modified = modified
        self.original_path = original_path
        self.group_header = bool(group_header)
        self.group_key = group_key
        self.group_label = group_label
        self.group_count = int(group_count)
        self.group_collapsed = bool(group_collapsed)
        self.tags = tuple(tags)
        self.rating = max(0, min(5, int(rating)))
        self.git_status = str(git_status or "")
        self.location = file.get_uri() if file is not None else f"omapatht-group://{group_key}"
        if self.group_header:
            self.gicon = Gio.ThemedIcon.new("pan-down-symbolic")
        elif is_dir:
            self.gicon = Gio.ThemedIcon.new("folder-symbolic")
        else:
            try:
                self.gicon = Gio.content_type_get_symbolic_icon(content_type)
            except (GLib.Error, TypeError, ValueError):
                self.gicon = Gio.ThemedIcon.new("text-x-generic-symbolic")

    @classmethod
    def make_group_header(
        cls,
        key: str,
        label: str,
        count: int,
        *,
        collapsed: bool = False,
    ) -> FileRow:
        return cls(
            None,
            label,
            label,
            False,
            "application/x-omapath-group",
            group_header=True,
            group_key=key,
            group_label=label,
            group_count=count,
            group_collapsed=collapsed,
        )


@dataclass(slots=True)
class DirectoryEntry:
    """Toolkit-neutral enumeration result kept until the single model commit."""

    file: Gio.File
    name: str
    display_name: str
    is_dir: bool
    content_type: str
    hidden: bool = False
    size: int = 0
    modified: int | None = None
    original_path: str | None = None
    tags: tuple[str, ...] = ()
    rating: int = 0
    git_status: str = ""


@dataclass(slots=True)
class _PendingConflictRequest:
    kind: OperationKind
    sources: list[str]
    destination: str | None
    default_policy: ConflictPolicy
    after: Callable[[OperationResult], None] | None
    conflicts: list[tuple[str, str]]
    decisions: dict[str, ConflictPolicy]


@dataclass(slots=True)
class _BoundItem:
    """Transient widgets associated with one realized list item."""

    list_item: object
    root: Gtk.Widget
    name_label: Gtk.Label
    marker: Gtk.Button | None = None
    marker_icon: Gtk.Image | None = None
    rename_entry: Gtk.Entry | None = None
    row_location: str | None = None
    hovered: bool = False


def visible_directory_entries(entries: list[DirectoryEntry], show_hidden: bool) -> list[DirectoryEntry]:
    """Filter hidden rows without re-enumerating the active directory."""

    if show_hidden:
        return list(entries)
    return [entry for entry in entries if not entry.hidden]


class SimpleFileView(Gtk.ScrolledWindow):
    """One model-backed GridView with a restrained empty-folder message."""

    def __init__(
        self,
        activate: Callable[[FileRow], None],
        context_menu: Callable[[FileRow | None, float, float], None] | None = None,
        zoom: int = 48,
        open_new_tab: Callable[[FileRow], None] | None = None,
        mode: ViewMode | str = ViewMode.ICONS,
        drop_callback: Callable[[object, FileRow | None, Gtk.DropTarget], bool] | None = None,
        drop_feedback: Callable[[FileRow | None], bool] | None = None,
        group_toggle: Callable[[str], None] | None = None,
        group_context_menu: Callable[[FileRow, float, float], None] | None = None,
        pane_activate: Callable[[], None] | None = None,
        show_previews: bool = True,
        thumbnail_manager: ThumbnailManager | None = None,
        single_click_open: bool = False,
        show_selection_marker: bool = False,
        show_tooltips: bool = True,
        inline_rename: bool = True,
        show_additional_info: bool = True,
        rename_callback: Callable[[FileRow, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.context_menu = context_menu
        self.open_new_tab = open_new_tab
        self.drop_callback = drop_callback
        self.drop_feedback = drop_feedback
        self.group_toggle = group_toggle
        self.group_context_menu = group_context_menu
        self.pane_activate = pane_activate
        self.show_previews = bool(show_previews)
        self.single_click_open = bool(single_click_open)
        self.show_selection_marker = bool(show_selection_marker)
        self.show_tooltips = bool(show_tooltips)
        self.inline_rename_enabled = bool(inline_rename)
        self.show_additional_info = bool(show_additional_info)
        self.rename_callback = rename_callback
        self.thumbnail_manager = thumbnail_manager
        self._thumbnail_tokens: dict[int, object] = {}
        self._bound_items: dict[int, _BoundItem] = {}
        self._bound_by_location: dict[str, _BoundItem] = {}
        self._inline_rename_item: _BoundItem | None = None
        self._layout_log_source = 0
        self._debug_location = "unknown"
        self.view_mode = normalize_view_mode(mode)
        self.responsive_mode = ResponsiveMode.WIDE.value
        self.zoom_icon_size = normalize_zoom(zoom)
        self.zoom_cell_width = cell_width(self.zoom_icon_size)
        self.add_css_class("simple-file-view")
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.store = Gio.ListStore.new(FileRow)
        self.selection = Gtk.MultiSelection.new(self.store)
        self.selection.connect("selection-changed", self._on_selection_changed)
        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._on_setup, self)
        self.factory.connect("bind", self._on_bind, self)
        self.factory.connect("unbind", self._on_unbind, self)
        self.factory.connect("teardown", self._on_teardown, self)

        self.grid = Gtk.GridView.new(self.selection, self.factory)
        # Let the GridView collapse to one column at narrow widths.
        self.grid.set_min_columns(1)
        self.grid.set_max_columns(20)
        self.grid.set_single_click_activate(False)
        self.grid.set_hexpand(True)
        self.grid.set_vexpand(True)
        self.grid.add_css_class("file-grid")
        self.grid.add_css_class("omapath-file-grid")
        self.grid.connect("activate", self._on_activate, activate)
        self.empty_context_gesture = Gtk.GestureClick()
        self.empty_context_gesture.set_button(3)
        self.empty_context_gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.empty_context_gesture.connect("pressed", self._on_empty_context_pressed)
        self.grid.add_controller(self.empty_context_gesture)

        # Compact and Details are separate virtualized GTK views over the
        # same Gio.ListStore → Gtk.MultiSelection chain.  Only one is attached
        # to this ScrolledWindow at a time; no view owns a second model.
        self.compact_factory = Gtk.SignalListItemFactory()
        self.compact_factory.connect("setup", self._on_compact_setup, self)
        self.compact_factory.connect("bind", self._on_compact_bind, self)
        self.compact_factory.connect("unbind", self._on_unbind, self)
        self.compact_factory.connect("teardown", self._on_teardown, self)
        self.compact = Gtk.GridView.new(self.selection, self.compact_factory)
        self.compact.set_min_columns(1)
        self.compact.set_max_columns(12)
        self.compact.set_single_click_activate(False)
        self.compact.set_hexpand(True)
        self.compact.set_vexpand(True)
        self.compact.add_css_class("file-grid")
        self.compact.add_css_class("compact-file-grid")
        self.compact.connect("activate", self._on_activate, activate)

        self.details_factory = Gtk.SignalListItemFactory()
        self.details_factory.connect("setup", self._on_details_setup, self)
        self.details_factory.connect("bind", self._on_details_bind, self)
        self.details_factory.connect("unbind", self._on_unbind, self)
        self.details_factory.connect("teardown", self._on_teardown, self)
        self.details = Gtk.ListView.new(self.selection, self.details_factory)
        self.details.set_single_click_activate(False)
        self.details.set_hexpand(True)
        self.details.set_vexpand(True)
        self.details.add_css_class("details-list")
        self.details.connect("activate", self._on_activate, activate)

        self._add_empty_context_gesture(self.compact)
        self._add_empty_context_gesture(self.details)
        for pane_view in (self.grid, self.compact, self.details):
            self._add_pane_activation_gesture(pane_view)
        self._add_drop_target(self.grid, None)
        self._add_drop_target(self.compact, None)
        self._add_drop_target(self.details, None)
        self.active_view: Gtk.Widget = self._view_for_mode(self.view_mode)
        self.set_single_click_open(self.single_click_open)

        # Keep exactly one virtualized view as the direct child of the owning
        # ScrolledWindow.  Wrapping GridView in Gtk.Stack/ Gtk.Overlay makes
        # GTK ask it for unconstrained natural height; that turns a one-column
        # measurement into the scroll extent even when the allocated width
        # would fit several columns.  The inactive view objects remain
        # available for an explicit view-mode switch, but never participate in
        # the active measurement chain.
        self.empty_label = Gtk.Label(label="This folder is empty")
        self.empty_label.add_css_class("empty-folder-label")
        self.empty_label.set_hexpand(True)
        self.empty_label.set_vexpand(True)
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        self._add_empty_context_gesture(self.empty_label)
        self.set_child(self.active_view)

        self._log_model_state("initialized")

    def _view_for_mode(self, mode: ViewMode | str) -> Gtk.Widget:
        selected = normalize_view_mode(mode)
        return {
            ViewMode.ICONS: self.grid,
            ViewMode.COMPACT: self.compact,
            ViewMode.DETAILS: self.details,
        }[selected]

    def _add_empty_context_gesture(self, view: Gtk.Widget) -> None:
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        gesture.connect("pressed", self._on_empty_context_pressed)
        view.add_controller(gesture)

    def _add_pane_activation_gesture(self, view: Gtk.Widget) -> None:
        if self.pane_activate is None:
            return
        gesture = Gtk.GestureClick()
        gesture.set_button(0)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", self._on_pane_pressed)
        view.add_controller(gesture)

    def _on_pane_pressed(
        self,
        _gesture: Gtk.GestureClick,
        _count: int,
        _x: float,
        _y: float,
    ) -> None:
        if self.pane_activate is not None:
            self.pane_activate()

    def _add_group_toggle_gesture(self, widget: Gtk.Widget, list_item: Gtk.ListItem) -> None:
        if self.group_toggle is None:
            return
        gesture = Gtk.GestureClick()
        gesture.set_button(1)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", self._on_group_pressed, list_item)
        widget.add_controller(gesture)

    def _on_group_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        _x: float,
        _y: float,
        list_item: Gtk.ListItem,
    ) -> None:
        row = list_item.get_item()
        if not isinstance(row, FileRow) or not row.group_header:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self.group_toggle is not None:
            self.group_toggle(row.group_key)

    def _configure_group_row(
        self,
        list_item: Gtk.ListItem,
        box: Gtk.Box,
        icon: Gtk.Image,
        label: Gtk.Label,
        row: FileRow,
        *,
        extra_labels: tuple[Gtk.Widget, ...] = (),
        icon_size: int = 24,
    ) -> bool:
        if not row.group_header:
            list_item.set_selectable(True)
            list_item.set_activatable(True)
            box.remove_css_class("group-header")
            for extra in extra_labels:
                extra.set_visible(True)
            return False
        list_item.set_selectable(False)
        list_item.set_activatable(False)
        box.add_css_class("group-header")
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)
        icon.set_from_icon_name("pan-end-symbolic" if row.group_collapsed else "pan-down-symbolic")
        icon.set_pixel_size(icon_size)
        label.set_text(f"{row.group_label}  ·  {row.group_count}")
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_tooltip_text("Expand group" if row.group_collapsed else "Collapse group")
        for extra in extra_labels:
            if isinstance(extra, Gtk.Label):
                extra.set_text("")
            extra.set_visible(True)
        return True

    def _add_drop_target(self, widget: Gtk.Widget, list_item: Gtk.ListItem | None) -> None:
        if self.drop_callback is None:
            return

        def valid() -> bool:
            if self.drop_feedback is None:
                return list_item is None
            row = list_item.get_item() if list_item is not None else None
            return bool(self.drop_feedback(row if isinstance(row, FileRow) else None))

        def feedback(target: Gtk.DropTarget, _x: float, _y: float) -> Gdk.DragAction:
            return set_drop_highlight(widget, valid())

        def leave(_target: Gtk.DropTarget) -> None:
            set_drop_highlight(widget, False)

        attach_drop_target(
            widget,
            on_drop=lambda value, target: self._on_drop(target, value, list_item),
            on_enter=feedback,
            on_motion=feedback,
            on_leave=leave,
            name="file-item-drop" if list_item is not None else "folder-background-drop",
        )

    def _on_drop(
        self,
        target: Gtk.DropTarget,
        value: object,
        list_item: Gtk.ListItem | None,
    ) -> bool:
        if self.drop_callback is None:
            return False
        set_drop_highlight(target.get_widget() or self, False)
        row: FileRow | None = None
        if list_item is not None:
            candidate = list_item.get_item()
            if isinstance(candidate, FileRow):
                row = candidate
        try:
            return bool(self.drop_callback(value, row, target))
        except Exception as error:
            LOGGER.error("file drop callback failed: %s", error)
            return False

    def _add_drag_source(self, widget: Gtk.Widget, list_item: Gtk.ListItem) -> None:
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        source.connect("prepare", self._on_drag_prepare, list_item)
        widget.add_controller(source)

    def _on_drag_prepare(
        self,
        _source: Gtk.DragSource,
        _x: float,
        _y: float,
        list_item: Gtk.ListItem,
    ) -> Gdk.ContentProvider | None:
        row = list_item.get_item()
        if not isinstance(row, FileRow) or row.group_header or row.file is None:
            return None
        locations = [selected.file.get_uri() for selected in self.selected_rows() if selected.file is not None]
        row_location = row.file.get_uri()
        if not locations or row_location not in locations:
            locations = [row_location] if row_location else []
        if not locations:
            return None
        try:
            return file_content_provider(locations)
        except Exception as error:
            LOGGER.error("drag content creation failed: %s", error)
            return None

    def set_view_mode(self, mode: ViewMode | str) -> None:
        """Switch presentation while retaining the one active model chain."""

        selected = normalize_view_mode(mode)
        self.view_mode = selected
        self.active_view = self._view_for_mode(selected)
        self.set_child(self.active_view)
        self._update_realized_geometry(self.active_view)
        self.active_view.queue_resize()
        self._log_model_state("view-mode")
        self._queue_layout_log("view-mode")

    def set_show_previews(self, enabled: bool) -> None:
        """Toggle image previews without changing the filesystem model."""

        self.show_previews = bool(enabled)
        self._thumbnail_tokens.clear()
        self.active_view.queue_resize()

    def set_single_click_open(self, enabled: bool) -> None:
        """Apply the interaction preference to all views sharing the model."""

        self.single_click_open = bool(enabled)
        for view in (self.grid, self.compact, self.details):
            view.set_single_click_activate(self.single_click_open)

    def set_show_selection_marker(self, enabled: bool) -> None:
        self.show_selection_marker = bool(enabled)
        self._sync_bound_items()

    def set_show_tooltips(self, enabled: bool) -> None:
        self.show_tooltips = bool(enabled)
        for item in self._bound_items.values():
            item.root.set_tooltip_text(self._tooltip_for_item(item.row_location) if self.show_tooltips else None)

    def set_inline_rename(self, enabled: bool) -> None:
        self.inline_rename_enabled = bool(enabled)
        if not self.inline_rename_enabled:
            self.cancel_inline_rename()

    def set_show_additional_info(self, enabled: bool) -> None:
        self.show_additional_info = bool(enabled)
        self._apply_details_visibility_to_bound_items()
        self.details.queue_resize()

    def set_responsive_mode(self, mode: ResponsiveMode | str) -> None:
        """Apply a breakpoint presentation without replacing model state."""

        try:
            selected = ResponsiveMode(mode)
        except ValueError:
            selected = ResponsiveMode.WIDE
        if selected.value == self.responsive_mode:
            return
        self.responsive_mode = selected.value
        self._apply_details_visibility_to_bound_items()
        for view in (self.grid, self.compact, self.details):
            view.queue_resize()

    def _apply_details_visibility(self, child: Gtk.Box) -> None:
        widgets: list[Gtk.Widget] = []
        current = child.get_first_child()
        while current is not None:
            widgets.append(current)
            current = current.get_next_sibling()
        if len(widgets) != 5:
            return
        columns = set(detail_columns_for_mode(self.responsive_mode))
        if not self.show_additional_info:
            columns = {"name"}
        for widget, column in zip(widgets[1:], ("name", "size", "type", "modified")):
            widget.set_visible(column in columns)
        if isinstance(widgets[1], Gtk.Label):
            widgets[1].set_hexpand(True)

    def _apply_details_visibility_to_bound_items(self) -> None:
        for item in self._bound_items.values():
            child = self._content_box(item.root)
            if child is not None and "details-row" in child.get_css_classes():
                self._apply_details_visibility(child)

    def set_rename_callback(self, callback: Callable[[FileRow, str], None] | None) -> None:
        self.rename_callback = callback

    @staticmethod
    def _content_box(child: object) -> Gtk.Box | None:
        if isinstance(child, Gtk.Box):
            return child
        if isinstance(child, Gtk.Overlay):
            content = child.get_child()
            return content if isinstance(content, Gtk.Box) else None
        return None

    @staticmethod
    def _filename_stem_end(name: str) -> int:
        """Return the default selection end for an inline filename editor."""

        if name.startswith(".") and name.count(".") == 1:
            return len(name)
        suffix = Path(name).suffix
        if suffix and suffix != name:
            return max(0, len(name) - len(suffix))
        return len(name)

    @staticmethod
    def _tooltip_for_row(row: FileRow) -> str:
        if row.group_header:
            action = "Expand" if row.group_collapsed else "Collapse"
            return f"{action} group: {row.group_label} ({row.group_count} items)"
        kind = "Folder" if row.is_dir else (row.content_type or "File")
        values = [
            f"Name: {row.display_name}",
            f"Type: {kind}",
            f"Size: {format_size(row.size)}",
            f"Modified: {format_date(row.modified)}",
        ]
        if row.tags:
            values.append(f"Tags: {', '.join(row.tags)}")
        if row.rating:
            values.append(f"Rating: {'★' * row.rating}")
        if row.git_status:
            values.append(f"Git: {row.git_status}")
        return "\n".join(values)

    def _tooltip_for_item(self, location: str | None) -> str | None:
        if not location:
            return None
        for position in range(self.store.get_n_items()):
            row = self.store.get_item(position)
            if isinstance(row, FileRow) and row.location == location:
                return self._tooltip_for_row(row)
        return None

    def _wrap_item(
        self,
        box: Gtk.Box,
        name_label: Gtk.Label,
        list_item: object,
        mode: ViewMode,
    ) -> Gtk.Widget:
        """Add optional marker/editor affordances around an existing item box."""

        root = Gtk.Overlay()
        root.add_css_class("file-item-overlay")
        # GridView measures recycled items from their natural geometry.  A
        # fill-aligned root can feed the current viewport height back into
        # that measurement, producing one enormous cell per row.  File items
        # must keep their natural height; only compact/details rows fill the
        # available width.
        root.set_halign(Gtk.Align.CENTER if mode is ViewMode.ICONS else Gtk.Align.FILL)
        root.set_valign(Gtk.Align.START)
        root.set_vexpand(False)
        box.set_valign(Gtk.Align.START)
        box.set_vexpand(False)
        root.set_child(box)

        marker_icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        marker_icon.set_pixel_size(16)
        marker = Gtk.Button()
        marker.add_css_class("selection-marker")
        marker.set_child(marker_icon)
        marker.set_halign(Gtk.Align.END)
        marker.set_valign(Gtk.Align.START)
        marker.set_margin_top(5)
        marker.set_margin_end(5)
        marker.set_focusable(False)
        marker.set_focus_on_click(False)
        set_accessible_label(marker, "Select item")
        marker.connect("clicked", self._on_marker_clicked, list_item)
        root.add_overlay(marker)

        rename_entry = Gtk.Entry()
        rename_entry.add_css_class("inline-rename-entry")
        rename_entry.set_visible(False)
        rename_entry.set_halign(Gtk.Align.CENTER if mode is ViewMode.ICONS else Gtk.Align.START)
        rename_entry.set_valign(Gtk.Align.END)
        rename_entry.set_margin_bottom(4)
        rename_entry.set_margin_start(30 if mode is not ViewMode.ICONS else 8)
        rename_entry.set_margin_end(8)
        rename_entry.set_width_chars(16 if mode is ViewMode.ICONS else 20)
        set_accessible_label(rename_entry, "Rename filename")
        rename_entry.connect("activate", self._on_inline_rename_activate, list_item)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_inline_rename_key, list_item)
        rename_entry.add_controller(key_controller)
        root.add_overlay(rename_entry)

        part = _BoundItem(
            list_item=list_item,
            root=root,
            name_label=name_label,
            marker=marker,
            marker_icon=marker_icon,
            rename_entry=rename_entry,
        )
        self._bound_items[id(list_item)] = part
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_item_hover_enter, list_item)
        motion.connect("leave", self._on_item_hover_leave, list_item)
        root.add_controller(motion)
        return root

    def _sync_bound_item(self, item: _BoundItem) -> None:
        """Refresh one realized item's selection affordance.

        Binding can realize many rows at once.  A bind must only update the
        row it just registered; rescanning every other realized row here makes
        a large directory quadratic in the number of GTK list items.
        """

        list_item = item.list_item
        try:
            position = list_item.get_position()  # type: ignore[attr-defined]
        except Exception:
            position = -1
        selected = position >= 0 and self.selection.is_selected(position)
        row = self.store.get_item(position) if position >= 0 else None
        is_group_header = isinstance(row, FileRow) and row.group_header
        if item.marker is None:
            return
        item.marker.set_visible(
            self.show_selection_marker and not is_group_header and (selected or item.hovered)
        )
        if item.marker_icon is not None:
            item.marker_icon.set_from_icon_name(
                "object-select-symbolic" if selected else "list-add-symbolic"
            )
        item.marker.set_css_classes(
            ["selection-marker", "selection-marker-selected"] if selected else ["selection-marker"]
        )
        item.marker.set_tooltip_text("Deselect item" if selected else "Select item")
        set_accessible_label(item.marker, "Deselect item" if selected else "Select item")

    def _sync_bound_items(self) -> None:
        for item in tuple(self._bound_items.values()):
            self._sync_bound_item(item)

    def _on_selection_changed(self, _selection: Gtk.MultiSelection, _position: int, _n_items: int) -> None:
        self._sync_bound_items()

    def _on_item_hover_enter(self, _motion: Gtk.EventControllerMotion, _x: float, _y: float, list_item: object) -> None:
        item = self._bound_items.get(id(list_item))
        if item is not None:
            item.hovered = True
            self._sync_bound_items()

    def _on_item_hover_leave(self, _motion: Gtk.EventControllerMotion, list_item: object) -> None:
        item = self._bound_items.get(id(list_item))
        if item is not None:
            item.hovered = False
            self._sync_bound_items()

    def _on_marker_clicked(self, _button: Gtk.Button, list_item: object) -> None:
        try:
            position = list_item.get_position()  # type: ignore[attr-defined]
        except Exception:
            return
        if position < 0 or position >= self.store.get_n_items():
            return
        if self.selection.is_selected(position):
            self.selection.unselect_item(position)
        else:
            self.selection.select_item(position, False)

    def begin_inline_rename(self) -> bool:
        if not self.inline_rename_enabled or self.rename_callback is None:
            return False
        rows = self.selected_rows()
        if len(rows) != 1:
            return False
        item = self._bound_by_location.get(rows[0].location)
        if item is None or item.rename_entry is None:
            return False
        if self._inline_rename_item is not None:
            self.cancel_inline_rename()
        entry = item.rename_entry
        entry.set_text(rows[0].name)
        entry.remove_css_class("inline-rename-error")
        entry.set_tooltip_text("Press Enter to rename, Escape to cancel")
        item.name_label.set_visible(False)
        entry.set_visible(True)
        self._inline_rename_item = item
        entry.grab_focus()
        entry.select_region(0, self._filename_stem_end(rows[0].name))
        return True

    def _on_inline_rename_activate(self, _entry: Gtk.Entry, list_item: object) -> None:
        self.commit_inline_rename(list_item)

    def _on_inline_rename_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
        list_item: object,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.cancel_inline_rename()
            return True
        return False

    def commit_inline_rename(self, list_item: object | None = None) -> bool:
        item = self._inline_rename_item
        if item is None or (list_item is not None and item.list_item is not list_item):
            return False
        try:
            row = item.list_item.get_item()  # type: ignore[attr-defined]
        except Exception:
            row = None
        entry = item.rename_entry
        if not isinstance(row, FileRow) or entry is None:
            self.cancel_inline_rename()
            return False
        new_name = entry.get_text()
        source = row.file.get_path()
        if source is None or not valid_entry_name(new_name):
            entry.add_css_class("inline-rename-error")
            entry.set_tooltip_text("Enter a valid filename without '/' or '\0'")
            return False
        target = os.path.join(os.path.dirname(source), new_name)
        if os.path.abspath(target) != os.path.abspath(source) and os.path.exists(target):
            entry.add_css_class("inline-rename-error")
            entry.set_tooltip_text("A file with this name already exists")
            return False
        self.cancel_inline_rename()
        try:
            self.rename_callback(row, new_name)
        except Exception as error:
            LOGGER.error("inline rename callback failed: %s", error)
        return True

    def cancel_inline_rename(self) -> None:
        item = self._inline_rename_item
        self._inline_rename_item = None
        if item is None:
            return
        item.name_label.set_visible(True)
        if item.rename_entry is not None:
            item.rename_entry.set_visible(False)
            item.rename_entry.remove_css_class("inline-rename-error")

    def _register_bound_row(self, list_item: object, row: FileRow, name_label: Gtk.Label, root: Gtk.Widget) -> None:
        item = self._bound_items.get(id(list_item))
        if item is None:
            return
        if item.row_location and item.row_location != row.location:
            self._bound_by_location.pop(item.row_location, None)
        item.row_location = row.location
        item.name_label = name_label
        self._bound_by_location[row.location] = item
        for status_class in (
            "git-status-modified",
            "git-status-staged",
            "git-status-untracked",
            "git-status-conflict",
        ):
            root.remove_css_class(status_class)
        status = row.git_status
        if status in {"UU", "AA", "DD", "AU", "UA", "DU"}:
            root.add_css_class("git-status-conflict")
        elif status.startswith("?"):
            root.add_css_class("git-status-untracked")
        elif status[:1] in {"A", "M", "D", "R", "C"} or status[1:2] in {"A", "M", "D", "R", "C"}:
            root.add_css_class("git-status-staged" if status[:1].strip() else "git-status-modified")
        root.set_tooltip_text(self._tooltip_for_row(row) if self.show_tooltips else None)
        # This is called once for every factory bind.  Updating only the newly
        # bound row keeps large-directory realization linear; selection and
        # hover changes still use _sync_bound_items() for a full refresh.
        self._sync_bound_item(item)

    def _unregister_bound_item(self, list_item: object) -> None:
        item = self._bound_items.pop(id(list_item), None)
        if item is None:
            return
        if item.row_location:
            self._bound_by_location.pop(item.row_location, None)
        if item is self._inline_rename_item:
            self.cancel_inline_rename()

    @staticmethod
    def _on_setup(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        view: SimpleFileView | None = None,
    ) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("file-item")
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.START)
        box.set_vexpand(False)
        icon_size = view.zoom_icon_size if isinstance(view, SimpleFileView) else 48
        box.set_size_request(cell_width(icon_size), -1)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(4)
        box.set_margin_end(4)

        icon = Gtk.Image()
        icon.add_css_class("file-icon")
        icon.set_pixel_size(icon_size)
        icon.set_halign(Gtk.Align.CENTER)
        label = Gtk.Label()
        label.add_css_class("file-name")
        label.set_halign(Gtk.Align.CENTER)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(2)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(label_width_chars(icon_size))
        label.set_justify(Gtk.Justification.CENTER)

        box.append(icon)
        box.append(label)
        root: Gtk.Widget = box

        if isinstance(view, SimpleFileView):
            root = view._wrap_item(box, label, list_item, ViewMode.ICONS)
            view._add_group_toggle_gesture(box, list_item)
            context_gesture = Gtk.GestureClick()
            context_gesture.set_button(3)
            context_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            context_gesture.connect("pressed", view._on_item_context_pressed, list_item)
            box.add_controller(context_gesture)
            middle_gesture = Gtk.GestureClick()
            middle_gesture.set_button(2)
            middle_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            middle_gesture.connect("pressed", view._on_item_middle_pressed, list_item)
            box.add_controller(middle_gesture)
            view._add_drag_source(box, list_item)
            view._add_drop_target(box, list_item)
        list_item.set_child(root)
        if trace_items_enabled():
            LOGGER.debug("factory setup")

    @staticmethod
    def _on_bind(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        view: SimpleFileView | None = None,
    ) -> None:
        try:
            row = list_item.get_item()
            child = list_item.get_child()
            box = view._content_box(child) if isinstance(view, SimpleFileView) else child
            if not isinstance(row, FileRow) or not isinstance(box, Gtk.Box):
                LOGGER.error("simple factory bind received an invalid list item")
                return
            icon = box.get_first_child()
            label = icon.get_next_sibling() if icon is not None else None
            if not isinstance(icon, Gtk.Image) or not isinstance(label, Gtk.Label):
                LOGGER.error("simple factory bind received an invalid child tree")
                return

            if isinstance(view, SimpleFileView) and view._configure_group_row(
                list_item, box, icon, label, row, icon_size=view.zoom_icon_size
            ):
                box.set_size_request(-1, -1)
                if isinstance(child, Gtk.Widget):
                    view._register_bound_row(list_item, row, label, child)
                if trace_items_enabled():
                    LOGGER.debug(
                        "simple factory bind group index=%d name=%s count=%d",
                        list_item.get_position(),
                        row.group_label,
                        row.group_count,
                    )
                return
            if isinstance(view, SimpleFileView):
                box.set_size_request(view.zoom_cell_width, -1)

            try:
                gicon = getattr(row, "gicon", None)
                if gicon is None:
                    icon.set_from_icon_name("text-x-generic-symbolic")
                else:
                    # GTK4/PyGObject accepts only the GIcon here.  The size
                    # is configured separately through set_pixel_size().
                    icon.set_from_gicon(gicon)
            except Exception as error:
                LOGGER.error(
                    "simple factory icon bind failed index=%d name=%s: %s",
                    list_item.get_position(),
                    row.display_name,
                    error,
                )
                try:
                    icon.set_from_icon_name("text-x-generic-symbolic")
                except Exception as fallback_error:
                    LOGGER.error(
                        "simple factory fallback icon bind failed index=%d name=%s: %s",
                        list_item.get_position(),
                        row.display_name,
                        fallback_error,
                    )

            icon_size = view.zoom_icon_size if isinstance(view, SimpleFileView) else 48
            box.set_size_request(cell_width(icon_size), -1)
            icon.set_pixel_size(icon_size)
            label.set_text(row.display_name)
            label.set_tooltip_text(row.display_name if not isinstance(view, SimpleFileView) else None)
            label.set_max_width_chars(label_width_chars(icon_size))
            if isinstance(view, SimpleFileView) and isinstance(child, Gtk.Widget):
                view._register_bound_row(list_item, row, label, child)
            if isinstance(view, SimpleFileView):
                view._request_thumbnail(list_item, row, icon, icon_size)
            if trace_items_enabled():
                LOGGER.debug("factory bind index=%d name=%s", list_item.get_position(), row.display_name)
                LOGGER.debug("simple factory bind index=%d name=%s", list_item.get_position(), row.display_name)
        except Exception as error:
            # A malformed row or a host-specific icon/theme error must not
            # escape a GTK factory callback and abort item rendering.
            LOGGER.error("simple factory bind failed: %s", error)

    def _request_thumbnail(self, list_item: Gtk.ListItem, row: FileRow, icon: Gtk.Image, size: int) -> None:
        if not self.show_previews or self.thumbnail_manager is None or row.is_dir or row.group_header or row.file is None:
            return
        if not row.content_type.casefold().startswith("image/"):
            return
        path = row.file.get_path()
        if path is None:
            return
        token = object()
        self._thumbnail_tokens[id(list_item)] = token
        self.thumbnail_manager.request(
            path,
            size,
            lambda pixbuf, item=list_item, uri=row.location, expected=token: self._apply_thumbnail(item, uri, expected, pixbuf),
            scheduler=GLib.idle_add,
        )

    def _apply_thumbnail(self, list_item: Gtk.ListItem, uri: str, token: object, pixbuf: object | None) -> None:
        if self._thumbnail_tokens.get(id(list_item)) is not token or pixbuf is None:
            return
        try:
            current = list_item.get_item()
            child = list_item.get_child()
            if not isinstance(current, FileRow) or current.location != uri or not isinstance(child, Gtk.Box):
                return
            icon = child.get_first_child()
            if not isinstance(icon, Gtk.Image):
                return
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            icon.set_from_paintable(texture)
        except Exception as error:
            LOGGER.debug("thumbnail bind ignored: %s", error)

    @staticmethod
    def _bind_icon(icon: Gtk.Image, row: FileRow, size: int, position: int) -> None:
        """Bind a GIcon using the real GTK4/PyGObject signature."""

        try:
            gicon = getattr(row, "gicon", None)
            if gicon is None:
                icon.set_from_icon_name("text-x-generic-symbolic")
            else:
                icon.set_from_gicon(gicon)
        except Exception as error:
            LOGGER.error("simple secondary factory icon bind failed index=%d name=%s: %s", position, row.display_name, error)
            try:
                icon.set_from_icon_name("text-x-generic-symbolic")
            except Exception as fallback_error:
                LOGGER.error("simple secondary factory fallback failed index=%d: %s", position, fallback_error)
        try:
            icon.set_pixel_size(size)
        except Exception as error:
            LOGGER.error("simple secondary factory icon size failed index=%d: %s", position, error)

    @staticmethod
    def _on_compact_setup(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        view: SimpleFileView,
    ) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("file-item")
        row.add_css_class("compact-file-item")
        row.set_halign(Gtk.Align.FILL)
        row.set_valign(Gtk.Align.START)
        row.set_hexpand(True)
        row.set_vexpand(False)
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        row.set_margin_start(6)
        row.set_margin_end(6)
        icon = Gtk.Image()
        icon.add_css_class("file-icon")
        icon.set_pixel_size(24)
        label = Gtk.Label()
        label.add_css_class("file-name")
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(34)
        row.append(icon)
        row.append(label)
        root: Gtk.Widget = row
        if isinstance(view, SimpleFileView):
            root = view._wrap_item(row, label, list_item, ViewMode.COMPACT)
        context = Gtk.GestureClick()
        context.set_button(3)
        context.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        context.connect("pressed", view._on_item_context_pressed, list_item)
        row.add_controller(context)
        view._add_group_toggle_gesture(row, list_item)
        middle = Gtk.GestureClick()
        middle.set_button(2)
        middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        middle.connect("pressed", view._on_item_middle_pressed, list_item)
        row.add_controller(middle)
        view._add_drag_source(row, list_item)
        view._add_drop_target(row, list_item)
        list_item.set_child(root)
        if trace_items_enabled():
            LOGGER.debug("factory setup mode=compact")

    @staticmethod
    def _on_compact_bind(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        _view: SimpleFileView,
    ) -> None:
        try:
            row = list_item.get_item()
            root = list_item.get_child()
            child = _view._content_box(root)
            if not isinstance(row, FileRow) or not isinstance(child, Gtk.Box):
                return
            icon = child.get_first_child()
            label = icon.get_next_sibling() if icon is not None else None
            if isinstance(icon, Gtk.Image) and isinstance(label, Gtk.Label):
                if _view._configure_group_row(
                    list_item, child, icon, label, row, icon_size=24
                ):
                    if isinstance(root, Gtk.Widget):
                        _view._register_bound_row(list_item, row, label, root)
                    return
                child.set_size_request(-1, -1)
                SimpleFileView._bind_icon(icon, row, 24, list_item.get_position())
                label.set_text(row.display_name)
                label.set_tooltip_text(None)
                if isinstance(root, Gtk.Widget):
                    _view._register_bound_row(list_item, row, label, root)
                if trace_items_enabled():
                    LOGGER.debug("factory bind index=%d name=%s mode=compact", list_item.get_position(), row.display_name)
        except Exception as error:
            LOGGER.error("compact factory bind failed: %s", error)

    @staticmethod
    def _on_details_setup(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        view: SimpleFileView,
    ) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("details-row")
        row.set_valign(Gtk.Align.START)
        row.set_vexpand(False)
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        row.set_margin_start(8)
        row.set_margin_end(8)
        icon = Gtk.Image()
        icon.add_css_class("file-icon")
        icon.set_pixel_size(24)
        name = Gtk.Label()
        name.add_css_class("details-name")
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_size_request(0, -1)
        size = Gtk.Label()
        size.add_css_class("details-cell")
        size.set_halign(Gtk.Align.END)
        size.set_size_request(0, -1)
        file_type = Gtk.Label()
        file_type.add_css_class("details-cell")
        file_type.set_halign(Gtk.Align.START)
        file_type.set_size_request(0, -1)
        modified = Gtk.Label()
        modified.add_css_class("details-cell")
        modified.set_halign(Gtk.Align.START)
        modified.set_size_request(0, -1)
        row.append(icon)
        row.append(name)
        row.append(size)
        row.append(file_type)
        row.append(modified)
        root: Gtk.Widget = row
        if isinstance(view, SimpleFileView):
            root = view._wrap_item(row, name, list_item, ViewMode.DETAILS)
        context = Gtk.GestureClick()
        context.set_button(3)
        context.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        context.connect("pressed", view._on_item_context_pressed, list_item)
        row.add_controller(context)
        view._add_group_toggle_gesture(row, list_item)
        middle = Gtk.GestureClick()
        middle.set_button(2)
        middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        middle.connect("pressed", view._on_item_middle_pressed, list_item)
        row.add_controller(middle)
        view._add_drag_source(row, list_item)
        view._add_drop_target(row, list_item)
        list_item.set_child(root)
        if trace_items_enabled():
            LOGGER.debug("factory setup mode=details")

    @staticmethod
    def _on_details_bind(
        _factory: Gtk.SignalListItemFactory,
        list_item: Gtk.ListItem,
        _view: SimpleFileView,
    ) -> None:
        try:
            row = list_item.get_item()
            root = list_item.get_child()
            child = _view._content_box(root)
            if not isinstance(row, FileRow) or not isinstance(child, Gtk.Box):
                return
            children: list[Gtk.Widget] = []
            current = child.get_first_child()
            while current is not None:
                children.append(current)
                current = current.get_next_sibling()
            if len(children) != 5 or not isinstance(children[0], Gtk.Image):
                return
            if not isinstance(children[1], Gtk.Label):
                return
            if _view._configure_group_row(
                list_item,
                child,
                children[0],
                children[1],
                row,
                extra_labels=tuple(children[2:]),
                icon_size=24,
            ):
                if isinstance(root, Gtk.Widget):
                    _view._register_bound_row(list_item, row, children[1], root)
                return
            child.set_size_request(-1, -1)
            SimpleFileView._bind_icon(children[0], row, 24, list_item.get_position())
            for widget, column in zip(children[1:], ("name", "size", "type", "modified")):
                if isinstance(widget, Gtk.Label):
                    widget.set_text(detail_value(row, column))
                    widget.set_tooltip_text(None)
            _view._apply_details_visibility(child)
            if isinstance(root, Gtk.Widget) and isinstance(children[1], Gtk.Label):
                _view._register_bound_row(list_item, row, children[1], root)
            if trace_items_enabled():
                LOGGER.debug("factory bind index=%d name=%s mode=details", list_item.get_position(), row.display_name)
        except Exception as error:
            LOGGER.error("details factory bind failed: %s", error)

    @staticmethod
    def _on_unbind(
        _factory: Gtk.SignalListItemFactory,
        list_item: object,
        view: SimpleFileView | None = None,
    ) -> None:
        # Thumbnail callbacks are guarded by the current row URI and token in
        # _apply_thumbnail(), so an unbound recycled item can never receive a
        # result for a previous row.
        if isinstance(view, SimpleFileView):
            view._unregister_bound_item(list_item)

    @staticmethod
    def _on_teardown(
        _factory: Gtk.SignalListItemFactory,
        list_item: object,
        view: SimpleFileView | None = None,
    ) -> None:
        if isinstance(view, SimpleFileView):
            view._unregister_bound_item(list_item)

    def _on_empty_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
    ) -> None:
        if gesture.get_current_button() != 3:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.selection.unselect_all()
        if self.context_menu is not None:
            source = gesture.get_widget()
            menu_x, menu_y = x, y
            if isinstance(source, Gtk.Widget):
                menu_x, menu_y = self._context_menu_coordinates(
                    source,
                    self,
                    x,
                    y,
                )
            self.context_menu(None, menu_x, menu_y)

    @staticmethod
    def _context_menu_coordinates(
        source: object,
        target: object,
        x: float,
        y: float,
    ) -> tuple[float, float]:
        """Translate a click point for the popover without trusting tuple shape."""

        fallback = (max(0.0, float(x)), max(0.0, float(y)))
        try:
            source_point = Graphene.Point().init(float(x), float(y))
            result = source.compute_point(target, source_point)  # type: ignore[attr-defined]
            success, translated_point = result
            if not success or translated_point is None:
                return fallback
            return (
                max(0.0, float(translated_point.x)),
                max(0.0, float(translated_point.y)),
            )
        except Exception as error:
            LOGGER.debug("simple context point translation failed: %s", error)
            return fallback

    def _on_item_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
        list_item: Gtk.ListItem,
    ) -> None:
        if gesture.get_current_button() != 3:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        position = list_item.get_position()
        row = self.store.get_item(position)
        if not isinstance(row, FileRow):
            return
        if row.group_header:
            if self.group_context_menu is not None:
                box = list_item.get_child()
                menu_x, menu_y = x, y
                if isinstance(box, Gtk.Widget):
                    menu_x, menu_y = self._context_menu_coordinates(box, self, x, y)
                self.group_context_menu(row, menu_x, menu_y)
            return
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)

        box = list_item.get_child()
        menu_x, menu_y = x, y
        if isinstance(box, Gtk.Widget):
            menu_x, menu_y = self._context_menu_coordinates(box, self, x, y)
        if self.context_menu is not None:
            self.context_menu(row, menu_x, menu_y)

    def _on_item_middle_pressed(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        list_item: Gtk.ListItem,
    ) -> None:
        if gesture.get_current_button() != 2:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        row = self.store.get_item(list_item.get_position())
        if isinstance(row, FileRow) and not row.group_header and row.is_dir and self.open_new_tab is not None:
            self.open_new_tab(row)

    def _on_activate(
        self,
        _view: Gtk.Widget,
        position: int,
        activate: Callable[[FileRow], None],
    ) -> None:
        row = self.store.get_item(position)
        if isinstance(row, FileRow) and not row.group_header:
            activate(row)

    def replace_rows(self, rows: list[FileRow], *, reset_scroll: bool = False) -> None:
        """Replace the single model after enumeration or a bounded preview."""

        self.cancel_inline_rename()
        self.selection.unselect_all()
        # GTK4/PyGObject exposes ListStore.splice(position, removals,
        # additions:list); using one exact bulk mutation avoids a notification
        # and realization churn for every row.
        self.store.splice(0, self.store.get_n_items(), rows)
        if reset_scroll:
            adjustment = self.get_vadjustment()
            adjustment.set_value(0.0)
        self.set_empty_visible(not rows)
        self._log_model_state("commit")
        self._queue_layout_log("commit")

    def set_zoom(self, zoom: int) -> None:
        """Apply zoom to realized item widgets without replacing the model."""

        self.zoom_icon_size = normalize_zoom(zoom)
        self.zoom_cell_width = cell_width(self.zoom_icon_size)
        self._update_realized_geometry(self.active_view)
        self.active_view.queue_resize()
        self._queue_layout_log("zoom")

    def _update_realized_geometry(self, widget: Gtk.Widget) -> None:
        classes = widget.get_css_classes()
        if "compact-file-item" in classes:
            # Compact rows are fluid; a zoom cell width here would reintroduce
            # a horizontal minimum when the grid has one narrow column.
            widget.set_size_request(-1, -1)
        elif "file-item" in classes:
            widget.set_size_request(self.zoom_cell_width, -1)
        elif "file-icon" in classes and isinstance(widget, Gtk.Image):
            widget.set_pixel_size(self.zoom_icon_size)
        elif "file-name" in classes and isinstance(widget, Gtk.Label):
            widget.set_max_width_chars(label_width_chars(self.zoom_icon_size))
        child = widget.get_first_child()
        while child is not None:
            self._update_realized_geometry(child)
            child = child.get_next_sibling()

    def set_empty_visible(self, visible: bool) -> None:
        """Show an empty state without wrapping the active GridView."""

        if visible:
            if self.get_child() is not self.empty_label:
                self.set_child(self.empty_label)
            self.empty_label.set_visible(True)
            return
        if self.get_child() is not self.active_view:
            self.set_child(self.active_view)

    def selected_rows(self) -> list[FileRow]:
        return [
            row
            for position in range(self.store.get_n_items())
            if self.selection.is_selected(position)
            for row in [self.store.get_item(position)]
            if isinstance(row, FileRow) and not row.group_header
        ]

    def selected_locations(self) -> set[str]:
        """Return stable row identities for preserving selection across refresh."""

        return {row.location for row in self.selected_rows()}

    def restore_selection(self, locations: set[str]) -> None:
        """Select rows whose URI identity survived a model replacement."""

        self.selection.unselect_all()
        if not locations:
            return
        for position in range(self.store.get_n_items()):
            row = self.store.get_item(position)
            if isinstance(row, FileRow) and not row.group_header and row.location in locations:
                self.selection.select_item(position, True)

    def select_all(self) -> None:
        self.selection.select_all()

    def clear_selection(self) -> None:
        self.selection.unselect_all()

    def invert_selection(self) -> None:
        for position in range(self.store.get_n_items()):
            if self.selection.is_selected(position):
                self.selection.unselect_item(position)
            else:
                self.selection.select_item(position, False)

    def _log_model_state(self, event: str) -> None:
        selection_model = self.selection.get_model()
        view_model = self.active_view.get_model()
        adjustment = self.get_vadjustment()
        viewport = (
            round(adjustment.get_value(), 1),
            round(adjustment.get_page_size(), 1),
            round(adjustment.get_upper(), 1),
        ) if adjustment is not None else (0.0, 0.0, 0.0)
        LOGGER.debug(
            "simple file view event=%s store items=%d selection model items=%d view model items=%d "
            "selection_wraps_store=%s view_uses_selection=%s active_view=%s mode=%s realized=%d viewport=%s",
            event,
            self.store.get_n_items(),
            selection_model.get_n_items(),
            view_model.get_n_items(),
            selection_model is self.store,
            view_model is self.selection,
            type(self.active_view).__name__,
            self.view_mode.value,
            len(self._bound_items),
            viewport,
        )
        active_name = "grid" if self.active_view is self.grid else "compact" if self.active_view is self.compact else "list"
        LOGGER.debug("selection model items=%d", selection_model.get_n_items())
        LOGGER.debug("gtk store items=%d", self.store.get_n_items())
        LOGGER.debug("view model items=%d", view_model.get_n_items())
        LOGGER.debug("active view=%s", active_name)
        LOGGER.debug("file view visible=%s", self.get_visible())

    def _queue_layout_log(self, event: str) -> None:
        """Log geometry after GTK has processed the model/layout mutation."""

        if self._layout_log_source:
            GLib.source_remove(self._layout_log_source)
        self._layout_log_source = GLib.timeout_add(80, self._log_layout_state, event)

    def _log_layout_state(self, event: str) -> bool:
        self._layout_log_source = 0
        adjustment = self.get_vadjustment()
        file_width = max(0, int(self.get_width()))
        file_height = max(0, int(self.get_height()))
        page_size = max(0.0, float(adjustment.get_page_size()))
        upper = max(0.0, float(adjustment.get_upper()))
        value = max(0.0, float(adjustment.get_value()))
        if self.active_view is self.grid:
            # GridView exposes min/max bounds, not the chosen column count.
            # Derive a conservative count from the actual allocated width and
            # the stable item cell policy; this is used for diagnostics only.
            cell = max(1, self.zoom_cell_width + 12)
            columns = max(1, min(self.grid.get_max_columns(), file_width // cell))
            row_height = max(1, 48 + 8 + 34 + 16)
            visible_rows = max(1, int((page_size + row_height - 1) // row_height))
            active_name = "grid"
        elif self.active_view is self.compact:
            columns = max(1, self.compact.get_max_columns())
            visible_rows = max(1, int(page_size // 36) + 1)
            active_name = "compact"
        else:
            columns = 1
            visible_rows = max(1, int(page_size // 36) + 1)
            active_name = "list"
        model_items = self.store.get_n_items()
        LOGGER.debug(
            "layout event=%s location=%s window=%dx%d file_area=%dx%d grid_columns=%d "
            "model_items=%d bound_items=%d visible_rows=%d vadj_upper=%.1f vadj_page=%.1f vadj_value=%.1f",
            event,
            getattr(self, "_debug_location", "unknown"),
            file_width,
            file_height,
            file_width,
            file_height,
            columns,
            model_items,
            len(self._bound_items),
            visible_rows,
            upper,
            page_size,
            value,
        )
        if model_items < 100 and upper > 20000:
            LOGGER.warning(
                "suspicious grid geometry model_items=%d vadj_upper=%.1f active_view=%s",
                model_items,
                upper,
                active_name,
            )
        return False


def build_css(colors: dict[str, str], font_family: str) -> str:
    """Build compact app-level styling from the active Omarchy palette.

    The theme parser exposes a small, stable palette rather than GTK CSS
    variables.  These local semantic roles keep the view readable while still
    making every surface and accent follow the active theme.
    """

    palette = {**FALLBACK_COLORS, **colors}
    background = palette["background"]
    surface = palette["dark_background"]
    surface_deep = palette["darker_background"]
    foreground = palette["foreground"]
    muted = palette["light_foreground"]
    accent = palette["accent"]
    border_tone = palette["muted"]
    border = rgba(border_tone, 0.42)
    border_subtle = rgba(border_tone, 0.24)
    surface_hover = rgba(foreground, 0.06)
    surface_active = rgba(foreground, 0.10)
    selection = rgba(accent, 0.14)
    selection_border = rgba(accent, 0.32)
    focus = rgba(accent, 0.68)

    return f"""
    * {{
        font-family: "{font_family}", monospace;
    }}

    window,
    .omapath-window,
    .simple-shell {{
        min-width: 0;
        min-height: 0;
        background-color: {background};
        color: {foreground};
    }}

    .topbar {{
        background-color: {surface};
        border-bottom: 1px solid {border};
        min-height: 32px;
        padding: 6px 10px;
    }}

    .toolbar-group {{
        min-width: 0;
    }}

    .toolbar-button {{
        min-width: 34px;
        min-height: 34px;
        padding: 0;
        border-radius: 6px;
        border: 1px solid transparent;
        background-color: transparent;
        color: {foreground};
    }}

    .toolbar-button:hover {{
        background-color: {surface_hover};
    }}

    .toolbar-button:active {{
        background-color: {surface_active};
    }}

    .toolbar-button:focus {{
        border-color: {focus};
    }}

    .toolbar-button:disabled {{
        opacity: 0.42;
    }}

    .panel-controls-shell,
    .panel-controls-expanded {{
        min-width: 0;
    }}

    .panel-menu-button {{
        min-width: 34px;
        min-height: 34px;
    }}

    .panel-menu-button.panel-menu-active {{
        background-color: {selection};
        border-color: {selection_border};
        color: {accent};
    }}

    .panel-controls-popover {{
        min-width: 190px;
    }}

    .panel-menu-row {{
        min-width: 0;
    }}

    .panel-shortcut {{
        min-width: 30px;
        color: {muted};
        font-size: 11px;
    }}

    .breadcrumbs {{
        min-width: 0;
        min-height: 34px;
        padding: 0 6px;
        background-color: transparent;
    }}

    .search-mode {{
        min-width: 0;
        min-height: 34px;
        padding: 0 2px;
    }}

    .search-mode > entry {{
        min-width: 0;
    }}

    .location-entry {{
        min-width: 0;
        min-height: 34px;
        padding: 0 8px;
        border: 1px solid transparent;
        border-radius: 6px;
        background-color: {surface_deep};
        color: {foreground};
    }}

    .location-entry:focus {{
        border-color: {focus};
    }}

    .location-entry-error {{
        border-color: {palette["red"]};
    }}

    .location-error {{
        min-width: 0;
        min-height: 30px;
        padding: 0 6px;
        color: {palette["red"]};
        font-size: 11px;
    }}

    .location-completion-popover {{
        min-width: 0;
        border: 1px solid {border};
        border-radius: 7px;
        background-color: {surface_deep};
    }}

    .location-completion-row {{
        min-height: 30px;
        color: {foreground};
    }}

    .location-completion-row:hover,
    .location-completion-row:selected {{
        background-color: {selection};
    }}

    .breadcrumb-button {{
        min-height: 30px;
        min-width: 0;
        padding: 0 6px;
        border-radius: 5px;
        border: 1px solid transparent;
        background-color: transparent;
        background-image: none;
        color: {muted};
    }}

    .breadcrumb-button:hover {{
        background-color: {surface_hover};
        color: {foreground};
    }}

    .breadcrumb-button:focus {{
        border-color: {focus};
    }}

    .breadcrumb-current {{
        color: {foreground};
        font-weight: 600;
    }}

    .breadcrumb-separator {{
        color: {muted};
        padding: 0 2px;
    }}

    .item-count {{
        min-width: 0;
        padding: 0 4px;
        color: {muted};
        font-size: 11px;
    }}

    .status-bar {{
        min-height: 22px;
        padding: 2px 10px;
        background-color: {surface};
        border-top: 1px solid {border_subtle};
    }}

    .tab-bar {{
        min-height: 32px;
        padding: 2px 8px;
        background-color: {surface};
        border-bottom: 1px solid {border_subtle};
    }}

    .tab-row {{
        min-width: 0;
        min-height: 28px;
    }}

    .tab-button,
    .tab-close {{
        min-height: 28px;
        padding: 0 8px;
        border: 1px solid transparent;
        border-radius: 5px;
        background-color: transparent;
        background-image: none;
        color: {muted};
    }}

    .tab-button:hover,
    .tab-close:hover {{
        background-color: {surface_hover};
        color: {foreground};
    }}

    .tab-button-active {{
        background-color: {selection};
        border-color: {selection_border};
        color: {foreground};
    }}

    .filter-bar,
    .filter-mode {{
        min-height: 34px;
        padding: 0 4px;
        background-color: {surface};
        border-bottom: 1px solid {border_subtle};
    }}

    .filter-error {{
        color: {accent};
        font-size: 11px;
    }}

    .sidebar {{
        min-width: 160px;
        background-color: {surface};
        padding: 10px 8px 14px;
    }}

    .sidebar-scroller,
    .workspace-overlay,
    .workspace-paned,
    .left-panel-paned,
    .content-paned,
    .terminal-paned,
    .split-paned {{
        min-width: 0;
        min-height: 0;
    }}

    .sidebar-section-label {{
        min-height: 22px;
        margin: 6px 0 2px;
        padding: 0 8px;
        color: {muted};
        font-size: 11px;
        font-weight: 600;
    }}

    .sidebar-row {{
        min-width: 0;
        min-height: 36px;
        padding: 0 8px;
        border: 1px solid transparent;
        border-radius: 6px;
        background-color: transparent;
        background-image: none;
        color: {foreground};
    }}

    .sidebar-row:hover {{
        background-color: {surface_hover};
    }}

    .sidebar-row:focus {{
        border-color: {focus};
    }}

    .sidebar-row-active {{
        border-left: 2px solid {accent};
        background-color: {selection};
    }}

    .sidebar-row-active:hover {{
        background-color: {selection};
    }}

    .dnd-drop-target {{
        border-color: {selection_border};
        background-color: {selection};
        box-shadow: inset 0 0 0 1px {selection_border};
    }}

    .sidebar-row:disabled {{
        opacity: 0.42;
    }}

    .folders-panel,
    .info-panel,
    .quick-look {{
        background-color: {surface};
        color: {foreground};
    }}

    .info-panel {{
        min-width: 0;
        padding: 4px;
    }}

    .terminal-panel {{
        min-width: 0;
        min-height: 0;
        background-color: {background};
    }}

    .folders-list {{
        padding: 8px;
    }}

    .sidebar-icon {{
        min-width: 18px;
        min-height: 18px;
    }}

    .sidebar-spacer {{
        min-height: 12px;
    }}

    .layout-separator {{
        background-color: {border_subtle};
    }}

    .simple-file-view {{
        background-color: {background};
    }}

    .simple-file-view.active-pane {{
        border-left: 2px solid {selection_border};
    }}

    .file-grid {{
        padding: 16px;
    }}

    .omapath-file-grid > child {{
        border: 1px solid transparent;
        border-radius: 7px;
        background-color: transparent;
        background-image: none;
        color: {foreground};
    }}

    .omapath-file-grid > child:hover {{
        background-color: {surface_hover};
    }}

    .omapath-file-grid > child:selected {{
        border-color: {selection_border};
        background-color: {selection};
        background-image: none;
        color: {foreground};
    }}

    .omapath-file-grid > child:selected:hover {{
        background-color: {selection};
    }}

    .omapath-file-grid > child:focus {{
        border-color: {focus};
    }}

    .file-item {{
        min-width: 0;
        min-height: 0;
        padding: 4px;
        border: 1px solid transparent;
        border-radius: 7px;
        background-color: transparent;
    }}

    .file-item-overlay {{
        min-width: 0;
        min-height: 0;
    }}

    .file-item.dnd-drop-target,
    .file-item-overlay.dnd-drop-target {{
        border-color: {selection_border};
        background-color: {selection};
    }}

    .git-status-modified {{
        border-right: 2px solid {accent};
    }}

    .git-status-staged {{
        border-right: 2px solid {selection_border};
    }}

    .git-status-untracked {{
        border-right: 2px dotted {muted};
    }}

    .git-status-conflict {{
        border-right: 2px solid {accent};
        box-shadow: inset 0 0 0 1px {selection_border};
    }}

    .selection-marker {{
        min-width: 28px;
        min-height: 28px;
        padding: 0;
        border: 1px solid transparent;
        border-radius: 6px;
        background-color: {surface_deep};
        background-image: none;
        color: {muted};
        opacity: 0.96;
        transition-property: background-color, color, opacity;
        transition-duration: 120ms;
    }}

    .selection-marker:hover {{
        background-color: {surface_hover};
        border-color: {border_subtle};
        color: {foreground};
    }}

    .selection-marker:focus-visible {{
        border-color: {focus};
    }}

    .selection-marker-selected {{
        background-color: {selection};
        border-color: {selection_border};
        color: {accent};
    }}

    .animations-disabled .selection-marker {{
        transition-duration: 0ms;
    }}

    .inline-rename-entry {{
        min-height: 30px;
        padding: 0 7px;
        border: 1px solid {selection_border};
        border-radius: 5px;
        background-color: {surface_deep};
        color: {foreground};
    }}

    .inline-rename-entry:focus {{
        border-color: {focus};
    }}

    .inline-rename-error {{
        border-color: {palette["red"]};
        color: {palette["red"]};
    }}

    .file-icon {{
        min-width: 0;
        min-height: 0;
        margin-bottom: 4px;
    }}

    .file-name {{
        color: {foreground};
        font-size: 13px;
        font-weight: 400;
    }}

    .compact-file-grid {{
        padding: 8px 12px 12px;
    }}

    .compact-file-item {{
        min-width: 0;
    }}

    .details-list {{
        min-width: 0;
        padding: 8px 12px 12px;
    }}

    .details-row {{
        min-width: 0;
        min-height: 36px;
        border-bottom: 1px solid {border_subtle};
        color: {foreground};
    }}

    .details-row:hover {{
        background-color: {surface_hover};
    }}

    .details-name,
    .details-cell {{
        min-width: 0;
        color: {foreground};
        font-size: 12px;
    }}

    .details-cell {{
        color: {muted};
    }}

    .empty-folder-label {{
        color: {muted};
        font-size: 13px;
    }}

    .simple-context-menu {{
        min-width: 168px;
        padding: 4px;
        border: 1px solid {border};
        border-radius: 8px;
        background-color: {surface_deep};
    }}

    .simple-context-popover {{
        border: 1px solid {border};
        border-radius: 8px;
        background-color: {surface_deep};
    }}

    .context-action {{
        min-height: 32px;
        padding: 0 10px;
        border-radius: 5px;
        background-color: transparent;
        color: {foreground};
    }}

    .context-action:hover {{
        background-color: {surface_hover};
    }}

    .destructive-action {{
        color: {palette["red"]};
    }}

    .destructive-action:hover {{
        background-color: {rgba(palette["red"], 0.14)};
    }}

    .context-separator {{
        min-height: 1px;
        margin: 4px 6px;
        background-color: {border_subtle};
    }}

    .simple-dialog,
    .simple-properties {{
        background-color: {surface};
        color: {foreground};
    }}

    .simple-dialog-entry {{
        min-height: 34px;
        border-color: {border_subtle};
    }}

    .simple-property {{
        color: {foreground};
    }}

    .settings-window,
    .settings-section-selector {{
        min-width: 0;
        min-height: 0;
    }}

    .responsive-drawer-backdrop {{
        background-color: rgba(0, 0, 0, 0.28);
    }}

    .responsive-drawer,
    .responsive-sidebar-drawer {{
        min-width: 0;
        background-color: {surface};
        box-shadow: 0 0 18px rgba(0, 0, 0, 0.24);
    }}

    .responsive-medium .topbar {{
        padding: 4px 6px;
    }}

    .responsive-narrow .topbar {{
        padding: 2px 4px;
    }}

    .responsive-narrow .toolbar-button {{
        min-width: 30px;
        min-height: 30px;
    }}

    .responsive-narrow .topbar,
    .responsive-narrow .breadcrumbs,
    .responsive-narrow .search-mode,
    .responsive-narrow .location-entry {{
        min-height: 30px;
    }}

    .responsive-medium .file-grid {{
        padding: 14px 16px 16px;
    }}

    .responsive-narrow .file-grid {{
        padding: 8px 9px 10px;
    }}

    .responsive-medium .compact-file-grid {{
        padding: 7px 10px 10px;
    }}

    .responsive-narrow .compact-file-grid {{
        padding: 5px 6px 7px;
    }}

    .responsive-medium .details-list,
    .responsive-narrow .details-list {{
        padding-left: 6px;
        padding-right: 6px;
    }}

    .tab-button,
    .tab-close {{
        min-width: 0;
    }}

    """


class OmapathApplication(Gtk.Application):
    """GTK application composition layer for the active Omapath runtime."""

    def __init__(self, safe_mode: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.safe_mode = bool(safe_mode)
        self.shutting_down = False
        self.operations = OperationManager(scheduler=self._schedule_operation_callback)
        self.active_operation_id: str | None = None
        self.operation_button: Gtk.MenuButton | None = None
        self.operation_controller = OperationController(self)
        self.search_controls = SearchControls(self)
        self.topbar_controller = TopbarController(self)
        self.panel_controls = PanelControlsController(self)
        self.actions = ActionRegistry()
        self.plugins = PluginRegistry(self.actions)
        self.plugin_report = PluginLoadReport()
        self.clipboard_paths: list[str] = []
        self.clipboard_cut = False
        self.clipboard_read_generation = 0
        self.window: Gtk.ApplicationWindow | None = None
        self.file_view: SimpleFileView | None = None
        self.body: Gtk.Box | None = None
        self.root_widget: Gtk.Box | None = None
        self.sidebar_widget: Gtk.Widget | None = None
        self.sidebar_scroll: Gtk.ScrolledWindow | None = None
        self.sidebar_separator: Gtk.Separator | None = None
        self.workspace_paned: Gtk.Paned | None = None
        self.workspace_overlay: Gtk.Overlay | None = None
        self.left_paned: Gtk.Paned | None = None
        self.content_paned: Gtk.Paned | None = None
        self.terminal_paned: Gtk.Paned | None = None
        self.file_area: Gtk.Box | None = None
        self.status_bar: Gtk.Box | None = None
        self.split_paned: Gtk.Paned | None = None
        self.split_file_view: SimpleFileView | None = None
        self.split_location: str | None = None
        self.active_pane = 0
        self.split_pane_state: PaneState | None = None
        self.split_generation = 0
        self.split_cancellable: Gio.Cancellable | None = None
        self.split_enumerator: Gio.FileEnumerator | None = None
        self.split_entries: list[DirectoryEntry] = []
        self.split_directory_ready = False
        self.split_directory_monitor: DirectoryMonitor | None = None
        self.folders_panel: FoldersPanel | None = None
        self.info_panel: Gtk.Box | None = None
        self.info_panel_labels: list[Gtk.Label] = []
        self.settings = load_settings()
        self.places_store = PlacesStore(
            pinned=self.settings.get("pinned_places"),
            starred=self.settings.get("starred_places"),
            recent=self.settings.get("recent_locations"),
        )
        self.location_history = [
            value
            for value in self.settings.get("location_history", [])
            if isinstance(value, str) and value
        ][:24]
        self.metadata_store = MetadataStore()
        self.pinned_box: Gtk.Box | None = None
        self.starred_box: Gtk.Box | None = None
        self.tags_box: Gtk.Box | None = None
        self.tags_label: Gtk.Label | None = None
        self.recent_box: Gtk.Box | None = None
        self.devices_box: Gtk.Box | None = None
        self.device_monitor: DeviceMonitor | None = None
        self.folder_settings = FolderViewSettings(self.settings.get("folder_view_settings"))
        self.breadcrumbs: Gtk.Box | None = None
        self.location_stack: Gtk.Stack | None = None
        self.location_entry: Gtk.Entry | None = None
        self.location_error_label: Gtk.Label | None = None
        self.location_completion_popover: Gtk.Popover | None = None
        self.location_completion_list: Gtk.ListBox | None = None
        self.location_completion_items: list[tuple[str, str]] = []
        self.location_completion_names: list[str] = []
        self.location_completion_parent = ""
        self.location_completion_prefix = ""
        self.location_completion_width_request = -1
        self.location_completion_index = -1
        self.location_completion_generation = 0
        self.location_completion_cancellable: Gio.Cancellable | None = None
        self.location_completion_enumerator: Gio.FileEnumerator | None = None
        self.location_validation_generation = 0
        self.location_validation_cancellable: Gio.Cancellable | None = None
        self._updating_location_entry = False
        self.breadcrumb_resize_source = 0
        self.tab_bar: Gtk.Box | None = None
        self.view_menu_button: Gtk.MenuButton | None = None
        self.filter_bar: Gtk.Box | None = None
        self.filter_entry: Gtk.SearchEntry | None = None
        self.filter_mode_combo: Gtk.ComboBoxText | None = None
        self.filter_error_label: Gtk.Label | None = None
        self.search_bar: Gtk.Box | None = None
        self.search_entry: Gtk.SearchEntry | None = None
        self.search_recursive_check: Gtk.CheckButton | None = None
        self.search_contents_check: Gtk.CheckButton | None = None
        self.search_case_check: Gtk.CheckButton | None = None
        self.search_hidden_check: Gtk.CheckButton | None = None
        self.search_status_label: Gtk.Label | None = None
        self.palette_window: CommandPalette | None = None
        self.settings_window: SettingsWindow | None = None
        self.show_hidden_check: Gtk.CheckButton | None = None
        self.sort_direction_check: Gtk.CheckButton | None = None
        self.folders_first_check: Gtk.CheckButton | None = None
        self.groups_check: Gtk.CheckButton | None = None
        self.zoom_label: Gtk.Label | None = None
        self._updating_view_menu = False
        self.status_label: Gtk.Label | None = None
        self.status_bar_label: Gtk.Label | None = None
        self.back_button: Gtk.Button | None = None
        self.forward_button: Gtk.Button | None = None
        self.up_button: Gtk.Button | None = None
        self.search_button: Gtk.Button | None = None
        self.key_controller: Gtk.EventControllerKey | None = None
        self.sidebar_toggle_button: Gtk.Button | None = None
        self.responsive_mode: ResponsiveMode | None = None
        self.responsive_short_height = False
        self.responsive_drawer_backdrop: Gtk.Widget | None = None
        self.responsive_drawer_widget: Gtk.Widget | None = None
        self.responsive_sidebar_drawer_open = False
        self.responsive_info_drawer_open = False
        self.responsive_info_suppressed = False
        self.responsive_terminal_suppressed = False
        self.sidebar_buttons: dict[str, Gtk.Button] = {}
        self.sidebar_controller = SidebarController(self)
        self.tab_bar_controller = TabBarController(self)
        self.quick_look_controller = QuickLookController(self)
        self.context_popover: Gtk.Popover | None = None
        self.context_menu_controller = ContextMenuController(self)
        self.tab_context_popover: Gtk.Popover | None = None
        self.breadcrumb_popover: Gtk.Popover | None = None
        self.name_dialog: Gtk.Dialog | None = None
        self.confirmation_dialog: Gtk.Dialog | None = None
        self.conflict_dialog: Gtk.Dialog | None = None
        self.metadata_dialog: Gtk.Dialog | None = None
        self.git_dialog: Gtk.Dialog | None = None
        self.info_tags_entry: Gtk.Entry | None = None
        self.info_rating_spin: Gtk.SpinButton | None = None
        self.info_comment_view: Gtk.TextView | None = None
        self.terminal_panel: TerminalPanel | None = None
        self.pending_conflict: _PendingConflictRequest | None = None
        self.dialog_controller = DialogController(self)
        self.css_provider: Gtk.CssProvider | None = None
        self.theme_watcher: ThemeWatcher | None = None
        self.theme_reload_source = 0
        self.theme_reload_failure_logged = False
        self.geometry_save_source = 0
        self.sidebar_position_save_source = 0
        self.folders_position_save_source = 0
        self.info_position_save_source = 0
        self.terminal_position_save_source = 0
        self._last_refresh_request: tuple[str, float] | None = None
        self.split_position_save_source = 0
        self.directory_monitor: DirectoryMonitor | None = None
        self.directory_monitor_location: str | None = None
        self.show_hidden = bool(self.settings.get("show_hidden", False))
        self.sort_key = normalize_sort_key(self.settings.get("sort_mode", "name"))
        self.sort_descending = bool(self.settings.get("sort_reverse", False))
        self.folders_first = bool(self.settings.get("folders_first", True))
        self.icon_zoom = normalize_zoom(self.settings.get("icon_zoom", 48))
        self.view_mode = normalize_view_mode(self.settings.get("default_view", "icons"))
        self.show_previews = bool(self.settings.get("show_previews", True))
        self.filter_query = ""
        self.filter_mode = FilterMode.PLAIN
        self.filter_error: str | None = None
        self._restoring_tab_state = False
        self.search_service = SearchService()
        self.search_generation = 0
        self.search_active = False
        self.search_pane = 0
        self.search_location: str | None = None
        self._updating_search_entry = False
        self._updating_search_options = False
        self.search_tags: tuple[str, ...] = ()
        self.search_min_rating: int | None = None
        self.preview_manager = PreviewManager(max_bytes=int(self.settings.get("preview_max_bytes", 262144)))
        self.thumbnail_manager = ThumbnailManager(
            limit=int(self.settings.get("thumbnail_cache_limit", 64)),
        )
        self.preview_window: Gtk.Window | None = None
        self.preview_content_box: Gtk.Box | None = None
        self.preview_title_label: Gtk.Label | None = None
        self.preview_generation = 0
        self.preview_uri: str | None = None
        self.group_criterion = normalize_group_criterion(self.settings.get("group_criterion", "none"))
        self.groups_enabled = bool(self.settings.get("show_groups", False))
        self.collapsed_groups: set[str] = set()
        self.directory_entries: list[DirectoryEntry] = []
        self.directory_entries_location: str | None = None
        self.directory_entries_ready = False
        self.git_status_cache: dict[str, dict[str, str]] = {}

        self.home = str(Path.home())
        self.navigation_state = NavigationHistory()
        initial_tab = TabState(
            location=self.home,
            show_hidden=self.show_hidden,
            sort_key=self.sort_key,
            sort_descending=self.sort_descending,
            folders_first=self.folders_first,
            icon_zoom=self.icon_zoom,
            view_mode=self.view_mode.value,
            collapsed_groups=[],
        )
        self.tabs: list[TabState] = [initial_tab]
        self.active_tab_index = 0
        self.closed_tabs: list[TabState] = []
        self._restore_session_tabs()
        active_tab = self.tabs[self.active_tab_index]
        self.current_location = active_tab.location
        self.back_history = list(active_tab.back_history)
        self.forward_history = list(active_tab.forward_history)
        self.load_generation = 0
        self.load_cancellable: Gio.Cancellable | None = None
        self.loading_enumerator: Gio.FileEnumerator | None = None
        self.loading_folder: Gio.File | None = None
        self.loading_entries: list[DirectoryEntry] = []
        self.loading_started_at = 0.0
        self.loading_presented = False
        self.loading_preview_queued = False
        self.selection_to_restore: tuple[int, set[str]] | None = None
        self.pending_selection_locations: set[str] = set()
        self._pending_main_scroll_position = 0.0

        self.theme_name = active_theme()
        self.colors = read_colors(self.theme_name)
        self.font_family = active_font()
        self._register_actions()
        self.plugin_report = load_plugins(self.plugins, safe_mode=self.safe_mode)
        self.actions.load_shortcuts(self.settings.get("shortcuts", {}))
        LOGGER.debug(
            "plugins safe_mode=%s loaded=%d failed=%d skipped=%d",
            self.safe_mode,
            len(self.plugin_report.loaded),
            len(self.plugin_report.failed),
            len(self.plugin_report.skipped),
        )

    def _ensure_navigation_state(self) -> NavigationHistory:
        """Return state even for lightweight ``__new__`` test doubles."""

        state = getattr(self, "navigation_state", None)
        if not isinstance(state, NavigationHistory):
            state = NavigationHistory()
            self.navigation_state = state
        return state

    @property
    def current_location(self) -> str | None:
        """Compatibility view of the active tab's navigation state."""

        return self._ensure_navigation_state().current

    @current_location.setter
    def current_location(self, value: str | None) -> None:
        self._ensure_navigation_state().current = value

    @property
    def back_history(self) -> list[str]:
        """Compatibility view retained while callers migrate to navigation_state."""

        return self._ensure_navigation_state().back

    @back_history.setter
    def back_history(self, value: list[str]) -> None:
        self._ensure_navigation_state().back = list(value)

    @property
    def forward_history(self) -> list[str]:
        """Compatibility view retained while callers migrate to navigation_state."""

        return self._ensure_navigation_state().forward

    @forward_history.setter
    def forward_history(self, value: list[str]) -> None:
        self._ensure_navigation_state().forward = list(value)

    def _restore_session_tabs(self) -> None:
        if not bool(self.settings.get("remember_tabs", True)):
            return
        raw_tabs = self.settings.get("session_tabs", [])
        restored: list[TabState] = []
        if isinstance(raw_tabs, list):
            for raw in raw_tabs[:32]:
                if not isinstance(raw, dict) or not isinstance(raw.get("location"), str):
                    continue
                location = self.normalize_location(raw["location"])
                restored.append(
                    TabState(
                        location=location,
                        show_hidden=bool(raw.get("show_hidden", self.show_hidden)),
                        sort_key=normalize_sort_key(raw.get("sort_key", self.sort_key)),
                        sort_descending=bool(raw.get("sort_descending", self.sort_descending)),
                        folders_first=bool(raw.get("folders_first", self.folders_first)),
                        icon_zoom=normalize_zoom(raw.get("icon_zoom", self.icon_zoom)),
                        view_mode=normalize_view_mode(raw.get("view_mode", self.view_mode)).value,
                        scroll_position=max(
                            0.0,
                            float(raw.get("scroll_position", 0.0))
                            if isinstance(raw.get("scroll_position", 0.0), (int, float))
                            else 0.0,
                        ),
                        filter_query=str(raw.get("filter_query", "")),
                        filter_mode=normalize_filter_mode(raw.get("filter_mode", "plain")).value,
                        group_criterion=normalize_group_criterion(raw.get("group_criterion", "none")).value,
                        groups_enabled=bool(raw.get("groups_enabled", False)),
                        collapsed_groups=[
                            value for value in raw.get("collapsed_groups", []) if isinstance(value, str)
                        ],
                        show_previews=bool(raw.get("show_previews", self.show_previews)),
                        selected_locations=[
                            value for value in raw.get("selected_locations", []) if isinstance(value, str)
                        ],
                    )
                )
        if restored:
            self.tabs = restored
            self.active_tab_index = min(max(0, int(self.settings.get("active_tab", 0))), len(restored) - 1)
            self._load_active_tab_state()

    def _register_actions(self) -> None:
        """Register the stable command vocabulary used by UI surfaces."""

        actions = (
            ("nav.back", "Back", self.go_back, "Alt+Left", "Navigation"),
            ("nav.forward", "Forward", self.go_forward, "Alt+Right", "Navigation"),
            ("nav.up", "Up", self.go_up, "Alt+Up", "Navigation"),
            ("nav.refresh", "Refresh", self.refresh, "F5", "Navigation"),
            ("nav.location", "Editable Location", self.show_location_entry, "Ctrl+L", "Navigation"),
            ("file.open", "Open", self.open_selected, "Enter", "File"),
            ("file.copy", "Copy", self.copy_selected, "Ctrl+C", "File"),
            ("file.cut", "Cut", self.cut_selected, "Ctrl+X", "File"),
            ("file.paste", "Paste", self.paste_clipboard, "Ctrl+V", "File"),
            ("file.rename", "Rename", self.rename_selected, "F2", "File"),
            ("file.trash", "Move to Trash", self.move_to_trash, "Delete", "File"),
            ("trash.restore", "Restore from Trash", self.restore_selected, "", "Trash"),
            ("trash.delete_permanently", "Delete Permanently", self.delete_permanently_selected, "", "Trash"),
            ("trash.empty", "Empty Trash", self.empty_trash, "", "Trash"),
            ("file.duplicate", "Duplicate Here", self.duplicate_selected, "", "File"),
            ("file.link", "Create Link Here", self.create_link_selected, "", "File"),
            ("file.copy_location", "Copy Location", self.copy_location, "", "File"),
            ("places.add", "Add to Places", self.add_selected_to_places, "", "Places"),
            ("places.remove", "Remove from Places", self.remove_selected_from_places, "", "Places"),
            ("places.star", "Add to Starred", self.add_selected_to_starred, "", "Places"),
            ("places.unstar", "Remove from Starred", self.remove_selected_from_starred, "", "Places"),
            ("file.open_with", "Open With", self.open_with, "", "File"),
            ("file.compare", "Compare Selected", self.compare_selected, "", "File"),
            ("file.service", "Run Service", self.run_selected_service, "", "File"),
            ("archive.create", "Compress Selection", self.compress_selected, "", "Archives"),
            ("archive.extract", "Extract Archive", self.extract_selected, "", "Archives"),
            ("vcs.status", "Git Status", self.show_git_status, "", "VCS"),
            ("panel.sidebar", "Toggle Sidebar", self.toggle_sidebar_panel, "F9", "Panels"),
            ("panel.terminal", "Toggle Terminal Panel", self.toggle_terminal_panel, "F4", "Panels"),
            ("panel.split", "Toggle Split View", self.toggle_split_view, "F3", "Panels"),
            ("pane.copy_other", "Copy to Other Pane", self.copy_to_other_pane, "", "Split View"),
            ("pane.move_other", "Move to Other Pane", self.move_to_other_pane, "", "Split View"),
            ("pane.open_other", "Open in Other Pane", self.open_in_other_pane, "", "Split View"),
            ("panel.folders", "Folders Panel", self.toggle_folders_panel, "F7", "Panels"),
            ("panel.info", "Information Panel", self.toggle_info_panel, "F11", "Panels"),
            ("file.properties", "Properties", self.show_properties, "Alt+Enter", "File"),
            ("file.metadata", "Edit Tags & Rating", self.edit_metadata_selected, "", "File"),
            ("edit.undo", "Undo", self.undo_last, "Ctrl+Z", "Edit"),
            ("edit.redo", "Redo", self.redo_last, "Ctrl+Shift+Z", "Edit"),
            ("selection.all", "Select All", self.select_all, "Ctrl+A", "Selection"),
            ("selection.invert", "Invert Selection", self.invert_selection, "Ctrl+Shift+A", "Selection"),
            ("view.icons", "Icons", lambda: self.set_view_mode(ViewMode.ICONS), "Ctrl+1", "View"),
            ("view.compact", "Compact", lambda: self.set_view_mode(ViewMode.COMPACT), "Ctrl+2", "View"),
            ("view.details", "Details", lambda: self.set_view_mode(ViewMode.DETAILS), "Ctrl+3", "View"),
            ("view.hidden", "Show Hidden Files", self.toggle_hidden, "Ctrl+H", "View"),
            ("view.filter", "Filter Bar", self.toggle_filter_bar, "Ctrl+I", "View"),
            ("view.groups", "Show in Groups", self.toggle_groups, "", "View"),
            ("view.zoom_in", "Zoom In", self.zoom_in, "Ctrl++", "View"),
            ("view.zoom_out", "Zoom Out", self.zoom_out, "Ctrl+-", "View"),
            ("view.zoom_reset", "Reset Zoom", self.zoom_reset, "Ctrl+0", "View"),
            ("search.open", "Search", self.toggle_search_bar, "Ctrl+F", "Search"),
            ("preview.quick_look", "Quick Look", self.quick_look, "Space", "Preview"),
            ("tab.new", "New Tab", self.new_tab, "Ctrl+T", "Tabs"),
            ("tab.close", "Close Tab", self.close_tab, "Ctrl+W", "Tabs"),
            ("tab.reopen", "Reopen Closed Tab", self.reopen_tab, "Ctrl+Shift+T", "Tabs"),
            ("tab.duplicate", "Duplicate Tab", self.duplicate_tab, "", "Tabs"),
            ("tab.move_left", "Move Tab Left", self.move_tab_left, "", "Tabs"),
            ("tab.move_right", "Move Tab Right", self.move_tab_right, "", "Tabs"),
            ("tab.close_left", "Close Tabs to Left", self.close_tabs_left, "", "Tabs"),
            ("tab.close_right", "Close Tabs to Right", self.close_tabs_right, "", "Tabs"),
            ("tab.close_other", "Close Other Tabs", self.close_other_tabs, "", "Tabs"),
            ("tab.next", "Next Tab", self.next_tab, "Ctrl+Tab", "Tabs"),
            ("tab.previous", "Previous Tab", self.previous_tab, "Ctrl+Shift+Tab", "Tabs"),
            ("app.command_palette", "Command Palette", self.show_command_palette, "Ctrl+K", "Application"),
            ("app.settings", "Settings", self.show_settings, "", "Application"),
        )
        for action_id, label, callback, shortcut, category in actions:
            self.actions.register(action_id, label, callback, shortcut=shortcut, category=category)
        terminal_action = self.actions.get("panel.terminal")
        if terminal_action is not None and not TerminalPanel.available():
            terminal_action.label = "Open External Terminal"
        undo_action = self.actions.get("edit.undo")
        redo_action = self.actions.get("edit.redo")
        if undo_action is not None:
            undo_action.enabled = lambda: len(self.operations.undo) > 0
        if redo_action is not None:
            redo_action.enabled = lambda: self.operations.undo.redo_count > 0
        self._sync_undo_labels()

    def _sync_undo_labels(self) -> None:
        undo = self.actions.get("edit.undo")
        redo = self.actions.get("edit.redo")
        if undo is not None:
            label = self.operations.undo.last_label
            undo.label = f"Undo {label}" if label else "Undo"
        if redo is not None:
            label = self.operations.undo.redo_label
            redo.label = f"Redo {label.removeprefix('Redo: ')}" if label else "Redo"

    def _execute_action(self, action_id: str) -> bool:
        """Execute a registered command from keyboard/UI dispatch safely."""

        try:
            self.actions.execute(action_id)
        except Exception as error:
            LOGGER.error("action execution failed id=%s: %s", action_id, error)
            self._set_status(f"Action failed: {error}")
        return True

    def show_command_palette(self, *_args: object) -> None:
        if self.window is None:
            return
        if self.palette_window is not None:
            self.palette_window.present()
            return
        palette_actions = [
            PaletteAction(
                action.action_id,
                action.label,
                lambda action_id=action.action_id: self.actions.execute(action_id),
                action.shortcut,
                action.is_enabled(),
            )
            for action in self.actions.all(include_disabled=True)
        ]
        palette = CommandPalette(self.window, palette_actions)
        self.palette_window = palette
        palette.connect("close-request", lambda *_close: self._clear_palette_reference(palette))
        palette.present()

    def show_settings(self, *_args: object) -> None:
        if self.window is None:
            return
        if self.settings_window is not None:
            self.settings_window.present()
            return
        dialog = SettingsWindow(self.window, self.settings, self.actions, self._apply_settings_from_window)
        self.settings_window = dialog
        dialog.connect("close-request", lambda *_close: self._clear_settings_reference(dialog))
        dialog.present()

    def _clear_settings_reference(self, dialog: SettingsWindow) -> bool:
        if self.settings_window is dialog:
            self.settings_window = None
        return False

    def _apply_settings_from_window(self, values: dict[str, object]) -> None:
        follow_theme_before = bool(self.settings.get("follow_omarchy_theme", True))
        self.settings.update(values)
        self.show_hidden = bool(self.settings.get("show_hidden", self.show_hidden))
        self.folders_first = bool(self.settings.get("folders_first", self.folders_first))
        self.groups_enabled = bool(self.settings.get("show_groups", self.groups_enabled))
        self.group_criterion = normalize_group_criterion(self.settings.get("group_criterion", self.group_criterion.value))
        self.folder_settings = FolderViewSettings(self.settings.get("folder_view_settings"))
        self.set_view_mode(self.settings.get("default_view", self.view_mode), persist=False)
        self.set_zoom(self.settings.get("icon_zoom", self.icon_zoom), persist=False)
        self.set_show_previews(bool(self.settings.get("show_previews", True)), persist=False)
        self.set_show_git_status(bool(self.settings.get("show_git_status", False)), persist=False)
        single_click_open = bool(self.settings.get("single_click_open", False))
        show_selection_marker = bool(self.settings.get("show_selection_marker", False))
        show_tooltips = bool(self.settings.get("show_tooltips", True))
        inline_rename = bool(self.settings.get("inline_rename", True))
        show_additional_info = bool(self.settings.get("show_additional_info", True))
        if self.file_view is not None:
            self.file_view.set_single_click_open(single_click_open)
            self.file_view.set_show_selection_marker(show_selection_marker)
            self.file_view.set_show_tooltips(show_tooltips)
            self.file_view.set_inline_rename(inline_rename)
            self.file_view.set_show_additional_info(show_additional_info)
        if self.split_file_view is not None:
            self.split_file_view.set_single_click_open(single_click_open)
            self.split_file_view.set_show_selection_marker(show_selection_marker)
            self.split_file_view.set_show_tooltips(show_tooltips)
            self.split_file_view.set_inline_rename(inline_rename)
            self.split_file_view.set_show_additional_info(show_additional_info)
        if self.split_paned is not None and self.split_paned.get_parent() is not None:
            self.split_paned.set_position(self._split_position_pixels())
        if self.sidebar_widget is not None:
            show_sidebar = bool(self.settings.get("show_sidebar", True))
            self.sidebar_widget.set_visible(show_sidebar)
            if self.left_paned is not None:
                self.left_paned.set_visible(show_sidebar or self.left_paned.get_end_child() is not None)
        if self.sidebar_separator is not None:
            self.sidebar_separator.set_visible(bool(self.settings.get("show_sidebar", True)))
        if self.workspace_paned is not None:
            self.workspace_paned.set_position(self._sidebar_position_pixels())
        if self.content_paned is not None and self.content_paned.get_end_child() is not None:
            self.content_paned.set_position(self._info_position_pixels())
        if self.status_bar is not None:
            self.status_bar.set_visible(bool(self.settings.get("show_status_bar", True)))
        if self.terminal_panel is not None and self.terminal_paned is not None:
            terminal_visible = bool(self.settings.get("show_terminal_panel", False))
            self.terminal_panel.set_visible(terminal_visible)
            if terminal_visible:
                self.terminal_paned.set_end_child(self.terminal_panel)
                self.terminal_paned.set_position(self._terminal_position_pixels())
                self._spawn_terminal_panel()
            else:
                self.terminal_paned.set_end_child(None)
        else:
            self.settings["show_terminal_panel"] = False
        desired_folders = bool(self.settings.get("show_folders_panel", False))
        folders_visible = (
            self.left_paned is not None
            and self.folders_panel is not None
            and self.left_paned.get_end_child() is self.folders_panel
        )
        if desired_folders != folders_visible:
            self.toggle_folders_panel()
        desired_info = bool(self.settings.get("show_inspector", False))
        info_visible = (
            self.content_paned is not None
            and self.info_panel is not None
            and self.content_paned.get_end_child() is self.info_panel
        )
        info_visible = info_visible or bool(
            self.info_panel is not None
            and self.info_panel.get_parent() is self.workspace_overlay
            and self.info_panel.get_visible()
        )
        if desired_info != info_visible:
            self.toggle_info_panel()
        self._updating_search_options = True
        try:
            if self.search_recursive_check is not None:
                self.search_recursive_check.set_active(bool(self.settings.get("search_recursive", False)))
            if self.search_contents_check is not None:
                self.search_contents_check.set_active(bool(self.settings.get("search_contents", False)))
            if self.search_case_check is not None:
                self.search_case_check.set_active(bool(self.settings.get("search_case_sensitive", False)))
            if self.search_hidden_check is not None:
                self.search_hidden_check.set_active(bool(self.settings.get("search_show_hidden", False)))
        finally:
            self._updating_search_options = False
        if self.search_active:
            self._on_search_changed()
        self._apply_animation_setting()
        if bool(self.settings.get("auto_refresh", True)):
            if self.current_location is not None:
                self._ensure_directory_monitor(self.current_location)
        else:
            self._stop_directory_monitor()
        follow_theme_after = bool(self.settings.get("follow_omarchy_theme", True))
        if follow_theme_after and not follow_theme_before:
            self._start_theme_monitor()
        elif follow_theme_before and not follow_theme_after:
            self._stop_theme_monitor()
        self._sync_view_menu()
        if self.directory_entries_ready:
            self._rerender_cached_entries()
        self._apply_responsive_layout(force=True)
        self.settings.update(self.places_store.to_dict())
        self.settings["folder_view_settings"] = self.folder_settings.to_dict()
        if bool(self.settings.get("remember_location", True)):
            self.settings["last_location"] = self.current_location or self.home
        save_settings(self.settings)

    def _clear_palette_reference(self, palette: CommandPalette) -> bool:
        if self.palette_window is palette:
            self.palette_window = None
        return False

    def do_activate(self) -> None:
        if self.window is None:
            self.build_window()
        assert self.window is not None
        self.window.present()
        if self.current_location is None:
            self.navigate(self.home, record_history=False)

    def do_open(self, files: list[Gio.File], _n_files: int, _hint: str) -> None:
        self.do_activate()
        if files:
            self.navigate(files[0].get_path() or files[0].get_uri())

    def do_shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        self._close_responsive_drawer()
        self.load_generation += 1
        if self.load_cancellable is not None:
            self.load_cancellable.cancel()
        self._cancel_location_completion()
        self._cancel_location_validation()
        self._close_enumerator()
        self._close_context_popover()
        self.sidebar_controller.close_context()
        if self.tab_context_popover is not None:
            self.tab_context_popover.popdown()
            self.tab_context_popover = None
        if self.breadcrumb_popover is not None:
            self.breadcrumb_popover.popdown()
            self.breadcrumb_popover = None
        if self.name_dialog is not None:
            self.name_dialog.close()
            self.name_dialog = None
        if self.confirmation_dialog is not None:
            self.confirmation_dialog.close()
            self.confirmation_dialog = None
        if self.conflict_dialog is not None:
            self.conflict_dialog.close()
            self.conflict_dialog = None
        if self.metadata_dialog is not None:
            self.metadata_dialog.close()
            self.metadata_dialog = None
        if self.git_dialog is not None:
            self.git_dialog.close()
            self.git_dialog = None
        self.pending_conflict = None
        if self.settings_window is not None:
            self.settings_window.close()
            self.settings_window = None
        self.close_quick_look()
        self._stop_directory_monitor()
        self._stop_split_view()
        if self.device_monitor is not None:
            self.device_monitor.stop()
            self.device_monitor = None
        self._stop_theme_monitor()
        if self.geometry_save_source:
            GLib.source_remove(self.geometry_save_source)
            self.geometry_save_source = 0
        if self.breadcrumb_resize_source:
            GLib.source_remove(self.breadcrumb_resize_source)
            self.breadcrumb_resize_source = 0
        for source_name in (
            "sidebar_position_save_source",
            "folders_position_save_source",
            "info_position_save_source",
            "terminal_position_save_source",
        ):
            source = getattr(self, source_name, 0)
            if source:
                save_callback = {
                    "sidebar_position_save_source": self._save_sidebar_position,
                    "folders_position_save_source": self._save_folders_position,
                    "info_position_save_source": self._save_info_position,
                    "terminal_position_save_source": self._save_terminal_position,
                }[source_name]
                save_callback()
                source = getattr(self, source_name, 0) or source
                GLib.source_remove(source)
                setattr(self, source_name, 0)
        self._save_window_geometry()
        if self.split_position_save_source:
            GLib.source_remove(self.split_position_save_source)
            self.split_position_save_source = 0
        self._save_split_position()
        if bool(self.settings.get("remember_tabs", True)):
            self._sync_active_tab_state()
            self.settings["session_tabs"] = self._session_tabs_payload()
            self.settings["active_tab"] = self.active_tab_index
        self.settings.update(self.places_store.to_dict())
        self.settings["location_history"] = list(self.location_history[:24])
        self.settings["folder_view_settings"] = self.folder_settings.to_dict()
        if bool(self.settings.get("remember_location", True)):
            self.settings["last_location"] = self.current_location or self.home
        save_settings(self.settings)
        self.search_service.shutdown()
        self.preview_manager.shutdown()
        self.thumbnail_manager.shutdown()
        self.operation_controller.shutdown()
        self.operations.shutdown()
        Gio.Application.do_shutdown(self)

    def _start_theme_monitor(self) -> None:
        if self.css_provider is None:
            return
        self._stop_theme_monitor()
        self.theme_watcher = ThemeWatcher(self._on_theme_changed)
        self.theme_watcher.start()

    def _stop_theme_monitor(self) -> None:
        if self.theme_reload_source:
            GLib.source_remove(self.theme_reload_source)
            self.theme_reload_source = 0
        if self.theme_watcher is not None:
            self.theme_watcher.stop()
            self.theme_watcher = None

    def _on_window_geometry_changed(self, *_args: object) -> None:
        if self.shutting_down:
            return
        self._apply_responsive_layout()
        if not bool(self.settings.get("remember_window_size", True)):
            return
        if self.geometry_save_source:
            GLib.source_remove(self.geometry_save_source)
        self.geometry_save_source = GLib.timeout_add(250, self._save_window_geometry)

    def _save_window_geometry(self) -> bool:
        self.geometry_save_source = 0
        if self.window is None or not bool(self.settings.get("remember_window_size", True)):
            return False
        width = self.window.get_width()
        height = self.window.get_height()
        if width >= 360:
            self.settings["window_width"] = width
        if height >= 280:
            self.settings["window_height"] = height
        self.settings["window_maximized"] = bool(self.window.is_maximized())
        save_settings(self.settings)
        return False

    def _responsive_window_size(self) -> tuple[int, int]:
        if self.window is None:
            return (
                int(self.settings.get("window_width", 1320)),
                int(self.settings.get("window_height", 820)),
            )
        width = self.window.get_width()
        height = self.window.get_height()
        if width <= 0:
            width = int(self.settings.get("window_width", 1320))
        if height <= 0:
            height = int(self.settings.get("window_height", 820))
        return max(0, width), max(0, height)

    def _apply_responsive_layout(self, *, force: bool = False) -> None:
        """Apply presentation changes only when a responsive state changes."""

        width, height = self._responsive_window_size()
        mode = responsive_mode_for_width(width, self.responsive_mode)
        if force or mode is not self.responsive_mode:
            self._apply_responsive_mode(mode)

        short_height = (
            height < 440
            if not self.responsive_short_height
            else height < 480
        )
        if force or short_height != self.responsive_short_height:
            self.responsive_short_height = short_height
            self._apply_responsive_vertical_presentation()
            self._apply_responsive_topbar()

        self._update_location_completion_width()
        if getattr(self, "panel_controls", None) is not None:
            self.panel_controls.sync()

    def _apply_responsive_mode(self, mode: ResponsiveMode) -> None:
        self.responsive_mode = mode
        for widget in (self.window, self.root_widget):
            if widget is None:
                continue
            for name in ("responsive-wide", "responsive-medium", "responsive-narrow"):
                widget.remove_css_class(name)
            widget.add_css_class(f"responsive-{mode.value}")

        self._apply_responsive_topbar()
        self._apply_responsive_sidebar()
        self._apply_responsive_optional_panels()
        for view in (self.file_view, self.split_file_view):
            if view is not None:
                view.set_responsive_mode(mode.value)
        self._render_breadcrumbs()

    def _apply_responsive_topbar(self) -> None:
        mode = self.responsive_mode or ResponsiveMode.WIDE
        narrow = mode_uses_sidebar_overlay(mode)
        if self.sidebar_toggle_button is not None:
            # PanelControlsController provides the single responsive sidebar
            # affordance.  This old left-side drawer button remains hidden for
            # compatibility with callers that still keep the reference.
            self.sidebar_toggle_button.set_visible(False)
        if self.forward_button is not None:
            self.forward_button.set_visible(not narrow)
        if self.up_button is not None:
            self.up_button.set_visible(True)
        if self.status_label is not None:
            self.status_label.set_visible(mode is ResponsiveMode.WIDE and not self.responsive_short_height)
        if self.view_menu_button is not None:
            self.view_menu_button.set_tooltip_text("View options")
        if self.status_bar is not None:
            self.status_bar.set_visible(
                bool(self.settings.get("show_status_bar", True)) and not self.responsive_short_height
            )
        if getattr(self, "panel_controls", None) is not None:
            self.panel_controls.sync()

    def _apply_responsive_sidebar(self) -> None:
        if self.workspace_paned is None or self.left_paned is None or self.workspace_overlay is None:
            return
        mode = self.responsive_mode or ResponsiveMode.WIDE
        overlay = mode_uses_sidebar_overlay(mode)
        wants_sidebar = bool(
            self.settings.get("show_sidebar", True)
            or self.settings.get("show_folders_panel", False)
        )
        if overlay:
            if self.workspace_paned.get_start_child() is self.left_paned:
                self.workspace_paned.set_start_child(None)
            if self.left_paned.get_parent() is not self.workspace_overlay:
                self.workspace_overlay.add_overlay(self.left_paned)
            self.left_paned.add_css_class("responsive-sidebar-drawer")
            self.left_paned.set_halign(Gtk.Align.START)
            self.left_paned.set_valign(Gtk.Align.FILL)
            self.left_paned.set_hexpand(False)
            self.left_paned.set_vexpand(True)
            self.left_paned.set_visible(self.responsive_sidebar_drawer_open and wants_sidebar)
            if not wants_sidebar:
                self._close_responsive_drawer()
            return

        self._close_responsive_drawer()
        if self.left_paned.get_parent() is self.workspace_overlay:
            self.workspace_overlay.remove_overlay(self.left_paned)
        if self.workspace_paned.get_start_child() is None:
            self.workspace_paned.set_start_child(self.left_paned)
        self.left_paned.remove_css_class("responsive-sidebar-drawer")
        self.left_paned.set_halign(Gtk.Align.FILL)
        self.left_paned.set_hexpand(False)
        self.left_paned.set_visible(wants_sidebar or self.left_paned.get_end_child() is not None)
        self.workspace_paned.set_position(self._sidebar_position_pixels())

    def _apply_responsive_optional_panels(self) -> None:
        mode = self.responsive_mode or ResponsiveMode.WIDE
        narrow = mode_uses_sidebar_overlay(mode)
        constrained_height = self.responsive_short_height
        if self.content_paned is not None and self.info_panel is not None:
            if narrow or constrained_height:
                if self.content_paned.get_end_child() is self.info_panel:
                    self.content_paned.set_end_child(None)
                    self.responsive_info_suppressed = True
            else:
                if self.responsive_drawer_widget is self.info_panel:
                    self._close_responsive_drawer()
                if self.info_panel.get_parent() is self.workspace_overlay and self.workspace_overlay is not None:
                    self.workspace_overlay.remove_overlay(self.info_panel)
                if bool(self.settings.get("show_inspector", False)) and self.content_paned.get_end_child() is None:
                    self.content_paned.set_end_child(self.info_panel)
                    self.content_paned.set_position(self._info_position_pixels())
                self.responsive_info_suppressed = False

        should_suppress_terminal = narrow or self.responsive_short_height
        if self.terminal_paned is not None and self.terminal_panel is not None:
            if should_suppress_terminal:
                if self.terminal_paned.get_end_child() is self.terminal_panel:
                    self.terminal_paned.set_end_child(None)
                    self.responsive_terminal_suppressed = True
            elif bool(self.settings.get("show_terminal_panel", False)):
                if self.terminal_panel.get_parent() is self.workspace_overlay and self.workspace_overlay is not None:
                    self.workspace_overlay.remove_overlay(self.terminal_panel)
                if self.terminal_paned.get_end_child() is None:
                    self.terminal_paned.set_end_child(self.terminal_panel)
                self.responsive_terminal_suppressed = False
                self.terminal_paned.set_position(self._terminal_position_pixels())
            elif self.terminal_paned.get_end_child() is self.terminal_panel:
                self.terminal_paned.set_end_child(None)
                self.terminal_panel.set_visible(False)
                self.responsive_terminal_suppressed = False

    def _apply_responsive_vertical_presentation(self) -> None:
        self._apply_responsive_topbar()
        self._apply_responsive_optional_panels()

    def _ensure_responsive_backdrop(self) -> Gtk.Widget | None:
        if self.workspace_overlay is None:
            return None
        if self.responsive_drawer_backdrop is None:
            backdrop = Gtk.Box()
            backdrop.add_css_class("responsive-drawer-backdrop")
            backdrop.set_hexpand(True)
            backdrop.set_vexpand(True)
            backdrop.set_can_target(True)
            gesture = Gtk.GestureClick()
            gesture.set_button(1)
            gesture.connect("pressed", self._on_responsive_backdrop_pressed)
            backdrop.add_controller(gesture)
            self.responsive_drawer_backdrop = backdrop
        if self.responsive_drawer_backdrop.get_parent() is not self.workspace_overlay:
            self.workspace_overlay.add_overlay(self.responsive_drawer_backdrop)
        return self.responsive_drawer_backdrop

    def _show_responsive_drawer(self, widget: Gtk.Widget, *, info: bool = False) -> None:
        if self.workspace_overlay is None:
            return
        self._close_responsive_drawer()
        if widget.get_parent() is not self.workspace_overlay:
            self.workspace_overlay.add_overlay(widget)
        widget.add_css_class("responsive-drawer")
        widget.set_halign(Gtk.Align.END if info else Gtk.Align.START)
        widget.set_valign(Gtk.Align.FILL)
        widget.set_hexpand(False)
        widget.set_vexpand(True)
        backdrop = self._ensure_responsive_backdrop()
        if backdrop is not None:
            backdrop.set_visible(True)
        widget.set_visible(True)
        self.responsive_drawer_widget = widget
        self.responsive_info_drawer_open = info
        self.responsive_sidebar_drawer_open = not info

    def _close_responsive_drawer(self) -> None:
        if self.responsive_drawer_backdrop is not None:
            self.responsive_drawer_backdrop.set_visible(False)
        widget = self.responsive_drawer_widget
        if widget is not None:
            mode = self.responsive_mode or ResponsiveMode.WIDE
            if mode_uses_sidebar_overlay(mode) or self.responsive_short_height:
                widget.set_visible(False)
        self.responsive_drawer_widget = None
        self.responsive_sidebar_drawer_open = False
        self.responsive_info_drawer_open = False
        if getattr(self, "panel_controls", None) is not None:
            self.panel_controls.sync()

    def _on_responsive_backdrop_pressed(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
    ) -> None:
        self._close_responsive_drawer()

    def panel_terminal_available(self) -> bool:
        """Report whether the optional embedded terminal really exists."""

        if self.terminal_panel is not None:
            return bool(self.terminal_panel.embedded)
        return TerminalPanel.available()

    def panel_control_visible(self, panel_id: str) -> bool:
        """Return actual presentation state for the panel-controls surface."""

        try:
            selected = PanelId(panel_id)
        except ValueError:
            return False
        mode = self.responsive_mode or ResponsiveMode.WIDE
        overlay = mode_uses_sidebar_overlay(mode)

        if selected is PanelId.SIDEBAR:
            if overlay:
                return bool(
                    self.left_paned is not None
                    and self.responsive_drawer_widget is self.left_paned
                    and self.responsive_sidebar_drawer_open
                    and self.left_paned.get_visible()
                    and self.settings.get("show_sidebar", True)
                )
            return bool(
                self.sidebar_widget is not None
                and self.sidebar_widget.get_visible()
                and self.settings.get("show_sidebar", True)
            )

        if selected is PanelId.FOLDERS:
            if self.left_paned is None or self.folders_panel is None:
                return False
            attached = self.left_paned.get_end_child() is self.folders_panel
            return bool(attached and (not overlay or self.left_paned.get_visible()))

        if selected is PanelId.INFORMATION:
            if self.info_panel is None or self.content_paned is None:
                return False
            shown_in_content = self.content_paned.get_end_child() is self.info_panel
            shown_as_drawer = (
                self.responsive_drawer_widget is self.info_panel
                and self.responsive_info_drawer_open
                and self.info_panel.get_visible()
            )
            return bool(shown_in_content or shown_as_drawer)

        if selected is PanelId.TERMINAL:
            return bool(
                self.panel_terminal_available()
                and self.terminal_panel is not None
                and self.terminal_paned is not None
                and self.terminal_paned.get_end_child() is self.terminal_panel
                and self.terminal_panel.get_visible()
            )
        return False

    def toggle_sidebar_panel(self, *_args: object) -> None:
        """Toggle the wide sidebar or its responsive drawer presentation."""

        mode = self.responsive_mode or ResponsiveMode.WIDE
        if mode_uses_sidebar_overlay(mode):
            drawer_open = self.responsive_drawer_widget is self.left_paned
            sidebar_enabled = bool(self.settings.get("show_sidebar", True))
            if drawer_open and not sidebar_enabled:
                # F7 may have opened the shared drawer while Places is
                # disabled.  F9 should enable Places in that open drawer
                # instead of unexpectedly closing it.
                self.settings["show_sidebar"] = True
                if self.sidebar_widget is not None:
                    self.sidebar_widget.set_visible(True)
                save_settings(self.settings)
                self._apply_responsive_layout(force=True)
                return
            if not bool(
                self.settings.get("show_sidebar", True)
                or self.settings.get("show_folders_panel", False)
            ):
                # A sidebar intentionally hidden in a wide layout remains
                # discoverable after a resize: F9 first restores the user's
                # sidebar intent, then opens the responsive drawer.
                self.settings["show_sidebar"] = True
                save_settings(self.settings)
                self._apply_responsive_layout(force=True)
            self.toggle_sidebar_drawer()
            return
        if self.left_paned is None or self.sidebar_widget is None:
            return
        visible = not bool(self.settings.get("show_sidebar", True))
        self.settings["show_sidebar"] = visible
        self.sidebar_widget.set_visible(visible)
        if self.sidebar_separator is not None:
            self.sidebar_separator.set_visible(visible)
        self.left_paned.set_visible(visible or self.left_paned.get_end_child() is not None)
        save_settings(self.settings)
        self._set_status("Sidebar shown" if visible else "Sidebar hidden")
        self._apply_responsive_layout(force=True)

    def toggle_sidebar_drawer(self, *_args: object) -> None:
        mode = self.responsive_mode or ResponsiveMode.WIDE
        if not mode_uses_sidebar_overlay(mode) or self.left_paned is None:
            return
        if self.responsive_drawer_widget is self.left_paned:
            self._close_responsive_drawer()
            return
        if bool(self.settings.get("show_sidebar", True) or self.settings.get("show_folders_panel", False)):
            self._show_responsive_drawer(self.left_paned)
        if getattr(self, "panel_controls", None) is not None:
            self.panel_controls.sync()

    def _update_location_completion_width(self) -> None:
        if self.location_completion_list is None or self.location_entry is None:
            return
        available = self.location_entry.get_width()
        requested = 0 if available <= 0 else max(0, min(320, available - 16))
        if requested == self.location_completion_width_request:
            return
        self.location_completion_width_request = requested
        self.location_completion_list.set_size_request(requested, -1)

    def _apply_animation_setting(self) -> None:
        if self.window is None:
            return
        enabled = bool(self.settings.get("animations", True))
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings is not None:
            try:
                enabled = enabled and bool(gtk_settings.get_property("gtk-enable-animations"))
            except (AttributeError, TypeError):
                pass
        if enabled:
            self.window.remove_css_class("animations-disabled")
        else:
            self.window.add_css_class("animations-disabled")

    def _ensure_directory_monitor(self, location: str) -> None:
        """Keep one monitor attached to the directory currently being loaded."""

        if self.shutting_down:
            return
        if not bool(self.settings.get("auto_refresh", True)):
            self._stop_directory_monitor()
            return
        if (
            self.directory_monitor is not None
            and self.directory_monitor.is_running
            and self.directory_monitor_location == location
        ):
            return
        self._stop_directory_monitor()
        monitor = DirectoryMonitor(self._on_directory_changed)
        if monitor.start(self.current_file(location), location):
            self.directory_monitor = monitor
            self.directory_monitor_location = location

    def _stop_directory_monitor(self) -> None:
        if self.directory_monitor is not None:
            self.directory_monitor.stop()
            self.directory_monitor = None
        self.directory_monitor_location = None

    def _on_directory_changed(self, location: str, event: str) -> None:
        if self.shutting_down:
            return
        if location != self.current_location:
            LOGGER.debug(
                "filesystem refresh ignored stale location=%s current=%s event=%s",
                location,
                self.current_location,
                event,
            )
            return
        LOGGER.debug("filesystem refresh requested location=%s event=%s", location, event)
        self._last_refresh_request = (location, time.monotonic())
        self.load_directory(location, preserve_selection=True)

    def _on_theme_changed(self, event: str = "unknown", source: str = "unknown") -> None:
        if self.shutting_down:
            return
        LOGGER.debug("theme event=%s source=%s", event, source)
        if self.theme_reload_source:
            GLib.source_remove(self.theme_reload_source)
        self.theme_reload_source = GLib.timeout_add(100, self.reload_theme)
        LOGGER.debug("theme reload scheduled")

    def reload_theme(self) -> bool:
        """Atomically apply the latest valid palette/font to one live provider."""

        self.theme_reload_source = 0
        if self.shutting_down:
            return False
        snapshot = read_theme_snapshot()
        if snapshot is None:
            if not self.theme_reload_failure_logged:
                LOGGER.warning("theme reload skipped; keeping last valid theme")
                self.theme_reload_failure_logged = True
            self.theme_reload_source = GLib.timeout_add(200, self.reload_theme)
            return False

        css = build_css(snapshot.colors, snapshot.font)
        if self.css_provider is not None:
            try:
                self.css_provider.load_from_string(css)
            except Exception as error:
                LOGGER.error("theme CSS reload failed; keeping last valid theme: %s", error)
                self.theme_reload_source = GLib.timeout_add(200, self.reload_theme)
                return False

        self.theme_name = snapshot.name
        self.colors = snapshot.colors
        self.font_family = snapshot.font
        self.theme_reload_failure_logged = False
        LOGGER.debug(
            "theme reloaded name=%s accent=%s font=%s",
            self.theme_name,
            self.colors.get("accent", "unknown"),
            self.font_family,
        )
        return False

    def build_window(self) -> None:
        if self.window is not None:
            return
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.add_css_class("omapath-window")
        self.window.set_title("Omapath")
        width = int(self.settings.get("window_width", 1320)) if bool(self.settings.get("remember_window_size", True)) else 1100
        height = int(self.settings.get("window_height", 820)) if bool(self.settings.get("remember_window_size", True)) else 700
        self.window.set_default_size(max(360, width), max(280, height))
        self.window.connect("notify::width", self._on_window_geometry_changed)
        self.window.connect("notify::height", self._on_window_geometry_changed)
        self.window.connect("notify::maximized", self._on_window_geometry_changed)

        display = Gdk.Display.get_default()
        if display is not None:
            self.css_provider = Gtk.CssProvider()
            self.css_provider.load_from_string(build_css(self.colors, self.font_family))
            Gtk.StyleContext.add_provider_for_display(
                display,
                self.css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root_widget = root
        root.add_css_class("simple-shell")
        root.append(self.build_topbar())
        self.tab_bar = self._build_tab_bar()
        root.append(self.tab_bar)

        workspace = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        workspace.add_css_class("workspace-paned")
        workspace.set_hexpand(True)
        workspace.set_vexpand(True)
        workspace.set_wide_handle(True)
        workspace.set_resize_start_child(False)
        workspace.set_resize_end_child(True)
        workspace.set_shrink_start_child(True)
        workspace.set_shrink_end_child(False)
        self.workspace_paned = workspace
        workspace.connect("notify::position", self._on_sidebar_position_changed)

        self.sidebar_widget = self.build_sidebar()
        self.sidebar_widget.set_visible(bool(self.settings.get("show_sidebar", True)))
        self.sidebar_scroll = self.sidebar_widget if isinstance(self.sidebar_widget, Gtk.ScrolledWindow) else None
        left_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        left_paned.add_css_class("left-panel-paned")
        left_paned.set_hexpand(False)
        left_paned.set_vexpand(True)
        left_paned.set_wide_handle(True)
        left_paned.set_resize_start_child(False)
        left_paned.set_resize_end_child(True)
        left_paned.set_shrink_start_child(True)
        left_paned.set_shrink_end_child(True)
        left_paned.set_start_child(self.sidebar_widget)
        left_paned.set_visible(bool(self.settings.get("show_sidebar", True)))
        self.left_paned = left_paned
        left_paned.connect("notify::position", self._on_folders_position_changed)
        workspace.set_start_child(left_paned)

        self.file_view = SimpleFileView(
            self.activate_row,
            self.show_context_menu,
            zoom=self.icon_zoom,
            open_new_tab=self.open_row_new_tab,
            mode=self.view_mode,
            drop_callback=lambda value, row, target: self.handle_drop(value, row, target, pane=0),
            drop_feedback=self._main_drop_feedback,
            group_toggle=lambda key: (self._activate_pane(0), self.toggle_group(key)),
            group_context_menu=lambda row, x, y: (self._activate_pane(0), self.show_group_context_menu(row, x, y)),
            pane_activate=lambda: self._activate_pane(0),
            show_previews=self.show_previews,
            thumbnail_manager=self.thumbnail_manager,
            single_click_open=bool(self.settings.get("single_click_open", False)),
            show_selection_marker=bool(self.settings.get("show_selection_marker", False)),
            show_tooltips=bool(self.settings.get("show_tooltips", True)),
            inline_rename=bool(self.settings.get("inline_rename", True)),
            show_additional_info=bool(self.settings.get("show_additional_info", True)),
            rename_callback=self._on_inline_rename,
        )
        self.file_view.selection.connect("selection-changed", lambda *_args: self._refresh_info_panel())
        self._activate_pane(0)
        file_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        file_area.set_hexpand(True)
        file_area.set_vexpand(True)
        self.body = file_area
        file_area.append(self.file_view)

        self.file_area = file_area
        # The default shell has one sidebar split and one file area.  Optional
        # information/terminal panes are inserted lazily by their actions so
        # they cannot reserve space or affect GridView measurement at launch.
        workspace.set_end_child(file_area)
        workspace.set_position(self._sidebar_position_pixels())
        self.terminal_panel = None
        if not TerminalPanel.available():
            self.settings["show_terminal_panel"] = False

        workspace_overlay = Gtk.Overlay()
        workspace_overlay.add_css_class("workspace-overlay")
        workspace_overlay.set_hexpand(True)
        workspace_overlay.set_vexpand(True)
        workspace_overlay.set_child(workspace)
        self.workspace_overlay = workspace_overlay
        root.append(workspace_overlay)
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_bar.add_css_class("status-bar")
        self.status_bar = status_bar
        status_bar.set_visible(bool(self.settings.get("show_status_bar", True)))
        self.status_bar_label = Gtk.Label(label="Ready")
        self.status_bar_label.set_halign(Gtk.Align.START)
        self.status_bar_label.set_hexpand(True)
        self.status_bar_label.add_css_class("item-count")
        status_bar.append(self.status_bar_label)
        status_bar.append(self.operation_controller.build())
        root.append(status_bar)
        self.window.set_child(root)
        self._apply_animation_setting()
        if bool(self.settings.get("window_maximized", False)):
            self.window.maximize()
        self._start_device_monitor()

        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self._on_key_pressed)
        self.window.add_controller(self.key_controller)

        self.update_navigation_ui()
        startup_location = self.home
        start_mode = self.settings.get("start_location")
        if start_mode == "custom":
            configured = str(self.settings.get("startup_location") or "").strip()
            if configured:
                candidate = self.normalize_location(configured)
                if candidate == TRASH_URI or candidate == RECENT_URI or self.is_local_directory(candidate):
                    startup_location = candidate
        elif bool(self.settings.get("remember_location", True)) and start_mode == "last":
            session_values = self.settings.get("session_tabs")
            has_session = (
                bool(self.settings.get("remember_tabs", True))
                and isinstance(session_values, list)
                and bool(session_values)
            )
            saved_location = self.settings.get("last_location")
            if not has_session and isinstance(saved_location, str) and saved_location:
                startup_location = self.normalize_location(saved_location)
            else:
                startup_location = self.current_location or self.home
        self.navigate(startup_location, record_history=False)
        if bool(self.settings.get("show_terminal_panel", False)) and TerminalPanel.available():
            self.toggle_terminal_panel()
        if bool(self.settings.get("split_view", False)):
            self.toggle_split_view()
        if bool(self.settings.get("show_folders_panel", False)):
            self.toggle_folders_panel()
        if bool(self.settings.get("show_inspector", False)):
            self.toggle_info_panel()
        self._apply_responsive_layout(force=True)
        self._start_theme_monitor()

    def build_filter_bar(self) -> Gtk.Box:
        return self.search_controls.build_filter_bar()

    def toggle_filter_bar(self, *_args: object) -> None:
        if self.filter_bar is None or self.filter_entry is None:
            return
        if self.location_stack is None:
            return
        visible = self.location_stack.get_visible_child_name() != "filter"
        if visible and self.location_stack.get_visible_child_name() == "search":
            self.close_search(reload=False)
        self.location_stack.set_visible_child_name("filter" if visible else "breadcrumbs")
        if visible:
            if self.active_pane == 1 and self.split_pane_state is not None:
                self.filter_entry.set_text(self.split_pane_state.filter_query)
            self.filter_entry.grab_focus()
            self.filter_entry.select_region(0, -1)
        else:
            self.filter_entry.set_text("")
            if self.active_pane == 1 and self.split_pane_state is not None:
                self.split_pane_state.filter_query = ""
                self._rerender_split_cached_entries()
            else:
                self.filter_query = ""
                self.filter_error = None
                self._rerender_cached_entries()

    def _on_filter_changed(self, entry: Gtk.SearchEntry) -> None:
        if getattr(self, "active_pane", 0) == 1 and getattr(self, "split_pane_state", None) is not None:
            self.split_pane_state.filter_query = entry.get_text()
            if not self._restoring_tab_state:
                self._rerender_split_cached_entries()
            return
        self.filter_query = entry.get_text()
        if not self._restoring_tab_state:
            self._rerender_cached_entries()

    def _on_filter_mode_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.filter_mode = combo.get_active_id() or "plain"
            if not self._restoring_tab_state:
                self._rerender_split_cached_entries()
            return
        self.filter_mode = normalize_filter_mode(combo.get_active_id())
        if not self._restoring_tab_state:
            self._rerender_cached_entries()

    def _update_filter_error(self) -> None:
        if self.filter_error_label is None:
            return
        self.filter_error_label.set_text(self.filter_error or "")
        self.filter_error_label.set_visible(bool(self.filter_error))

    def build_search_bar(self) -> Gtk.Box:
        return self.search_controls.build_search_bar()

    def toggle_search_bar(self, *_args: object) -> None:
        if self.search_bar is None or self.search_entry is None:
            return
        visible = self.location_stack is None or self.location_stack.get_visible_child_name() != "search"
        if visible:
            if self.location_stack is not None:
                self.location_stack.set_visible_child_name("search")
            self.search_pane = 1 if self.active_pane == 1 and self.split_pane_state is not None else 0
            if self.search_pane == 1 and self.split_pane_state is not None:
                query = self.split_pane_state.search_query
                if self.search_entry.get_text() != query:
                    self._updating_search_entry = True
                    try:
                        self.search_entry.set_text(query)
                    finally:
                        self._updating_search_entry = False
            self.search_entry.grab_focus()
            self.search_entry.select_region(0, -1)
        else:
            self.close_search()

    def search_for_tag(self, tag: str) -> None:
        """Open the compact search surface for a sidebar tag."""

        if self.search_bar is None or self.search_entry is None:
            return
        self.search_pane = 1 if self.active_pane == 1 and self.split_pane_state is not None else 0
        if self.location_stack is not None:
            self.location_stack.set_visible_child_name("search")
        self.search_entry.set_text(f"tag:{tag}")
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def _on_search_changed(self, *_args: object) -> None:
        if self.search_entry is None or self._updating_search_entry or self._updating_search_options:
            return
        raw_query = self.search_entry.get_text()
        query_text, tags, minimum_rating = parse_search_query(raw_query)
        if not raw_query:
            self.close_search()
            return
        target_pane = self.search_pane
        split_state = self.split_pane_state if target_pane == 1 else None
        location = split_state.location if split_state is not None else (self.current_location or self.home)
        if location == TRASH_URI or "://" in location:
            self._set_status("Recursive search is unavailable for this location")
            return
        self.search_active = True
        self.search_location = location
        if split_state is not None:
            split_state.search_query = raw_query
        self.search_tags = tags
        self.search_min_rating = minimum_rating
        self.settings["search_recursive"] = self.search_recursive_check.get_active() if self.search_recursive_check is not None else True
        self.settings["search_contents"] = self.search_contents_check.get_active() if self.search_contents_check is not None else False
        self.settings["search_case_sensitive"] = self.search_case_check.get_active() if self.search_case_check is not None else False
        self.settings["search_show_hidden"] = self.search_hidden_check.get_active() if self.search_hidden_check is not None else False
        self.search_generation += 1
        generation = self.search_generation
        self.search_service.search(
            SearchQuery(
                root=location,
                text=query_text,
                recursive=self.search_recursive_check.get_active() if self.search_recursive_check is not None else True,
                case_sensitive=self.search_case_check.get_active() if self.search_case_check is not None else False,
                include_hidden=self.search_hidden_check.get_active() if self.search_hidden_check is not None else False,
                search_contents=self.search_contents_check.get_active() if self.search_contents_check is not None else False,
            ),
            lambda paths, token=generation: self._commit_search_results(token, paths),
            on_error=lambda error, token=generation: self._show_search_error(token, error),
            scheduler=lambda callback, *args: GLib.idle_add(callback, *args),
        )
        self._set_status("Searching…")

    def close_search(self, *_args: object, reload: bool = True) -> None:
        self.search_generation += 1
        self.search_service.cancel()
        was_active = self.search_active
        self.search_active = False
        if self.search_entry is not None:
            self._updating_search_entry = True
            try:
                self.search_entry.set_text("")
            finally:
                self._updating_search_entry = False
        if self.location_stack is not None:
            self.location_stack.set_visible_child_name("breadcrumbs")
        self.search_tags = ()
        self.search_min_rating = None
        target_pane = self.search_pane
        if target_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.search_query = ""
        self.search_location = None
        if not reload:
            return
        if was_active and target_pane == 1 and self.split_file_view is not None and self.split_pane_state is not None:
            self._load_split_directory(self.split_pane_state.location)
        elif was_active and self.file_view is not None:
            self.load_directory(self.current_location or self.home, preserve_selection=True)

    def _show_search_error(self, generation: int, error: Exception) -> None:
        if generation != self.search_generation:
            return
        self._set_status(f"Search failed: {error}")

    def _commit_search_results(self, generation: int, paths: list[str]) -> None:
        target_view = self.split_file_view if self.search_pane == 1 else self.file_view
        if generation != self.search_generation or not self.search_active or target_view is None:
            return
        location = self.search_location or (self.current_location or self.home)
        rows: list[FileRow] = []
        for raw_path in paths:
            path = os.path.abspath(raw_path)
            file = Gio.File.new_for_path(path)
            metadata = self.metadata_store.get(file.get_uri())
            normalized_tags = {tag.casefold() for tag in metadata.tags}
            if self.search_tags and not all(tag.casefold() in normalized_tags for tag in self.search_tags):
                continue
            if self.search_min_rating is not None and metadata.rating < self.search_min_rating:
                continue
            try:
                info = file.query_info(FILE_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
                file_type = info.get_file_type()
                size = int(info.get_attribute_uint64("standard::size"))
                modified = int(info.get_attribute_uint64("time::modified")) or None
                content_type = info.get_content_type() or "application/octet-stream"
                display_name = info.get_display_name() or os.path.basename(path)
            except GLib.Error:
                try:
                    stat_result = os.lstat(path)
                    file_type = Gio.FileType.DIRECTORY if os.path.isdir(path) else Gio.FileType.REGULAR
                    size = int(stat_result.st_size)
                    modified = int(stat_result.st_mtime)
                except OSError:
                    continue
                content_type = "inode/directory" if file_type == Gio.FileType.DIRECTORY else "application/octet-stream"
                display_name = os.path.basename(path)
            rows.append(
                FileRow(
                    file,
                    os.path.basename(path),
                    display_name,
                    file_type == Gio.FileType.DIRECTORY,
                    content_type,
                    os.path.basename(path).startswith("."),
                    size,
                    modified,
                    tags=tuple(metadata.tags),
                    rating=metadata.rating,
                    git_status=self._git_status_for_location(location).get(path, ""),
                )
            )
        target_view.replace_rows(rows)
        target_view.set_empty_visible(not rows)
        self._set_status(f"Search: {len(rows)} result(s)")

    def open_parent_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one search result")
            return
        parent = rows[0].file.get_parent()
        if parent is not None:
            location = parent.get_path() or parent.get_uri()
            if self.search_pane == 1 and self.split_pane_state is not None:
                self._navigate_split(location)
            else:
                self.navigate(location)

    def _build_tab_bar(self) -> Gtk.Box:
        return self.tab_bar_controller.build()

    def _render_tab_bar(self) -> None:
        self.tab_bar_controller.render()

    def _on_tab_middle_pressed(self, gesture: Gtk.GestureClick, index: int) -> None:
        self.tab_bar_controller.on_middle_pressed(gesture, index)

    def _on_tab_context_pressed(
        self,
        gesture: Gtk.GestureClick,
        _count: int,
        x: float,
        y: float,
        index: int,
        parent: Gtk.Widget,
    ) -> None:
        self.tab_bar_controller.on_context_pressed(gesture, _count, x, y, index, parent)

    @staticmethod
    def _tab_popover_coordinates(
        source: Gtk.Widget,
        target: Gtk.Widget,
        x: float,
        y: float,
    ) -> tuple[float, float]:
        return TabBarController._coordinates(source, target, x, y)

    def _close_tab_context_popover(self) -> None:
        self.tab_bar_controller.close_context()

    def _tab_label(self, location: str) -> str:
        return self.tab_bar_controller.tab_label(location)

    def _active_tab_state(self) -> TabState:
        return self.tabs[self.active_tab_index]

    def _sync_active_tab_state(self) -> None:
        if not self.tabs:
            return
        tab = self._active_tab_state()
        tab.location = self.current_location or tab.location
        tab.back_history = list(self.back_history)
        tab.forward_history = list(self.forward_history)
        tab.show_hidden = self.show_hidden
        tab.sort_key = self.sort_key
        tab.sort_descending = self.sort_descending
        tab.folders_first = self.folders_first
        tab.icon_zoom = self.icon_zoom
        tab.view_mode = self.view_mode.value
        tab.filter_query = self.filter_query
        tab.filter_mode = self.filter_mode.value
        tab.group_criterion = self.group_criterion.value
        tab.groups_enabled = self.groups_enabled
        tab.collapsed_groups = sorted(self.collapsed_groups)
        tab.show_previews = self.show_previews
        if self.file_view is not None:
            tab.selected_locations = sorted(self.file_view.selected_locations())
            tab.scroll_position = self._scroll_position(self.file_view)

    def _load_active_tab_state(self) -> None:
        tab = self._active_tab_state()
        self.current_location = tab.location
        self.back_history = list(tab.back_history)
        self.forward_history = list(tab.forward_history)
        self.show_hidden = tab.show_hidden
        self.sort_key = normalize_sort_key(tab.sort_key)
        self.sort_descending = bool(tab.sort_descending)
        self.folders_first = bool(tab.folders_first)
        self.icon_zoom = normalize_zoom(tab.icon_zoom)
        self.view_mode = normalize_view_mode(tab.view_mode)
        self._restoring_tab_state = True
        try:
            self.filter_query = str(tab.filter_query or "")
            self.filter_mode = normalize_filter_mode(tab.filter_mode)
            self.group_criterion = normalize_group_criterion(tab.group_criterion)
            self.groups_enabled = bool(tab.groups_enabled)
            self.collapsed_groups = set(tab.collapsed_groups)
            self.show_previews = bool(tab.show_previews)
            if self.filter_entry is not None and self.filter_entry.get_text() != self.filter_query:
                self.filter_entry.set_text(self.filter_query)
            if self.filter_mode_combo is not None:
                self.filter_mode_combo.set_active_id(self.filter_mode.value)
            if self.filter_query and self.location_stack is not None:
                self.location_stack.set_visible_child_name("filter")
            elif self.location_stack is not None and self.location_stack.get_visible_child_name() == "filter":
                self.location_stack.set_visible_child_name("breadcrumbs")
        finally:
            self._restoring_tab_state = False
        self.pending_selection_locations = set(tab.selected_locations)
        self._pending_main_scroll_position = max(0.0, float(tab.scroll_position or 0.0))
        if self.file_view is not None:
            self.file_view.set_zoom(self.icon_zoom)
            self.file_view.set_view_mode(self.view_mode)
            self.file_view.set_show_previews(self.show_previews)
        self._sync_view_menu()

    def new_tab(self, *_args: object) -> None:
        self.open_location_new_tab(self.current_location or self.home)

    def close_tab(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        if target < 0 or target >= len(self.tabs):
            return
        if len(self.tabs) == 1:
            if self.window is not None:
                self.window.close()
            return
        self._sync_active_tab_state()
        self.closed_tabs.append(self.tabs[target].clone())
        del self.tabs[target]
        del self.closed_tabs[:-10]
        next_index = min(target, len(self.tabs) - 1)
        self._stop_directory_monitor()
        self.active_tab_index = next_index
        self._load_active_tab_state()
        self.directory_entries = []
        self.directory_entries_location = None
        self.directory_entries_ready = False
        self.update_navigation_ui()
        self._render_tab_bar()
        self.load_directory(self.current_location or self.home)

    def _remove_tab_indices(self, indices: set[int]) -> None:
        valid = {index for index in indices if 0 <= index < len(self.tabs)}
        if not valid or len(valid) >= len(self.tabs):
            return
        self._sync_active_tab_state()
        active_ref = self.tabs[self.active_tab_index]
        for index in sorted(valid, reverse=True):
            self.closed_tabs.append(self.tabs[index].clone())
            del self.tabs[index]
        del self.closed_tabs[:-10]
        active_position = next((position for position, tab in enumerate(self.tabs) if tab is active_ref), None)
        self.active_tab_index = active_position if active_position is not None else min(self.active_tab_index, len(self.tabs) - 1)
        self._stop_directory_monitor()
        self._load_active_tab_state()
        self.directory_entries = []
        self.directory_entries_location = None
        self.directory_entries_ready = False
        self.update_navigation_ui()
        self._render_tab_bar()
        self.load_directory(self.current_location or self.home)

    def close_tabs_left(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        self._remove_tab_indices(set(range(max(0, target))))

    def close_tabs_right(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        self._remove_tab_indices(set(range(min(len(self.tabs), target + 1), len(self.tabs))))

    def close_other_tabs(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        if 0 <= target < len(self.tabs):
            self._remove_tab_indices(set(range(len(self.tabs))) - {target})

    def duplicate_tab(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        if target < 0 or target >= len(self.tabs):
            return
        self._sync_active_tab_state()
        self.tabs.insert(target + 1, self.tabs[target].clone())
        self.switch_tab(target + 1)

    def move_tab_left(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        if target <= 0 or target >= len(self.tabs):
            return
        self._sync_active_tab_state()
        self.tabs[target - 1], self.tabs[target] = self.tabs[target], self.tabs[target - 1]
        if self.active_tab_index == target:
            self.active_tab_index -= 1
        elif self.active_tab_index == target - 1:
            self.active_tab_index += 1
        self.settings["session_tabs"] = self._session_tabs_payload()
        self.settings["active_tab"] = self.active_tab_index
        save_settings(self.settings)
        self._render_tab_bar()

    def move_tab_right(self, index: int | None = None, *_args: object) -> None:
        target = self.active_tab_index if index is None else index
        if target < 0 or target >= len(self.tabs) - 1:
            return
        self._sync_active_tab_state()
        self.tabs[target + 1], self.tabs[target] = self.tabs[target], self.tabs[target + 1]
        if self.active_tab_index == target:
            self.active_tab_index += 1
        elif self.active_tab_index == target + 1:
            self.active_tab_index -= 1
        self.settings["session_tabs"] = self._session_tabs_payload()
        self.settings["active_tab"] = self.active_tab_index
        save_settings(self.settings)
        self._render_tab_bar()

    def reopen_tab(self, *_args: object) -> None:
        if not self.closed_tabs:
            return
        self._sync_active_tab_state()
        restored = self.closed_tabs.pop()
        self.tabs.insert(self.active_tab_index + 1, restored)
        self.switch_tab(self.active_tab_index + 1)

    def switch_tab(self, index: int, *_args: object) -> None:
        if index < 0 or index >= len(self.tabs) or index == self.active_tab_index:
            return
        self._sync_active_tab_state()
        self._stop_directory_monitor()
        self.active_tab_index = index
        self._load_active_tab_state()
        self.directory_entries = []
        self.directory_entries_location = None
        self.directory_entries_ready = False
        self.update_navigation_ui()
        self._render_tab_bar()
        self.load_directory(self.current_location or self.home)

    def _session_tabs_payload(self) -> list[dict[str, object]]:
        return [
            {
                "location": tab.location,
                "show_hidden": tab.show_hidden,
                "sort_key": tab.sort_key,
                "sort_descending": tab.sort_descending,
                "folders_first": tab.folders_first,
                "icon_zoom": tab.icon_zoom,
                "view_mode": tab.view_mode,
                "scroll_position": tab.scroll_position,
                "filter_query": tab.filter_query,
                "filter_mode": tab.filter_mode,
                "group_criterion": tab.group_criterion,
                "groups_enabled": tab.groups_enabled,
                "collapsed_groups": list(tab.collapsed_groups),
                "show_previews": tab.show_previews,
                "selected_locations": list(tab.selected_locations),
            }
            for tab in self.tabs
        ]

    def reorder_tab(self, source_index: int, target_index: int) -> bool:
        """Move a tab to the drop target while keeping the active tab identity."""

        if source_index == target_index or not (0 <= source_index < len(self.tabs)):
            return False
        if not (0 <= target_index < len(self.tabs)):
            return False
        self._sync_active_tab_state()
        active_ref = self.tabs[self.active_tab_index]
        moved = self.tabs.pop(source_index)
        adjusted_target = target_index - 1 if source_index < target_index else target_index
        self.tabs.insert(max(0, min(adjusted_target, len(self.tabs))), moved)
        self.active_tab_index = next(
            index for index, tab in enumerate(self.tabs) if tab is active_ref
        )
        self.settings["session_tabs"] = self._session_tabs_payload()
        self.settings["active_tab"] = self.active_tab_index
        save_settings(self.settings)
        self._render_tab_bar()
        return True

    def next_tab(self, *_args: object) -> None:
        if len(self.tabs) > 1:
            self.switch_tab((self.active_tab_index + 1) % len(self.tabs))

    def previous_tab(self, *_args: object) -> None:
        if len(self.tabs) > 1:
            self.switch_tab((self.active_tab_index - 1) % len(self.tabs))

    def open_row_new_tab(self, row: FileRow) -> None:
        if not row.is_dir:
            return
        self.open_location_new_tab(row.location)

    def open_location_new_tab(self, location: str) -> None:
        """Open a location in a new tab, honoring the inherit-view setting."""

        if not location:
            return
        self._sync_active_tab_state()
        source = self._active_tab_state()
        inherit = bool(self.settings.get("new_tab_inherit_view", True))
        state = TabState(location=location)
        if inherit:
            state.show_hidden = source.show_hidden
            state.sort_key = source.sort_key
            state.sort_descending = source.sort_descending
            state.folders_first = source.folders_first
            state.icon_zoom = source.icon_zoom
            state.view_mode = source.view_mode
            state.filter_query = source.filter_query
            state.filter_mode = source.filter_mode
            state.group_criterion = source.group_criterion
            state.groups_enabled = source.groups_enabled
            state.collapsed_groups = list(source.collapsed_groups)
            state.show_previews = source.show_previews
        self.tabs.append(
            state
        )
        self.switch_tab(len(self.tabs) - 1)

    def open_selected_new_tab(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) == 1:
            self.open_row_new_tab(rows[0])

    def is_local_directory(self, location: str) -> bool:
        if not location or location == TRASH_URI or "://" in location:
            return False
        return os.path.isdir(location)

    def copy_breadcrumb_location(self, location: str) -> None:
        if self.window is None or not location:
            return
        try:
            provider = Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8",
                GLib.Bytes.new(location.encode("utf-8")),
            )
            self.window.get_clipboard().set_content(provider)
            self._set_status("Copied breadcrumb location")
        except Exception as error:
            self._set_status(f"Could not copy location: {error}")

    def add_breadcrumb_to_places(self, location: str) -> None:
        if not self.is_local_directory(location):
            self._set_status("Only local folders can be added to Places")
            return
        if not self.places_store.add_pinned(location):
            self._set_status("Folder is already in Places")
            return
        self._render_pinned_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status("Added to Places")

    def add_sidebar_place(self, location: str) -> None:
        self.add_breadcrumb_to_places(location)

    def remove_sidebar_place(self, location: str) -> None:
        self.places_store.remove_pinned(location)
        self._render_pinned_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status("Removed from Places")

    def reorder_pinned(self, location: str, target_index: int) -> None:
        """Persist a pinned-place reorder requested by the sidebar DnD target."""

        before = [entry.location for entry in self.places_store.pinned]
        self.places_store.reorder_pinned(location, target_index)
        if before == [entry.location for entry in self.places_store.pinned]:
            return
        self._render_pinned_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status("Reordered Places")

    def open_terminal_at(self, location: str) -> None:
        if not self.is_local_directory(location):
            self._set_status("Terminal is available only for local folders")
            return
        self._open_terminal_for_directory(location)

    def toggle_split_view(self, *_args: object) -> None:
        if self.body is None or self.file_view is None:
            return
        if self.split_paned is not None and self.split_paned.get_parent() is self.body:
            self.disable_split_view()
            return
        if self.split_paned is None:
            split_location = self.current_location or self.home
            self.split_pane_state = PaneState(
                location=split_location,
                view_mode=self.view_mode.value,
                zoom=self.icon_zoom,
                show_hidden=self.show_hidden,
                sort_key=self.sort_key,
                sort_descending=self.sort_descending,
                folders_first=self.folders_first,
                groups_enabled=self.groups_enabled,
                group_criterion=self.group_criterion.value,
                collapsed_groups=set(self.collapsed_groups),
                show_previews=self.show_previews,
            )
            self.split_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            self.split_paned.add_css_class("split-paned")
            self.split_paned.set_hexpand(True)
            self.split_paned.set_vexpand(True)
            self.split_paned.set_resize_start_child(True)
            self.split_paned.set_resize_end_child(True)
            self.split_paned.set_shrink_start_child(True)
            self.split_paned.set_shrink_end_child(True)
            self.split_paned.set_position(self._split_position_pixels())
            self.split_paned.connect("notify::position", self._on_split_position_changed)
            self.split_file_view = SimpleFileView(
                self.activate_split_row,
                self.show_context_menu,
                zoom=self.icon_zoom,
                open_new_tab=self.open_row_new_tab,
                mode=self.view_mode,
                drop_callback=lambda value, row, target: self.handle_drop(value, row, target, pane=1),
                drop_feedback=self._split_drop_feedback,
                group_toggle=lambda key: (self._activate_pane(1), self.toggle_group(key)),
                group_context_menu=lambda row, x, y: (self._activate_pane(1), self.show_group_context_menu(row, x, y)),
                pane_activate=lambda: self._activate_pane(1),
                show_previews=self.show_previews,
                thumbnail_manager=self.thumbnail_manager,
                single_click_open=bool(self.settings.get("single_click_open", False)),
                show_selection_marker=bool(self.settings.get("show_selection_marker", False)),
                show_tooltips=bool(self.settings.get("show_tooltips", True)),
                inline_rename=bool(self.settings.get("inline_rename", True)),
                show_additional_info=bool(self.settings.get("show_additional_info", True)),
                rename_callback=self._on_inline_rename,
            )
            self.split_file_view.add_css_class("split-pane")
            self.split_file_view.selection.connect("selection-changed", lambda *_args: self._refresh_info_panel())
            if self.file_view.get_parent() is self.body:
                self.body.remove(self.file_view)
            self.split_paned.set_start_child(self.file_view)
            self.split_paned.set_end_child(self.split_file_view)
        if self.split_paned.get_parent() is None:
            self.body.append(self.split_paned)
        self.settings["split_view"] = True
        if self.split_pane_state is None:
            self.split_pane_state = PaneState(location=self.current_location or self.home)
        self.split_location = self.split_pane_state.location
        self._activate_pane(0)
        self._start_split_monitor(self.split_location)
        self._load_split_directory(self.split_location)
        self._set_status("Split view enabled")
        save_settings(self.settings)

    def disable_split_view(self, *_args: object) -> None:
        if self.body is None or self.split_paned is None or self.file_view is None:
            return
        if self.split_paned.get_parent() is self.body:
            self.body.remove(self.split_paned)
        self.split_paned.set_start_child(None)
        self.split_paned.set_end_child(None)
        self.body.append(self.file_view)
        self._activate_pane(0)
        self._stop_split_view()
        self.settings["split_view"] = False
        save_settings(self.settings)
        self._set_status("Split view disabled")

    def toggle_folders_panel(self, *_args: object) -> None:
        if self.left_paned is None:
            return
        if self.folders_panel is None:
            try:
                self.folders_panel = FoldersPanel(self.home, self.navigate, self.handle_drop_to_location)
            except Exception as error:
                self._set_status(f"Folders panel unavailable: {error}")
                return
        if self.left_paned.get_end_child() is self.folders_panel:
            self.left_paned.set_end_child(None)
            self.left_paned.set_visible(bool(self.settings.get("show_sidebar", True)))
            if self.responsive_drawer_widget is self.left_paned:
                self._close_responsive_drawer()
            if self.workspace_paned is not None:
                self.workspace_paned.set_position(self._sidebar_position_pixels())
            self.settings["show_folders_panel"] = False
            self._set_status("Folders panel hidden")
        else:
            self.left_paned.set_end_child(self.folders_panel)
            sidebar_width = max(160, min(320, int(self.settings.get("sidebar_width", 200))))
            folders_width = max(180, min(440, int(self.settings.get("folders_panel_width", 260))))
            if self.workspace_paned is not None:
                self.workspace_paned.set_position(sidebar_width + folders_width)
            self.left_paned.set_position(sidebar_width)
            self.left_paned.set_visible(True)
            self.settings["show_folders_panel"] = True
            self._set_status("Folders panel shown")
        save_settings(self.settings)
        if mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE):
            if self.settings.get("show_folders_panel", False):
                self._show_responsive_drawer(self.left_paned)
            self._apply_responsive_topbar()
        else:
            self._apply_responsive_layout(force=True)

    def _ensure_content_paned(self) -> Gtk.Paned | None:
        """Attach the information split only when the user requests it."""

        if self.content_paned is not None:
            return self.content_paned
        if self.workspace_paned is None or self.body is None:
            return None
        parent = self.terminal_paned
        base = parent.get_start_child() if parent is not None else self.workspace_paned.get_end_child()
        if base is None:
            base = self.body
        if parent is not None:
            parent.set_start_child(None)
        else:
            self.workspace_paned.set_end_child(None)
        pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        pane.add_css_class("content-paned")
        pane.set_hexpand(True)
        pane.set_vexpand(True)
        pane.set_wide_handle(True)
        pane.set_resize_start_child(True)
        pane.set_resize_end_child(False)
        pane.set_shrink_start_child(False)
        pane.set_shrink_end_child(True)
        pane.set_start_child(base)
        pane.connect("notify::position", self._on_info_position_changed)
        self.content_paned = pane
        if parent is not None:
            parent.set_start_child(pane)
        else:
            self.workspace_paned.set_end_child(pane)
        return pane

    def _ensure_terminal_paned(self) -> Gtk.Paned | None:
        """Attach the terminal split only when VTE is actually requested."""

        if self.terminal_paned is not None:
            return self.terminal_paned
        if self.workspace_paned is None or self.body is None:
            return None
        base = self.workspace_paned.get_end_child() or self.body
        self.workspace_paned.set_end_child(None)
        pane = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("terminal-paned")
        pane.set_hexpand(True)
        pane.set_vexpand(True)
        pane.set_wide_handle(True)
        pane.set_resize_start_child(True)
        pane.set_resize_end_child(False)
        pane.set_shrink_start_child(False)
        pane.set_shrink_end_child(True)
        pane.set_start_child(base)
        pane.connect("notify::position", self._on_terminal_position_changed)
        self.terminal_paned = pane
        self.workspace_paned.set_end_child(pane)
        return pane

    def toggle_info_panel(self, *_args: object) -> None:
        content_paned = self._ensure_content_paned()
        if content_paned is None:
            return
        if self.info_panel is None:
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            panel.add_css_class("info-panel")
            panel.set_hexpand(False)
            panel.set_vexpand(True)
            panel.set_margin_top(12)
            panel.set_margin_bottom(12)
            panel.set_margin_start(12)
            panel.set_margin_end(12)
            self.info_panel = panel
            self.info_panel_labels = []
        narrow = mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE)
        constrained = narrow or self.responsive_short_height
        shown_in_content = content_paned.get_end_child() is self.info_panel
        shown_as_drawer = (
            self.responsive_drawer_widget is self.info_panel
            and self.responsive_info_drawer_open
            and self.info_panel.get_visible()
        )
        if shown_in_content or shown_as_drawer:
            if shown_in_content:
                content_paned.set_end_child(None)
            if shown_as_drawer:
                self._close_responsive_drawer()
            if self.info_panel.get_parent() is self.workspace_overlay:
                self.workspace_overlay.remove_overlay(self.info_panel)
            self.settings["show_inspector"] = False
            self._set_status("Information panel hidden")
        else:
            self.settings["show_inspector"] = True
            if constrained:
                if content_paned.get_end_child() is self.info_panel:
                    content_paned.set_end_child(None)
                self._show_responsive_drawer(self.info_panel, info=True)
            else:
                if self.info_panel.get_parent() is self.workspace_overlay:
                    self.workspace_overlay.remove_overlay(self.info_panel)
                content_paned.set_end_child(self.info_panel)
                content_paned.set_position(self._info_position_pixels())
            self._refresh_info_panel()
            self._set_status("Information panel shown" if not narrow else "Information drawer shown")
        save_settings(self.settings)
        self._apply_responsive_topbar()

    def _refresh_info_panel(self) -> None:
        if self.info_panel is None or self.info_panel.get_parent() is None:
            return
        child = self.info_panel.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.info_panel.remove(child)
            child = next_child
        self.info_panel_labels = []
        self.info_tags_entry = None
        self.info_rating_spin = None
        self.info_comment_view = None
        rows = self._selected_rows()
        if not rows:
            empty = Gtk.Label(label="No selection")
            empty.add_css_class("empty-info-label")
            empty.set_halign(Gtk.Align.CENTER)
            empty.set_valign(Gtk.Align.CENTER)
            empty.set_hexpand(True)
            empty.set_vexpand(True)
            self.info_panel.append(empty)
            self.info_panel_labels.append(empty)
            return
        elif len(rows) > 1:
            total = sum(int(row.size or 0) for row in rows)
            values = [f"{len(rows)} items selected", f"Combined size: {format_size(total)}"]
        else:
            row = rows[0]
            values = [f"Name: {row.display_name}", f"Type: {row.content_type}", f"Size: {format_size(row.size)}", f"Location: {row.location}"]
            path = row.file.get_path()
            if path is not None:
                try:
                    metadata = metadata_for_path(path, row.location)
                    values.extend([f"Modified: {format_date(metadata.modified)}", f"Permissions: {metadata.permissions}"])
                    if metadata.symlink_target:
                        values.append(f"Target: {metadata.symlink_target}")
                except OSError:
                    values.append("Metadata unavailable")
        heading = Gtk.Label(label="INFORMATION")
        heading.add_css_class("sidebar-section-label")
        heading.set_halign(Gtk.Align.START)
        self.info_panel.append(heading)
        for value in values:
            label = Gtk.Label(label=value)
            label.set_wrap(True)
            label.set_selectable(True)
            label.set_halign(Gtk.Align.START)
            label.set_max_width_chars(36)
            label.add_css_class("simple-property")
            self.info_panel.append(label)
            self.info_panel_labels.append(label)
        if len(rows) == 1 and rows[0].file.get_path() is not None:
            row = rows[0]
            metadata = self.metadata_store.get(row.location)
            tags_label = Gtk.Label(label="Tags")
            tags_label.add_css_class("sidebar-section-label")
            tags_label.set_halign(Gtk.Align.START)
            self.info_panel.append(tags_label)
            tags = Gtk.Entry()
            tags.set_text(", ".join(metadata.tags))
            tags.set_placeholder_text("work, linux")
            tags.set_hexpand(True)
            self.info_panel.append(tags)
            rating_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            rating_line.append(Gtk.Label(label="Rating"))
            rating = Gtk.SpinButton.new_with_range(0, 5, 1)
            rating.set_value(metadata.rating)
            rating_line.append(rating)
            self.info_panel.append(rating_line)
            comment = Gtk.TextView()
            comment.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            comment.set_vexpand(False)
            comment.set_size_request(-1, 72)
            comment.get_buffer().set_text(metadata.comment)
            self.info_panel.append(comment)
            save = Gtk.Button(label="Save metadata")
            save.add_css_class("action-button")
            save.set_halign(Gtk.Align.START)
            save.connect("clicked", lambda _button: self._save_info_metadata(row, tags, rating, comment))
            self.info_panel.append(save)
            copy_path = Gtk.Button(label="Copy location")
            copy_path.set_halign(Gtk.Align.START)
            copy_path.connect("clicked", lambda _button: self.copy_location())
            self.info_panel.append(copy_path)
            self.info_tags_entry = tags
            self.info_rating_spin = rating
            self.info_comment_view = comment

    def _save_info_metadata(
        self,
        row: FileRow,
        tags: Gtk.Entry,
        rating: Gtk.SpinButton,
        comment: Gtk.TextView,
    ) -> None:
        buffer = comment.get_buffer()
        start, end = buffer.get_bounds()
        tag_values = [value.strip() for value in tags.get_text().split(",") if value.strip()]
        self.metadata_store.set(
            row.location,
            tags=tag_values,
            rating=rating.get_value_as_int(),
            comment=buffer.get_text(start, end, False),
        )
        if not self.metadata_store.save():
            self._set_status("Could not save metadata")
            return
        self._set_status("Metadata saved")
        self.sidebar_controller.render_tags()
        self.refresh()

    def _stop_split_view(self) -> None:
        self.split_generation += 1
        if self.split_cancellable is not None:
            self.split_cancellable.cancel()
        self.split_cancellable = None
        if self.split_enumerator is not None:
            try:
                self.split_enumerator.close(None)
            except GLib.Error:
                pass
        self.split_enumerator = None
        if self.split_directory_monitor is not None:
            self.split_directory_monitor.stop()
            self.split_directory_monitor = None

    def _sidebar_position_pixels(self) -> int:
        if mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE):
            return 0
        width = self.workspace_paned.get_width() if self.workspace_paned is not None else 0
        if width <= 0:
            width = int(self.settings.get("window_width", 1320))
        configured = int(self.settings.get("sidebar_width", 200))
        available = max(0, width - 360)
        if available < 160:
            return available
        return max(160, min(320, available, configured))

    def _info_position_pixels(self) -> int:
        if mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE):
            return 0
        width = self.content_paned.get_width() if self.content_paned is not None else 0
        if width <= 0:
            width = int(self.settings.get("window_width", 1320)) - self._sidebar_position_pixels()
        info_width = int(self.settings.get("info_panel_width", 280))
        if width < 360:
            return max(0, width)
        info_width = max(180, min(360, info_width))
        return max(180, min(width - 180, width - info_width))

    def _terminal_position_pixels(self) -> int:
        if mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE) or self.responsive_short_height:
            return 0
        height = self.terminal_paned.get_height() if self.terminal_paned is not None else 0
        if height <= 0:
            height = int(self.settings.get("window_height", 820))
        terminal_height = int(self.settings.get("terminal_panel_height", 220))
        if height < 240:
            return max(0, height)
        terminal_height = max(96, min(height - 96, terminal_height))
        return max(96, min(height - 96, height - terminal_height))

    def _on_sidebar_position_changed(self, *_args: object) -> None:
        if self.shutting_down or self.workspace_paned is None:
            return
        if self.sidebar_position_save_source:
            GLib.source_remove(self.sidebar_position_save_source)
        self.sidebar_position_save_source = GLib.timeout_add(250, self._save_sidebar_position)

    def _save_sidebar_position(self) -> bool:
        self.sidebar_position_save_source = 0
        if self.workspace_paned is None:
            return False
        position = self.workspace_paned.get_position()
        if 160 <= position <= 320:
            self.settings["sidebar_width"] = position
            save_settings(self.settings)
        return False

    def _on_folders_position_changed(self, *_args: object) -> None:
        if self.shutting_down or self.left_paned is None or self.left_paned.get_end_child() is None:
            return
        if self.folders_position_save_source:
            GLib.source_remove(self.folders_position_save_source)
        self.folders_position_save_source = GLib.timeout_add(250, self._save_folders_position)

    def _save_folders_position(self) -> bool:
        self.folders_position_save_source = 0
        if self.left_paned is None or self.left_paned.get_end_child() is None:
            return False
        width = self.left_paned.get_width()
        if width <= 0:
            return False
        folders_width = width - self.left_paned.get_position()
        self.settings["folders_panel_width"] = max(220, min(440, folders_width))
        sidebar_width = self.left_paned.get_position()
        if 160 <= sidebar_width <= 320:
            self.settings["sidebar_width"] = sidebar_width
        save_settings(self.settings)
        return False

    def _on_info_position_changed(self, *_args: object) -> None:
        if self.shutting_down or self.content_paned is None or self.content_paned.get_end_child() is None:
            return
        if self.info_position_save_source:
            GLib.source_remove(self.info_position_save_source)
        self.info_position_save_source = GLib.timeout_add(250, self._save_info_position)

    def _save_info_position(self) -> bool:
        self.info_position_save_source = 0
        if self.content_paned is None or self.content_paned.get_end_child() is None:
            return False
        width = self.content_paned.get_width()
        if width <= 0:
            return False
        info_width = width - self.content_paned.get_position()
        self.settings["info_panel_width"] = max(220, min(440, info_width))
        save_settings(self.settings)
        return False

    def _on_terminal_position_changed(self, *_args: object) -> None:
        if self.shutting_down or self.terminal_paned is None or self.terminal_paned.get_end_child() is None:
            return
        if self.terminal_position_save_source:
            GLib.source_remove(self.terminal_position_save_source)
        self.terminal_position_save_source = GLib.timeout_add(250, self._save_terminal_position)

    def _save_terminal_position(self) -> bool:
        self.terminal_position_save_source = 0
        if self.terminal_paned is None or self.terminal_paned.get_end_child() is None:
            return False
        height = self.terminal_paned.get_height()
        if height <= 0:
            return False
        panel_height = height - self.terminal_paned.get_position()
        self.settings["terminal_panel_height"] = max(120, min(480, panel_height))
        save_settings(self.settings)
        return False

    def _split_position_pixels(self) -> int:
        width = self.body.get_width() if self.body is not None else 0
        if width <= 0:
            width = int(self.settings.get("window_width", 1320))
        fraction = float(self.settings.get("split_position", 0.5))
        return max(1, min(max(1, width - 1), int(width * fraction)))

    def _on_split_position_changed(self, *_args: object) -> None:
        if self.shutting_down or self.split_paned is None:
            return
        if self.split_position_save_source:
            GLib.source_remove(self.split_position_save_source)
        self.split_position_save_source = GLib.timeout_add(250, self._save_split_position)

    def _save_split_position(self) -> bool:
        self.split_position_save_source = 0
        if self.split_paned is None:
            return False
        width = self.body.get_width() if self.body is not None else 0
        if width <= 0:
            return False
        position = self.split_paned.get_position()
        self.settings["split_position"] = max(0.25, min(0.75, position / width))
        save_settings(self.settings)
        return False

    def _start_split_monitor(self, location: str) -> None:
        if self.split_directory_monitor is not None:
            self.split_directory_monitor.stop()
        monitor = DirectoryMonitor(self._on_split_directory_changed)
        if monitor.start(self.current_file(location), location):
            self.split_directory_monitor = monitor

    def _on_split_directory_changed(self, location: str, _event: str) -> None:
        if self.split_paned is not None and self.split_paned.get_parent() is not None and location == self.split_location:
            self._last_refresh_request = (location, time.monotonic())
            self._load_split_directory(location)

    def _load_split_directory(self, location: str) -> None:
        if self.split_file_view is None:
            return
        if self.split_pane_state is not None:
            self.split_pane_state.location = location
            self.split_location = location
            self.split_file_view.set_view_mode(self.split_pane_state.view_mode)
            self.split_file_view.set_zoom(self.split_pane_state.zoom)
            self.split_file_view.set_show_previews(self.split_pane_state.show_previews)
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
        self.split_entries = []
        self.split_directory_ready = False
        self.split_location = location
        folder = self.current_file(location)
        try:
            folder.enumerate_children_async(
                FILE_ATTRIBUTES,
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.split_cancellable,
                lambda source, result, token=generation, target=location, parent=folder: self._on_split_enumerator_ready(
                    source, result, token, target, parent
                ),
            )
        except GLib.Error as error:
            self._set_status(f"Split view: {error.message}")

    def _on_split_enumerator_ready(
        self,
        source: Gio.File,
        result: Gio.AsyncResult,
        generation: int,
        location: str,
        folder: Gio.File,
    ) -> None:
        if generation != self.split_generation or self.split_cancellable is None:
            return
        try:
            self.split_enumerator = source.enumerate_children_finish(result)
            self._load_split_chunk(self.split_enumerator, generation, location, folder)
        except GLib.Error as error:
            self._set_status(f"Split view: {error.message}")

    def _load_split_chunk(self, enumerator: Gio.FileEnumerator, generation: int, location: str, folder: Gio.File) -> None:
        if generation != self.split_generation or self.split_cancellable is None:
            return
        enumerator.next_files_async(
            128,
            GLib.PRIORITY_DEFAULT,
            self.split_cancellable,
            lambda source, result: self._on_split_chunk(source, result, generation, location, folder),
        )

    def _on_split_chunk(
        self,
        source: Gio.FileEnumerator,
        result: Gio.AsyncResult,
        generation: int,
        location: str,
        folder: Gio.File,
    ) -> None:
        if generation != self.split_generation or self.split_cancellable is None:
            return
        try:
            infos = source.next_files_finish(result)
        except GLib.Error as error:
            self._set_status(f"Split view: {error.message}")
            return
        for info in infos:
            name = info.get_name()
            child_file = folder.get_child(name)
            try:
                size = int(info.get_attribute_uint64("standard::size"))
            except (GLib.Error, TypeError, ValueError):
                size = 0
            try:
                modified = int(info.get_attribute_uint64("time::modified")) or None
            except (GLib.Error, TypeError, ValueError):
                modified = None
            metadata = self.metadata_store.get(child_file.get_uri())
            child_path = child_file.get_path()
            git_status = self._git_status_for_location(location).get(
                os.path.abspath(child_path) if child_path is not None else "",
                "",
            )
            self.split_entries.append(
                DirectoryEntry(
                    child_file,
                    name,
                    info.get_display_name() or name,
                    info.get_file_type() == Gio.FileType.DIRECTORY,
                    info.get_content_type() or "application/octet-stream",
                    name.startswith(".") or info.get_attribute_boolean("standard::is-hidden"),
                    size,
                    modified,
                    tags=tuple(metadata.tags),
                    rating=metadata.rating,
                    git_status=git_status,
                )
            )
        if len(infos) < 128:
            try:
                source.close(None)
            except GLib.Error:
                pass
            self.split_enumerator = None
            self._commit_split_directory(generation, location)
        else:
            self._load_split_chunk(source, generation, location, folder)

    def _commit_split_directory(self, generation: int, location: str) -> None:
        if generation != self.split_generation or self.split_file_view is None or location != self.split_location:
            return
        state = self.split_pane_state
        if state is None:
            rows = self._rows_for_entries(self.split_entries)
        else:
            rows = self._rows_for_presentation(
                self.split_entries,
                show_hidden=state.show_hidden,
                filter_query=state.filter_query,
                filter_mode=state.filter_mode,
                sort_key=state.sort_key,
                sort_descending=state.sort_descending,
                folders_first=state.folders_first,
                groups_enabled=state.groups_enabled,
                group_criterion=state.group_criterion,
                collapsed_groups=state.collapsed_groups,
            )
        selected = self.split_file_view.selected_locations()
        self.split_file_view.replace_rows(rows)
        self.split_file_view.restore_selection(selected)
        self.split_file_view.set_empty_visible(not rows)
        self.split_directory_ready = True
        if state is not None:
            GLib.idle_add(self._restore_scroll_position, self.split_file_view, state.scroll_position)

    @staticmethod
    def _scroll_position(view: Gtk.ScrolledWindow) -> float:
        try:
            adjustment = view.get_vadjustment()
            return max(0.0, float(adjustment.get_value()))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _restore_scroll_position(view: Gtk.ScrolledWindow, value: float) -> bool:
        try:
            adjustment = view.get_vadjustment()
            upper = max(0.0, float(adjustment.get_upper() - adjustment.get_page_size()))
            adjustment.set_value(max(0.0, min(float(value), upper)))
        except (AttributeError, TypeError, ValueError):
            pass
        return False

    def activate_split_row(self, row: FileRow) -> None:
        if row.is_dir:
            if self.split_pane_state is not None:
                self.split_pane_state.navigate(row.location)
            self.split_location = row.location
            self._start_split_monitor(row.location)
            self._load_split_directory(row.location)
            self._render_breadcrumbs()
            self.update_navigation_ui()
        else:
            self.open_default(row.location)

    def _navigate_split(self, location: str, *, record_history: bool = True) -> None:
        if self.split_pane_state is None:
            return
        target = self.normalize_location(location)
        if target not in {TRASH_URI, RECENT_URI} and "://" not in target and not os.path.isdir(target):
            self._set_status("Split destination is not a folder")
            return
        self._activate_pane(1)
        if self.split_location == target and self.split_directory_ready:
            return
        self.split_pane_state.navigate(target, record_history=record_history)
        self.split_location = target
        self._start_split_monitor(target)
        self._load_split_directory(target)
        self._render_breadcrumbs()
        self.update_navigation_ui()

    def build_topbar(self) -> Gtk.Widget:
        return self.topbar_controller.build()

    def _build_view_popover(self) -> Gtk.Popover:
        return self.topbar_controller.build_view_popover()

    def _on_show_hidden_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating_view_menu:
            return
        self.set_show_hidden(button.get_active())

    def _on_sort_direction_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating_view_menu:
            return
        self.set_sort_direction(button.get_active())

    def _on_folders_first_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating_view_menu:
            return
        self.set_folders_first(button.get_active())

    def _on_previews_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating_view_menu:
            return
        self.set_show_previews(button.get_active())

    def _on_groups_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating_view_menu:
            return
        self.set_groups_enabled(button.get_active())

    def _sync_view_menu(self) -> None:
        if self.show_hidden_check is None:
            return
        split_state = self.split_pane_state if self.active_pane == 1 else None
        effective_hidden = split_state.show_hidden if split_state is not None else self.show_hidden
        effective_descending = split_state.sort_descending if split_state is not None else self.sort_descending
        effective_folders_first = split_state.folders_first if split_state is not None else self.folders_first
        effective_previews = split_state.show_previews if split_state is not None else self.show_previews
        effective_groups = split_state.groups_enabled if split_state is not None else self.groups_enabled
        effective_zoom = split_state.zoom if split_state is not None else self.icon_zoom
        self._updating_view_menu = True
        try:
            self.show_hidden_check.set_active(effective_hidden)
            if getattr(self, "sort_direction_check", None) is not None:
                self.sort_direction_check.set_active(effective_descending)
            if getattr(self, "folders_first_check", None) is not None:
                self.folders_first_check.set_active(effective_folders_first)
            if getattr(self, "previews_check", None) is not None:
                self.previews_check.set_active(effective_previews)
            if self.groups_check is not None:
                self.groups_check.set_active(effective_groups)
            if self.zoom_label is not None:
                self.zoom_label.set_text(f"Icon size: {effective_zoom}px")
        finally:
            self._updating_view_menu = False

    def set_view_mode(self, mode: ViewMode | str, *, persist: bool = True) -> None:
        selected = normalize_view_mode(mode)
        if self.active_pane == 1 and self.split_pane_state is not None and self.split_file_view is not None:
            self.split_pane_state.view_mode = selected.value
            self.split_file_view.set_view_mode(selected)
            self._sync_view_menu()
            if persist:
                save_settings(self.settings)
            return
        self.view_mode = selected
        self.settings["default_view"] = self.view_mode.value
        if getattr(self, "tabs", None):
            self._active_tab_state().view_mode = self.view_mode.value
        if self.file_view is not None:
            self.file_view.set_view_mode(self.view_mode)
        self._sync_view_menu()
        if persist:
            save_settings(self.settings)

    def set_show_previews(self, enabled: bool, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None and self.split_file_view is not None:
            self.split_pane_state.show_previews = bool(enabled)
            self.split_file_view.set_show_previews(bool(enabled))
            self._sync_view_menu()
            if self.split_entries:
                self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.show_previews = bool(enabled)
        self.settings["show_previews"] = self.show_previews
        if self.file_view is not None:
            self.file_view.set_show_previews(self.show_previews)
        if self.split_file_view is not None:
            self.split_file_view.set_show_previews(self.show_previews)
        if self.directory_entries_ready:
            self._rerender_cached_entries()
        self._sync_view_menu()
        if persist:
            save_settings(self.settings)

    def set_show_git_status(self, enabled: bool, *, persist: bool = True) -> None:
        """Toggle one cached Git status query per loaded directory."""

        self.settings["show_git_status"] = bool(enabled)
        self.git_status_cache.clear()
        if self.directory_entries_location is not None:
            status = self._git_status_for_location(self.directory_entries_location)
            for entry in self.directory_entries:
                path = entry.file.get_path()
                entry.git_status = status.get(os.path.abspath(path), "") if path is not None else ""
            if self.directory_entries_ready:
                self._rerender_cached_entries()
        if self.split_pane_state is not None and self.split_location is not None:
            status = self._git_status_for_location(self.split_location)
            for entry in self.split_entries:
                path = entry.file.get_path()
                entry.git_status = status.get(os.path.abspath(path), "") if path is not None else ""
            if self.split_entries:
                self._rerender_split_cached_entries()
        if persist:
            save_settings(self.settings)

    def _current_folder_properties(self) -> FolderViewProperties:
        return FolderViewProperties(
            view_mode=self.view_mode.value,
            sort_mode=self.sort_key,
            sort_reverse=self.sort_descending,
            folders_first=self.folders_first,
            show_hidden=self.show_hidden,
            show_previews=self.show_previews,
            show_groups=self.groups_enabled,
            group_criterion=self.group_criterion.value,
            icon_zoom=self.icon_zoom,
        )

    def save_current_folder_view(self, *_args: object) -> None:
        location = self.current_location or self.home
        if "://" in location and not location.startswith("file://"):
            self._set_status("Per-folder settings are unavailable for this remote location")
            return
        self.folder_settings.apply_to_current(location, self._current_folder_properties())
        self.settings["folder_view_settings"] = self.folder_settings.to_dict()
        save_settings(self.settings)
        self._set_status("View properties saved for this folder")

    def save_all_folder_view(self, *_args: object) -> None:
        self.folder_settings.apply_to_all(self._current_folder_properties())
        self.settings["folder_view_settings"] = self.folder_settings.to_dict()
        save_settings(self.settings)
        self._set_status("View properties saved as the default")

    def set_groups_enabled(self, enabled: bool, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.groups_enabled = bool(enabled)
            if self.split_pane_state.groups_enabled and normalize_group_criterion(self.split_pane_state.group_criterion) is GroupCriterion.NONE:
                self.split_pane_state.group_criterion = GroupCriterion.TYPE.value
            self._sync_view_menu()
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.groups_enabled = bool(enabled)
        if self.groups_enabled and self.group_criterion is GroupCriterion.NONE:
            # Enabling the visible-groups control must produce a useful view
            # immediately; the user can still choose Ungroup or another
            # criterion from the view popover.
            self.group_criterion = GroupCriterion.TYPE
        self.settings["show_groups"] = self.groups_enabled
        self.settings["group_criterion"] = self.group_criterion.value
        self._sync_view_menu()
        if self.directory_entries_ready:
            self._rerender_cached_entries()
        if persist:
            save_settings(self.settings)

    def toggle_groups(self, *_args: object) -> None:
        self.set_groups_enabled(not self.groups_enabled)

    def set_group_criterion(self, criterion: GroupCriterion | str, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            selected = normalize_group_criterion(criterion)
            self.split_pane_state.group_criterion = selected.value
            self.split_pane_state.collapsed_groups.clear()
            if selected is not GroupCriterion.NONE:
                self.split_pane_state.groups_enabled = True
            self._sync_view_menu()
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.group_criterion = normalize_group_criterion(criterion)
        self.collapsed_groups.clear()
        self.settings["group_criterion"] = self.group_criterion.value
        if self.groups_enabled:
            self._set_status(f"Grouped by {self.group_criterion.value}")
            if self.directory_entries_ready:
                self._rerender_cached_entries()
        if persist:
            save_settings(self.settings)

    def toggle_group(self, key: str) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            if not self.split_pane_state.groups_enabled or normalize_group_criterion(self.split_pane_state.group_criterion) is GroupCriterion.NONE:
                return
            if key in self.split_pane_state.collapsed_groups:
                self.split_pane_state.collapsed_groups.remove(key)
            else:
                self.split_pane_state.collapsed_groups.add(key)
            self._rerender_split_cached_entries()
            return
        if not self.groups_enabled or self.group_criterion is GroupCriterion.NONE:
            return
        if key in self.collapsed_groups:
            self.collapsed_groups.remove(key)
        else:
            self.collapsed_groups.add(key)
        self._sync_active_tab_state()
        if self.directory_entries_ready:
            self._rerender_cached_entries()

    def set_show_hidden(self, enabled: bool, *, persist: bool = True) -> None:
        """Toggle hidden entries in the current presentation model."""

        if getattr(self, "active_pane", 0) == 1 and getattr(self, "split_pane_state", None) is not None:
            self.split_pane_state.show_hidden = bool(enabled)
            self._sync_view_menu()
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return

        self.show_hidden = bool(enabled)
        self.settings["show_hidden"] = self.show_hidden
        if getattr(self, "tabs", None):
            self._active_tab_state().show_hidden = self.show_hidden
        self._sync_view_menu()
        if self.directory_entries_ready and self.file_view is not None:
            selected_locations = self.file_view.selected_locations()
            rows = self._rows_for_entries(self.directory_entries)
            self.file_view.replace_rows(rows)
            self.file_view.restore_selection(selected_locations)
            self.file_view.set_empty_visible(not rows)
            self._set_status(format_item_count(len(rows)))
            LOGGER.debug(
                "hidden visibility changed show_hidden=%s visible_items=%d",
                self.show_hidden,
                len(rows),
            )
        if persist:
            save_settings(self.settings)

    def toggle_hidden(self, *_args: object, persist: bool = True) -> None:
        self.set_show_hidden(not self.show_hidden, persist=persist)

    def set_sort_key(self, sort_key: str, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.sort_key = normalize_sort_key(sort_key)
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.sort_key = normalize_sort_key(sort_key)
        self.settings["sort_mode"] = self.sort_key
        if getattr(self, "tabs", None):
            self._active_tab_state().sort_key = self.sort_key
        self._rerender_cached_entries()
        if persist:
            save_settings(self.settings)

    def set_sort_direction(self, descending: bool, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.sort_descending = bool(descending)
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.sort_descending = bool(descending)
        self.settings["sort_reverse"] = self.sort_descending
        if getattr(self, "tabs", None):
            self._active_tab_state().sort_descending = self.sort_descending
        self._rerender_cached_entries()
        if persist:
            save_settings(self.settings)

    def set_folders_first(self, enabled: bool, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            self.split_pane_state.folders_first = bool(enabled)
            self._rerender_split_cached_entries()
            if persist:
                save_settings(self.settings)
            return
        self.folders_first = bool(enabled)
        self.settings["folders_first"] = self.folders_first
        if getattr(self, "tabs", None):
            self._active_tab_state().folders_first = self.folders_first
        self._rerender_cached_entries()
        if persist:
            save_settings(self.settings)

    def _rerender_cached_entries(self) -> None:
        if not self.directory_entries_ready or self.file_view is None:
            self._sync_view_menu()
            return
        selected_locations = self.file_view.selected_locations()
        rows = self._rows_for_entries(self.directory_entries)
        self.file_view.replace_rows(rows)
        self.file_view.restore_selection(selected_locations)
        self.file_view.set_empty_visible(not rows)
        self._set_status(format_item_count(len(rows)))
        self._sync_view_menu()
        LOGGER.debug(
            "directory presentation rerender sort=%s descending=%s folders_first=%s items=%d",
            self.sort_key,
            self.sort_descending,
            self.folders_first,
            len(rows),
        )

    def _rerender_split_cached_entries(self) -> None:
        if self.split_file_view is None or self.split_pane_state is None:
            return
        state = self.split_pane_state
        selected_locations = self.split_file_view.selected_locations()
        rows = self._rows_for_presentation(
            self.split_entries,
            show_hidden=state.show_hidden,
            filter_query=state.filter_query,
            filter_mode=state.filter_mode,
            sort_key=state.sort_key,
            sort_descending=state.sort_descending,
            folders_first=state.folders_first,
            groups_enabled=state.groups_enabled,
            group_criterion=state.group_criterion,
            collapsed_groups=state.collapsed_groups,
        )
        self.split_file_view.replace_rows(rows)
        self.split_file_view.restore_selection(selected_locations)
        self.split_file_view.set_empty_visible(not rows)

    def set_zoom(self, zoom: object, *, persist: bool = True) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None and self.split_file_view is not None:
            self.split_pane_state.zoom = normalize_zoom(zoom)
            self.split_file_view.set_zoom(self.split_pane_state.zoom)
            self._sync_view_menu()
            if persist:
                save_settings(self.settings)
            return
        self.icon_zoom = normalize_zoom(zoom)
        self.settings["icon_zoom"] = self.icon_zoom
        if getattr(self, "tabs", None):
            self._active_tab_state().icon_zoom = self.icon_zoom
        if self.file_view is not None:
            self.file_view.set_zoom(self.icon_zoom)
        self._sync_view_menu()
        if persist:
            save_settings(self.settings)

    def zoom_in(self, *_args: object) -> None:
        self.set_zoom(zoom_in(self.icon_zoom))

    def zoom_out(self, *_args: object) -> None:
        self.set_zoom(zoom_out(self.icon_zoom))

    def zoom_reset(self, *_args: object) -> None:
        self.set_zoom(48)

    def build_sidebar(self) -> Gtk.Widget:
        return self.sidebar_controller.build()

    def _render_pinned_places(self) -> None:
        self.sidebar_controller.render_pinned()

    @staticmethod
    def _clear_box(box: Gtk.Box | None) -> None:
        SidebarController._clear_box(box)

    def _render_starred_places(self) -> None:
        self.sidebar_controller.render_starred()

    def _render_recent_places(self) -> None:
        self.sidebar_controller.render_recent()

    def _location_label(self, location: str) -> str:
        return self.sidebar_controller.location_label(location)

    def _render_devices(self) -> None:
        self.sidebar_controller.render_devices()

    def _start_device_monitor(self) -> None:
        if self.device_monitor is None:
            self.device_monitor = DeviceMonitor(lambda: GLib.idle_add(self._on_devices_changed))
            self.device_monitor.start()
        self._render_devices()

    def _on_devices_changed(self) -> bool:
        self._render_devices()
        return False

    def _mount_device(self, volume: Gio.Volume) -> None:
        try:
            volume.mount(
                Gio.MountMountFlags.NONE,
                None,
                None,
                lambda source, result: self._finish_mount_device(source, result),
            )
        except GLib.Error as error:
            self._set_status(f"Could not mount device: {error.message}")

    def _finish_mount_device(self, volume: Gio.Volume, result: Gio.AsyncResult) -> None:
        try:
            volume.mount_finish(result)
        except GLib.Error as error:
            self._set_status(f"Could not mount device: {error.message}")
        else:
            self._on_devices_changed()

    def _unmount_device(self, mount: Gio.Mount) -> None:
        try:
            unmount_device(mount, self._finish_unmount_device)
        except GLib.Error as error:
            self._set_status(f"Could not unmount device: {error.message}")

    def _eject_device(self, mount: Gio.Mount | None, drive: Gio.Drive | None = None) -> None:
        """Prefer provider eject, then safe drive stop, then unmount."""

        if mount is not None and mount.can_eject():
            try:
                eject_device(mount, self._finish_unmount_device)
            except GLib.Error as error:
                self._set_status(f"Could not eject device: {error.message}")
            return
        if drive is not None and drive.can_eject():
            try:
                eject_drive(drive, self._finish_unmount_device)
            except GLib.Error as error:
                self._set_status(f"Could not eject drive: {error.message}")
            return
        if drive is not None and drive.can_stop():
            try:
                stop_drive(drive, self._finish_unmount_device)
            except GLib.Error as error:
                self._set_status(f"Could not safely remove device: {error.message}")
            return
        if mount is not None:
            self._unmount_device(mount)
        else:
            self._set_status("Device is no longer mounted")

    def _finish_unmount_device(self, success: bool, error: str | None) -> None:
        if not success:
            self._set_status(f"Could not unmount device: {error or 'operation failed'}")
        else:
            self._set_status("Device unmounted")
        self._on_devices_changed()

    def add_selected_to_places(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1 or not rows[0].is_dir:
            self._set_status("Select one folder to add to Places")
            return
        path = rows[0].file.get_path()
        if path is None or not self.places_store.add_pinned(path, rows[0].display_name):
            self._set_status("Folder is already in Places or unavailable")
            return
        self._render_pinned_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status("Added to Places")

    def remove_selected_from_places(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        path = rows[0].file.get_path()
        if path is None:
            return
        self.places_store.remove_pinned(path)
        self._render_pinned_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status("Removed from Places")

    def add_selected_to_starred(self, *_args: object) -> None:
        rows = self._selected_rows()
        if not rows:
            self._set_status("Select at least one item to star")
            return
        for row in rows:
            self.places_store.add_starred(row.file.get_path() or row.location)
        self._render_starred_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status(f"Starred {len(rows)} item(s)")

    def remove_selected_from_starred(self, *_args: object) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in rows:
            self.places_store.remove_starred(row.file.get_path() or row.location)
        self._render_starred_places()
        self.settings.update(self.places_store.to_dict())
        save_settings(self.settings)
        self._set_status(f"Unstarred {len(rows)} item(s)")

    def _build_sidebar_row(
        self,
        name: str,
        location: str | None,
        always_enabled: bool,
        icon_name: str,
    ) -> Gtk.Button:
        return self.sidebar_controller.build_row(name, location, always_enabled, icon_name)

    @staticmethod
    def _special_directory(directory: GLib.UserDirectory) -> str | None:
        path = GLib.get_user_special_dir(directory)
        return path if path and os.path.isdir(path) else None

    @staticmethod
    def normalize_location(location: str) -> str:
        location = location.strip()
        if location == RECENT_URI:
            return RECENT_URI
        if location in {"trash:", "trash://", TRASH_URI}:
            return TRASH_URI
        return parse_location(location, str(Path.home())) or str(Path.home())

    def current_file(self, location: str | None = None) -> Gio.File:
        target = location or self.current_location or self.home
        return Gio.File.new_for_uri(target) if "://" in target else Gio.File.new_for_path(target)

    def navigate(self, location: str, record_history: bool = True) -> None:
        self._sync_active_tab_state()
        if self.search_active or (
            self.location_stack is not None and self.location_stack.get_visible_child_name() == "search"
        ):
            self.close_search(reload=False)
        target = self.normalize_location(location)
        if target != TRASH_URI and "://" not in target and not os.path.isdir(target):
            if os.path.isfile(target):
                self.open_default(Gio.File.new_for_path(target).get_uri())
            else:
                self._set_status(f"Not a folder: {target}")
            return

        self._close_responsive_drawer()
        if target != RECENT_URI:
            self.places_store.add_recent(target)
            self._render_recent_places()

        self._apply_folder_view_settings(target)

        if self.current_location == target:
            # Same-location navigation is not a refresh.  Explicit refresh
            # and monitor callbacks own reloads, avoiding duplicate scans.
            if not self.directory_entries_ready or self.directory_entries_location != target:
                self.load_directory(target, preserve_selection=True)
            return
        if record_history and self.current_location is not None:
            self.navigation_state.visit(target)

        # Drop the old directory monitor before changing identity.  Any
        # already queued callback is invalidated by DirectoryMonitor.stop().
        self._stop_directory_monitor()
        self.current_location = target
        self._active_tab_state().location = target
        self.update_navigation_ui()
        self._render_tab_bar()
        self.load_directory(target)

    def _apply_folder_view_settings(self, location: str) -> None:
        current = FolderViewProperties(
            view_mode=self.view_mode.value,
            sort_mode=self.sort_key,
            sort_reverse=self.sort_descending,
            folders_first=self.folders_first,
            show_hidden=self.show_hidden,
            show_previews=self.show_previews,
            show_groups=self.groups_enabled,
            group_criterion=self.group_criterion.value,
            icon_zoom=self.icon_zoom,
        )
        resolved = self.folder_settings.resolve(location, current)
        self.show_hidden = resolved.show_hidden
        self.sort_key = normalize_sort_key(resolved.sort_mode)
        self.sort_descending = resolved.sort_reverse
        self.folders_first = resolved.folders_first
        self.groups_enabled = resolved.show_groups
        self.group_criterion = normalize_group_criterion(resolved.group_criterion)
        self.icon_zoom = normalize_zoom(resolved.icon_zoom)
        self.view_mode = normalize_view_mode(resolved.view_mode)
        self.show_previews = resolved.show_previews
        self.settings["show_previews"] = self.show_previews
        if self.file_view is not None:
            self.file_view.set_zoom(self.icon_zoom)
            self.file_view.set_view_mode(self.view_mode)
            self.file_view.set_show_previews(self.show_previews)
        if self.split_file_view is not None:
            self.split_file_view.set_show_previews(self.show_previews)
        self._sync_view_menu()

    def load_directory(self, location: str, *, preserve_selection: bool = False) -> None:
        if self.shutting_down or self.file_view is None:
            return
        self.load_generation += 1
        generation = self.load_generation
        self.loading_started_at = time.perf_counter()
        if self.directory_entries_location != location:
            self.directory_entries = []
            self.directory_entries_location = location
            self.directory_entries_ready = False
        if preserve_selection:
            selected_locations = self.file_view.selected_locations()
            # Refreshes keep the user's scroll position; a new navigation is
            # reset to the top below and can restore a tab-history position
            # after the new model has been measured.
            self._pending_main_scroll_position = self._scroll_position(self.file_view)
        else:
            selected_locations = set(self.pending_selection_locations)
            self.pending_selection_locations.clear()
        self.selection_to_restore = (generation, selected_locations)
        if location == RECENT_URI:
            self._stop_directory_monitor()
            if self.load_cancellable is not None:
                self.load_cancellable.cancel()
                self.load_cancellable = None
            self._close_enumerator()
            self._set_status("Loading…")
            self._load_recent_directory(generation)
            return
        self._ensure_directory_monitor(location)
        if self.load_cancellable is not None:
            self.load_cancellable.cancel()
        self._close_enumerator()
        self.load_cancellable = Gio.Cancellable()
        self.loading_entries = []
        self.loading_presented = False
        self.loading_preview_queued = False
        self.loading_folder = self.current_file(location)
        self._set_status("Loading…")
        folder = self.loading_folder
        try:
            folder.enumerate_children_async(
                FILE_ATTRIBUTES,
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.load_cancellable,
                lambda source, result, token=generation, target=location, parent=folder: self._on_enumerator_ready(
                    source,
                    result,
                    token,
                    target,
                    parent,
                ),
            )
        except GLib.Error as error:
            self._queue_commit(generation, [], f"Unable to read folder: {error.message}")

    def _load_recent_directory(self, generation: int) -> None:
        """Load the bounded Recent history as a virtual directory."""

        entries: list[DirectoryEntry] = []
        for target in self.places_store.recent:
            if target in {RECENT_URI, TRASH_URI}:
                continue
            file = Gio.File.new_for_uri(target) if "://" in target else Gio.File.new_for_path(target)
            try:
                info = file.query_info(
                    "standard::name,standard::display-name,standard::type,standard::content-type,"
                    "standard::is-hidden,standard::size,time::modified",
                    Gio.FileQueryInfoFlags.NONE,
                    None,
                )
            except GLib.Error:
                continue
            name = info.get_name() or file.get_basename() or target
            try:
                size = int(info.get_attribute_uint64("standard::size"))
            except (GLib.Error, TypeError, ValueError):
                size = 0
            try:
                modified_value = int(info.get_attribute_uint64("time::modified"))
                modified = modified_value or None
            except (GLib.Error, TypeError, ValueError):
                modified = None
            entries.append(
                DirectoryEntry(
                    file=file,
                    name=name,
                    display_name=info.get_display_name() or name,
                    is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
                    content_type=info.get_content_type() or "application/octet-stream",
                    hidden=name.startswith(".") or info.get_attribute_boolean("standard::is-hidden"),
                    size=size,
                    modified=modified,
                )
            )
        self._queue_commit(generation, entries, None)

    def _on_enumerator_ready(
        self,
        source: Gio.File,
        result: Gio.AsyncResult,
        generation: int,
        location: str,
        folder: Gio.File,
    ) -> None:
        if self.shutting_down or generation != self.load_generation or self.load_cancellable is None:
            try:
                stale = source.enumerate_children_finish(result)
                stale.close(None)
            except GLib.Error:
                pass
            return
        try:
            enumerator = source.enumerate_children_finish(result)
            self.loading_enumerator = enumerator
            self._load_next_chunk(enumerator, generation, location, folder)
        except GLib.Error as error:
            self._queue_commit(generation, [], f"Unable to read folder: {error.message}")

    def _load_next_chunk(
        self,
        enumerator: Gio.FileEnumerator,
        generation: int,
        location: str,
        folder: Gio.File,
    ) -> None:
        if self.shutting_down or generation != self.load_generation or self.load_cancellable is None:
            return
        enumerator.next_files_async(
            128,
            GLib.PRIORITY_DEFAULT,
            self.load_cancellable,
            lambda source, result: self._on_items_chunk(source, result, generation, location, folder),
        )

    def _on_items_chunk(
        self,
        source: Gio.FileEnumerator,
        result: Gio.AsyncResult,
        generation: int,
        location: str,
        folder: Gio.File,
    ) -> None:
        if self.shutting_down or generation != self.load_generation or self.load_cancellable is None:
            return
        try:
            infos = source.next_files_finish(result)
        except GLib.Error as error:
            self._queue_commit(generation, [], f"Unable to read folder: {error.message}")
            return

        for info in infos:
            name = info.get_name()
            child_file = folder.get_child(name)
            file_type = info.get_file_type()
            try:
                size = int(info.get_attribute_uint64("standard::size"))
            except (GLib.Error, TypeError, ValueError):
                size = 0
            try:
                modified_value = int(info.get_attribute_uint64("time::modified"))
                modified = modified_value or None
            except (GLib.Error, TypeError, ValueError):
                modified = None
            metadata = self.metadata_store.get(child_file.get_uri())
            child_path = child_file.get_path()
            git_status = self._git_status_for_location(location).get(
                os.path.abspath(child_path) if child_path is not None else "",
                "",
            )
            self.loading_entries.append(
                DirectoryEntry(
                    file=child_file,
                    name=name,
                    display_name=info.get_display_name() or name,
                    is_dir=file_type == Gio.FileType.DIRECTORY,
                    content_type=info.get_content_type() or "application/octet-stream",
                    hidden=name.startswith(".") or info.get_attribute_boolean("standard::is-hidden"),
                    size=size,
                    modified=modified,
                    original_path=attribute_text(info, "trash::orig-path") if location == TRASH_URI else None,
                    tags=tuple(metadata.tags),
                    rating=metadata.rating,
                    git_status=git_status,
                )
            )

        if self.loading_entries and not self.loading_presented and not self.loading_preview_queued:
            self.loading_preview_queued = True
            GLib.idle_add(self._commit_loading_preview, generation, location)

        if len(infos) < 128:
            self._close_enumerator()
            self._queue_commit(generation, list(self.loading_entries), None)
        else:
            self._load_next_chunk(source, generation, location, folder)

    def _queue_commit(self, generation: int, entries: list[DirectoryEntry], error: str | None) -> None:
        self._close_enumerator()
        GLib.idle_add(self._commit_directory, generation, entries, error)

    def _commit_loading_preview(self, generation: int, location: str) -> bool:
        """Paint the first bounded enumeration chunk while the rest loads.

        Sorting/grouping is intentionally deferred to the final atomic commit;
        this keeps the first paint fast and avoids sorting the growing list on
        every 128-entry callback.
        """

        self.loading_preview_queued = False
        if (
            self.shutting_down
            or generation != self.load_generation
            or self.file_view is None
            or self.current_location != location
            or not self.loading_entries
        ):
            return False
        preview_rows = self._rows_for_entries(self.loading_entries)
        self.file_view._debug_location = location
        self.file_view.replace_rows(preview_rows, reset_scroll=True)
        self.file_view.set_empty_visible(False)
        self.loading_presented = True
        elapsed_ms = (time.perf_counter() - self.loading_started_at) * 1000.0
        self._set_status(f"Loading… {len(self.loading_entries)} items")
        LOGGER.debug(
            "directory first-visible location=%s items=%d elapsed_ms=%.1f",
            location,
            len(preview_rows),
            elapsed_ms,
        )
        return False

    def _commit_directory(
        self,
        generation: int,
        entries: list[DirectoryEntry],
        error: str | None,
    ) -> bool:
        if self.shutting_down or generation != self.load_generation or self.file_view is None:
            return False
        selection_locations: set[str] = set()
        if self.selection_to_restore is not None and self.selection_to_restore[0] == generation:
            selection_locations = set(self.selection_to_restore[1])
        self.directory_entries = list(entries)
        self.directory_entries_location = self.current_location
        self.directory_entries_ready = True
        presentation_started_at = time.perf_counter()
        rows = self._rows_for_entries(self.directory_entries)
        presentation_elapsed_ms = (time.perf_counter() - presentation_started_at) * 1000.0
        model_started_at = time.perf_counter()
        pending_scroll = self._pending_main_scroll_position
        self._pending_main_scroll_position = 0.0
        self.file_view._debug_location = self.current_location or "unknown"
        self.file_view.replace_rows(rows, reset_scroll=True)
        model_elapsed_ms = (time.perf_counter() - model_started_at) * 1000.0
        if not error:
            self.file_view.restore_selection(selection_locations)
        self.selection_to_restore = None
        if error:
            self.file_view.set_empty_visible(False)
            self._set_status(error)
        else:
            self.file_view.set_empty_visible(not rows)
            self._set_status(format_item_count(len(rows)))
        if pending_scroll:
            GLib.timeout_add(50, self._restore_scroll_position, self.file_view, pending_scroll)
        self.loading_presented = False
        self.loading_preview_queued = False
        LOGGER.debug("backend items=%d", len(rows))
        LOGGER.debug("store items=%d", self.file_view.store.get_n_items())
        LOGGER.debug(
            "navigation committed location=%s backend items=%d store items=%d",
            self.current_location,
            len(rows),
            self.file_view.store.get_n_items(),
        )
        LOGGER.debug(
            "directory performance location=%s items=%d total_ms=%.1f presentation_ms=%.1f model_commit_ms=%.1f realized=%d",
            self.current_location,
            len(rows),
            (time.perf_counter() - self.loading_started_at) * 1000.0 if self.loading_started_at else 0.0,
            presentation_elapsed_ms,
            model_elapsed_ms,
            len(self.file_view._bound_items),
        )
        return False

    def _git_status_for_location(self, location: str) -> dict[str, str]:
        """Load one bounded Git status snapshot per visible directory."""

        if not bool(self.settings.get("show_git_status", False)) or location == TRASH_URI or "://" in location:
            return {}
        cached = self.git_status_cache.get(location)
        if cached is not None:
            return cached
        status = GitAdapter(location).status()
        self.git_status_cache[location] = status
        return status

    @staticmethod
    def _file_row_from_entry(entry: DirectoryEntry) -> FileRow:
        return FileRow(
            entry.file,
            entry.name,
            entry.display_name,
            entry.is_dir,
            entry.content_type,
            entry.hidden,
            entry.size,
            entry.modified,
            entry.original_path,
            tags=entry.tags,
            rating=entry.rating,
            git_status=entry.git_status,
        )

    def _rows_for_entries(self, entries: list[DirectoryEntry]) -> list[FileRow]:
        return self._rows_for_presentation(
            entries,
            show_hidden=self.show_hidden,
            filter_query=self.filter_query,
            filter_mode=self.filter_mode,
            sort_key=self.sort_key,
            sort_descending=self.sort_descending,
            folders_first=self.folders_first,
            groups_enabled=self.groups_enabled,
            group_criterion=self.group_criterion,
            collapsed_groups=self.collapsed_groups,
            update_filter_error=True,
        )

    def _rows_for_presentation(
        self,
        entries: list[DirectoryEntry],
        *,
        show_hidden: bool,
        filter_query: str,
        filter_mode: FilterMode | str,
        sort_key: str,
        sort_descending: bool,
        folders_first: bool,
        groups_enabled: bool,
        group_criterion: GroupCriterion | str,
        collapsed_groups: set[str] | frozenset[str] = frozenset(),
        update_filter_error: bool = False,
    ) -> list[FileRow]:
        visible = visible_directory_entries(entries, show_hidden)
        filtered = filter_entries(
            visible,
            filter_query,
            filter_mode,
            include_path=False,
        )
        if update_filter_error:
            self.filter_error = filtered.error
            self._update_filter_error()
        ordered = sort_entries(
            list(filtered.items),
            sort_key,
            sort_descending,
            folders_first,
            bool(self.settings.get("natural_sort", True)),
        )
        selected_criterion = normalize_group_criterion(group_criterion)
        if not groups_enabled or selected_criterion is GroupCriterion.NONE:
            return [self._file_row_from_entry(entry) for entry in ordered]
        rows: list[FileRow] = []
        for group in group_entries(ordered, selected_criterion):
            collapsed = group.key in collapsed_groups
            rows.append(
                FileRow.make_group_header(
                    group.key,
                    group.label,
                    len(group.items),
                    collapsed=collapsed,
                )
            )
            if not collapsed:
                rows.extend(self._file_row_from_entry(entry) for entry in group.items)
        return rows

    @staticmethod
    def _schedule_operation_callback(callback: Callable[..., object], *args: object) -> int:
        def dispatch() -> bool:
            callback(*args)
            return False

        return GLib.idle_add(dispatch)

    def _current_directory_path(self) -> str | None:
        location = self._active_location()
        if location == TRASH_URI or "://" in location:
            return None
        return location

    def _active_location(self) -> str:
        if self.active_pane == 1 and self.split_pane_state is not None:
            return self.split_pane_state.location
        return self.current_location or self.home

    def _active_file_view(self) -> SimpleFileView | None:
        if self.active_pane == 1 and self.split_file_view is not None:
            return self.split_file_view
        return self.file_view

    def _other_pane_location(self) -> str | None:
        if self.split_pane_state is None or self.split_file_view is None:
            return None
        return self.split_pane_state.location if self.active_pane == 0 else (self.current_location or self.home)

    def _refresh_other_pane(self) -> None:
        if self.split_pane_state is None or self.split_file_view is None:
            return
        if self.active_pane == 0:
            self._load_split_directory(self.split_pane_state.location)
        else:
            self.load_directory(self.current_location or self.home, preserve_selection=True)

    def copy_to_other_pane(self, *_args: object) -> None:
        paths = self._selected_paths()
        destination = self._other_pane_location()
        if not paths or destination is None:
            self._set_status("Enable Split View and select local items")
            return
        if not os.path.isdir(destination):
            self._set_status("The other pane is not a local folder")
            return
        self._submit_operation(
            OperationKind.COPY,
            paths,
            destination,
            after=lambda _result: self._refresh_other_pane(),
        )

    def move_to_other_pane(self, *_args: object) -> None:
        paths = self._selected_paths()
        destination = self._other_pane_location()
        if not paths or destination is None:
            self._set_status("Enable Split View and select local items")
            return
        if not os.path.isdir(destination):
            self._set_status("The other pane is not a local folder")
            return
        self._submit_operation(
            OperationKind.MOVE,
            paths,
            destination,
            after=lambda _result: self._refresh_other_pane(),
        )

    def open_in_other_pane(self, *_args: object) -> None:
        rows = self._selected_rows()
        destination = self._other_pane_location()
        if len(rows) != 1 or not rows[0].is_dir or destination is None:
            self._set_status("Select one folder with Split View enabled")
            return
        target = rows[0].location
        if self.active_pane == 0:
            self._navigate_split(target)
        else:
            self.navigate(target)

    def _activate_pane(self, index: int) -> None:
        if self.active_pane == 0:
            self._sync_active_tab_state()
        elif self.active_pane == 1 and self.split_pane_state is not None and self.split_file_view is not None:
            self.split_pane_state.scroll_position = self._scroll_position(self.split_file_view)
        if index == 1 and self.split_file_view is None:
            index = 0
        self.active_pane = 1 if index == 1 else 0
        if self.file_view is not None:
            self.file_view.remove_css_class("active-pane")
            if self.active_pane == 0:
                self.file_view.add_css_class("active-pane")
        if self.split_file_view is not None:
            self.split_file_view.remove_css_class("active-pane")
            if self.active_pane == 1:
                self.split_file_view.add_css_class("active-pane")
        self._sync_view_menu()

    def _selected_rows(self) -> list[FileRow]:
        view = self._active_file_view()
        return view.selected_rows() if view is not None else []

    def _selected_paths(self) -> list[str]:
        rows = self._selected_rows()
        paths: list[str] = []
        for row in rows:
            path = row.file.get_path()
            if path is None:
                self._set_status("This operation supports local files only")
                return []
            paths.append(path)
        return paths

    def _selected_locations(self) -> list[str]:
        return [
            row.file.get_path() or row.file.get_uri()
            for row in self._selected_rows()
            if row.file is not None
        ]

    @staticmethod
    def _file_for_location(location: str) -> Gio.File:
        return Gio.File.new_for_uri(location) if "://" in location else Gio.File.new_for_path(location)

    def _submit_gio_operation(
        self,
        kind: OperationKind,
        sources: list[Gio.File],
        destination: Gio.File | str | None = None,
        *,
        policy: ConflictPolicy = ConflictPolicy.RENAME,
        after: Callable[[OperationResult], None] | None = None,
    ) -> None:
        if not sources:
            self._set_status("No items selected")
            return
        self._set_status(f"{kind.value.replace('-', ' ').title()}…")
        try:
            handle = self.operations.submit_gio(
                kind,
                sources,
                destination,
                policy=policy,
                on_progress=self._on_operation_progress,
                on_complete=lambda result: self._on_operation_complete(result, after),
            )
            self.operation_controller.track(handle, kind)
        except RuntimeError as error:
            self._set_status(str(error))

    def _submit_operation(
        self,
        kind: OperationKind,
        sources: list[str],
        destination: str | None = None,
        *,
        policy: ConflictPolicy | None = None,
        after: Callable[[OperationResult], None] | None = None,
    ) -> None:
        configured = str(self.settings.get("conflict_behavior", "ask") or "ask").casefold()
        configured_policy: ConflictPolicy | None = None
        allowed = {item.value for item in ConflictPolicy} - {
            ConflictPolicy.CANCEL.value,
            ConflictPolicy.MERGE.value,
        }
        if configured in allowed:
            configured_policy = ConflictPolicy(configured)
        selected_policy = policy or configured_policy or ConflictPolicy.RENAME
        conflicts = self._operation_conflicts(kind, sources, destination)
        if conflicts and policy is None and configured == "ask":
            self._begin_conflict_queue(kind, sources, destination, selected_policy, after, conflicts)
            return
        self._submit_operation_now(kind, sources, destination, policy=selected_policy, after=after)

    @staticmethod
    def _operation_conflicts(
        kind: OperationKind,
        sources: list[str],
        destination: str | None,
    ) -> list[tuple[str, str]]:
        """Return source/target pairs requiring a user decision before I/O."""

        if kind in {OperationKind.COPY, OperationKind.MOVE, OperationKind.DUPLICATE, OperationKind.LINK}:
            if not destination or not os.path.isdir(destination):
                return []
            pairs = []
            for source in sources:
                target = os.path.join(destination, os.path.basename(os.path.normpath(source)))
                if os.path.abspath(source) != os.path.abspath(target) and os.path.lexists(target):
                    pairs.append((source, target))
            return pairs
        if kind == OperationKind.RENAME and len(sources) == 1 and destination:
            source = sources[0]
            target = os.path.join(os.path.dirname(source), destination)
            if os.path.abspath(source) != os.path.abspath(target) and os.path.lexists(target):
                return [(source, target)]
        return []

    def _begin_conflict_queue(
        self,
        kind: OperationKind,
        sources: list[str],
        destination: str | None,
        policy: ConflictPolicy,
        after: Callable[[OperationResult], None] | None,
        conflicts: list[tuple[str, str]],
    ) -> None:
        if self.pending_conflict is not None:
            self._set_status("Another conflict decision is already open")
            return
        self.pending_conflict = _PendingConflictRequest(
            kind,
            list(sources),
            destination,
            policy,
            after,
            list(conflicts),
            {},
        )
        self._show_next_conflict()

    def _show_next_conflict(self) -> None:
        request = self.pending_conflict
        if request is None:
            return
        if not request.conflicts:
            self._finish_conflict_queue()
            return
        source, target = request.conflicts[0]
        self.dialog_controller.choose_conflict(
            target,
            source,
            len(request.conflicts),
            self._on_conflict_choice,
            directory_conflict=os.path.isdir(source) and os.path.isdir(target),
        )

    def _on_conflict_choice(self, policy: ConflictPolicy, apply_all: bool) -> None:
        request = self.pending_conflict
        if request is None or not request.conflicts:
            return
        source, target = request.conflicts.pop(0)
        if policy is ConflictPolicy.CANCEL:
            self.pending_conflict = None
            self._set_status("Operation cancelled")
            return
        request.decisions[source] = policy
        if apply_all:
            incoming_is_dir = os.path.isdir(source)
            remaining: list[tuple[str, str]] = []
            for other_source, other_target in request.conflicts:
                if os.path.isdir(other_source) == incoming_is_dir and os.path.isdir(other_target) == incoming_is_dir:
                    request.decisions[other_source] = policy
                else:
                    remaining.append((other_source, other_target))
            request.conflicts = remaining
        self._show_next_conflict()

    def _finish_conflict_queue(self) -> None:
        request = self.pending_conflict
        self.pending_conflict = None
        if request is None:
            return
        grouped: dict[ConflictPolicy, list[str]] = {}
        for source in request.sources:
            selected_policy = request.decisions.get(source, request.default_policy)
            grouped.setdefault(selected_policy, []).append(source)
        groups = [(selected_policy, grouped_sources) for selected_policy, grouped_sources in grouped.items()]
        if not groups:
            self._set_status("Operation cancelled")
            return
        results: list[OperationResult] = []
        remaining_groups = len(groups)

        def on_group_complete(result: OperationResult) -> None:
            nonlocal remaining_groups
            results.append(result)
            remaining_groups -= 1
            if remaining_groups == 0 and request.after is not None:
                aggregate = OperationResult(
                    result.operation_id,
                    request.kind,
                    sum(item.completed for item in results),
                    skipped=sum(item.skipped for item in results),
                    errors=[error for item in results for error in item.errors],
                    cancelled=any(item.cancelled for item in results),
                )
                request.after(aggregate)

        for selected_policy, grouped_sources in groups:
            self._submit_operation_now(
                request.kind,
                grouped_sources,
                request.destination,
                policy=selected_policy,
                after=on_group_complete,
            )

    def _submit_operation_now(
        self,
        kind: OperationKind,
        sources: list[str],
        destination: str | None = None,
        *,
        policy: ConflictPolicy = ConflictPolicy.RENAME,
        after: Callable[[OperationResult], None] | None = None,
    ) -> None:
        if not sources:
            self._set_status("No items selected")
            return
        self._set_status(f"{kind.value.replace('-', ' ').title()}…")
        try:
            handle = self.operations.submit(
                kind,
                sources,
                destination,
                policy=policy,
                on_progress=self._on_operation_progress,
                on_complete=lambda result: self._on_operation_complete(result, after),
            )
            self.operation_controller.track(handle, kind)
        except RuntimeError as error:
            self._set_status(str(error))

    def _on_operation_progress(self, progress: object) -> None:
        self.operation_controller.on_progress(progress)
        operation_id = getattr(progress, "operation_id", None)
        current_name = str(getattr(progress, "current_name", "") or "")
        fraction = float(getattr(progress, "fraction", 0.0) or 0.0)
        self.active_operation_id = operation_id
        percent = int(fraction * 100)
        self._set_status(f"{current_name} · {percent}%" if current_name else f"Operation · {percent}%")

    def _on_operation_complete(
        self,
        result: OperationResult,
        after: Callable[[OperationResult], None] | None = None,
    ) -> None:
        self.operation_controller.on_complete(result)
        self._sync_undo_labels()
        if after is not None:
            after(result)
        if result.cancelled:
            self._set_status("Operation cancelled")
        elif result.errors:
            self._set_status("; ".join(result.errors))
        else:
            count = result.completed
            suffix = " item" if count == 1 else " items"
            self._set_status(f"{count}{suffix}")
        self.active_operation_id = None
        location = self._active_location()
        if self._last_refresh_request is not None:
            last_location, last_time = self._last_refresh_request
            if last_location == location and time.monotonic() - last_time < 0.18:
                LOGGER.debug("operation refresh coalesced location=%s", location)
                return
        self.refresh()

    def undo_last(self, *_args: object) -> None:
        result = self.operations.undo_last()
        self._sync_undo_labels()
        if result.errors:
            self._set_status("; ".join(result.errors))
        else:
            self._set_status(f"Undid {result.completed} item(s)")
            self.refresh()

    def redo_last(self, *_args: object) -> None:
        result = self.operations.redo_last()
        self._sync_undo_labels()
        if result.errors:
            self._set_status("; ".join(result.errors))
        else:
            self._set_status(f"Redid {result.completed} item(s)")
            self.refresh()

    def refresh(self, *_args: object) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            location = self.split_pane_state.location
            self._last_refresh_request = (location, time.monotonic())
            self._load_split_directory(location)
            return
        location = self.current_location or self.home
        self._last_refresh_request = (location, time.monotonic())
        self.load_directory(location, preserve_selection=True)

    def copy_selected(self, *_args: object) -> None:
        locations = self._selected_locations()
        if not locations:
            return
        self.clipboard_paths = locations
        self.clipboard_cut = False
        self._set_file_clipboard(locations, cut=False)
        self._set_status(f"Copied {len(locations)} item(s)")

    def cut_selected(self, *_args: object) -> None:
        locations = self._selected_locations()
        if not locations:
            return
        self.clipboard_paths = locations
        self.clipboard_cut = True
        self._set_file_clipboard(locations, cut=True)
        self._set_status(f"Cut {len(locations)} item(s)")

    def paste_clipboard(self, *_args: object) -> None:
        if not self.clipboard_paths:
            self._read_file_clipboard()
            return
        self._paste_paths(self.clipboard_paths, self.clipboard_cut)

    def _paste_paths(self, paths: list[str], cut: bool) -> None:
        destination = self._current_directory_path()
        if not paths:
            self._set_status("Clipboard is empty")
            return
        active_location = self._active_location()
        if destination is None:
            self._paste_gio_locations(paths, cut, active_location)
            return
        if any("://" in str(path) for path in paths):
            self._paste_gio_locations(paths, cut, active_location)
            return
        if not os.path.isdir(destination):
            self._set_status("The destination folder no longer exists")
            self.refresh()
            return
        sources = list(paths)
        was_cut = bool(cut)
        kind = OperationKind.MOVE if was_cut else OperationKind.COPY

        def clear_cut(result: OperationResult) -> None:
            if was_cut and self.clipboard_paths == sources and result.completed and not result.errors:
                self.clipboard_paths.clear()
                self.clipboard_cut = False

        self._submit_operation(kind, sources, destination, after=clear_cut)

    def _paste_gio_locations(self, locations: list[str], cut: bool, destination: str) -> None:
        capabilities = location_capabilities(destination)
        if not capabilities.exists or not capabilities.is_directory or not capabilities.can_paste:
            self._set_status("Paste is not supported by this location")
            return
        sources = [self._file_for_location(location) for location in locations]
        was_cut = bool(cut)
        kind = OperationKind.MOVE if was_cut else OperationKind.COPY

        def clear_cut(result: OperationResult) -> None:
            if was_cut and self.clipboard_paths == locations and result.completed and not result.errors:
                self.clipboard_paths.clear()
                self.clipboard_cut = False

        self._submit_gio_operation(
            kind,
            sources,
            self._file_for_location(destination),
            after=clear_cut,
        )

    def _set_file_clipboard(self, paths: list[str], *, cut: bool) -> None:
        """Publish both standard URI-list and GNOME cut/copy clipboard MIME."""

        if self.window is None or not bool(self.settings.get("external_clipboard", True)):
            return
        try:
            uris = uri_list(paths)
            operation = "cut" if cut else "copy"
            gnome_payload = f"{operation}\n{uris}"
            providers = [
                Gdk.ContentProvider.new_for_bytes(
                    "text/uri-list",
                    GLib.Bytes.new(uris.encode("utf-8")),
                ),
                Gdk.ContentProvider.new_for_bytes(
                    "x-special/gnome-copied-files",
                    GLib.Bytes.new(gnome_payload.encode("utf-8")),
                ),
            ]
            self.window.get_clipboard().set_content(Gdk.ContentProvider.new_union(providers))
        except Exception as error:
            LOGGER.debug("system file clipboard publish failed: %s", error)

    def _read_file_clipboard(self) -> None:
        if self.window is None or not bool(self.settings.get("external_clipboard", True)):
            self._set_status("Clipboard is empty")
            return
        self.clipboard_read_generation += 1
        generation = self.clipboard_read_generation
        try:
            self.window.get_clipboard().read_text_async(
                None,
                lambda clipboard, result, token=generation: self._on_file_clipboard_read(clipboard, result, token),
            )
        except Exception as error:
            self._set_status(f"Could not read clipboard: {error}")

    def _on_file_clipboard_read(self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult, generation: int) -> None:
        if generation != self.clipboard_read_generation:
            return
        try:
            text = clipboard.read_text_finish(result) or ""
        except GLib.Error as error:
            self._set_status(f"Could not read clipboard: {error.message}")
            return
        locations, cut = locations_from_clipboard_text(text)
        if not locations:
            self._set_status("Clipboard does not contain files")
            return
        locations = [self._local_path_for_location(location) or location for location in locations]
        self.clipboard_paths = locations
        self.clipboard_cut = cut
        self._paste_paths(locations, cut)

    def open_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one item to open")
            return
        self.activate_row(rows[0])

    def quick_look(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one item to preview")
            return
        self._show_quick_look_row(rows[0])

    def _show_quick_look_row(self, row: FileRow) -> None:
        self.quick_look_controller.show(row)

    def _clear_preview_content(self) -> None:
        self.quick_look_controller._clear_content()

    def _show_preview_text(self, value: str) -> None:
        self.quick_look_controller._show_text(value)

    def _on_preview_result(self, generation: int, uri: str, result: PreviewResult) -> None:
        self.quick_look_controller.on_result(generation, uri, result)

    def _on_preview_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        return self.quick_look_controller.on_key(_controller, keyval, _keycode, _state)

    def _preview_adjacent(self, delta: int) -> None:
        self.quick_look_controller.adjacent(delta)

    def _on_preview_closed(self, _window: Gtk.Window) -> bool:
        return self.quick_look_controller.on_closed(_window)

    def close_quick_look(self, *_args: object) -> None:
        self.quick_look_controller.close()

    def activate_selected(self, *_args: object) -> None:
        self.open_selected()

    def rename_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) > 1:
            self._prompt_batch_rename(rows)
            return
        if len(rows) != 1:
            self._set_status("Select one item to rename")
            return
        row = rows[0]
        path = row.file.get_path()
        if path is None:
            if not item_capabilities(row.file).can_rename:
                self._set_status("Rename is not supported by this provider")
                return
            self._prompt_for_name(
                "Rename",
                row.name,
                "Rename",
                lambda name, source=row.file: self._rename_gio_file(source, name),
            )
            return
        active_view = self._active_file_view()
        if bool(self.settings.get("inline_rename", True)) and active_view is not None:
            if active_view.begin_inline_rename():
                return
        self._prompt_for_name(
            "Rename",
            rows[0].name,
            "Rename",
            lambda name, source=path: self._rename_path(source, name),
        )

    def _rename_gio_file(self, source: Gio.File, name: str) -> None:
        if not valid_entry_name(name):
            self._set_status("Invalid name")
            return
        self._submit_gio_operation(OperationKind.RENAME, [source], name)

    def _on_inline_rename(self, row: FileRow, name: str) -> None:
        path = row.file.get_path()
        if path is None:
            self._set_status("Rename is available only for local files")
            return
        self._rename_path(path, name)

    def _prompt_batch_rename(self, rows: list[FileRow]) -> None:
        if self.window is None:
            return
        if self.name_dialog is not None:
            self.name_dialog.close()
        paths = [row.file.get_path() for row in rows]
        if any(path is None for path in paths):
            self._set_status("Batch rename is available only for local files")
            return
        source_paths = [path for path in paths if path is not None]
        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title("Batch Rename")
        dialog.set_default_size(560, 520)
        dialog.set_modal(True)
        dialog.set_transient_for(self.window)
        dialog.set_destroy_with_parent(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        def field(label_text: str, initial: str = "") -> Gtk.Entry:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=label_text)
            label.set_width_chars(12)
            label.set_halign(Gtk.Align.START)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_text(initial)
            line.append(label)
            line.append(entry)
            content.append(line)
            return entry

        find_entry = field("Find")
        replace_entry = field("Replace")
        prefix_entry = field("Prefix")
        suffix_entry = field("Suffix")
        options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        regex_check = Gtk.CheckButton(label="Regex")
        number_check = Gtk.CheckButton(label="Number")
        number_check.set_active(False)
        preserve_check = Gtk.CheckButton(label="Preserve extension")
        preserve_check.set_active(True)
        options.append(regex_check)
        options.append(number_check)
        options.append(preserve_check)
        content.append(options)
        numbering = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start_spin = Gtk.SpinButton.new_with_range(1, 999999, 1)
        padding_spin = Gtk.SpinButton.new_with_range(1, 8, 1)
        start_spin.set_value(1)
        padding_spin.set_value(2)
        numbering.append(Gtk.Label(label="Start"))
        numbering.append(start_spin)
        numbering.append(Gtk.Label(label="Padding"))
        numbering.append(padding_spin)
        content.append(numbering)
        error_label = Gtk.Label()
        error_label.add_css_class("filter-error")
        error_label.set_wrap(True)
        error_label.set_halign(Gtk.Align.START)
        content.append(error_label)
        preview_label = Gtk.Label(label="Preview")
        preview_label.add_css_class("sidebar-section-label")
        preview_label.set_halign(Gtk.Align.START)
        content.append(preview_label)
        preview = Gtk.ListBox()
        preview.set_vexpand(True)
        content.append(preview)

        def current_spec() -> BatchRenameSpec:
            return BatchRenameSpec(
                find=find_entry.get_text(),
                replace=replace_entry.get_text(),
                regex=regex_check.get_active(),
                prefix=prefix_entry.get_text(),
                suffix=suffix_entry.get_text(),
                numbering=number_check.get_active(),
                start_number=start_spin.get_value_as_int(),
                padding=padding_spin.get_value_as_int(),
                preserve_extension=preserve_check.get_active(),
            )

        def update_preview(*_args: object) -> None:
            child = preview.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                preview.remove(child)
                child = next_child
            try:
                plan = build_rename_plan(source_paths, current_spec())
            except RenamePlanError as error:
                error_label.set_text(str(error))
                return
            error_label.set_text("")
            for pair in plan[:100]:
                row = Gtk.ListBoxRow()
                row.set_child(Gtk.Label(label=f"{Path(pair.source).name}  →  {Path(pair.target).name}"))
                preview.append(row)

        for widget in (find_entry, replace_entry, prefix_entry, suffix_entry, regex_check, number_check, preserve_check, start_spin, padding_spin):
            signal = "value-changed" if isinstance(widget, Gtk.SpinButton) else "toggled" if isinstance(widget, Gtk.CheckButton) else "changed"
            widget.connect(signal, update_preview)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Rename", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK:
                try:
                    spec = current_spec()
                    build_rename_plan(source_paths, spec)
                except RenamePlanError as error:
                    error_label.set_text(str(error))
                    return
                if self.name_dialog is dialog:
                    self.name_dialog = None
                dialog.close()
                self._submit_batch_rename(source_paths, spec)
            else:
                if self.name_dialog is dialog:
                    self.name_dialog = None
                dialog.close()

        dialog.connect("response", on_response)
        self.name_dialog = dialog
        dialog.present()
        update_preview()

    def _submit_batch_rename(self, paths: list[str], spec: BatchRenameSpec) -> None:
        self._set_status("Batch rename…")
        try:
            handle = self.operations.submit(
                OperationKind.BATCH_RENAME,
                paths,
                spec,
                on_progress=self._on_operation_progress,
                on_complete=self._on_operation_complete,
            )
            self.operation_controller.track(handle, OperationKind.BATCH_RENAME)
        except RuntimeError as error:
            self._set_status(str(error))

    def _rename_path(self, source: str, name: str) -> None:
        if not valid_entry_name(name):
            self._set_status("Invalid name")
            return
        self._submit_operation(OperationKind.RENAME, [source], name)

    def move_to_trash(self, *_args: object) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        local_paths = [row.file.get_path() for row in rows]
        if all(path is not None for path in local_paths):
            paths = [path for path in local_paths if path is not None]
            submit = lambda sources=paths: self._submit_operation(OperationKind.TRASH, sources)
        else:
            if not all(item_capabilities(row.file).can_trash for row in rows):
                if all(item_capabilities(row.file).can_delete for row in rows):
                    self.delete_remote_selected()
                else:
                    self._set_status("Trash is not supported by this provider")
                return
            submit = lambda files=[row.file for row in rows]: self._submit_gio_operation(OperationKind.TRASH, files)
        self._confirm_destructive(
            "Move to Trash",
            f"Move {len(rows)} selected item(s) to Trash?",
            "Move to Trash",
            "confirm_delete",
            submit,
        )

    def delete_remote_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if not rows or any(row.file.get_path() is not None for row in rows):
            self._set_status("Permanent remote delete requires only remote items")
            return
        if not all(item_capabilities(row.file).can_delete for row in rows):
            self._set_status("Delete is not supported by this provider")
            return
        self._confirm_destructive(
            "Delete Permanently",
            f"Permanently delete {len(rows)} remote item(s)? This cannot be undone.",
            "Delete Permanently",
            "confirm_permanent_delete",
            lambda files=[row.file for row in rows]: self._submit_gio_operation(OperationKind.DELETE, files),
        )

    def restore_selected(self, *_args: object) -> None:
        """Restore selected Trash rows to their recorded original paths."""

        if self.current_location != TRASH_URI:
            self._set_status("Restore is available only in Trash")
            return
        rows = self._selected_rows()
        if not rows:
            self._set_status("Select at least one trashed item")
            return
        pending = [(row.location, row.original_path) for row in rows if row.original_path]
        if not pending:
            self._set_status("The original location is unavailable")
            return
        if len(pending) != len(rows):
            self._set_status("Some Trash items have no original location")
        for trash_uri, original in pending:
            self._submit_operation(OperationKind.RESTORE, [trash_uri], original)

    def delete_permanently_selected(self, *_args: object) -> None:
        if self.current_location != TRASH_URI:
            self._set_status("Permanent delete is available only in Trash")
            return
        paths = [row.location for row in self._selected_rows()]
        if not paths:
            self._set_status("Select at least one trashed item")
            return
        self._confirm_destructive(
            "Delete Permanently",
            f"Permanently delete {len(paths)} selected item(s)? This cannot be undone.",
            "Delete Permanently",
            "confirm_permanent_delete",
            lambda sources=paths: self._submit_operation(OperationKind.DELETE, sources),
        )

    def empty_trash(self, *_args: object) -> None:
        if self.current_location != TRASH_URI or self.file_view is None:
            self._set_status("Empty Trash is available only in Trash")
            return
        paths = [
            row.location
            for position in range(self.file_view.store.get_n_items())
            if isinstance((row := self.file_view.store.get_item(position)), FileRow)
        ]
        if not paths:
            self._set_status("Trash is already empty")
            return
        self._confirm_destructive(
            "Empty Trash",
            f"Permanently delete all {len(paths)} item(s) in Trash? This cannot be undone.",
            "Empty Trash",
            "confirm_empty_trash",
            lambda sources=paths: self._submit_operation(OperationKind.DELETE, sources),
        )

    def delete_selected(self, *_args: object) -> None:
        self.move_to_trash()

    def duplicate_selected(self, *_args: object) -> None:
        paths = self._selected_paths()
        destination = self._current_directory_path()
        if paths and destination is not None:
            self._submit_operation(OperationKind.DUPLICATE, paths, destination)

    def create_link_selected(self, *_args: object) -> None:
        paths = self._selected_paths()
        destination = self._current_directory_path()
        if paths and destination is not None:
            self._submit_operation(OperationKind.LINK, paths, destination)

    def compress_selected(self, *_args: object) -> None:
        paths = self._selected_paths()
        directory = self._current_directory_path()
        if not paths or directory is None:
            self._set_status("Select local files or folders to compress")
            return
        self._prompt_for_name(
            "Create Archive",
            "archive.zip",
            "Create",
            lambda name, sources=paths, parent=directory: self._create_archive(sources, parent, name),
        )

    def _create_archive(self, paths: list[str], directory: str, name: str) -> None:
        clean_name = str(name).strip()
        if not valid_entry_name(clean_name):
            self._set_status("Invalid archive name")
            return
        if archive_kind(clean_name) is None:
            clean_name += ".zip"
        target = os.path.join(directory, clean_name)
        self._submit_operation(OperationKind.CREATE_ARCHIVE, paths, target)

    def extract_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one archive to extract")
            return
        path = rows[0].file.get_path()
        if path is None or archive_kind(path) is None:
            self._set_status("Select a supported local ZIP or tar archive")
            return
        parent = os.path.dirname(path)
        base = os.path.basename(path)
        for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".zip", ".tar", ".tgz", ".tbz2", ".txz"):
            if base.casefold().endswith(suffix):
                base = base[: -len(suffix)]
                break
        self._prompt_for_name(
            "Extract Archive",
            base or "extracted",
            "Extract",
            lambda name, source=path, destination=parent: self._extract_archive(source, destination, name),
        )

    def _extract_archive(self, source: str, parent: str, name: str) -> None:
        clean_name = str(name).strip()
        if not valid_entry_name(clean_name):
            self._set_status("Invalid extraction folder name")
            return
        self._submit_operation(OperationKind.EXTRACT_ARCHIVE, [source], os.path.join(parent, clean_name))

    def copy_location(self, *_args: object) -> None:
        rows = self._selected_rows()
        if not rows or self.window is None:
            self._set_status("Select at least one item")
            return
        locations = [row.file.get_path() or row.location for row in rows]
        try:
            provider = Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8",
                GLib.Bytes.new("\n".join(locations).encode("utf-8")),
            )
            self.window.get_clipboard().set_content(provider)
            self._set_status(f"Copied location for {len(locations)} item(s)")
        except Exception as error:
            self._set_status(f"Could not copy location: {error}")

    @staticmethod
    def _row_drop_path(row: FileRow | None) -> str | None:
        if row is None or not row.is_dir:
            return None
        return row.file.get_uri()

    def _main_drop_feedback(self, row: FileRow | None) -> bool:
        if row is not None:
            location = self._row_drop_path(row)
            return bool(location and location_capabilities(location).can_drop)
        location = self.current_location or self.home
        return location != TRASH_URI and location_capabilities(location).can_drop

    def _split_drop_feedback(self, row: FileRow | None) -> bool:
        if row is not None:
            location = self._row_drop_path(row)
            return bool(location and location_capabilities(location).can_drop)
        location = self.split_pane_state.location if self.split_pane_state is not None else self.split_location
        return bool(location and location != TRASH_URI and location_capabilities(location).can_drop)

    @staticmethod
    def _drop_event_state(target: Gtk.DropTarget) -> Gdk.ModifierType:
        try:
            return target.get_current_event_state()
        except (AttributeError, TypeError):
            return Gdk.ModifierType(0)

    def _submit_drop(
        self,
        paths: list[str],
        destination: str,
        target: Gtk.DropTarget,
        *,
        label: str = "Drop",
    ) -> bool:
        valid, reason = validate_drop(paths, destination)
        if not valid:
            self._set_status(reason or "Drop is not allowed")
            return False
        state = self._drop_event_state(target)
        action = choose_drop_action(
            paths,
            destination,
            copy_modifier=bool(state & Gdk.ModifierType.CONTROL_MASK),
            move_modifier=bool(state & Gdk.ModifierType.SHIFT_MASK),
            link_modifier=bool(state & Gdk.ModifierType.ALT_MASK),
        )
        kind = {
            "move": OperationKind.MOVE,
            "link": OperationKind.LINK,
            "copy": OperationKind.COPY,
        }[action]
        self._submit_operation(kind, paths, destination)
        self._set_status(f"{label}: {action.title()} {len(paths)} item(s)")
        return True

    def _submit_gio_drop(
        self,
        locations: list[str],
        destination: str,
        target: Gtk.DropTarget,
        *,
        label: str = "Drop",
    ) -> bool:
        capabilities = location_capabilities(destination)
        if not capabilities.exists or not capabilities.is_directory or not capabilities.can_drop:
            self._set_status("Drop destination is unavailable")
            return False
        state = self._drop_event_state(target)
        if state & Gdk.ModifierType.ALT_MASK:
            self._set_status("Link drops are unavailable for this provider")
            return False
        kind = OperationKind.MOVE if state & Gdk.ModifierType.SHIFT_MASK else OperationKind.COPY
        self._submit_gio_operation(
            kind,
            [self._file_for_location(location) for location in locations],
            self._file_for_location(destination),
        )
        self._set_status(f"{label}: {kind.value.title()} {len(locations)} item(s)")
        return True

    @staticmethod
    def _local_path_for_location(location: str) -> str | None:
        if not location or location == TRASH_URI:
            return None
        try:
            file = Gio.File.new_for_uri(location) if "://" in location else Gio.File.new_for_path(location)
            return file.get_path()
        except (GLib.Error, TypeError, ValueError):
            return None

    def drop_location_valid(self, location: str) -> bool:
        """Return whether a breadcrumb/sidebar/tab can accept local files."""

        return location_capabilities(location).can_drop

    def handle_drop_to_location(self, value: object, location: str, target: Gtk.DropTarget) -> bool:
        locations = locations_from_uri_list(value)
        if not locations:
            return False
        if location == TRASH_URI:
            local_paths = [self._local_path_for_location(item) for item in locations]
            if all(path is not None for path in local_paths):
                paths = [path for path in local_paths if path is not None]
                submit = lambda sources=paths: self._submit_operation(OperationKind.TRASH, sources)
            else:
                submit = lambda sources=locations: self._submit_gio_operation(
                    OperationKind.TRASH,
                    [self._file_for_location(item) for item in sources],
                )
            self._confirm_destructive(
                "Move to Trash",
                f"Move {len(locations)} dropped item(s) to Trash?",
                "Move to Trash",
                "confirm_delete",
                submit,
            )
            return True
        local_sources = [self._local_path_for_location(item) for item in locations]
        destination = self._local_path_for_location(location)
        if all(path is not None for path in local_sources) and destination is not None:
            return self._submit_drop(
                [path for path in local_sources if path is not None],
                destination,
                target,
                label="Drop",
            )
        return self._submit_gio_drop(locations, location, target, label="Drop")

    def handle_drop_to_tab(self, value: object, index: int, target: Gtk.DropTarget) -> bool:
        if index < 0 or index >= len(self.tabs):
            return False
        return self.handle_drop_to_location(value, self.tabs[index].location, target)

    def handle_drop(
        self,
        value: object,
        row: FileRow | None,
        target: Gtk.DropTarget,
        *,
        pane: int | None = None,
    ) -> bool:
        locations = locations_from_uri_list(value)
        if not locations:
            return False
        destination_location: str
        if pane == 1 and self.split_pane_state is not None:
            destination_location = self.split_pane_state.location
        elif pane == 0:
            destination_location = self.current_location or self.home
        else:
            destination_location = self._active_location()
        if row is not None:
            if not row.is_dir:
                return False
            destination_location = row.file.get_uri()
        local_sources = [self._local_path_for_location(item) for item in locations]
        destination = self._local_path_for_location(destination_location)
        if all(path is not None for path in local_sources) and destination is not None:
            return self._submit_drop(
                [path for path in local_sources if path is not None],
                destination,
                target,
            )
        return self._submit_gio_drop(locations, destination_location, target)

    def open_with(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1 or self.window is None:
            self._set_status("Select one item to open with")
            return
        row = rows[0]
        apps = Gio.AppInfo.get_all_for_type(row.content_type)
        if not apps:
            self._set_status("No application is registered for this file type")
            return
        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title("Open With")
        dialog.set_default_size(420, 320)
        dialog.set_modal(True)
        dialog.set_transient_for(self.window)
        content = dialog.get_content_area()
        content.set_spacing(4)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        for application in apps[:64]:
            button = Gtk.Button(label=application.get_display_name() or application.get_name())
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, selected=application: self._launch_with(selected, row))
            content.append(button)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.connect("response", lambda current, _response: current.close())
        dialog.present()

    def _launch_with(self, application: Gio.AppInfo, row: FileRow) -> None:
        try:
            application.launch([row.file], None)
        except GLib.Error as error:
            self._set_status(f"Could not open with application: {error.message}")

    def compare_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        try:
            left, right = validate_compare_paths([row.file.get_path() or row.location for row in rows])
        except (ValueError, FileNotFoundError) as error:
            self._set_status(str(error))
            return
        tool = detect_compare_tool()
        if tool is None:
            self._set_status("No external compare tool is installed")
            return
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
            launcher.spawnv(tool.argv([left, right]))
            self._set_status(f"Started {tool.name}")
        except GLib.Error as error:
            self._set_status(f"Could not start compare tool: {error.message}")

    def show_git_status(self, *_args: object) -> None:
        directory = self._current_directory_path()
        if directory is None:
            self._set_status("Git status is available only for local folders")
            return
        adapter = GitAdapter(directory)
        if not adapter.available:
            self._set_status("This folder is not a Git repository")
            return
        statuses = adapter.status()
        if self.window is None:
            self._set_status(f"Git: {len(statuses)} changed path(s)")
            return
        if self.git_dialog is not None:
            self.git_dialog.present()
            return
        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title(f"Git · {os.path.basename(adapter.root or directory)}")
        dialog.set_default_size(680, 480)
        dialog.set_modal(False)
        dialog.set_transient_for(self.window)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        output = Gtk.TextView()
        output.set_editable(False)
        output.set_cursor_visible(False)
        output.set_monospace(True)
        output.set_wrap_mode(Gtk.WrapMode.NONE)
        output.set_vexpand(True)
        content.append(output)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for action, label in (
            ("status", "Refresh"),
            ("diff", "Diff"),
            ("log", "Log"),
            ("fetch", "Fetch"),
            ("pull", "Pull"),
            ("push", "Push"),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, selected=action, view=output: self._run_git_action_into(
                adapter, selected, view
            ))
            actions.append(button)
        content.append(actions)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda current, _response: current.close())
        dialog.connect("close-request", lambda current: self._clear_git_dialog(current))
        self.git_dialog = dialog
        self._run_git_action_into(adapter, "status", output)
        dialog.present()

    def _clear_git_dialog(self, dialog: Gtk.Dialog) -> bool:
        if self.git_dialog is dialog:
            self.git_dialog = None
        return False

    def _run_git_action_into(self, adapter: GitAdapter, action: str, output: Gtk.TextView) -> None:
        try:
            selected = self._selected_paths() if action in {"add", "unstage", "restore"} else []
            completed = adapter.run(action, selected)
            text = (completed.stdout or "") + (completed.stderr or "")
            if not text:
                text = "No output"
            output.get_buffer().set_text(text)
            self._set_status(f"Git {action}: exit {completed.returncode}")
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
            output.get_buffer().set_text(str(error))
            self._set_status(f"Git {action} failed: {error}")

    def run_git_action(self, action: str) -> None:
        directory = self._current_directory_path()
        if directory is None:
            self._set_status("Git actions are available only for local folders")
            return
        adapter = GitAdapter(directory)
        if not adapter.available:
            self._set_status("This folder is not a Git repository")
            return
        if self.git_dialog is not None:
            child = self.git_dialog.get_content_area().get_first_child()
            if isinstance(child, Gtk.TextView):
                self._run_git_action_into(adapter, action, child)
            self.git_dialog.present()
            return
        self.show_git_status()
        if self.git_dialog is not None:
            child = self.git_dialog.get_content_area().get_first_child()
            if isinstance(child, Gtk.TextView):
                self._run_git_action_into(adapter, action, child)

    def restore_git_selected(self, *_args: object) -> None:
        """Restore selected working-tree paths only after explicit confirmation."""

        paths = self._selected_paths()
        if not paths:
            return
        self._confirm_destructive(
            "Restore Git changes",
            f"Discard working-tree changes for {len(paths)} selected item(s)?",
            "Restore",
            "confirm_permanent_delete",
            lambda: self.run_git_action("restore"),
        )

    def commit_git(self, *_args: object) -> None:
        directory = self._current_directory_path()
        if directory is None or not GitAdapter(directory).available:
            self._set_status("Git commit is available only inside a repository")
            return
        self._prompt_for_name(
            "Git Commit",
            "Update files",
            "Commit",
            self._commit_git_message,
        )

    def _commit_git_message(self, message: str) -> None:
        directory = self._current_directory_path()
        if directory is None:
            return
        adapter = GitAdapter(directory)
        if not adapter.available:
            self._set_status("This folder is not a Git repository")
            return
        if not str(message).strip():
            self._set_status("Commit message is empty")
            return
        if self.git_dialog is None:
            self.show_git_status()
        if self.git_dialog is None:
            return
        child = self.git_dialog.get_content_area().get_first_child()
        if not isinstance(child, Gtk.TextView):
            return
        try:
            completed = adapter.run("commit", message=str(message).strip())
            output = (completed.stdout or "") + (completed.stderr or "") or "No output"
            child.get_buffer().set_text(output)
            self._set_status(f"Git commit: exit {completed.returncode}")
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
            child.get_buffer().set_text(str(error))
            self._set_status(f"Git commit failed: {error}")
            self.git_dialog.present()
            return
    def run_service_action(self, action: ServiceAction) -> None:
        rows = self._selected_rows()
        paths = [row.file.get_path() for row in rows]
        if any(path is None for path in paths):
            self._set_status("Service actions support local files only")
            return
        local_paths = [path for path in paths if path is not None]
        if not action.applies(local_paths, [row.content_type for row in rows]):
            self._set_status("This service does not apply to the selection")
            return
        try:
            process = run_service(action, local_paths, self._current_directory_path())
            self._set_status(f"Started {action.name} (pid {process.pid})")
        except (OSError, ValueError) as error:
            self._set_status(f"Could not run service: {error}")

    def run_selected_service(self, *_args: object) -> None:
        actions = discover_services()
        if actions:
            self.run_service_action(actions[0])
        else:
            self._set_status("No Omapath services installed")

    def create_folder(self, *_args: object) -> None:
        directory = self._current_directory_path()
        if directory is None:
            location = self._active_location()
            if not location_capabilities(location).can_create:
                self._set_status("New folders cannot be created here")
                return
            self._prompt_for_name(
                "New Folder",
                "New Folder",
                "Create",
                lambda name, parent=location: self._create_gio_path(parent, name, OperationKind.CREATE_FOLDER),
            )
            return
        self._prompt_for_name(
            "New Folder",
            "New Folder",
            "Create",
            lambda name, parent=directory: self._create_path(parent, name, OperationKind.CREATE_FOLDER),
        )

    def create_file(self, *_args: object) -> None:
        directory = self._current_directory_path()
        if directory is None:
            location = self._active_location()
            if not location_capabilities(location).can_create:
                self._set_status("New files cannot be created here")
                return
            self._prompt_for_name(
                "New File",
                "New File",
                "Create",
                lambda name, parent=location: self._create_gio_path(parent, name, OperationKind.CREATE_FILE),
            )
            return
        self._prompt_for_name(
            "New File",
            "New File",
            "Create",
            lambda name, parent=directory: self._create_path(parent, name, OperationKind.CREATE_FILE),
        )

    def _create_path(self, directory: str, name: str, kind: OperationKind) -> None:
        if not valid_entry_name(name):
            self._set_status("Invalid name")
            return
        self._submit_operation(kind, [os.path.join(directory, name)])

    def _create_gio_path(self, directory: str, name: str, kind: OperationKind) -> None:
        if not valid_entry_name(name):
            self._set_status("Invalid name")
            return
        parent = self._file_for_location(directory)
        self._submit_gio_operation(kind, [parent.get_child(name)])

    def select_all(self, *_args: object) -> None:
        view = self._active_file_view()
        if view is None:
            return
        view.select_all()
        self._set_status(f"Selected {len(view.selected_rows())} item(s)")

    def invert_selection(self, *_args: object) -> None:
        view = self._active_file_view()
        if view is None:
            return
        view.invert_selection()
        self._set_status(f"Selected {len(view.selected_rows())} item(s)")

    def _on_breadcrumb_background_pressed(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
    ) -> None:
        """Make the unused breadcrumb area a discoverable location affordance."""

        self.show_location_entry()

    def _on_breadcrumb_width_changed(self, *_args: object) -> None:
        """Debounce breadcrumb relayout while a window is being resized."""

        if self.shutting_down or self.breadcrumb_resize_source:
            return
        self.breadcrumb_resize_source = GLib.timeout_add(80, self._render_breadcrumbs_after_resize)

    def _render_breadcrumbs_after_resize(self) -> bool:
        self.breadcrumb_resize_source = 0
        if not self.shutting_down:
            self._render_breadcrumbs()
        return False

    def _set_location_error(self, message: str | None) -> None:
        if self.location_error_label is not None:
            self.location_error_label.set_text(message or "")
            self.location_error_label.set_tooltip_text(message)
            self.location_error_label.set_visible(bool(message))
        if self.location_entry is not None:
            if message:
                self.location_entry.add_css_class("location-entry-error")
                self.location_entry.set_tooltip_text(message)
            else:
                self.location_entry.remove_css_class("location-entry-error")
                self.location_entry.set_tooltip_text("Type a path or URI and press Enter")

    def _cancel_location_validation(self) -> None:
        self.location_validation_generation += 1
        if self.location_validation_cancellable is not None:
            self.location_validation_cancellable.cancel()
            self.location_validation_cancellable = None

    def _close_location_completion_enumerator(self) -> None:
        if self.location_completion_enumerator is not None:
            try:
                self.location_completion_enumerator.close(None)
            except GLib.Error:
                pass
            self.location_completion_enumerator = None

    def _cancel_location_completion(self) -> None:
        self.location_completion_generation += 1
        if self.location_completion_cancellable is not None:
            self.location_completion_cancellable.cancel()
            self.location_completion_cancellable = None
        self._close_location_completion_enumerator()
        self.location_completion_items = []
        self.location_completion_index = -1
        self._hide_location_completion()

    def _hide_location_completion(self) -> None:
        if self.location_completion_popover is not None:
            self.location_completion_popover.popdown()
        if self.location_completion_list is not None:
            child = self.location_completion_list.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                self.location_completion_list.remove(child)
                child = next_child

    def _show_location_completion(self, items: list[tuple[str, str]]) -> None:
        if self.location_completion_list is None or self.location_completion_popover is None:
            return
        self._hide_location_completion()
        if not items or not self._location_entry_is_active():
            self.location_completion_items = []
            self.location_completion_index = -1
            return
        self.location_completion_items = items[:64]
        for display, value in self.location_completion_items:
            row = Gtk.ListBoxRow()
            row.add_css_class("location-completion-row")
            label = Gtk.Label(label=display)
            label.set_halign(Gtk.Align.START)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(40)
            label.set_tooltip_text(value)
            label.set_margin_top(5)
            label.set_margin_bottom(5)
            label.set_margin_start(8)
            label.set_margin_end(8)
            row.set_child(label)
            row.set_hexpand(True)
            self.location_completion_list.append(row)
        self.location_completion_index = 0
        self.location_completion_list.select_row(self.location_completion_list.get_row_at_index(0))
        self._update_location_completion_width()
        self.location_completion_popover.popup()

    def _location_completion_is_visible(self) -> bool:
        return bool(
            self.location_completion_popover is not None
            and self.location_completion_popover.get_visible()
            and self.location_completion_items
        )

    def _select_location_completion(self, index: int) -> bool:
        if self.location_completion_list is None or not self.location_completion_items:
            return False
        count = len(self.location_completion_items)
        self.location_completion_index = index % count
        row = self.location_completion_list.get_row_at_index(self.location_completion_index)
        if row is not None:
            self.location_completion_list.select_row(row)
            row.grab_focus()
        if self.location_entry is not None:
            self.location_entry.grab_focus()
        return True

    def _complete_location_item(self, index: int | None = None) -> bool:
        if self.location_entry is None or not self.location_completion_items:
            return False
        selected = self.location_completion_index if index is None else index
        if selected < 0 or selected >= len(self.location_completion_items):
            return False
        _display, value = self.location_completion_items[selected]
        self._updating_location_entry = True
        try:
            self.location_entry.set_text(value)
            self.location_entry.set_position(-1)
        finally:
            self._updating_location_entry = False
        self._set_location_error(None)
        self._start_location_completion()
        return True

    def _on_location_completion_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._complete_location_item(row.get_index())

    def _on_location_entry_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if not self._location_entry_is_active():
            return False
        if keyval == Gdk.KEY_Escape and self._location_completion_is_visible():
            self._hide_location_completion()
            return True
        if keyval in {Gdk.KEY_Up, Gdk.KEY_Down}:
            if not self._location_completion_is_visible():
                self._start_location_completion()
            if not self._location_completion_is_visible():
                return False
            delta = -1 if keyval == Gdk.KEY_Up else 1
            return self._select_location_completion(self.location_completion_index + delta)
        tab_keyvals = {Gdk.KEY_Tab, getattr(Gdk, "KEY_ISO_Left_Tab", -1)}
        if keyval in tab_keyvals and self._location_completion_is_visible():
            return self._complete_location_item()
        return False

    def _on_location_entry_changed(self, _entry: Gtk.Entry) -> None:
        if not self._location_entry_is_active() or self._updating_location_entry:
            return
        self._set_location_error(None)
        self._start_location_completion()

    def _start_location_completion(self) -> None:
        if self.location_entry is None or not self._location_entry_is_active():
            return
        self._cancel_location_completion()
        text = self.location_entry.get_text()
        if not text.strip() and self.location_history:
            self._show_location_completion([(value, value) for value in self.location_history[:24]])
            return
        parts = completion_location_parts(text, self.home, current=self._active_location())
        if parts is None:
            return
        parent, prefix = parts
        if not parent or not os.path.isdir(parent):
            return
        self.location_completion_names: list[str] = []
        self.location_completion_parent = parent
        self.location_completion_prefix = prefix
        self.location_completion_cancellable = Gio.Cancellable()
        generation = self.location_completion_generation
        folder = Gio.File.new_for_path(parent)
        try:
            folder.enumerate_children_async(
                "standard::name,standard::type",
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.location_completion_cancellable,
                lambda source, result, token=generation, base=parent, typed=prefix: self._on_location_completion_ready(
                    source,
                    result,
                    token,
                    base,
                    typed,
                ),
            )
        except (GLib.Error, TypeError, ValueError):
            self._cancel_location_completion()

    def _on_location_completion_ready(
        self,
        source: Gio.File,
        result: Gio.AsyncResult,
        generation: int,
        parent: str,
        prefix: str,
    ) -> None:
        if generation != self.location_completion_generation or not self._location_entry_is_active():
            return
        try:
            self.location_completion_enumerator = source.enumerate_children_finish(result)
        except GLib.Error:
            self._hide_location_completion()
            return
        self._load_location_completion_chunk(self.location_completion_enumerator, generation, parent, prefix)

    def _load_location_completion_chunk(
        self,
        enumerator: Gio.FileEnumerator | None,
        generation: int,
        parent: str,
        prefix: str,
    ) -> None:
        if enumerator is None or generation != self.location_completion_generation:
            return
        if self.location_completion_cancellable is None:
            return
        enumerator.next_files_async(
            64,
            GLib.PRIORITY_DEFAULT,
            self.location_completion_cancellable,
            lambda source, result: self._on_location_completion_chunk(
                source,
                result,
                generation,
                parent,
                prefix,
            ),
        )

    def _on_location_completion_chunk(
        self,
        source: Gio.FileEnumerator,
        result: Gio.AsyncResult,
        generation: int,
        parent: str,
        prefix: str,
    ) -> None:
        if generation != self.location_completion_generation or not self._location_entry_is_active():
            return
        try:
            infos = source.next_files_finish(result)
        except GLib.Error:
            self._close_location_completion_enumerator()
            self._hide_location_completion()
            return
        for info in infos:
            if info.get_file_type() == Gio.FileType.DIRECTORY:
                name = info.get_name()
                if name:
                    self.location_completion_names.append(name)
        names = filter_completion_names(self.location_completion_names, prefix)
        if len(infos) < 64 or len(names) >= 64:
            self._close_location_completion_enumerator()
            self.location_completion_cancellable = None
            self._show_location_completion(
                [(name, os.path.join(parent, name) + os.sep) for name in names]
            )
            return
        self._load_location_completion_chunk(source, generation, parent, prefix)

    def _record_location_history(self, location: str) -> None:
        self.location_history = record_location_history(self.location_history, location)
        self.settings["location_history"] = list(self.location_history)
        save_settings(self.settings)

    def show_location_entry(self, *_args: object) -> None:
        if self.location_stack is None or self.location_entry is None:
            return
        self._cancel_location_completion()
        self._cancel_location_validation()
        self._set_location_error(None)
        location = self._active_location()
        self.location_entry.set_text(location_display_text(location, self.home))
        self.location_stack.set_visible_child_name("location-entry")
        self.location_entry.grab_focus()
        self.location_entry.select_region(0, -1)

    def _hide_location_entry(self, focus_file_view: bool = True) -> None:
        if self.location_stack is None:
            return
        self._cancel_location_completion()
        self._cancel_location_validation()
        self._set_location_error(None)
        self.location_stack.set_visible_child_name("breadcrumbs")
        if focus_file_view:
            view = self._active_file_view()
            if view is not None:
                view.active_view.grab_focus()

    def _on_location_entry_activate(self, entry: Gtk.Entry) -> None:
        target = parse_location(entry.get_text(), self.home, current=self._active_location())
        if target is None and self._location_completion_is_visible():
            # Enter on an empty/history prefix or a non-existent typed prefix
            # commits the highlighted safe completion before validating it.
            self._complete_location_item()
            target = parse_location(entry.get_text(), self.home, current=self._active_location())
        if target is None:
            self._set_location_error("Enter a folder path or URI")
            return
        if target in {TRASH_URI, RECENT_URI}:
            self._commit_location_entry(target)
            return
        error = local_location_error(target)
        if error:
            self._set_location_error(error)
            return
        if "://" in target and not target.startswith("file://"):
            self._validate_remote_location(target)
            return
        self._commit_location_entry(target)

    def _validate_remote_location(self, target: str) -> None:
        self._cancel_location_validation()
        self.location_validation_cancellable = Gio.Cancellable()
        generation = self.location_validation_generation
        location_file = Gio.File.new_for_uri(target)
        try:
            location_file.query_info_async(
                "standard::type,access::can-read",
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                self.location_validation_cancellable,
                lambda source, result: self._on_remote_location_info(
                    source,
                    result,
                    generation,
                    target,
                ),
            )
        except (GLib.Error, TypeError, ValueError) as error:
            self._set_location_error(self._location_error_from_exception(error))

    @staticmethod
    def _location_error_from_exception(error: Exception) -> str:
        message = str(getattr(error, "message", error)).casefold()
        if "permission" in message or "denied" in message or "access" in message:
            return "Permission denied"
        if "not a directory" in message:
            return "Not a folder"
        return "Location does not exist"

    def _on_remote_location_info(
        self,
        source: Gio.File,
        result: Gio.AsyncResult,
        generation: int,
        target: str,
    ) -> None:
        if generation != self.location_validation_generation or not self._location_entry_is_active():
            return
        self.location_validation_cancellable = None
        try:
            info = source.query_info_finish(result)
        except GLib.Error as error:
            self._set_location_error(self._location_error_from_exception(error))
            return
        if info.get_file_type() != Gio.FileType.DIRECTORY:
            self._set_location_error("Not a folder")
            return
        if (
            info.get_attribute_type("access::can-read") != Gio.FileAttributeType.INVALID
            and not info.get_attribute_boolean("access::can-read")
        ):
            self._set_location_error("Permission denied")
            return
        self._commit_location_entry(target)

    def _commit_location_entry(self, target: str) -> None:
        self._record_location_history(target)
        if self.active_pane == 1 and self.split_pane_state is not None:
            self._navigate_split(target)
        else:
            self.navigate(target)
        self._hide_location_entry()

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        lowered = Gdk.keyval_to_lower(keyval)
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if keyval == Gdk.KEY_Escape and self._location_entry_is_active():
            if self._location_completion_is_visible():
                self._hide_location_completion()
                return True
            self._hide_location_entry()
            return True
        if (
            self._location_entry_is_active()
            and self.location_entry is not None
            and self.location_entry.has_focus()
        ):
            # Keep Ctrl+A/C/X/V/Delete and all other editing keys inside the
            # native Gtk.Entry while the path field owns keyboard focus.
            return False
        if keyval == Gdk.KEY_Escape and (
            self.search_active
            or (self.location_stack is not None and self.location_stack.get_visible_child_name() == "search")
        ):
            self.close_search()
            return True
        if keyval == Gdk.KEY_Escape and (
            self.location_stack is not None and self.location_stack.get_visible_child_name() == "filter"
        ):
            self.toggle_filter_bar()
            return True
        if keyval == Gdk.KEY_Escape and self.responsive_drawer_widget is not None:
            self._close_responsive_drawer()
            return True
        if self._focused_widget_is_editable():
            return False
        modifier_names: list[str] = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifier_names.append("Ctrl")
        if state & Gdk.ModifierType.ALT_MASK:
            modifier_names.append("Alt")
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifier_names.append("Shift")
        if state & Gdk.ModifierType.SUPER_MASK:
            modifier_names.append("Super")
        key_name = Gdk.keyval_name(keyval)
        if key_name:
            try:
                shortcut = shortcut_from_key_event(key_name, modifier_names)
            except ValueError:
                shortcut = ""
            action = self.actions.find_shortcut(shortcut)
            if action is not None:
                return self._execute_action(action.action_id)
        if alt and keyval == Gdk.KEY_Left:
            return self._execute_action("nav.back")
        if alt and keyval == Gdk.KEY_Right:
            return self._execute_action("nav.forward")
        if alt and keyval == Gdk.KEY_Up:
            return self._execute_action("nav.up")
        if control and lowered == Gdk.KEY_l:
            return self._execute_action("nav.location")
        if control and lowered == Gdk.KEY_k:
            return self._execute_action("app.command_palette")
        if control and lowered == Gdk.KEY_i:
            return self._execute_action("view.filter")
        if control and lowered == Gdk.KEY_f:
            return self._execute_action("search.open")
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if control and shift and lowered == Gdk.KEY_t:
            return self._execute_action("tab.reopen")
        if control and lowered == Gdk.KEY_t:
            return self._execute_action("tab.new")
        if control and lowered == Gdk.KEY_w:
            return self._execute_action("tab.close")
        tab_keyvals = {Gdk.KEY_Tab, getattr(Gdk, "KEY_ISO_Left_Tab", -1)}
        if control and keyval in tab_keyvals:
            return self._execute_action("tab.previous" if shift else "tab.next")
        if control and lowered == Gdk.KEY_h:
            return self._execute_action("view.hidden")
        if control and lowered in {Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3}:
            return self._execute_action({Gdk.KEY_1: "view.icons", Gdk.KEY_2: "view.compact", Gdk.KEY_3: "view.details"}[lowered])
        keypad_add = getattr(Gdk, "KEY_KP_Add", -1)
        keypad_subtract = getattr(Gdk, "KEY_KP_Subtract", -1)
        if control and keyval in {Gdk.KEY_plus, Gdk.KEY_equal, keypad_add}:
            return self._execute_action("view.zoom_in")
        if control and keyval in {Gdk.KEY_minus, keypad_subtract}:
            return self._execute_action("view.zoom_out")
        if control and lowered == Gdk.KEY_0:
            return self._execute_action("view.zoom_reset")
        if control and lowered == Gdk.KEY_c:
            return self._execute_action("file.copy")
        if control and lowered == Gdk.KEY_x:
            return self._execute_action("file.cut")
        if control and lowered == Gdk.KEY_v:
            return self._execute_action("file.paste")
        if control and shift and lowered == Gdk.KEY_z:
            return self._execute_action("edit.redo")
        if control and lowered == Gdk.KEY_z:
            return self._execute_action("edit.undo")
        if control and shift and lowered == Gdk.KEY_a:
            return self._execute_action("selection.invert")
        if control and lowered == Gdk.KEY_a:
            return self._execute_action("selection.all")
        if keyval == Gdk.KEY_F5:
            return self._execute_action("nav.refresh")
        if keyval == Gdk.KEY_F4:
            return self._execute_action("panel.terminal")
        if keyval == Gdk.KEY_F3:
            return self._execute_action("panel.split")
        if keyval == Gdk.KEY_F7:
            return self._execute_action("panel.folders")
        if keyval == Gdk.KEY_F11:
            return self._execute_action("panel.info")
        if keyval == Gdk.KEY_F2:
            return self._execute_action("file.rename")
        if keyval == Gdk.KEY_Delete:
            return self._execute_action("file.trash")
        if keyval == Gdk.KEY_space:
            return self._execute_action("preview.quick_look")
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            return self._execute_action("file.open")
        return False

    def _focused_widget_is_editable(self) -> bool:
        if self.window is None:
            return False
        focus = self.window.get_focus()
        return isinstance(focus, Gtk.Editable)

    def _location_entry_is_active(self) -> bool:
        if self.location_stack is None or self.location_entry is None:
            return False
        return self.location_stack.get_visible_child() is self.location_entry

    def _prompt_for_name(
        self,
        title: str,
        initial: str,
        action_label: str,
        on_submit: Callable[[str], None],
    ) -> None:
        self.dialog_controller.prompt_for_name(title, initial, action_label, on_submit)

    def _confirm_destructive(
        self,
        title: str,
        message: str,
        action_label: str,
        setting_key: str,
        action: Callable[[], None],
    ) -> None:
        if not bool(self.settings.get(setting_key, True)):
            action()
            return
        self.dialog_controller.confirm_action(title, message, action_label, action)

    def _append_menu_action(
        self,
        menu: Gtk.Box,
        label: str,
        action: Callable[[], None],
        sensitive: bool = True,
    ) -> None:
        self.context_menu_controller.append_menu_action(menu, label, action, sensitive)

    def _on_context_action(self, _button: Gtk.Button, action: Callable[[], None]) -> None:
        self.context_menu_controller._on_context_action(_button, action)

    @staticmethod
    def _append_menu_separator(menu: Gtk.Box) -> None:
        ContextMenuController.append_separator(menu)

    def show_context_menu(self, row: FileRow | None, x: float, y: float) -> None:
        self.context_menu_controller.show(row, x, y)

    def show_group_context_menu(self, row: FileRow, x: float, y: float) -> None:
        """Expose grouping commands without routing a synthetic row to file actions."""

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.add_css_class("simple-context-menu")
        menu.set_margin_top(6)
        menu.set_margin_bottom(6)
        menu.set_margin_start(6)
        menu.set_margin_end(6)
        title = Gtk.Label(label=f"Group: {row.group_label}")
        title.add_css_class("sidebar-section-label")
        title.set_halign(Gtk.Align.START)
        menu.append(title)
        for criterion in (GroupCriterion.NAME, GroupCriterion.TYPE, GroupCriterion.DATE, GroupCriterion.SIZE):
            button = Gtk.Button(label=f"Group by {criterion.value.title()}")
            button.add_css_class("context-action")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, selected=criterion: self.set_group_criterion(selected))
            menu.append(button)
        ungroup = Gtk.Button(label="Ungroup")
        ungroup.add_css_class("context-action")
        ungroup.set_halign(Gtk.Align.FILL)
        ungroup.connect("clicked", lambda _button: self.set_groups_enabled(False))
        menu.append(ungroup)
        self.context_menu_controller.show_popover(menu, x, y)

    def _show_popover(self, child: Gtk.Widget, x: float, y: float) -> None:
        self.context_menu_controller.show_popover(child, x, y)

    def _on_context_popover_closed(self, popover: Gtk.Popover) -> None:
        self.context_menu_controller._on_popover_closed(popover)

    def _close_context_popover(self) -> None:
        self.context_menu_controller.close()

    def show_properties(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one item to view properties")
            return
        self.show_location_properties(rows[0].location, display_name=rows[0].display_name)

    def edit_metadata_selected(self, *_args: object) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            self._set_status("Select one local item to edit tags")
            return
        path = rows[0].file.get_path()
        if path is None or self.window is None:
            self._set_status("Tags are available only for local files")
            return
        if self.metadata_dialog is not None:
            self.metadata_dialog.close()
        current = self.metadata_store.get(rows[0].location)
        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title("Tags & Rating")
        dialog.set_default_size(460, 320)
        dialog.set_modal(True)
        dialog.set_transient_for(self.window)
        dialog.set_destroy_with_parent(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        name = Gtk.Label(label=rows[0].display_name)
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(name)
        tags = Gtk.Entry()
        tags.set_placeholder_text("tag1, tag2")
        tags.set_text(", ".join(current.tags))
        content.append(tags)
        rating_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rating_line.append(Gtk.Label(label="Rating"))
        rating = Gtk.SpinButton.new_with_range(0, 5, 1)
        rating.set_value(current.rating)
        rating_line.append(rating)
        content.append(rating_line)
        comment = Gtk.TextView()
        comment.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        comment.set_vexpand(True)
        comment.get_buffer().set_text(current.comment)
        content.append(comment)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if self.metadata_dialog is dialog:
                self.metadata_dialog = None
            if response_id == Gtk.ResponseType.OK:
                tag_values = [value.strip() for value in tags.get_text().split(",") if value.strip()]
                buffer = comment.get_buffer()
                start, end = buffer.get_bounds()
                self.metadata_store.set(
                    rows[0].location,
                    tags=tag_values,
                    rating=rating.get_value_as_int(),
                    comment=buffer.get_text(start, end, False),
                )
                if not self.metadata_store.save():
                    self._set_status("Could not save metadata")
                else:
                    self._set_status("Metadata saved")
                    self.refresh()
            dialog.close()

        dialog.connect("response", on_response)
        self.metadata_dialog = dialog
        dialog.present()
        tags.grab_focus()

    def show_location_properties(self, location: str, display_name: str | None = None) -> None:
        file = self.current_file(location)
        path = file.get_path()
        details: list[tuple[str, str]] = [
            ("Name", display_name or file.get_basename() or location),
            ("Location", location),
        ]
        if path is not None:
            try:
                metadata = metadata_for_path(path, location)
            except OSError as error:
                self._set_status(str(error))
                self.refresh()
                return
            details.extend(
                [
                    ("Type", metadata.file_type),
                    ("Size", format_size(metadata.size)),
                    ("Modified", format_date(metadata.modified)),
                    ("Permissions", metadata.permissions),
                ]
            )
        self._show_properties_popover(details)

    def _show_properties_popover(self, details: list[tuple[str, str]]) -> None:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        content.add_css_class("simple-properties")
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)
        for key, value in details:
            detail = Gtk.Label(label=f"{key}: {value}")
            detail.add_css_class("simple-property")
            detail.set_halign(Gtk.Align.START)
            detail.set_wrap(True)
            content.append(detail)
        self._show_popover(content, 0, 0)

    def _close_enumerator(self) -> None:
        if self.loading_enumerator is None:
            return
        try:
            self.loading_enumerator.close(None)
        except GLib.Error:
            pass
        self.loading_enumerator = None

    def activate_row(self, row: FileRow) -> None:
        if row.is_dir:
            self.navigate(row.location)
        else:
            self.open_default(row.location)

    def open_default(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as error:
            self._set_status(f"Could not open item: {error.message}")

    def open_terminal_here(self, *_args: object) -> None:
        directory = self._current_directory_path()
        if directory is None:
            self._set_status("Terminal is available only for local folders")
            return
        self._open_terminal_for_directory(directory)

    def toggle_terminal_panel(self, *_args: object) -> None:
        """Toggle VTE; use the external terminal action when VTE is absent."""

        if not TerminalPanel.available():
            self.open_terminal_here()
            return
        terminal_paned = self._ensure_terminal_paned()
        if terminal_paned is None:
            self.open_terminal_here()
            return
        if self.terminal_panel is None:
            self.terminal_panel = TerminalPanel(self)
            self.terminal_panel.set_visible(False)
        requested = bool(self.settings.get("show_terminal_panel", False))
        visible = not requested
        self.terminal_panel.set_visible(visible)
        self.settings["show_terminal_panel"] = visible
        save_settings(self.settings)
        if visible:
            constrained = mode_uses_sidebar_overlay(self.responsive_mode or ResponsiveMode.WIDE) or self.responsive_short_height
            if constrained:
                terminal_paned.set_end_child(None)
                self._set_status("Terminal panel will appear when the window is larger")
            else:
                terminal_paned.set_end_child(self.terminal_panel)
                terminal_paned.set_position(self._terminal_position_pixels())
                self._spawn_terminal_panel()
                self._set_status("Terminal panel opened")
        else:
            terminal_paned.set_end_child(None)
            self._set_status("Terminal panel hidden")
        self._apply_responsive_layout(force=True)

    def _spawn_terminal_panel(self) -> None:
        if self.terminal_panel is None:
            return
        directory = self._current_directory_path()
        if directory is None:
            self._set_status("Embedded terminal is available only for local folders")
            return
        if not self.terminal_panel.spawn(directory):
            self._set_status("Embedded terminal unavailable; use the fallback action")

    def _open_terminal_for_directory(self, directory: str) -> None:
        terminal = configured_terminal()
        if terminal is None:
            self._set_status("No configured terminal was found")
            return
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
            launcher.set_cwd(directory)
            shell = str(self.settings.get("terminal_shell") or "") or None
            launcher.spawnv(terminal_argv(terminal, directory, shell))
            self._set_status("Terminal started")
        except (GLib.Error, ValueError) as error:
            self._set_status(f"Could not open terminal: {error}")

    def go_back(self, *_args) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            target = self.split_pane_state.back_history.pop() if self.split_pane_state.back_history else None
            if target is not None:
                self.split_pane_state.forward_history.append(self.split_pane_state.location)
                self._navigate_split(target, record_history=False)
            return
        target = self.navigation_state.go_back()
        if target is not None:
            self.navigate(target, record_history=False)

    def go_forward(self, *_args) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            target = self.split_pane_state.forward_history.pop() if self.split_pane_state.forward_history else None
            if target is not None:
                self.split_pane_state.back_history.append(self.split_pane_state.location)
                self._navigate_split(target, record_history=False)
            return
        target = self.navigation_state.go_forward()
        if target is not None:
            self.navigate(target, record_history=False)

    def go_up(self, *_args) -> None:
        if self.active_pane == 1 and self.split_pane_state is not None:
            if self.split_pane_state.location == TRASH_URI:
                return
            parent = self.current_file(self.split_pane_state.location).get_parent()
            if parent is not None:
                self._navigate_split(parent.get_path() or parent.get_uri())
            return
        if not self.current_location or self.current_location == TRASH_URI:
            return
        parent = self.current_file().get_parent()
        if parent is not None:
            self.navigate(parent.get_path() or parent.get_uri())

    def update_navigation_ui(self) -> None:
        self._render_breadcrumbs()
        active_location = self._active_location()
        active_back = (
            self.split_pane_state.back_history
            if self.active_pane == 1 and self.split_pane_state is not None
            else self.back_history
        )
        active_forward = (
            self.split_pane_state.forward_history
            if self.active_pane == 1 and self.split_pane_state is not None
            else self.forward_history
        )
        if self.back_button is not None:
            self.back_button.set_sensitive(bool(active_back))
        if self.forward_button is not None:
            self.forward_button.set_sensitive(bool(active_forward))
        if self.up_button is not None:
            self.up_button.set_sensitive(
                bool(
                    active_location
                    and active_location != TRASH_URI
                    and self.current_file(active_location).get_parent() is not None
                )
            )
        current = self.current_location
        if self.active_pane == 1 and self.split_pane_state is not None:
            current = self.split_pane_state.location
        for location, button in self.sidebar_buttons.items():
            same = location == current
            if location != TRASH_URI and current != TRASH_URI and current and "://" not in current:
                same = os.path.abspath(location) == os.path.abspath(current)
            button.set_css_classes(
                ["sidebar-row", "flat", "sidebar-row-active"] if same else ["sidebar-row", "flat"]
            )

    def _show_hidden_breadcrumbs(
        self,
        hidden: list[tuple[str, str | None]],
        source: Gtk.Widget,
        x: float = 0.0,
        y: float = 0.0,
    ) -> None:
        """Show the path segments hidden behind the adaptive ellipsis."""

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.add_css_class("simple-context-menu")
        menu.set_margin_top(6)
        menu.set_margin_bottom(6)
        menu.set_margin_start(6)
        menu.set_margin_end(6)
        for label, target in hidden:
            if target is None:
                continue
            button = Gtk.Button(label=label)
            button.add_css_class("context-action")
            button.set_halign(Gtk.Align.FILL)
            button.set_hexpand(True)
            button.set_tooltip_text(target)
            button.connect(
                "clicked",
                lambda _button, destination=target: (
                    self._navigate_split(destination)
                    if self.active_pane == 1 and self.split_pane_state is not None
                    else self.navigate(destination)
                ),
            )
            middle = Gtk.GestureClick()
            middle.set_button(2)

            def open_new_tab(
                gesture: Gtk.GestureClick,
                _count: int,
                _x: float,
                _y: float,
                destination: str = target,
            ) -> None:
                if gesture.get_current_button() != 2:
                    return
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.open_location_new_tab(destination)

            middle.connect("pressed", open_new_tab)
            button.add_controller(middle)
            menu.append(button)
        if menu.get_first_child() is not None:
            self.topbar_controller._show_breadcrumb_popover(menu, source, x, y)

    def _render_breadcrumbs(self) -> None:
        if self.breadcrumbs is None:
            return
        child = self.breadcrumbs.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.breadcrumbs.remove(child)
            child = next_child

        location = self._active_location()
        available_width = self.breadcrumbs.get_width()
        if available_width <= 0:
            available_width = {
                ResponsiveMode.WIDE: 720,
                ResponsiveMode.MEDIUM: 480,
                ResponsiveMode.NARROW: 220,
            }.get(self.responsive_mode or ResponsiveMode.WIDE, 480)
        full_segments = _all_breadcrumb_segments(location, self.home)
        segments = breadcrumb_segments(location, self.home, available_width)
        visible_targets = {target for _label, target in segments if target is not None}
        hidden_segments = [
            (label, target)
            for label, target in full_segments
            if target is not None and target not in visible_targets
        ]
        for index, (label, target) in enumerate(segments):
            if index:
                separator = Gtk.Label(label="/")
                separator.add_css_class("breadcrumb-separator")
                separator.set_valign(Gtk.Align.CENTER)
                self.breadcrumbs.append(separator)

            if target is None:
                collapsed = Gtk.Button(label=label)
                collapsed.add_css_class("breadcrumb-button")
                collapsed.add_css_class("flat")
                collapsed.set_tooltip_text("Show hidden path segments")
                collapsed.connect(
                    "clicked",
                    lambda _button, hidden=list(hidden_segments): self._show_hidden_breadcrumbs(
                        hidden, collapsed
                    ),
                )
                context = Gtk.GestureClick()
                context.set_button(3)

                def show_collapsed_context(
                    gesture: Gtk.GestureClick,
                    _count: int,
                    x: float,
                    y: float,
                    hidden: list[tuple[str, str | None]] = list(hidden_segments),
                    source: Gtk.Button = collapsed,
                ) -> None:
                    if gesture.get_current_button() != 3:
                        return
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    self._show_hidden_breadcrumbs(hidden, source, x, y)

                context.connect(
                    "pressed",
                    show_collapsed_context,
                )
                collapsed.add_controller(context)
                middle = Gtk.GestureClick()
                middle.set_button(2)

                def open_hidden_new_tab(
                    gesture: Gtk.GestureClick,
                    _count: int,
                    _x: float,
                    _y: float,
                    hidden: list[tuple[str, str | None]] = list(hidden_segments),
                ) -> None:
                    if gesture.get_current_button() != 2:
                        return
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    destination = next((value for _name, value in hidden if value is not None), None)
                    if destination is not None:
                        self.open_location_new_tab(destination)

                middle.connect("pressed", open_hidden_new_tab)
                collapsed.add_controller(middle)
                self.breadcrumbs.append(collapsed)
                continue

            button = Gtk.Button()
            button.add_css_class("breadcrumb-button")
            button.add_css_class("flat")
            if index == len(segments) - 1:
                button.add_css_class("breadcrumb-current")
            button_label = Gtk.Label(label=label)
            button_label.set_ellipsize(Pango.EllipsizeMode.END)
            button_label.set_max_width_chars(24)
            button.set_hexpand(False)
            button.set_halign(Gtk.Align.START)
            button.set_child(button_label)
            button.set_tooltip_text(target)
            button.connect(
                "clicked",
                lambda _button, destination=target: self._navigate_split(destination)
                if self.active_pane == 1 and self.split_pane_state is not None
                else self.navigate(destination),
            )
            context = Gtk.GestureClick()
            context.set_button(3)
            context.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            context.connect(
                "pressed",
                self.topbar_controller.on_breadcrumb_context_pressed,
                target,
                button,
            )
            button.add_controller(context)
            middle = Gtk.GestureClick()
            middle.set_button(2)
            middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            middle.connect(
                "pressed",
                self.topbar_controller.on_breadcrumb_middle_pressed,
                target,
            )
            button.add_controller(middle)
            attach_drop_target(
                button,
                on_drop=lambda value, drop, destination=target: self.handle_drop_to_location(
                    value, destination, drop
                ),
                on_enter=lambda drop, _x, _y, destination=target: set_drop_highlight(
                    button, self.drop_location_valid(destination)
                ),
                on_motion=lambda drop, _x, _y, destination=target: set_drop_highlight(
                    button, self.drop_location_valid(destination)
                ),
                on_leave=lambda _drop: set_drop_highlight(button, False),
                name="breadcrumb-drop",
            )
            self.breadcrumbs.append(button)

    def display_location(self, location: str) -> str:
        return " / ".join(label for label, _target in breadcrumb_segments(location, self.home))

    def _set_status(self, value: str) -> None:
        if self.status_label is not None:
            self.status_label.set_text(value)
        if self.status_bar_label is not None:
            self.status_bar_label.set_text(value)


__all__ = [
    "DirectoryEntry",
    "FileRow",
    "OmapathApplication",
    "SimpleFileView",
    "breadcrumb_segments",
    "build_css",
    "format_item_count",
    "visible_directory_entries",
]

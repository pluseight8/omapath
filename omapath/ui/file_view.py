"""Virtualized GTK4 grid/list adapters.

The view owns a Gio.ListStore and creates widgets only for visible rows.  This
keeps 50k-entry directories model-heavy rather than widget-heavy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from ..diagnostics import LOGGER
from ..filesystem import format_date, format_size


class FileRow(GObject.Object):
    def __init__(self, item: Any) -> None:
        super().__init__()
        self.item = item


def make_file_rows(items: list[Any]) -> list[FileRow]:
    """Convert each filesystem entry to a distinct GObject model item."""

    return [FileRow(item) for item in items]


@dataclass(slots=True)
class _ItemWidgets:
    """Widgets created once for one Gtk.ListItem during ``setup``."""

    root: Gtk.Box
    group: Gtk.Label
    body: Gtk.Box
    icon: Gtk.Image
    icon_box: Gtk.Box | None = None
    name: Gtk.Label | None = None
    meta: Gtk.Label | None = None
    kind: Gtk.Label | None = None
    size: Gtk.Label | None = None
    modified: Gtk.Label | None = None
    extra: Gtk.Label | None = None
    item: Any | None = None


class VirtualFileView(Gtk.Box):
    """A reusable virtualized grid or list with keyboard/mouse/DnD hooks."""

    def __init__(
        self,
        mode: str,
        *,
        activate: Callable[[Any], None],
        context: Callable[[Gtk.Widget, Any, float, float], None],
        selection_changed: Callable[[], None],
        dropped: Callable[[Any, str | None, bool, bool], bool] | None = None,
        drag_paths: Callable[[Any], list[str]] | None = None,
        open_new_tab: Callable[[Any], None] | None = None,
        icon_name: Callable[[Any], str] | None = None,
        compact: Callable[[], bool] | None = None,
        zoom: Callable[[], int] | None = None,
        additional_info: Callable[[Any], str] | None = None,
        thumbnail: Callable[[Any, Gtk.Image, int], None] | None = None,
        thumbnail_clear: Callable[[Gtk.Image], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.mode = mode
        self._activate = activate
        self._context = context
        self._selection_changed = selection_changed
        self._dropped = dropped
        self._drag_paths = drag_paths
        self._open_new_tab = open_new_tab
        self._icon_name = icon_name or (lambda _item: "text-x-generic-symbolic")
        self._compact = compact or (lambda: False)
        self._zoom = zoom or (lambda: 100)
        self._additional_info = additional_info
        self._thumbnail = thumbnail
        self._thumbnail_clear = thumbnail_clear
        self.factory_setup_count = 0
        self.factory_bind_count = 0
        self.factory_child_count = 0
        self._item_widgets: dict[int, _ItemWidgets] = {}
        self.model = Gio.ListStore.new(FileRow)
        self.selection = Gtk.MultiSelection.new(self.model)
        self.selection.connect("selection-changed", lambda *_args: self._selection_changed())
        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._setup_item)
        self.factory.connect("bind", self._bind_item)
        self.factory.connect("unbind", self._unbind_item)
        self.factory.connect("teardown", self._teardown_item)
        if mode == "grid":
            self.view: Gtk.ListBase = Gtk.GridView.new(self.selection, self.factory)
            self.view.set_min_columns(2)
            self.view.set_max_columns(7)
            self.view.set_single_click_activate(False)
            self.view.set_enable_rubberband(True)
        else:
            self.view = Gtk.ListView.new(self.selection, self.factory)
            self.view.set_single_click_activate(False)
        self.view.set_hexpand(True)
        self.view.set_vexpand(True)
        self.view.connect("activate", self._on_activate)
        self._add_blank_context(self.view)
        if dropped is not None:
            self._add_drop_target(self.view, None)
        self.append(self.view)
        self._log_model_chain("initialized")

    def take_view(self) -> Gtk.ListBase:
        """Return the scrollable view without an intermediate Gtk.Box/Viewport."""

        if self.view.get_parent() is not None:
            self.view.unparent()
        return self.view

    @staticmethod
    def _model_count(model: Any) -> int | None:
        getter = getattr(model, "get_n_items", None)
        if getter is None:
            return None
        try:
            return int(getter())
        except (GLib.Error, TypeError, ValueError):
            return None

    def _log_model_chain(self, event: str) -> None:
        selection_model = getattr(self.selection, "get_model", lambda: None)()
        view_model = getattr(self.view, "get_model", lambda: None)()
        LOGGER.debug(
            "gtk file view event=%s mode=%s store=%s store_items=%s selection=%s selection_items=%s "
            "view=%s view_model=%s view_items=%s selection_wraps_store=%s view_uses_selection=%s",
            event,
            self.mode,
            type(self.model).__name__,
            self._model_count(self.model),
            type(self.selection).__name__,
            self._model_count(selection_model),
            type(self.view).__name__,
            type(view_model).__name__,
            self._model_count(view_model),
            selection_model is self.model,
            view_model is self.selection,
        )
        self._log_model_identity()

    @staticmethod
    def _gobject_identity(obj: Any) -> int:
        """Return the underlying GObject pointer identity when available."""

        try:
            return int(hash(obj))
        except (TypeError, ValueError):
            return id(obj)

    @classmethod
    def _list_item_key(cls, list_item: Gtk.ListItem) -> int:
        return cls._gobject_identity(list_item)

    def _log_model_identity(self) -> None:
        """Audit model object identity only in explicitly enabled debug mode."""

        if not LOGGER.isEnabledFor(logging.DEBUG):
            return
        items = [self.model.get_item(position) for position in range(self.model.get_n_items())]
        identities = [self._gobject_identity(item) for item in items if item is not None]
        file_rows = [item for item in items if isinstance(item, FileRow)]
        file_row_identities = [self._gobject_identity(item) for item in file_rows]
        unique = len(set(identities))
        unique_file_rows = len(set(file_row_identities))
        LOGGER.debug(
            "store items=%d unique model identities=%d duplicate identities=%d",
            len(items),
            unique,
            len(identities) - unique,
        )
        LOGGER.debug(
            "FileRow count=%d unique FileRow identity=%d duplicate FileRow identities=%d",
            len(file_rows),
            unique_file_rows,
            len(file_row_identities) - unique_file_rows,
        )
        children = [state.root for state in self._item_widgets.values()]
        unique_children = len({self._gobject_identity(child) for child in children})
        LOGGER.debug(
            "unique list-item children=%d setup items=%d duplicate children=%d",
            unique_children,
            len(children),
            len(children) - unique_children,
        )
        for position, row in enumerate(items):
            if isinstance(row, FileRow):
                name = getattr(row.item, "display_name", getattr(row.item, "name", ""))
                LOGGER.debug(
                    "position=%d object_id=%d gobject_pointer=%d name=%s",
                    position,
                    id(row),
                    self._gobject_identity(row),
                    name,
                )
            else:
                LOGGER.debug(
                    "position=%d object_id=%s gobject_pointer=%s name=%s",
                    position,
                    id(row) if row is not None else None,
                    self._gobject_identity(row) if row is not None else None,
                    None,
                )
        if len(identities) != unique:
            LOGGER.error("duplicate FileRow GObject identities detected mode=%s", self.mode)

    @staticmethod
    def _log_widget_geometry(widget: Gtk.Widget, mode: str, index: int, name: str) -> None:
        try:
            horizontal = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
            vertical = widget.measure(Gtk.Orientation.VERTICAL, -1)
            geometry = f"h(min={horizontal[0]},natural={horizontal[1]}) v(min={vertical[0]},natural={vertical[1]})"
        except (GLib.Error, TypeError, ValueError, RuntimeError) as error:
            geometry = f"measure-error={error}"
        LOGGER.debug(
            "factory child mode=%s index=%d name=%s type=%s visible=%s opacity=%.2f "
            "hexpand=%s vexpand=%s geometry=%s parent=%s",
            mode,
            index,
            name,
            type(widget).__name__,
            widget.get_visible(),
            widget.get_opacity(),
            widget.get_hexpand(),
            widget.get_vexpand(),
            geometry,
            type(widget.get_parent()).__name__ if widget.get_parent() is not None else None,
        )

    def _setup_item(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        self.factory_setup_count += 1
        state = self._create_item_widgets()
        self._item_widgets[self._list_item_key(list_item)] = state
        list_item.set_child(state.root)
        self._add_context(state.root, state, list_item)
        if self._drag_paths is not None:
            self._add_drag_source(state.root, state)
        if self._dropped is not None:
            self._add_drop_target(state.root, state)
        LOGGER.debug(
            "factory setup mode=%s list_item=%s child=%s visible=%s",
            self.mode,
            id(list_item),
            type(state.root).__name__,
            state.root.get_visible(),
        )

    def _bind_item(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        index = list_item.get_position()
        row = list_item.get_item()
        if not isinstance(row, FileRow):
            LOGGER.error(
                "factory bind skipped mode=%s index=%s item_type=%s expected=%s",
                self.mode,
                index,
                type(row).__name__ if row is not None else None,
                FileRow.__name__,
            )
            return
        item = row.item
        name = str(getattr(item, "display_name", getattr(item, "name", "")))
        self.factory_bind_count += 1
        LOGGER.debug(
            "factory bind index=%s name=%s mode=%s list_item_item=%s",
            index,
            name,
            self.mode,
            type(row).__name__,
        )
        try:
            state = self._item_widgets.get(self._list_item_key(list_item))
            if state is None:
                raise RuntimeError(f"factory bind without setup for list item {id(list_item)}")
            state.item = item
            self._update_item_widgets(state, item)
            self._log_widget_geometry(state.root, self.mode, index, name)
            self.factory_child_count += 1
        except Exception:
            LOGGER.exception("factory bind failed mode=%s index=%s name=%s", self.mode, index, name)
            raise

    def _unbind_item(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        state = self._item_widgets.get(self._list_item_key(list_item))
        LOGGER.debug(
            "factory unbind mode=%s index=%s child=%s",
            self.mode,
            list_item.get_position(),
            type(state.root).__name__ if state is not None else None,
        )
        # The child remains owned by Gtk.ListItem for the whole setup
        # lifetime.  GTK performs the actual child teardown after this hook.
        if state is not None:
            if self._thumbnail_clear is not None:
                self._thumbnail_clear(state.icon)
            state.item = None

    def _teardown_item(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        state = self._item_widgets.pop(self._list_item_key(list_item), None)
        if state is not None and self._thumbnail_clear is not None:
            self._thumbnail_clear(state.icon)
        LOGGER.debug("factory teardown mode=%s list_item=%s", self.mode, id(list_item))

    def _create_item_widgets(self) -> _ItemWidgets:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        root.set_hexpand(True)
        root.set_halign(Gtk.Align.FILL)
        group = Gtk.Label(label="")
        group.set_halign(Gtk.Align.START)
        group.add_css_class("group-label")
        group.set_visible(False)
        root.append(group)
        scale = max(0.6, min(1.8, float(self._zoom()) / 100.0))

        if self.mode == "grid":
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tile.set_hexpand(True)
            tile.set_halign(Gtk.Align.FILL)
            tile.add_css_class("file-tile")
            icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            icon_box.set_size_request(-1, int((54 if not self._compact() else 44) * scale))
            icon_box.set_halign(Gtk.Align.FILL)
            icon_box.set_valign(Gtk.Align.CENTER)
            icon_box.add_css_class("file-tile-icon")
            icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
            icon.set_pixel_size(int((38 if not self._compact() else 30) * scale))
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            icon_box.append(icon)
            tile.append(icon_box)
            name = Gtk.Label(label="")
            name.set_halign(Gtk.Align.START)
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_lines(2)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.add_css_class("file-tile-name")
            tile.append(name)
            meta = Gtk.Label(label="")
            meta.set_halign(Gtk.Align.START)
            meta.add_css_class("file-tile-meta")
            tile.append(meta)
            root.append(tile)
            return _ItemWidgets(root, group, tile, icon, icon_box=icon_box, name=name, meta=meta)

        if self.mode == "compact":
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_hexpand(True)
            row.set_halign(Gtk.Align.FILL)
            row.add_css_class("compact-row")
            icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
            icon.set_pixel_size(int(27 * scale))
            row.append(icon)
            name = Gtk.Label(label="")
            name.set_halign(Gtk.Align.START)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name.add_css_class("compact-name")
            row.append(name)
            meta = Gtk.Label(label="")
            meta.set_halign(Gtk.Align.END)
            meta.add_css_class("file-meta")
            row.append(meta)
            root.append(row)
            return _ItemWidgets(root, group, row, icon, name=name, meta=meta)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        row.set_halign(Gtk.Align.FILL)
        row.add_css_class("list-row")
        icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        icon.set_pixel_size(int(30 * scale))
        row.append(icon)
        name = Gtk.Label(label="")
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.add_css_class("list-name")
        row.append(name)
        type_label = Gtk.Label(label="")
        type_label.set_width_chars(16)
        type_label.set_max_width_chars(20)
        type_label.set_halign(Gtk.Align.END)
        type_label.set_ellipsize(Pango.EllipsizeMode.END)
        type_label.add_css_class("file-meta")
        row.append(type_label)
        size_label = Gtk.Label(label="")
        size_label.set_width_chars(10)
        size_label.set_max_width_chars(12)
        size_label.set_halign(Gtk.Align.END)
        size_label.set_ellipsize(Pango.EllipsizeMode.END)
        size_label.add_css_class("file-meta")
        row.append(size_label)
        modified = Gtk.Label(label="")
        modified.set_width_chars(18)
        modified.set_max_width_chars(20)
        modified.set_halign(Gtk.Align.END)
        modified.set_ellipsize(Pango.EllipsizeMode.END)
        modified.add_css_class("file-meta")
        row.append(modified)
        extra = None
        if self._additional_info is not None:
            extra = Gtk.Label(label="")
            extra.set_width_chars(12)
            extra.set_halign(Gtk.Align.END)
            extra.set_ellipsize(Pango.EllipsizeMode.END)
            extra.add_css_class("file-meta")
            row.append(extra)
        root.append(row)
        return _ItemWidgets(
            root,
            group,
            row,
            icon,
            name=name,
            kind=type_label,
            size=size_label,
            modified=modified,
            extra=extra,
        )

    def _update_item_widgets(self, state: _ItemWidgets, item: Any) -> None:
        scale = max(0.6, min(1.8, float(self._zoom()) / 100.0))
        group = str(getattr(item, "group", "") or "")
        state.group.set_text(group.upper())
        state.group.set_visible(bool(group))
        state.icon.set_from_icon_name(self._icon_name(item))
        if state.icon_box is not None:
            state.icon_box.set_size_request(-1, int((54 if not self._compact() else 44) * scale))
            state.icon.set_pixel_size(int((38 if not self._compact() else 30) * scale))
        elif self.mode == "compact":
            state.icon.set_pixel_size(int(27 * scale))
        else:
            state.icon.set_pixel_size(int(30 * scale))
        if state.name is not None:
            state.name.set_text(str(getattr(item, "display_name", getattr(item, "name", ""))))
        if state.meta is not None:
            if self.mode == "grid" and getattr(item, "folder_size", None) is not None:
                meta = format_size(int(item.folder_size))
            elif getattr(item, "is_dir", False):
                meta = "Folder"
            else:
                meta = format_size(int(getattr(item, "size", 0)))
            state.meta.set_text(meta)
        if state.kind is not None:
            kind = "Folder" if getattr(item, "is_dir", False) else getattr(item, "content_type", "File") or "File"
            state.kind.set_text(kind)
        if state.size is not None:
            size = "—" if getattr(item, "is_dir", False) else format_size(int(getattr(item, "size", 0)))
            state.size.set_text(size)
        if state.modified is not None:
            state.modified.set_text(format_date(getattr(item, "modified", 0)))
        if state.extra is not None and self._additional_info is not None:
            state.extra.set_text(self._additional_info(item))
        if self._thumbnail_clear is not None:
            self._thumbnail_clear(state.icon)
        if self._thumbnail is not None:
            thumbnail_size = (
                int((42 if not self._compact() else 34) * scale)
                if self.mode == "grid"
                else int((30 if self.mode == "compact" else 32) * scale)
            )
            self._thumbnail(item, state.icon, thumbnail_size)

    def _on_activate(self, _view: Gtk.ListBase, position: int) -> None:
        row = self.model.get_item(position)
        if isinstance(row, FileRow):
            self._activate(row.item)

    def _add_context(self, widget: Gtk.Widget, state: _ItemWidgets, list_item: Gtk.ListItem) -> None:
        if self._open_new_tab is not None:
            middle = Gtk.GestureClick()
            middle.set_button(2)
            middle.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

            def open_middle(active: Gtk.GestureClick, _count: int, _x: float, _y: float) -> None:
                item = state.item
                if item is None or not getattr(item, "is_dir", False):
                    return
                active.set_state(Gtk.EventSequenceState.CLAIMED)
                self._open_new_tab(item)

            middle.connect("pressed", open_middle)
            widget.add_controller(middle)

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def pressed(active: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
            item = state.item
            if item is None:
                return
            position = list_item.get_position()
            if position >= 0 and not self.selection.is_selected(position):
                self.selection.select_item(position, False)
            active.set_state(Gtk.EventSequenceState.CLAIMED)
            self._context(widget, item, x, y)

        gesture.connect("pressed", pressed)
        widget.add_controller(gesture)

    def _add_blank_context(self, widget: Gtk.Widget) -> None:
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        gesture.connect("pressed", lambda _g, _n, x, y: self._context(widget, None, x, y))
        widget.add_controller(gesture)

    def _add_drag_source(self, widget: Gtk.Widget, state: _ItemWidgets) -> None:
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)

        def prepare(_source: Gtk.DragSource, _x: float, _y: float) -> Gdk.ContentProvider | None:
            item = state.item
            paths = self._drag_paths(item) if self._drag_paths is not None and item is not None else []
            uris = []
            for path in paths:
                try:
                    uris.append(Gio.File.new_for_path(path).get_uri())
                except (TypeError, ValueError):
                    continue
            if not uris:
                return None
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_value("\n".join(uris))
            return Gdk.ContentProvider.new_for_value(value)

        source.connect("prepare", prepare)
        widget.add_controller(source)

    def _add_drop_target(self, widget: Gtk.Widget, state: _ItemWidgets | None) -> None:
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        target.set_gtypes([GObject.TYPE_STRING, Gdk.FileList])
        def on_drop(active_target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
            if self._dropped is None:
                return False
            if state is not None:
                item = state.item
                if item is None or not getattr(item, "is_dir", False) or not getattr(item, "path", None):
                    return False
                destination = item.path
            else:
                destination = None
            current_drop = active_target.get_current_drop()
            drag = current_drop.get_drag() if current_drop is not None else None
            action = drag.get_selected_action() if drag is not None else Gdk.DragAction.COPY
            return self._dropped(value, destination, action == Gdk.DragAction.MOVE, action == Gdk.DragAction.LINK)

        target.connect("drop", on_drop)
        target.connect("enter", lambda *_args: self._target_enter(widget, state))
        target.connect("leave", lambda *_args: widget.remove_css_class("drop-target"))
        widget.add_controller(target)

    @staticmethod
    def _target_enter(widget: Gtk.Widget, state: _ItemWidgets | None = None) -> None:
        if state is not None:
            item = state.item
            if item is None or not getattr(item, "is_dir", False) or not getattr(item, "path", None):
                return
        widget.add_css_class("drop-target")

    def set_items(self, items: list[Any]) -> None:
        LOGGER.debug("file view set_items mode=%s incoming=%d", self.mode, len(items))
        self.model.remove_all()
        self.append_items(items)
        self._log_model_chain("set_items")

    def append_items(self, items: list[Any]) -> None:
        LOGGER.debug("file view append_items mode=%s incoming=%d", self.mode, len(items))
        for row in make_file_rows(items):
            self.model.append(row)
        if items:
            self._log_model_chain("append_items")

    def get_selected_items(self) -> list[Any]:
        result: list[Any] = []
        for position in range(self.model.get_n_items()):
            if self.selection.is_selected(position):
                row = self.model.get_item(position)
                if isinstance(row, FileRow):
                    result.append(row.item)
        return result

    def get_selected_item(self) -> Any | None:
        selected = self.get_selected_items()
        return selected[0] if selected else None

    def select_all(self) -> None:
        self.selection.select_all()

    def unselect_all(self) -> None:
        self.selection.unselect_all()

    def invert_selection(self) -> None:
        """Invert selection without materializing any row widgets."""

        selected = [
            self.selection.is_selected(position)
            for position in range(self.model.get_n_items())
        ]
        for position, is_selected in enumerate(selected):
            if is_selected:
                self.selection.unselect_item(position)
            else:
                self.selection.select_item(position, False)

    def select_uris(self, uris: set[str]) -> None:
        """Restore selection after a model refresh without creating row widgets."""
        self.selection.unselect_all()
        if not uris:
            return
        for position in range(self.model.get_n_items()):
            row = self.model.get_item(position)
            if isinstance(row, FileRow) and getattr(row.item, "uri", "") in uris:
                self.selection.select_item(position, False)

    def set_single_click_activate(self, enabled: bool) -> None:
        self.view.set_single_click_activate(enabled)

    def set_zoom(self, level: int) -> None:
        self._zoom = lambda: level

    def set_max_children_per_line(self, count: int) -> None:
        if self.mode == "grid":
            self.view.set_max_columns(count)  # type: ignore[attr-defined]

    def set_min_children_per_line(self, count: int) -> None:
        if self.mode == "grid":
            self.view.set_min_columns(count)  # type: ignore[attr-defined]

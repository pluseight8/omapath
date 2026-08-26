"""Optional lazy folders panel using GTK4 TreeListModel."""

from __future__ import annotations

import os
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GObject, Gtk, Pango

from .dnd import attach_drop_target, file_content_provider, set_drop_highlight


class FolderNode(GObject.Object):
    def __init__(self, file: Gio.File, name: str | None = None) -> None:
        super().__init__()
        self.file = file
        self.name = name or file.get_basename() or file.get_path() or file.get_uri()


def _folder_children(node: FolderNode) -> Gio.ListStore:
    children = Gio.ListStore.new(FolderNode)
    try:
        enumerator = node.file.enumerate_children(
            "standard::name,standard::display-name,standard::type,standard::is-hidden",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
    except Exception:
        return children
    try:
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            if info.get_attribute_boolean("standard::is-hidden") or info.get_file_type() != Gio.FileType.DIRECTORY:
                continue
            name = info.get_name()
            children.append(FolderNode(node.file.get_child(name), info.get_display_name() or name))
    finally:
        try:
            enumerator.close(None)
        except Exception:
            pass
    return children


class FoldersPanel(Gtk.ScrolledWindow):
    """A folders-only tree that enumerates children only when expanded."""

    def __init__(
        self,
        home: str,
        navigate: Callable[[str], None],
        drop_callback: Callable[[object, str, Gtk.DropTarget], bool] | None = None,
    ) -> None:
        super().__init__()
        self.add_css_class("folders-panel")
        self.set_hexpand(False)
        self.set_vexpand(True)
        self.navigate = navigate
        self.drop_callback = drop_callback
        roots = Gio.ListStore.new(FolderNode)
        roots.append(FolderNode(Gio.File.new_for_path(home), "Home"))
        self.model = Gtk.TreeListModel.new(roots, False, False, _folder_children)
        self.selection = Gtk.SingleSelection.new(self.model)
        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._on_setup)
        self.factory.connect("bind", self._on_bind)
        self.view = Gtk.ListView.new(self.selection, self.factory)
        self.view.add_css_class("folders-list")
        self.view.connect("activate", self._on_activate)
        self.set_child(self.view)

    def _on_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        label = Gtk.Label()
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(28)
        expander.set_child(label)
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)

        def prepare(
            _source: Gtk.DragSource,
            _x: float,
            _y: float,
            item: Gtk.ListItem = list_item,
        ) -> Gdk.ContentProvider | None:
            tree_row = item.get_item()
            node = tree_row.get_item() if isinstance(tree_row, Gtk.TreeListRow) else None
            path = node.file.get_path() if isinstance(node, FolderNode) else None
            return file_content_provider([path]) if path is not None else None

        source.connect("prepare", prepare)
        expander.add_controller(source)
        if self.drop_callback is not None:
            attach_drop_target(
                expander,
                on_drop=lambda value, target, item=list_item: self._on_drop(value, target, item),
                on_enter=lambda target, _x, _y, item=list_item, source=expander: self._tree_drop_feedback(
                    target, item, source
                ),
                on_motion=lambda target, _x, _y, item=list_item, source=expander: self._tree_drop_feedback(
                    target, item, source
                ),
                on_leave=lambda _target, source=expander: set_drop_highlight(source, False),
                name="folder-tree-drop",
            )
        list_item.set_child(expander)

    def _tree_drop_feedback(
        self,
        _target: Gtk.DropTarget,
        list_item: Gtk.ListItem,
        source: Gtk.Widget,
    ) -> Gdk.DragAction:
        tree_row = list_item.get_item()
        node = tree_row.get_item() if isinstance(tree_row, Gtk.TreeListRow) else None
        valid = isinstance(node, FolderNode) and node.file.get_path() is not None
        return set_drop_highlight(source, valid)

    def _on_drop(self, value: object, target: Gtk.DropTarget, list_item: Gtk.ListItem) -> bool:
        if self.drop_callback is None:
            return False
        tree_row = list_item.get_item()
        node = tree_row.get_item() if isinstance(tree_row, Gtk.TreeListRow) else None
        if not isinstance(node, FolderNode):
            return False
        return bool(self.drop_callback(value, node.file.get_path() or node.file.get_uri(), target))

    @staticmethod
    def _on_bind(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        tree_row = list_item.get_item()
        expander = list_item.get_child()
        if not isinstance(tree_row, Gtk.TreeListRow) or not isinstance(expander, Gtk.TreeExpander):
            return
        child = expander.get_child()
        node = tree_row.get_item()
        if isinstance(child, Gtk.Label) and isinstance(node, FolderNode):
            child.set_text(node.name)
            child.set_tooltip_text(node.file.get_uri())
        expander.set_list_row(tree_row)

    def _on_activate(self, _view: Gtk.ListView, position: int) -> None:
        tree_row = self.model.get_child_row(position)
        if tree_row is None:
            return
        node = tree_row.get_item()
        if isinstance(node, FolderNode):
            path = node.file.get_path()
            self.navigate(path or node.file.get_uri())


__all__ = ["FolderNode", "FoldersPanel"]

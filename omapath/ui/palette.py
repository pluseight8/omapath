"""Keyboard-first fuzzy command palette."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk, Pango

from ..compat import set_accessible_label


@dataclass(slots=True)
class PaletteAction:
    action_id: str
    label: str
    callback: Callable[[], None]
    shortcut: str = ""
    enabled: bool = True


def fuzzy_score(query: str, candidate: str) -> int | None:
    query = query.casefold().strip()
    candidate_folded = candidate.casefold()
    if not query:
        return 0
    position = -1
    score = 0
    for character in query:
        position = candidate_folded.find(character, position + 1)
        if position < 0:
            return None
        score += 10 if position == 0 or candidate[position - 1] in " _-/" else 1
    score -= len(candidate) - len(query)
    return score


class CommandPalette(Gtk.Window):
    def __init__(self, parent: Gtk.Window, actions: list[PaletteAction]) -> None:
        super().__init__(title="Command Palette", transient_for=parent, modal=True)
        parent_width = parent.get_width() or 620
        parent_height = parent.get_height() or 480
        self.set_default_size(
            max(420, min(620, parent_width - 40)),
            max(320, min(480, parent_height - 80)),
        )
        self.set_resizable(False)
        self.actions = actions
        self.filtered: list[PaletteAction] = []
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shell.set_margin_top(14)
        shell.set_margin_bottom(14)
        shell.set_margin_start(14)
        shell.set_margin_end(14)
        shell.add_css_class("palette-shell")
        title = Gtk.Label(label="COMMAND PALETTE")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("eyebrow")
        shell.append(title)
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Search actions…")
        set_accessible_label(self.entry, "Search commands")
        self.entry.connect("search-changed", lambda *_args: self._refresh())
        self.entry.connect("activate", lambda *_args: self._activate_selected())
        shell.append(self.entry)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda _box, row: self._activate_row(row))
        shell.append(self.listbox)
        self.set_child(shell)
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.add_controller(controller)
        self.connect("close-request", lambda *_args: self._clear())
        self._refresh()

    def _refresh(self) -> None:
        query = self.entry.get_text()
        self.filtered = sorted(
            [action for action in self.actions if action.enabled and fuzzy_score(query, action.label) is not None],
            key=lambda action: fuzzy_score(query, action.label) or 0,
            reverse=True,
        )
        child = self.listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.listbox.remove(child)
            child = next_child
        for action in self.filtered:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            set_accessible_label(row, action.label)
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=action.label)
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            line.append(label)
            if action.shortcut:
                shortcut = Gtk.Label(label=action.shortcut)
                shortcut.add_css_class("shortcut-hint")
                line.append(shortcut)
            row.set_child(line)
            self.listbox.append(row)
        if self.filtered:
            self.listbox.select_row(self.listbox.get_row_at_index(0))

    def _activate_selected(self) -> None:
        row = self.listbox.get_selected_row()
        if row is not None:
            self._activate_row(row)

    def _activate_row(self, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if 0 <= index < len(self.filtered):
            action = self.filtered[index]
            self.close()
            action.callback()

    def _on_key(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _clear(self) -> None:
        self.listbox.unselect_all()

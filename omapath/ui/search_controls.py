"""GTK filter/search control construction for the active window."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango

from ..filtering import FilterMode


class SearchControls:
    """Build controls and connect them to host-owned search/filter callbacks."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def build_filter_bar(self) -> Gtk.Box:
        host = self.host
        if host.filter_bar is not None:
            return host.filter_bar
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("filter-mode")
        bar.set_hexpand(True)
        label = Gtk.Label(label="Filter")
        label.add_css_class("sidebar-section-label")
        bar.append(label)
        host.filter_entry = Gtk.SearchEntry()
        host.filter_entry.set_hexpand(True)
        host.filter_entry.set_placeholder_text("Name, glob or regular expression")
        host.filter_entry.connect("search-changed", host._on_filter_changed)
        bar.append(host.filter_entry)
        host.filter_mode_combo = Gtk.ComboBoxText()
        host.filter_mode_combo.append(FilterMode.PLAIN.value, "Plain")
        host.filter_mode_combo.append(FilterMode.GLOB.value, "Glob")
        host.filter_mode_combo.append(FilterMode.REGEX.value, "Regex")
        host.filter_mode_combo.set_active_id(host.filter_mode.value)
        host.filter_mode_combo.connect("changed", host._on_filter_mode_changed)
        bar.append(host.filter_mode_combo)
        host.filter_error_label = Gtk.Label()
        host.filter_error_label.add_css_class("filter-error")
        host.filter_error_label.set_ellipsize(Pango.EllipsizeMode.END)
        host.filter_error_label.set_max_width_chars(28)
        host.filter_error_label.set_visible(False)
        bar.append(host.filter_error_label)
        host.filter_bar = bar
        return bar

    def build_topbar_search(self) -> Gtk.Box:
        """Build the fixed-height search mode used inside the topbar stack."""

        host = self.host
        if host.search_bar is not None:
            return host.search_bar
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("search-mode")
        bar.set_hexpand(True)
        host.search_entry = Gtk.SearchEntry()
        host.search_entry.set_hexpand(True)
        host.search_entry.set_placeholder_text("Search this folder")
        host.search_entry.connect("search-changed", host._on_search_changed)
        bar.append(host.search_entry)

        options_button = Gtk.MenuButton()
        options_button.add_css_class("toolbar-button")
        options_button.set_tooltip_text("Search options")
        options_button.set_child(Gtk.Image.new_from_icon_name("open-menu-symbolic"))
        options_button.set_popover(self._build_search_options())
        bar.append(options_button)

        close_button = Gtk.Button()
        close_button.add_css_class("toolbar-button")
        close_button.set_tooltip_text("Close search (Esc)")
        close_button.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        close_button.connect("clicked", host.close_search)
        bar.append(close_button)

        host.search_bar = bar
        return bar

    def _build_search_options(self) -> Gtk.Popover:
        host = self.host
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("simple-context-menu")
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        recursive = Gtk.CheckButton(label="Include subfolders")
        recursive.set_active(bool(host.settings.get("search_recursive", True)))
        recursive.connect("toggled", host._on_search_changed)
        box.append(recursive)
        contents = Gtk.CheckButton(label="Inside text")
        contents.set_active(bool(host.settings.get("search_contents", False)))
        contents.connect("toggled", host._on_search_changed)
        box.append(contents)
        case_sensitive = Gtk.CheckButton(label="Case sensitive")
        case_sensitive.set_active(bool(host.settings.get("search_case_sensitive", False)))
        case_sensitive.connect("toggled", host._on_search_changed)
        box.append(case_sensitive)
        include_hidden = Gtk.CheckButton(label="Include hidden files")
        include_hidden.set_active(bool(host.settings.get("search_show_hidden", False)))
        include_hidden.connect("toggled", host._on_search_changed)
        box.append(include_hidden)

        host.search_recursive_check = recursive
        host.search_contents_check = contents
        host.search_case_check = case_sensitive
        host.search_hidden_check = include_hidden
        host.search_status_label = Gtk.Label()
        host.search_status_label.add_css_class("item-count")
        host.search_status_label.set_ellipsize(Pango.EllipsizeMode.END)
        popover.set_child(box)
        return popover

    def build_search_bar(self) -> Gtk.Box:
        """Compatibility entry point; the search surface now lives in topbar."""

        return self.build_topbar_search()


__all__ = ["SearchControls"]

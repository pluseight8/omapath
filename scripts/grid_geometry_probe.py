#!/usr/bin/env python3
"""Measure GTK4 GridView geometry without any Omapath UI layers.

This probe is intentionally independent from the application.  It answers
whether a plain Gio.ListStore -> Gtk.MultiSelection -> Gtk.GridView chain can
keep sane geometry while the model grows from a handful of rows to a large
directory-sized model.
"""

from __future__ import annotations

import math
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango


COUNTS = (10, 20, 100, 1000, 4000)
CELL_WIDTH = 120
CELL_HEIGHT = 96
ICON_SIZE = 48


class ProbeRow(GObject.GObject):
    name = GObject.Property(type=str, default="")

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class Probe:
    def __init__(self, application: Gtk.Application) -> None:
        self.application = application
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_title("Omapath Grid Geometry Probe")
        self.window.set_default_size(950, 700)

        self.store = Gio.ListStore.new(ProbeRow)
        self.selection = Gtk.MultiSelection.new(self.store)
        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._on_setup)
        self.factory.connect("bind", self._on_bind)
        self.factory.connect("unbind", self._on_unbind)
        self.factory.connect("teardown", self._on_teardown)
        self.view = Gtk.GridView.new(self.selection, self.factory)
        self.view.set_min_columns(1)
        self.view.set_max_columns(20)
        self.view.set_hexpand(True)
        self.view.set_vexpand(True)
        self.view.add_css_class("probe-grid")

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_vexpand(True)
        self.scrolled.set_child(self.view)
        self.window.set_child(self.scrolled)

        self.bound: dict[int, Gtk.ListItem] = {}
        self.item_samples: dict[int, tuple[int, int, int, int]] = {}
        self.index = 0
        self.report_source = 0

    @staticmethod
    def _on_setup(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.set_size_request(CELL_WIDTH, CELL_HEIGHT)
        root.set_halign(Gtk.Align.CENTER)
        root.set_valign(Gtk.Align.START)
        icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        icon.set_pixel_size(ICON_SIZE)
        icon.set_halign(Gtk.Align.CENTER)
        label = Gtk.Label()
        label.set_lines(2)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(16)
        label.set_halign(Gtk.Align.CENTER)
        root.append(icon)
        root.append(label)
        list_item.set_child(root)

    def _on_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        row = list_item.get_item()
        child = list_item.get_child()
        if isinstance(row, ProbeRow) and isinstance(child, Gtk.Box):
            label = child.get_last_child()
            if isinstance(label, Gtk.Label):
                label.set_text(row.name)
        self.bound[id(list_item)] = list_item

    def _on_unbind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        self.bound.pop(id(list_item), None)

    def _on_teardown(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        self.bound.pop(id(list_item), None)

    def _populate(self, count: int) -> None:
        rows = [ProbeRow(f"probe-{index:04d} two line name") for index in range(count)]
        self.store.splice(0, self.store.get_n_items(), rows)
        self.item_samples.clear()

    @staticmethod
    def _allocation(widget: Gtk.Widget) -> tuple[int, int, int, int]:
        # GTK4/PyGObject deprecated get_allocation(); this probe only needs
        # the allocated size. Coordinates are intentionally normalized to
        # zero because they do not affect the scroll-extent measurement.
        return (0, 0, int(widget.get_width()), int(widget.get_height()))

    def _report(self) -> bool:
        window_allocation = self._allocation(self.window)
        scrolled_allocation = self._allocation(self.scrolled)
        view_allocation = self._allocation(self.view)
        adjustment = self.scrolled.get_vadjustment()
        upper = adjustment.get_upper()
        page_size = adjustment.get_page_size()
        value = adjustment.get_value()
        columns = max(1, int(view_allocation[2] / CELL_WIDTH))
        columns = min(columns, self.view.get_max_columns())
        rows = math.ceil(self.store.get_n_items() / columns)
        samples: list[tuple[int, int, int, int]] = []
        for list_item in tuple(self.bound.values())[:5]:
            child = list_item.get_child()
            if isinstance(child, Gtk.Widget):
                allocation = self._allocation(child)
                samples.append(allocation)
        print(
            "count=%d window=%s scrolled=%s view=%s vadj=(lower=%.1f upper=%.1f page_size=%.1f value=%.1f) "
            "realized_list_items=%d estimated_columns=%d estimated_rows=%d item_samples=%s "
            "view_measure=(min_h=%d natural_h=%d min_w=%d natural_w=%d)"
            % (
                self.store.get_n_items(),
                window_allocation,
                scrolled_allocation,
                view_allocation,
                adjustment.get_lower(),
                upper,
                page_size,
                value,
                len(self.bound),
                columns,
                rows,
                samples,
                self.view.measure(Gtk.Orientation.VERTICAL, -1)[0],
                self.view.measure(Gtk.Orientation.VERTICAL, -1)[1],
                self.view.measure(Gtk.Orientation.HORIZONTAL, -1)[0],
                self.view.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
            ),
            flush=True,
        )
        self.index += 1
        if self.index >= len(COUNTS):
            GLib.timeout_add(400, self.application.quit)
            return False
        GLib.timeout_add(300, self._next_count)
        return False

    def _next_count(self) -> bool:
        self._populate(COUNTS[self.index])
        self.report_source = GLib.timeout_add(300, self._report)
        return False

    def start(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            print("grid geometry probe: no graphical display", file=sys.stderr)
            self.application.quit()
            return
        self.window.present()
        self._populate(COUNTS[0])
        self.report_source = GLib.timeout_add(300, self._report)


def main() -> int:
    application = Gtk.Application(application_id="dev.omarchy.Omapath.GeometryProbe")
    probe: Probe | None = None

    def activate(app: Gtk.Application) -> None:
        nonlocal probe
        probe = Probe(app)
        probe.start()

    application.connect("activate", activate)
    return application.run([])


if __name__ == "__main__":
    raise SystemExit(main())

"""Small, explicit GTK4/PyGObject compatibility helpers.

Only APIs whose binding spelling or calling convention is easy to confuse
with GTK3 are kept here.  The rest of the UI calls the GTK4 binding directly
so unsupported methods remain visible during review and smoke testing.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject, Gtk


def set_accessible_label(widget: Gtk.Widget, label: str) -> None:
    """Set a GTK4 accessible label using the binding-safe array API.

    GTK4 does not expose the GTK3-era ``set_accessible_name`` convenience
    method on ``Gtk.Widget``.  ``update_property`` is the API intended for
    language bindings and requires a ``GValue`` for each property.
    """

    value = GObject.Value(GObject.TYPE_STRING, label)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [value])

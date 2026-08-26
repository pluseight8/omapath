"""Quiet diagnostics for development and support sessions."""

from __future__ import annotations

import logging
import os
import platform

import gi


LOGGER = logging.getLogger("omapath")


def trace_items_enabled() -> bool:
    """Return whether high-volume per-item GTK diagnostics were requested."""

    value = os.environ.get("OMAPATH_TRACE_ITEMS", "").casefold()
    return value in {"1", "true", "yes", "on", "trace"}


def configure_logging() -> None:
    """Enable useful lifecycle logs only when explicitly requested."""
    value = os.environ.get("OMAPATH_DEBUG", "").casefold()
    if value not in {"1", "true", "yes", "on", "debug"}:
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def log_runtime_versions() -> None:
    """Log the GTK/PyGObject runtime only for an explicitly debugged launch."""

    if not LOGGER.isEnabledFor(logging.DEBUG):
        return
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk

        gtk_version = ".".join(
            str(value) for value in (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
        )
        glib_version = ".".join(str(value) for value in (GLib.MAJOR_VERSION, GLib.MINOR_VERSION, GLib.MICRO_VERSION))
        pygobject_version = getattr(gi, "__version__", "unknown")
        LOGGER.debug(
            "runtime versions python=%s pygobject=%s gtk=%s glib=%s",
            platform.python_version(),
            pygobject_version,
            gtk_version,
            glib_version,
        )
    except (ImportError, ValueError, AttributeError) as error:
        LOGGER.debug("runtime version probe unavailable: %s", error)

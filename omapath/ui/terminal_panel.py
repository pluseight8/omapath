"""Optional embedded terminal surface.

When VTE is unavailable the active window must not create a fake panel.  The
external terminal action lives in the host and remains available separately.
"""

from __future__ import annotations

import os
from typing import Any

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk


class TerminalPanel(Gtk.Box):
    """Embed VTE when installed; construct no fallback UI."""

    @classmethod
    def available(cls) -> bool:
        try:
            gi.require_version("Vte", "2.91")
            from gi.repository import Vte  # noqa: F401
        except (ImportError, ValueError):
            return False
        return True

    def __init__(self, host: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.host = host
        self.terminal: Any | None = None
        self._vte: Any | None = None
        self.add_css_class("terminal-panel")
        self.set_hexpand(True)
        self.set_vexpand(True)
        try:
            gi.require_version("Vte", "2.91")
            from gi.repository import Vte

            self._vte = Vte
        except (ImportError, ValueError):
            return
        else:
            self.terminal = Vte.Terminal()
            self.terminal.set_hexpand(True)
            self.terminal.set_vexpand(True)
            self.append(self.terminal)

    @property
    def embedded(self) -> bool:
        return self.terminal is not None

    def spawn(self, directory: str) -> bool:
        if self.terminal is None or self._vte is None or not os.path.isdir(directory):
            return False
        shell = str(self.host.settings.get("terminal_shell") or os.environ.get("SHELL") or "/bin/sh")
        argv = [shell, "-i"]
        try:
            self.terminal.reset(True, True)
            self.terminal.spawn_async(
                self._vte.PtyFlags.DEFAULT,
                directory,
                argv,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
                None,
            )
        except (GLib.Error, TypeError, ValueError):
            return False
        return True


__all__ = ["TerminalPanel"]

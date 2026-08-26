"""Production entry point for Omapath."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

from .diagnostics import LOGGER, configure_logging, log_runtime_versions
from .ui.simple_window import OmapathApplication


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    log_runtime_versions()
    display = Gdk.Display.get_default()
    LOGGER.debug("startup display=%s", display is not None)
    if display is None:
        print("GUI runtime verification could not be performed because the environment has no graphical display.", file=sys.stderr)
        return 2
    arguments = list(argv) if argv is not None else sys.argv
    safe_mode = "--safe-mode" in arguments
    if safe_mode:
        arguments = [argument for argument in arguments if argument != "--safe-mode"]
    return OmapathApplication(safe_mode=safe_mode).run(arguments)


__all__ = ["OmapathApplication", "main"]

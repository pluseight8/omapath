#!/usr/bin/env python3
"""Assist real Omarchy GUI smoke testing without faking headless success.

This helper only verifies that a GTK4 display exists and prints the short
interactive sequence to perform.  It deliberately does not report pointer,
Wayland, rendering, or activation success from a headless process.
"""

from __future__ import annotations

import argparse
import sys

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk


CHECKLIST = (
    "launch Omapath and confirm the active folder renders",
    "open a nested folder, then test Back, Forward and Up",
    "select one and multiple rows; test Enter, F2, Ctrl+C/X/V and Delete",
    "right-click an item and empty space; invoke one safe operation",
    "switch Icons/Compact/Details, Search, Filter and Quick Look",
    "open a second tab and F3 split; verify independent locations",
    "change the Omarchy theme and confirm the open window updates",
    "create/remove a temporary file externally and confirm live refresh",
    "close the window and confirm no traceback remains in the terminal",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checklist", action="store_true", help="print the interactive smoke checklist")
    args = parser.parse_args(argv)
    if args.checklist:
        for index, item in enumerate(CHECKLIST, 1):
            print(f"{index}. {item}")
    display = Gdk.Display.get_default()
    if display is None:
        print(
            "No graphical display detected; GUI smoke was not run. "
            "Use this command inside the real Omarchy/Hyprland session.",
            file=sys.stderr,
        )
        return 2
    print(f"GTK display detected: {display.get_name() or 'unnamed'}")
    print("Run the checklist interactively; this helper does not claim visual success.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

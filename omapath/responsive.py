"""Toolkit-neutral responsive layout policy for the active Omapath window."""

from __future__ import annotations

from enum import StrEnum


class ResponsiveMode(StrEnum):
    """Discrete presentation states used by the GTK composition layer."""

    WIDE = "wide"
    MEDIUM = "medium"
    NARROW = "narrow"


# The enter/leave pairs deliberately differ.  The gap prevents a resize drag
# hovering around a breakpoint from repeatedly changing widget presentation.
WIDE_ENTER = 1100
WIDE_LEAVE = 1080
MEDIUM_ENTER = 800
MEDIUM_LEAVE = 820


def responsive_mode_for_width(width: int, previous: ResponsiveMode | str | None = None) -> ResponsiveMode:
    """Return a stable responsive state for ``width`` with hysteresis."""

    width = max(0, int(width))
    try:
        current = ResponsiveMode(previous) if previous is not None else None
    except ValueError:
        current = None

    if current is None:
        if width >= WIDE_ENTER:
            return ResponsiveMode.WIDE
        if width >= MEDIUM_ENTER:
            return ResponsiveMode.MEDIUM
        return ResponsiveMode.NARROW

    if current is ResponsiveMode.WIDE:
        return ResponsiveMode.MEDIUM if width < WIDE_LEAVE else current
    if current is ResponsiveMode.MEDIUM:
        if width >= WIDE_ENTER + 20:
            return ResponsiveMode.WIDE
        if width < MEDIUM_ENTER - 20:
            return ResponsiveMode.NARROW
        return current
    if current is ResponsiveMode.NARROW:
        if width >= MEDIUM_LEAVE:
            return ResponsiveMode.MEDIUM
        return current
    return ResponsiveMode.NARROW


def detail_columns_for_mode(mode: ResponsiveMode | str) -> tuple[str, ...]:
    """Return the temporary Details columns visible at one responsive state."""

    selected = ResponsiveMode(mode)
    if selected is ResponsiveMode.WIDE:
        return ("name", "size", "type", "modified")
    if selected is ResponsiveMode.MEDIUM:
        return ("name", "size", "modified")
    return ("name",)


def grid_inset_for_mode(mode: ResponsiveMode | str) -> tuple[int, int, int]:
    """Return ``(top, horizontal, bottom)`` content inset for file views."""

    selected = ResponsiveMode(mode)
    if selected is ResponsiveMode.WIDE:
        return (18, 20, 20)
    if selected is ResponsiveMode.MEDIUM:
        return (14, 16, 16)
    return (8, 9, 10)


def mode_uses_sidebar_overlay(mode: ResponsiveMode | str) -> bool:
    return ResponsiveMode(mode) is ResponsiveMode.NARROW


__all__ = [
    "MEDIUM_ENTER",
    "ResponsiveMode",
    "WIDE_ENTER",
    "detail_columns_for_mode",
    "grid_inset_for_mode",
    "mode_uses_sidebar_overlay",
    "responsive_mode_for_width",
]

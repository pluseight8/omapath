"""Logical icon zoom levels and matching GridView cell geometry."""

from __future__ import annotations

ZOOM_LEVELS = (32, 40, 48, 56, 64, 72, 96)
DEFAULT_ZOOM = 48
CELL_WIDTHS = {
    32: 92,
    40: 104,
    48: 116,
    56: 128,
    64: 140,
    72: 152,
    96: 184,
}


def normalize_zoom(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ZOOM
    return min(ZOOM_LEVELS, key=lambda level: abs(level - number))


def zoom_in(value: object) -> int:
    current = normalize_zoom(value)
    return next((level for level in ZOOM_LEVELS if level > current), current)


def zoom_out(value: object) -> int:
    current = normalize_zoom(value)
    return next((level for level in reversed(ZOOM_LEVELS) if level < current), current)


def cell_width(value: object) -> int:
    return CELL_WIDTHS[normalize_zoom(value)]


def label_width_chars(value: object) -> int:
    return max(12, min(26, (cell_width(value) - 16) // 7))

"""URI-list parsing and modifier/action policy for GTK4 drag and drop."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def paths_from_uri_list(value: object) -> list[str]:
    """Convert a text/uri-list or Gdk.FileList-like value to local paths."""

    getter = getattr(value, "get_files", None)
    if getter is not None:
        try:
            result: list[str] = []
            for file in getter():
                path = file.get_path()
                if path is not None:
                    result.append(os.path.abspath(path))
            return result
        except Exception:
            return []
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return []
    result = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlparse(line)
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                continue
            result.append(os.path.abspath(unquote(parsed.path)))
        elif not parsed.scheme:
            result.append(os.path.abspath(line))
    return result


def locations_from_uri_list(value: object) -> list[str]:
    """Return canonical local paths or provider URIs from a drag payload."""

    getter = getattr(value, "get_files", None)
    if getter is not None:
        try:
            return [file.get_uri() for file in getter() if file.get_uri()]
        except Exception:
            return []
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return []
    result: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlparse(line)
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                continue
            result.append(Path(os.path.abspath(unquote(parsed.path))).as_uri())
        elif parsed.scheme:
            result.append(line)
        else:
            result.append(Path(os.path.abspath(line)).as_uri())
    return result


def paths_from_clipboard_text(value: str) -> tuple[list[str], bool]:
    """Parse GTK/GNOME clipboard text and return paths plus cut state.

    ``x-special/gnome-copied-files`` prefixes the URI list with ``copy`` or
    ``cut``.  A plain ``text/uri-list`` has no operation marker and is treated
    as a copy, which is the safe default for external applications.
    """

    lines = [line.strip() for line in value.replace("\r", "").split("\n") if line.strip()]
    cut = False
    if lines and lines[0].casefold() in {"copy", "cut"}:
        cut = lines.pop(0).casefold() == "cut"
    return paths_from_uri_list("\n".join(lines)), cut


def locations_from_clipboard_text(value: str) -> tuple[list[str], bool]:
    """Parse clipboard file locations while retaining remote provider URIs."""

    lines = [line.strip() for line in value.replace("\r", "").split("\n") if line.strip()]
    cut = False
    if lines and lines[0].casefold() in {"copy", "cut"}:
        cut = lines.pop(0).casefold() == "cut"
    return locations_from_uri_list("\n".join(lines)), cut


def uri_list(paths: list[str]) -> str:
    return "\r\n".join(pathlib_uri(path) for path in paths) + ("\r\n" if paths else "")


def pathlib_uri(path: str) -> str:
    if "://" in str(path):
        return str(path)
    return Path(os.path.abspath(path)).as_uri()


def drop_action(*, move: bool = False, link: bool = False) -> str:
    if link:
        return "link"
    return "move" if move else "copy"


def same_filesystem(paths: list[str], destination: str) -> bool:
    """Return whether all sources and the destination share a device."""

    try:
        destination_device = os.stat(destination).st_dev
        return bool(paths) and all(os.stat(path).st_dev == destination_device for path in paths)
    except OSError:
        # A missing or inaccessible path must use the conservative copy
        # default. The operation worker still performs its own validation.
        return False


def choose_drop_action(
    paths: list[str],
    destination: str,
    *,
    copy_modifier: bool = False,
    move_modifier: bool = False,
    link_modifier: bool = False,
) -> str:
    """Choose familiar file-manager DnD semantics from modifiers.

    Same-device drops move by default; cross-device drops copy by default.
    Explicit Ctrl/Shift/Alt modifiers win over the default. The function is
    toolkit-neutral so the policy can be regression-tested without GTK.
    """

    if link_modifier:
        return "link"
    if copy_modifier:
        return "copy"
    if move_modifier:
        return "move"
    return "move" if same_filesystem(paths, destination) else "copy"


def validate_drop(paths: list[str], destination: str) -> tuple[bool, str | None]:
    """Reject invalid DnD destinations before opening an async operation."""

    if not paths:
        return False, "No files were provided"
    if not os.path.isdir(destination):
        return False, "Drop destination is unavailable"
    destination_real = os.path.realpath(destination)
    for source in paths:
        if not os.path.lexists(source):
            return False, f"Source no longer exists: {Path(source).name}"
        source_real = os.path.realpath(source)
        same_target = os.path.abspath(source) == os.path.abspath(
            os.path.join(destination, os.path.basename(os.path.normpath(source)))
        )
        if same_target:
            return False, "Source and destination are the same"
        if os.path.isdir(source) and (
            destination_real == source_real or destination_real.startswith(source_real + os.path.sep)
        ):
            return False, "Cannot drop a folder into itself"
    return True, None


__all__ = [
    "choose_drop_action",
    "drop_action",
    "paths_from_clipboard_text",
    "locations_from_uri_list",
    "locations_from_clipboard_text",
    "paths_from_uri_list",
    "same_filesystem",
    "uri_list",
    "validate_drop",
]

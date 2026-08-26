"""Filesystem primitives: sorting, filtering, metadata and safety checks."""

from __future__ import annotations

import datetime as dt
import mimetypes
import os
import re
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .model import FileItem


NATURAL_PARTS = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> tuple[object, ...]:
    """Sort names the way people expect: file2 before file10."""

    # Tag each chunk so names beginning with a number and names beginning with
    # a letter remain comparable in Python 3 (mixed ``int``/``str`` tuples are
    # otherwise a runtime TypeError).
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in NATURAL_PARTS.split(value.casefold())
    )


def sort_items(
    items: Iterable[FileItem],
    mode: str = "name",
    reverse: bool = False,
    folders_first: bool = True,
    natural: bool = True,
) -> list[FileItem]:
    """Sort entries with a stable folders-first pass independent of direction."""

    entries = list(items)

    def primary(item: FileItem) -> object:
        if mode == "size":
            return item.size
        if mode == "modified":
            return item.modified
        if mode == "type":
            return (item.content_type.casefold(), natural_sort_key(item.display_name))
        return natural_sort_key(item.display_name) if natural else item.display_name.casefold()

    entries.sort(key=primary, reverse=reverse)
    if folders_first:
        entries.sort(key=lambda item: not item.is_dir)
    return entries


def filter_items(
    items: Iterable[FileItem],
    query: str = "",
    *,
    case_sensitive: bool = False,
    files_only: bool = False,
    folders_only: bool = False,
    extension: str | None = None,
    content_type: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    modified_after: int | None = None,
    modified_before: int | None = None,
) -> list[FileItem]:
    needle = query if case_sensitive else query.casefold()
    normalized_extension = extension.casefold().lstrip(".") if extension else None
    result: list[FileItem] = []
    for item in items:
        if files_only and item.is_dir:
            continue
        if folders_only and not item.is_dir:
            continue
        haystack = item.display_name if case_sensitive else item.display_name.casefold()
        if needle and needle not in haystack:
            continue
        if normalized_extension and (item.name.rsplit(".", 1)[-1].casefold() if "." in item.name else "") != normalized_extension:
            continue
        if content_type and not item.content_type.casefold().startswith(content_type.casefold()):
            continue
        if min_size is not None and item.size < min_size:
            continue
        if max_size is not None and item.size > max_size:
            continue
        if modified_after is not None and item.modified < modified_after:
            continue
        if modified_before is not None and item.modified > modified_before:
            continue
        result.append(item)
    return result


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB", "PB")
    value = float(size)
    for unit in units:
        value /= 1024
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{size} B"


def format_date(timestamp: int | float | None) -> str:
    if not timestamp:
        return "—"
    try:
        return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


def permissions_text(path: str) -> str:
    try:
        return stat.filemode(os.lstat(path).st_mode)
    except OSError:
        return "Unavailable"


def directory_size(path: str, cancel: object | None = None) -> int:
    """Calculate size without following symlinked directories."""

    total = 0
    pending = [path]
    while pending:
        if getattr(cancel, "is_set", lambda: False)():
            break
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if getattr(cancel, "is_set", lambda: False)():
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def unique_destination(directory: str, name: str) -> str:
    candidate = os.path.join(directory, name)
    if not os.path.lexists(candidate):
        return candidate
    stem, suffix = os.path.splitext(name)
    index = 2
    while True:
        candidate = os.path.join(directory, f"{stem} (copy {index}){suffix}")
        if not os.path.lexists(candidate):
            return candidate
        index += 1


def valid_entry_name(name: str) -> bool:
    return bool(name and name not in {".", ".."} and os.path.basename(name) == name and "\x00" not in name)


def validate_transfer(source: str, destination_dir: str) -> tuple[bool, str | None]:
    """Reject dangerous self/parent transfers before an operation starts."""

    try:
        source_real = os.path.realpath(source)
        destination_real = os.path.realpath(destination_dir)
    except OSError as error:
        return False, str(error)
    if not os.path.exists(source) and not os.path.lexists(source):
        return False, "Source no longer exists"
    if os.path.isdir(source) and (destination_real == source_real or destination_real.startswith(source_real + os.path.sep)):
        return False, "Cannot copy a folder into itself"
    return True, None


@dataclass(slots=True)
class FileMetadata:
    path: str
    uri: str
    name: str
    file_type: str
    mime: str
    size: int
    modified: int | None
    created: int | None
    accessed: int | None
    owner: str
    group: str
    permissions: str
    executable: bool
    symlink_target: str | None
    filesystem: str
    mount: str
    free_space: int | None


def metadata_for_path(path: str, uri: str | None = None) -> FileMetadata:
    """Collect properties with best-effort optional fields."""

    info = os.lstat(path)
    is_dir = stat.S_ISDIR(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        file_type = "Symbolic link"
    elif is_dir:
        file_type = "Folder"
    elif stat.S_ISREG(info.st_mode):
        file_type = "File"
    else:
        file_type = "Special file"
    mime, _ = mimetypes.guess_type(path)
    try:
        import grp
        import pwd

        owner = pwd.getpwuid(info.st_uid).pw_name
        group = grp.getgrgid(info.st_gid).gr_name
    except (ImportError, KeyError):
        owner = str(info.st_uid)
        group = str(info.st_gid)
    symlink_target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
    mount = os.path.abspath(path)
    while mount != os.path.dirname(mount) and not os.path.ismount(mount):
        mount = os.path.dirname(mount)
    free_space: int | None
    try:
        filesystem_stats = os.statvfs(path)
        free_space = filesystem_stats.f_bavail * filesystem_stats.f_frsize
    except OSError:
        free_space = None
    return FileMetadata(
        path=path,
        uri=uri or Path(path).as_uri(),
        name=os.path.basename(path),
        file_type=file_type,
        mime=mime or "application/octet-stream",
        size=info.st_size,
        modified=int(info.st_mtime),
        created=int(getattr(info, "st_ctime", 0)) or None,
        accessed=int(info.st_atime),
        owner=owner,
        group=group,
        permissions=stat.filemode(info.st_mode),
        executable=bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)),
        symlink_target=symlink_target,
        filesystem="local",
        mount=mount,
        free_space=free_space,
    )


class BoundedCache:
    """A tiny LRU cache used for metadata and preview adapters."""

    def __init__(self, limit: int = 64) -> None:
        self.limit = max(1, limit)
        self._data: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> object | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.limit:
                self._data.popitem(last=False)

    def set_limit(self, limit: int) -> None:
        """Change the bound and evict oldest entries immediately."""
        with self._lock:
            self.limit = max(1, int(limit))
            while len(self._data) > self.limit:
                self._data.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

"""Asynchronous, bounded quick-preview service."""

from __future__ import annotations

import mimetypes
import os
import threading
import tarfile
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .filesystem import BoundedCache, format_size, format_date
from .diagnostics import LOGGER
from .archive import archive_kind


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".xml", ".csv", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".h", ".cpp", ".rs", ".go", ".sh", ".fish",
    ".ini", ".conf", ".service", ".desktop", ".diff", ".patch",
}


@dataclass(slots=True)
class PreviewResult:
    path: str
    kind: str
    content: Any
    mime: str
    size: int
    modified: int
    error: str | None = None
    truncated: bool = False

    @property
    def subtitle(self) -> str:
        return f"{self.mime} · {format_size(self.size)} · {format_date(self.modified)}"


def preview_kind(path: str, mime: str | None = None) -> str:
    if os.path.isdir(path):
        return "folder"
    if archive_kind(path) is not None:
        return "archive"
    guessed = mime or mimetypes.guess_type(path)[0] or ""
    if guessed.startswith("image/") or Path(path).suffix.lower() == ".svg":
        return "image"
    if guessed.startswith("audio/"):
        return "audio"
    if guessed.startswith("video/"):
        return "video"
    if guessed == "application/pdf" or Path(path).suffix.lower() == ".pdf":
        return "pdf"
    if guessed.startswith("text/") or Path(path).suffix.lower() in TEXT_EXTENSIONS:
        return "text"
    return "fallback"


class PreviewManager:
    def __init__(self, max_bytes: int = 262144, cache_limit: int = 32, workers: int = 2) -> None:
        self.max_bytes = max_bytes
        self.cache = BoundedCache(cache_limit)
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="omapath-preview")
        self._cancel = threading.Event()
        self._future: Future[PreviewResult] | None = None

    def request(self, path: str, callback: Callable[[PreviewResult], None], scheduler: Callable[..., object] | None = None) -> None:
        self.cancel()
        cancel = threading.Event()
        self._cancel = cancel
        cache_key = self._cache_key(path)
        cached = self.cache.get(cache_key)
        if isinstance(cached, PreviewResult):
            self._dispatch(callback, cached, scheduler, cancel)
            return
        future = self._executor.submit(self._load, path, cancel)
        self._future = future

        def done(completed: Future[PreviewResult]) -> None:
            try:
                result = completed.result()
            except Exception as error:
                result = PreviewResult(path, "fallback", None, "application/octet-stream", 0, 0, str(error))
            if not cancel.is_set() and result.error is None:
                self.cache.put(cache_key, result)
            if not cancel.is_set():
                self._dispatch(callback, result, scheduler, cancel)

        future.add_done_callback(done)
        LOGGER.debug("preview requested path=%s", path)

    def cancel(self) -> None:
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()

    def clear(self) -> None:
        self.cache.clear()

    def shutdown(self) -> None:
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _cache_key(path: str) -> str:
        try:
            info = os.stat(path, follow_symlinks=False)
            return f"{path}:{info.st_mtime_ns}:{info.st_size}"
        except OSError:
            return path

    @staticmethod
    def _dispatch(
        callback: Callable[[PreviewResult], None],
        result: PreviewResult,
        scheduler: Callable[..., object] | None,
        cancel: threading.Event | None = None,
    ) -> None:
        def deliver() -> None:
            if cancel is None or not cancel.is_set():
                callback(result)

        if scheduler is None:
            deliver()
        else:
            scheduler(deliver)

    def _load(self, path: str, cancel: threading.Event) -> PreviewResult:
        info = os.stat(path, follow_symlinks=False)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        kind = preview_kind(path, mime)
        if kind == "folder":
            return PreviewResult(path, kind, self._folder_summary(path), "inode/directory", info.st_size, int(info.st_mtime))
        if kind == "archive":
            return PreviewResult(path, kind, self._archive_summary(path), mime, info.st_size, int(info.st_mtime))
        if kind == "text":
            with open(path, "rb") as handle:
                content = handle.read(self.max_bytes + 1)
            if cancel.is_set():
                raise RuntimeError("Preview cancelled")
            truncated = len(content) > self.max_bytes
            text = content[: self.max_bytes].decode("utf-8", errors="replace")
            if truncated:
                text += "\n\n… preview truncated …"
            return PreviewResult(path, kind, text, mime, info.st_size, int(info.st_mtime), truncated=truncated)
        if kind == "image":
            try:
                import gi

                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf

                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 1400, 900, True)
                return PreviewResult(path, kind, pixbuf, mime, info.st_size, int(info.st_mtime))
            except (ImportError, ValueError, OSError, RuntimeError) as error:
                return PreviewResult(path, kind, None, mime, info.st_size, int(info.st_mtime), str(error))
        return PreviewResult(path, kind, None, mime, info.st_size, int(info.st_mtime))

    @staticmethod
    def _folder_summary(path: str) -> str:
        count = 0
        total_size = 0
        folders = 0
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    count += 1
                    if entry.is_dir(follow_symlinks=False):
                        folders += 1
                    else:
                        try:
                            total_size += int(entry.stat(follow_symlinks=False).st_size)
                        except OSError:
                            pass
        except OSError as error:
            return f"Folder preview unavailable: {error}"
        return f"{count} items\n{folders} folders\n{format_size(total_size)} direct file size"

    @staticmethod
    def _archive_summary(path: str) -> str:
        names: list[str] = []
        total = 0
        try:
            if archive_kind(path) == "zip":
                with zipfile.ZipFile(path) as source:
                    infos = source.infolist()
                    total = len(infos)
                    names = [info.filename for info in infos[:40]]
            else:
                with tarfile.open(path, mode="r:*") as source:
                    members = source.getmembers()
                    total = len(members)
                    names = [member.name for member in members[:40]]
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
            return f"Archive preview unavailable: {error}"
        listing = "\n".join(names)
        suffix = "\n… more entries …" if total > len(names) else ""
        return f"{total} entries\n\n{listing}{suffix}".strip()


class ThumbnailManager:
    """Small concurrent thumbnail pool with an LRU memory bound."""

    def __init__(self, limit: int = 64, workers: int = 2) -> None:
        self.cache = BoundedCache(limit)
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="omapath-thumb")
        self._cancel = threading.Event()
        self._futures: set[Future[object]] = set()

    def request(self, path: str, size: int, callback: Callable[[object | None], None], scheduler: Callable[..., object] | None = None) -> None:
        key = f"{self._cache_key(path)}:{size}"
        cached = self.cache.get(key)
        if cached is not None:
            self._dispatch(callback, cached, scheduler)
            return
        future = self._executor.submit(self._load, path, size, self._cancel)
        self._futures.add(future)

        def done(completed: Future[object]) -> None:
            self._futures.discard(completed)
            if self._cancel.is_set():
                return
            try:
                result = completed.result()
            except Exception:
                # GdkPixbuf decoders surface GLib.Error in addition to Python I/O
                # errors; a bad image is a cache miss, never a UI failure.
                result = None
            if result is not None:
                self.cache.put(key, result)
            self._dispatch(callback, result, scheduler)

        future.add_done_callback(done)

    def clear(self) -> None:
        self.cache.clear()

    def shutdown(self) -> None:
        self._cancel.set()
        for future in list(self._futures):
            future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _dispatch(callback: Callable[[object | None], None], result: object | None, scheduler: Callable[..., object] | None) -> None:
        if scheduler is None:
            callback(result)
        else:
            scheduler(callback, result)

    @staticmethod
    def _load(path: str, size: int, cancel: threading.Event) -> object:
        if cancel.is_set():
            raise RuntimeError("Thumbnail cancelled")
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)

    @staticmethod
    def _cache_key(path: str) -> str:
        try:
            info = os.stat(path, follow_symlinks=False)
            return f"{path}:{info.st_mtime_ns}:{info.st_size}"
        except OSError:
            return path

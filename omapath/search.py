"""Cancellable recursive search that never walks the GTK main loop."""

from __future__ import annotations

import os
import mimetypes
import re
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from .diagnostics import LOGGER


@dataclass(slots=True)
class SearchQuery:
    root: str
    text: str
    recursive: bool = False
    case_sensitive: bool = False
    include_hidden: bool = False
    search_contents: bool = False
    folders_only: bool = False
    files_only: bool = False
    extension: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    path_text: str = ""
    content_type: str | None = None
    modified_after: int | None = None
    modified_before: int | None = None
    created_after: int | None = None
    created_before: int | None = None


def parse_search_query(value: str) -> tuple[str, tuple[str, ...], int | None]:
    """Extract compact metadata predicates from the visible search field.

    The remaining text is still handled by the existing bounded filesystem
    search worker, so a malformed predicate simply remains ordinary text.
    """

    tags: list[str] = []
    minimum_rating: int | None = None
    remaining: list[str] = []
    for token in str(value or "").split():
        if token.casefold().startswith("tag:") and len(token) > 4:
            tag = token[4:].strip().strip('"\'')
            if tag:
                tags.append(tag)
                continue
        match = re.fullmatch(r"rating:\s*(?:>=\s*)?(\d+)", token, flags=re.IGNORECASE)
        if match is not None:
            minimum_rating = max(0, min(5, int(match.group(1))))
            continue
        remaining.append(token)
    return " ".join(remaining), tuple(dict.fromkeys(tags)), minimum_rating


class SearchService:
    def __init__(self, workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="omapath-search")
        self._cancel = threading.Event()
        self._future: Future[list[str]] | None = None

    def search(
        self,
        query: SearchQuery,
        on_result: Callable[[list[str]], None],
        on_error: Callable[[Exception], None] | None = None,
        scheduler: Callable[..., object] | None = None,
    ) -> None:
        self.cancel()
        cancel = threading.Event()
        self._cancel = cancel
        future = self._executor.submit(self._run, query, cancel)
        self._future = future

        def done(completed: Future[list[str]]) -> None:
            if cancel.is_set():
                return
            try:
                result = completed.result()
            except CancelledError:
                return
            except Exception as error:  # worker boundary: surface to UI, never silently lose it
                if on_error is not None:
                    self._dispatch(
                        scheduler,
                        lambda failure=error: on_error(failure) if not cancel.is_set() else None,
                    )
                return
            if cancel.is_set():
                return
            self._dispatch(
                scheduler,
                lambda matches=result: on_result(matches) if not cancel.is_set() else None,
            )

        future.add_done_callback(done)
        LOGGER.debug("search submitted root=%s recursive=%s", query.root, query.recursive)

    def cancel(self) -> None:
        self._cancel.set()
        if self._future is not None:
            LOGGER.debug("search cancel requested")
            self._future.cancel()

    def shutdown(self) -> None:
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _dispatch(scheduler: Callable[..., object] | None, callback: Callable[..., object], *args: object) -> None:
        if scheduler is None:
            callback(*args)
        else:
            scheduler(callback, *args)

    @staticmethod
    def _run(query: SearchQuery, cancel: threading.Event) -> list[str]:
        needle = query.text if query.case_sensitive else query.text.casefold()
        extension = query.extension.casefold().lstrip(".") if query.extension else None
        matches: list[str] = []
        pending = [query.root]
        while pending and not cancel.is_set():
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if cancel.is_set():
                            break
                        if entry.name.startswith(".") and not query.include_hidden:
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if is_dir and query.recursive:
                                pending.append(entry.path)
                            if query.folders_only and not is_dir:
                                continue
                            if query.files_only and is_dir:
                                continue
                            if needle:
                                candidate_value = entry.path if query.path_text else entry.name
                                candidate = candidate_value if query.case_sensitive else candidate_value.casefold()
                                if needle not in candidate:
                                    if not query.search_contents or is_dir:
                                        continue
                                    try:
                                        with open(entry.path, "rb") as stream:
                                            content = stream.read(262144).decode("utf-8", errors="ignore")
                                    except (OSError, UnicodeError):
                                        continue
                                    content_candidate = content if query.case_sensitive else content.casefold()
                                    if needle not in content_candidate:
                                        continue
                            if extension and (entry.name.rsplit(".", 1)[-1].casefold() if "." in entry.name else "") != extension:
                                continue
                            if query.content_type:
                                guessed = mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
                                if not guessed.casefold().startswith(query.content_type.casefold()):
                                    continue
                            if any(value is not None for value in (query.modified_after, query.modified_before, query.created_after, query.created_before)):
                                stat_result = entry.stat(follow_symlinks=False)
                                modified = int(stat_result.st_mtime)
                                created = int(getattr(stat_result, "st_ctime", 0))
                                if query.modified_after is not None and modified < query.modified_after:
                                    continue
                                if query.modified_before is not None and modified > query.modified_before:
                                    continue
                                if query.created_after is not None and created < query.created_after:
                                    continue
                                if query.created_before is not None and created > query.created_before:
                                    continue
                            if query.min_size is not None or query.max_size is not None:
                                size = entry.stat(follow_symlinks=False).st_size
                                if query.min_size is not None and size < query.min_size:
                                    continue
                                if query.max_size is not None and size > query.max_size:
                                    continue
                        except OSError:
                            continue
                        matches.append(entry.path)
            except OSError:
                continue
        return matches

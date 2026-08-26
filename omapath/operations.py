"""Central, cancellable filesystem operation manager.

The manager owns all long-running local operations.  GTK only receives
progress/completion callbacks through the injected scheduler, so no worker
thread ever touches a widget.
"""

from __future__ import annotations

import enum
import os
import shutil
import threading
import uuid
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .filesystem import unique_destination, valid_entry_name, validate_transfer
from .diagnostics import LOGGER
from .archive import ArchiveSafetyError, create_archive, extract_archive
from .rename import BatchRenameSpec, RenamePlanError, apply_rename_plan, build_rename_plan
from .trash import attribute_text


class OperationCancelled(Exception):
    """Internal control flow for cancelling a transfer without a fake error."""


class OperationKind(str, enum.Enum):
    COPY = "copy"
    MOVE = "move"
    TRASH = "trash"
    DELETE = "delete"
    RESTORE = "restore"
    RENAME = "rename"
    CREATE_FILE = "create-file"
    CREATE_FOLDER = "create-folder"
    DUPLICATE = "duplicate"
    LINK = "link"
    BATCH_RENAME = "batch-rename"
    CREATE_ARCHIVE = "create-archive"
    EXTRACT_ARCHIVE = "extract-archive"


class ConflictPolicy(str, enum.Enum):
    REPLACE = "replace"
    SKIP = "skip"
    RENAME = "rename"
    MERGE = "merge"
    CANCEL = "cancel"


@dataclass(slots=True)
class OperationProgress:
    operation_id: str
    kind: OperationKind
    completed: int
    total: int
    bytes_done: int = 0
    bytes_total: int = 0
    current_name: str = ""
    state: str = "running"

    @property
    def fraction(self) -> float:
        if self.bytes_total:
            return min(1.0, self.bytes_done / self.bytes_total)
        if self.total:
            return min(1.0, self.completed / self.total)
        return 0.0


@dataclass(slots=True)
class OperationResult:
    operation_id: str
    kind: OperationKind
    completed: int
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    undo: "UndoRecord | None" = None


@dataclass(slots=True)
class UndoRecord:
    label: str
    undo_paths: list[tuple[str, str]] = field(default_factory=list)
    created_paths: list[str] = field(default_factory=list)
    trash_paths: list[tuple[str, str]] = field(default_factory=list)
    ensure_dirs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OperationHandle:
    operation_id: str
    cancel_event: threading.Event
    future: Future[OperationResult]

    def cancel(self) -> None:
        self.cancel_event.set()
        self.future.cancel()


class UndoManager:
    def __init__(self, limit: int = 32) -> None:
        self.limit = max(1, limit)
        self._records: list[UndoRecord] = []
        self._redo_records: list[UndoRecord] = []
        self._lock = threading.RLock()

    def push(self, record: UndoRecord | None) -> None:
        if record is None:
            return
        with self._lock:
            self._records.append(record)
            del self._records[:-self.limit]
            self._redo_records.clear()

    def pop(self) -> UndoRecord | None:
        with self._lock:
            return self._records.pop() if self._records else None

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._redo_records.clear()

    def push_redo(self, record: UndoRecord | None) -> None:
        if record is None:
            return
        with self._lock:
            self._redo_records.append(record)
            del self._redo_records[:-self.limit]

    def pop_redo(self) -> UndoRecord | None:
        with self._lock:
            return self._redo_records.pop() if self._redo_records else None

    @property
    def redo_count(self) -> int:
        with self._lock:
            return len(self._redo_records)

    @property
    def last_label(self) -> str | None:
        with self._lock:
            return self._records[-1].label if self._records else None

    @property
    def redo_label(self) -> str | None:
        with self._lock:
            return self._redo_records[-1].label if self._redo_records else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class OperationManager:
    def __init__(self, workers: int = 2, scheduler: Callable[..., object] | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="omapath-io")
        self._scheduler = scheduler
        self._handles: dict[str, OperationHandle] = {}
        self._handles_lock = threading.RLock()
        self.undo = UndoManager()
        self._closed = False

    def submit(
        self,
        kind: OperationKind,
        sources: Iterable[str],
        destination: object | None = None,
        *,
        policy: ConflictPolicy = ConflictPolicy.RENAME,
        on_progress: Callable[[OperationProgress], None] | None = None,
        on_complete: Callable[[OperationResult], None] | None = None,
    ) -> OperationHandle:
        if self._closed:
            raise RuntimeError("operation manager is closed")
        operation_id = uuid.uuid4().hex
        cancel = threading.Event()
        source_list = [os.fspath(source) for source in sources]
        future = self._executor.submit(
            self._run,
            operation_id,
            kind,
            source_list,
            destination,
            policy,
            cancel,
            on_progress,
        )
        handle = OperationHandle(operation_id, cancel, future)
        with self._handles_lock:
            self._handles[operation_id] = handle

        def done(completed: Future[OperationResult]) -> None:
            with self._handles_lock:
                self._handles.pop(operation_id, None)
            try:
                result = completed.result()
            except CancelledError:
                result = OperationResult(operation_id, kind, 0, cancelled=True)
            except Exception as error:  # worker boundary: turn unexpected failures into user-visible result
                result = OperationResult(operation_id, kind, 0, errors=[str(error)])
            if result.undo is not None and (
                result.undo.undo_paths or result.undo.created_paths or result.undo.trash_paths
            ):
                self.undo.push(result.undo)
            if on_complete is not None:
                self._dispatch(
                    lambda completed_result=result: on_complete(completed_result) if not self._closed else None,
                )

        future.add_done_callback(done)
        LOGGER.debug("operation submitted id=%s kind=%s sources=%d", operation_id, kind.value, len(source_list))
        return handle

    def submit_gio(
        self,
        kind: OperationKind,
        sources: Iterable[object],
        destination: object | None = None,
        *,
        policy: ConflictPolicy = ConflictPolicy.RENAME,
        on_progress: Callable[[OperationProgress], None] | None = None,
        on_complete: Callable[[OperationResult], None] | None = None,
    ) -> OperationHandle:
        """Submit a provider-backed operation using Gio.File objects.

        Local path operations continue to use :meth:`submit`; this boundary
        prevents remote URI assumptions from leaking into the local worker.
        """

        if self._closed:
            raise RuntimeError("operation manager is closed")
        operation_id = uuid.uuid4().hex
        cancel = threading.Event()
        source_list = list(sources)
        future = self._executor.submit(
            self._run_gio,
            operation_id,
            kind,
            source_list,
            destination,
            policy,
            cancel,
            on_progress,
        )
        handle = OperationHandle(operation_id, cancel, future)
        with self._handles_lock:
            self._handles[operation_id] = handle

        def done(completed: Future[OperationResult]) -> None:
            with self._handles_lock:
                self._handles.pop(operation_id, None)
            try:
                result = completed.result()
            except CancelledError:
                result = OperationResult(operation_id, kind, 0, cancelled=True)
            except Exception as error:
                result = OperationResult(operation_id, kind, 0, errors=[str(error)])
            if on_complete is not None:
                self._dispatch(
                    lambda completed_result=result: on_complete(completed_result) if not self._closed else None,
                )

        future.add_done_callback(done)
        LOGGER.debug("GIO operation submitted id=%s kind=%s sources=%d", operation_id, kind.value, len(source_list))
        return handle

    def _run_gio(
        self,
        operation_id: str,
        kind: OperationKind,
        sources: list[object],
        destination: object | None,
        policy: ConflictPolicy,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        from .gio_operations import run_gio_operation

        progress = None
        if progress_callback is not None:
            progress = lambda current: self._dispatch(
                lambda snapshot=current: progress_callback(snapshot) if not self._closed else None,
            )
        return run_gio_operation(
            operation_id,
            kind,
            sources,  # type: ignore[arg-type]
            destination,  # type: ignore[arg-type]
            policy,
            cancel,
            progress,
        )

    def cancel(self, operation_id: str) -> None:
        with self._handles_lock:
            handle = self._handles.get(operation_id)
        if handle is not None:
            LOGGER.debug("operation cancel requested id=%s", operation_id)
            handle.cancel()

    def cancel_all(self) -> None:
        with self._handles_lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch(self, callback: Callable[..., object], *args: object) -> None:
        if self._scheduler is None:
            callback(*args)
        else:
            self._scheduler(callback, *args)

    def _emit_progress(self, callback: Callable[[OperationProgress], None] | None, progress: OperationProgress) -> None:
        if callback is not None:
            self._dispatch(
                lambda current_progress=progress: callback(current_progress) if not self._closed else None,
            )

    def _run(
        self,
        operation_id: str,
        kind: OperationKind,
        sources: list[str],
        destination: str | None,
        policy: ConflictPolicy,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        if kind in {OperationKind.COPY, OperationKind.MOVE, OperationKind.DUPLICATE}:
            return self._run_transfer(operation_id, kind, sources, destination, policy, cancel, progress_callback)
        if kind == OperationKind.LINK:
            return self._run_link(operation_id, sources, destination, policy, cancel, progress_callback)
        if kind == OperationKind.TRASH:
            return self._run_trash(operation_id, sources, cancel, progress_callback)
        if kind == OperationKind.DELETE:
            return self._run_delete(operation_id, sources, cancel, progress_callback)
        if kind == OperationKind.RENAME:
            return self._run_rename(operation_id, sources, destination, cancel, progress_callback)
        if kind == OperationKind.BATCH_RENAME:
            return self._run_batch_rename(operation_id, sources, destination, cancel, progress_callback)
        if kind == OperationKind.CREATE_ARCHIVE:
            return self._run_create_archive(operation_id, sources, destination, cancel, progress_callback)
        if kind == OperationKind.EXTRACT_ARCHIVE:
            return self._run_extract_archive(operation_id, sources, destination, cancel, progress_callback)
        if kind in {OperationKind.CREATE_FILE, OperationKind.CREATE_FOLDER}:
            return self._run_create(operation_id, kind, sources, cancel, progress_callback)
        if kind == OperationKind.RESTORE:
            return self._run_restore(operation_id, sources, destination, cancel, progress_callback)
        return OperationResult(operation_id, kind, 0, errors=[f"Unsupported operation: {kind.value}"])

    def _run_transfer(self, operation_id: str, kind: OperationKind, sources: list[str], destination: str | None, policy: ConflictPolicy, cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        result = OperationResult(operation_id, kind, 0, undo=UndoRecord(kind.value.title()))
        if not destination:
            result.errors.append("Destination is missing")
            return result
        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as error:
            result.errors.append(f"Cannot access destination: {self._describe_error(error)}")
            return result
        total = len(sources)
        for source in sources:
            if cancel.is_set():
                result.cancelled = True
                break
            valid, reason = validate_transfer(source, destination)
            if not valid:
                result.errors.append(f"{os.path.basename(source)}: {reason or 'invalid transfer'}")
                continue
            name = os.path.basename(os.path.normpath(source))
            target = os.path.join(destination, name)
            if os.path.abspath(source) == os.path.abspath(target):
                result.errors.append(f"{name}: source and destination are the same")
                continue
            staging: str | None = None
            backup: str | None = None
            transferred = False
            merged = False
            try:
                if os.path.lexists(target):
                    if policy == ConflictPolicy.SKIP:
                        result.skipped += 1
                        continue
                    if policy == ConflictPolicy.CANCEL:
                        result.errors.append(f"{name}: a file with that name already exists")
                        break
                    if policy == ConflictPolicy.RENAME:
                        target = unique_destination(destination, name)
                    elif policy == ConflictPolicy.MERGE:
                        if not (os.path.isdir(source) and os.path.isdir(target)):
                            result.errors.append(f"{name}: merge requires two folders")
                            continue
                        created, moved, ensure_dirs = self._merge_directory(
                            source,
                            target,
                            kind,
                            cancel,
                        )
                        if result.undo is not None:
                            result.undo.created_paths.extend(created)
                            result.undo.undo_paths.extend(moved)
                            result.undo.ensure_dirs.extend(ensure_dirs)
                        merged = True
                    elif kind in {OperationKind.COPY, OperationKind.DUPLICATE}:
                        staging = os.path.join(destination, f".omapath-copy-{uuid.uuid4().hex}.tmp")
                        self._copy_path(source, staging, cancel)
                        if cancel.is_set():
                            raise OperationCancelled()
                        backup = os.path.join(destination, f".omapath-replace-{uuid.uuid4().hex}.tmp")
                        os.replace(target, backup)
                        try:
                            os.replace(staging, target)
                        except OSError:
                            if os.path.lexists(backup) and not os.path.lexists(target):
                                try:
                                    os.replace(backup, target)
                                except OSError:
                                    pass
                                else:
                                    backup = None
                            raise
                        staging = None
                        self._remove_if_exists(backup)
                        backup = None
                        transferred = True
                    else:
                        # Keep the old target recoverable until the move has
                        # succeeded.  Removing it first makes a failed Move
                        # with REPLACE a data-loss operation.
                        backup = os.path.join(destination, f".omapath-replace-{uuid.uuid4().hex}.tmp")
                        os.replace(target, backup)
                if merged:
                    pass
                elif kind == OperationKind.MOVE:
                    try:
                        shutil.move(source, target)
                    except Exception:
                        if backup is not None and os.path.lexists(backup) and not os.path.lexists(target):
                            try:
                                os.replace(backup, target)
                            except OSError:
                                pass
                            else:
                                backup = None
                        raise
                    if backup is not None:
                        self._remove_if_exists(backup)
                        backup = None
                elif not transferred:
                    if staging is None:
                        self._copy_path(source, target, cancel)
                result.completed += 1
                if not merged:
                    if kind == OperationKind.MOVE:
                        result.undo.undo_paths.append((target, source))
                    else:
                        result.undo.created_paths.append(target)
            except OperationCancelled:
                if staging is not None:
                    self._remove_if_exists(staging)
                elif kind in {OperationKind.COPY, OperationKind.DUPLICATE}:
                    self._remove_if_exists(target)
                if backup is not None:
                    self._remove_if_exists(backup)
                result.cancelled = True
                break
            except (OSError, shutil.Error) as error:
                if staging is not None:
                    self._remove_if_exists(staging)
                elif kind in {OperationKind.COPY, OperationKind.DUPLICATE}:
                    self._remove_if_exists(target)
                if backup is not None:
                    if os.path.lexists(target) and os.path.lexists(source):
                        self._remove_if_exists(target)
                    if os.path.lexists(backup) and not os.path.lexists(target):
                        try:
                            os.replace(backup, target)
                            backup = None
                        except OSError:
                            pass
                result.errors.append(f"{name}: {self._describe_error(error)}")
            self._emit_progress(progress_callback, OperationProgress(operation_id, kind, result.completed, total, current_name=name, state="running"))
        if cancel.is_set():
            result.cancelled = True
        return result

    def _merge_directory(
        self,
        source: str,
        target: str,
        kind: OperationKind,
        cancel: threading.Event,
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        """Merge two directories without replacing an existing child.

        A same-name file/type collision is assigned a unique destination.  A
        directory/directory collision is merged recursively.  The helper
        returns enough journal data for copy and move undo; it never touches
        the existing target contents.
        """

        created: list[str] = []
        moved: list[tuple[str, str]] = []
        ensure_dirs: list[str] = []

        def visit(current_source: str, current_target: str) -> None:
            if cancel.is_set():
                raise OperationCancelled()
            os.makedirs(current_target, exist_ok=True)
            try:
                entries = list(os.scandir(current_source))
            except OSError:
                raise
            for entry in entries:
                if cancel.is_set():
                    raise OperationCancelled()
                child_source = entry.path
                child_name = entry.name
                child_target = os.path.join(current_target, child_name)
                if os.path.lexists(child_target):
                    if entry.is_dir(follow_symlinks=False) and os.path.isdir(child_target) and not os.path.islink(child_target):
                        visit(child_source, child_target)
                        continue
                    child_target = unique_destination(current_target, child_name)
                if kind == OperationKind.MOVE:
                    parent = os.path.dirname(child_source)
                    ensure_dirs.extend((parent, os.path.dirname(child_target)))
                    shutil.move(child_source, child_target)
                    moved.append((child_target, child_source))
                else:
                    self._copy_path(child_source, child_target, cancel)
                    created.append(child_target)

        visit(source, target)
        if kind == OperationKind.MOVE:
            try:
                os.rmdir(source)
            except OSError:
                # A provider may leave an unremovable source directory.  The
                # moved children are still valid and remain undoable.
                pass
        return created, moved, ensure_dirs

    def _run_link(
        self,
        operation_id: str,
        sources: list[str],
        destination: str | None,
        policy: ConflictPolicy,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.LINK, 0, undo=UndoRecord("Link"))
        if not destination:
            result.errors.append("Destination is missing")
            return result
        if not os.path.isdir(destination):
            result.errors.append("Destination is not a folder")
            return result
        for source in sources:
            if cancel.is_set():
                result.cancelled = True
                break
            valid, reason = validate_transfer(source, destination)
            if not valid:
                result.errors.append(f"{os.path.basename(source)}: {reason or 'invalid link'}")
                continue
            name = os.path.basename(os.path.normpath(source))
            target = os.path.join(destination, name)
            if os.path.lexists(target):
                if policy == ConflictPolicy.SKIP:
                    result.skipped += 1
                    continue
                if policy == ConflictPolicy.CANCEL:
                    result.errors.append(f"{name}: a file with that name already exists")
                    break
                if policy == ConflictPolicy.RENAME:
                    target = unique_destination(destination, name)
                else:
                    result.errors.append(f"{name}: replacing an existing path with a link is disabled for safety")
                    continue
            try:
                os.symlink(source, target, target_is_directory=os.path.isdir(source))
                result.completed += 1
                result.undo.created_paths.append(target)
            except OSError as error:
                result.errors.append(f"{name}: {self._describe_error(error)}")
            self._emit_progress(
                progress_callback,
                OperationProgress(operation_id, OperationKind.LINK, result.completed, len(sources), current_name=name),
            )
        return result

    def _run_trash(self, operation_id: str, sources: list[str], cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.TRASH, 0, undo=UndoRecord("Move to Trash"))
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError) as error:
            return OperationResult(operation_id, OperationKind.TRASH, 0, errors=[str(error)])
        for index, source in enumerate(sources):
            if cancel.is_set():
                result.cancelled = True
                break
            try:
                Gio.File.new_for_path(source).trash(None)
                result.completed += 1
                trash_uri = self._find_trash_uri(Gio, source)
                if trash_uri and result.undo is not None:
                    result.undo.trash_paths.append((trash_uri, source))
            except Exception as error:
                result.errors.append(f"{os.path.basename(source)}: {self._describe_error(error)}")
            self._emit_progress(progress_callback, OperationProgress(operation_id, OperationKind.TRASH, result.completed, len(sources), index + 1, current_name=os.path.basename(source)))
        return result

    def _run_delete(self, operation_id: str, sources: list[str], cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.DELETE, 0)
        for index, source in enumerate(sources):
            if cancel.is_set():
                result.cancelled = True
                break
            try:
                if source.startswith("trash://"):
                    import gi

                    gi.require_version("Gio", "2.0")
                    from gi.repository import Gio

                    Gio.File.new_for_uri(source).delete(None)
                else:
                    self._remove_path(source)
                result.completed += 1
            except OSError as error:
                result.errors.append(f"{os.path.basename(source)}: {self._describe_error(error)}")
            except Exception as error:
                result.errors.append(f"{os.path.basename(source)}: {self._describe_error(error)}")
            self._emit_progress(progress_callback, OperationProgress(operation_id, OperationKind.DELETE, result.completed, len(sources), index + 1, current_name=os.path.basename(source)))
        return result

    def _run_rename(self, operation_id: str, sources: list[str], destination: str | None, cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.RENAME, 0, undo=UndoRecord("Rename"))
        if len(sources) != 1 or not destination:
            result.errors.append("Rename requires one source and a destination name")
            return result
        source = sources[0]
        if os.path.isabs(destination) or not valid_entry_name(destination):
            result.errors.append("Rename requires a single valid destination name")
            return result
        target = os.path.join(os.path.dirname(source), destination)
        same_location = os.path.abspath(source) == os.path.abspath(target)
        if not same_location and os.path.lexists(target):
            try:
                same_location = os.path.samefile(source, target)
            except OSError:
                same_location = False
        if not same_location and os.path.lexists(target):
            result.errors.append("A file with that name already exists")
            return result
        if cancel.is_set():
            result.cancelled = True
            return result
        try:
            os.rename(source, target)
            result.completed = 1
            result.undo.undo_paths.append((target, source))
        except OSError as error:
            result.errors.append(self._describe_error(error))
        self._emit_progress(progress_callback, OperationProgress(operation_id, OperationKind.RENAME, result.completed, 1, current_name=os.path.basename(source)))
        return result

    def _run_batch_rename(
        self,
        operation_id: str,
        sources: list[str],
        destination: object | None,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.BATCH_RENAME, 0, undo=UndoRecord("Batch Rename"))
        if not isinstance(destination, BatchRenameSpec):
            result.errors.append("Batch rename specification is missing")
            return result
        try:
            plan = build_rename_plan(sources, destination)
            if cancel.is_set():
                result.cancelled = True
                return result
            apply_rename_plan(plan)
        except RenamePlanError as error:
            result.errors.append(str(error))
            return result
        except (OSError, ValueError) as error:
            result.errors.append(self._describe_error(error))
            return result
        result.completed = len(plan)
        if result.undo is not None:
            result.undo.undo_paths.extend((pair.target, pair.source) for pair in plan)
        for index, pair in enumerate(plan, start=1):
            self._emit_progress(
                progress_callback,
                OperationProgress(
                    operation_id,
                    OperationKind.BATCH_RENAME,
                    index,
                    len(plan),
                    current_name=os.path.basename(pair.target),
                ),
            )
        return result

    def _run_create_archive(
        self,
        operation_id: str,
        sources: list[str],
        destination: object | None,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.CREATE_ARCHIVE, 0, undo=UndoRecord("Create Archive"))
        if not isinstance(destination, str) or not destination:
            result.errors.append("Archive destination is missing")
            return result
        if cancel.is_set():
            result.cancelled = True
            return result
        try:
            if os.path.lexists(destination):
                raise FileExistsError(destination)
            create_archive(sources, destination)
            result.completed = 1
            result.undo.created_paths.append(destination)
        except (OSError, ValueError, ArchiveSafetyError) as error:
            result.errors.append(self._describe_error(error))
        self._emit_progress(
            progress_callback,
            OperationProgress(operation_id, OperationKind.CREATE_ARCHIVE, result.completed, 1, current_name=os.path.basename(destination)),
        )
        return result

    def _run_extract_archive(
        self,
        operation_id: str,
        sources: list[str],
        destination: object | None,
        cancel: threading.Event,
        progress_callback: Callable[[OperationProgress], None] | None,
    ) -> OperationResult:
        result = OperationResult(operation_id, OperationKind.EXTRACT_ARCHIVE, 0, undo=UndoRecord("Extract Archive"))
        if len(sources) != 1 or not isinstance(destination, str) or not destination:
            result.errors.append("Extract requires one archive and a destination")
            return result
        if cancel.is_set():
            result.cancelled = True
            return result
        try:
            extract_archive(sources[0], destination)
            result.completed = 1
            result.undo.created_paths.append(destination)
        except (OSError, ValueError, ArchiveSafetyError) as error:
            result.errors.append(self._describe_error(error))
        self._emit_progress(
            progress_callback,
            OperationProgress(operation_id, OperationKind.EXTRACT_ARCHIVE, result.completed, 1, current_name=os.path.basename(sources[0])),
        )
        return result

    def _run_create(self, operation_id: str, kind: OperationKind, sources: list[str], cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        result = OperationResult(operation_id, kind, 0, undo=UndoRecord(kind.value.title()))
        for path in sources:
            if cancel.is_set():
                result.cancelled = True
                break
            try:
                if not valid_entry_name(os.path.basename(path)):
                    result.errors.append(f"{path}: invalid name")
                    continue
                if kind == OperationKind.CREATE_FOLDER:
                    os.mkdir(path)
                else:
                    with open(path, "xb"):
                        pass
                result.completed += 1
                result.undo.created_paths.append(path)
            except OSError as error:
                result.errors.append(f"{os.path.basename(path)}: {self._describe_error(error)}")
            self._emit_progress(progress_callback, OperationProgress(operation_id, kind, result.completed, len(sources), current_name=os.path.basename(path)))
        return result

    def _run_restore(self, operation_id: str, sources: list[str], destination: str | None, cancel: threading.Event, progress_callback: Callable[[OperationProgress], None] | None) -> OperationResult:
        if not destination:
            return OperationResult(operation_id, OperationKind.RESTORE, 0, errors=["Original location is missing"])
        result = OperationResult(operation_id, OperationKind.RESTORE, 0, undo=UndoRecord("Restore"))
        if len(sources) != 1:
            result.errors.append("Restore requires one source")
            return result
        source = sources[0]
        if cancel.is_set():
            result.cancelled = True
            return result
        if os.path.lexists(destination):
            result.errors.append("The original location already exists")
            return result
        try:
            parent = os.path.dirname(destination)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if source.startswith("trash://"):
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                Gio.File.new_for_uri(source).move(Gio.File.new_for_path(destination), Gio.FileCopyFlags.NONE, None)
            else:
                shutil.move(source, destination)
            result.completed = 1
            if not source.startswith("trash://"):
                result.undo.undo_paths.append((destination, source))
        except OSError as error:
            result.errors.append(self._describe_error(error))
        except Exception as error:
            result.errors.append(self._describe_error(error))
        return result

    def undo_last(self) -> OperationResult:
        """Undo the latest reversible journal entry synchronously.

        The GTK layer invokes this only for a user command; all paths are
        already preflighted by the operation workers.  Failed inverse moves
        remain visible as errors and are never silently discarded.
        """

        record = self.undo.pop()
        if record is None:
            return OperationResult(uuid.uuid4().hex, OperationKind.RESTORE, 0, errors=["Nothing to undo"])
        result, inverse = self._apply_undo_record(record)
        if result.errors:
            # Keep the record available for a later retry when the failure was
            # caused by a transient vanished/locked target.
            self.undo.push(record)
        elif inverse is not None:
            self.undo.push_redo(inverse)
        return result

    def redo_last(self) -> OperationResult:
        """Redo the latest inverse move when the operation is safely reversible."""

        record = self.undo.pop_redo()
        if record is None:
            return OperationResult(uuid.uuid4().hex, OperationKind.RESTORE, 0, errors=["Nothing to redo"])
        result, inverse = self._apply_undo_record(record)
        if result.errors:
            self.undo.push_redo(record)
        elif inverse is not None:
            self.undo.push(inverse)
        return result

    def _apply_undo_record(self, record: UndoRecord) -> tuple[OperationResult, UndoRecord | None]:
        result = OperationResult(uuid.uuid4().hex, OperationKind.RESTORE, 0)
        inverse = UndoRecord(f"Redo: {record.label}")
        for directory in record.ensure_dirs:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as error:
                result.errors.append(f"{os.path.basename(directory)}: {self._describe_error(error)}")
                break
        if result.errors:
            return result, None
        for current, original in reversed(record.undo_paths):
            try:
                if not os.path.lexists(current):
                    raise FileNotFoundError(current)
                if os.path.lexists(original):
                    raise FileExistsError(original)
                os.replace(current, original)
                result.completed += 1
                inverse.undo_paths.append((original, current))
            except OSError as error:
                result.errors.append(f"{os.path.basename(current)}: {self._describe_error(error)}")
        for path in reversed(record.created_paths):
            try:
                if os.path.lexists(path):
                    self._remove_path(path)
                    result.completed += 1
            except OSError as error:
                result.errors.append(f"{os.path.basename(path)}: {self._describe_error(error)}")
        for trash_uri, original in reversed(record.trash_paths):
            try:
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                if os.path.lexists(original):
                    raise FileExistsError(original)
                Gio.File.new_for_uri(trash_uri).move(Gio.File.new_for_path(original), Gio.FileCopyFlags.NONE, None)
                result.completed += 1
            except Exception as error:
                result.errors.append(f"{os.path.basename(original)}: {self._describe_error(error)}")
        # Restoring trash or deleting a newly-created path cannot be replayed
        # without a content snapshot, so expose no fake redo entry for those.
        if record.trash_paths or record.created_paths:
            return result, None
        return result, inverse if (inverse.undo_paths and not result.errors) else None

    @staticmethod
    def _copy_path(source: str, target: str, cancel: threading.Event) -> None:
        if os.path.islink(source):
            os.symlink(os.readlink(source), target)
            return
        if os.path.isdir(source):
            os.makedirs(target)
            for root, directories, files in os.walk(source, followlinks=False):
                if cancel.is_set():
                    raise OperationCancelled()
                relative = os.path.relpath(root, source)
                current_target = target if relative == "." else os.path.join(target, relative)
                for directory in directories:
                    source_directory = os.path.join(root, directory)
                    target_directory = os.path.join(current_target, directory)
                    if os.path.islink(source_directory):
                        os.symlink(os.readlink(source_directory), target_directory)
                    else:
                        os.makedirs(target_directory, exist_ok=True)
                for filename in files:
                    if cancel.is_set():
                        raise OperationCancelled()
                    shutil.copy2(os.path.join(root, filename), os.path.join(current_target, filename), follow_symlinks=False)
                    if cancel.is_set():
                        raise OperationCancelled()
        else:
            shutil.copy2(source, target, follow_symlinks=False)
            if cancel.is_set():
                raise OperationCancelled()

    @staticmethod
    def _remove_if_exists(path: str) -> None:
        try:
            if os.path.lexists(path):
                OperationManager._remove_path(path)
        except OSError:
            pass

    @staticmethod
    def _describe_error(error: BaseException) -> str:
        if isinstance(error, PermissionError):
            return "You don't have permission to modify this location"
        if isinstance(error, FileNotFoundError):
            return "The source or destination no longer exists"
        if isinstance(error, FileExistsError):
            return "A file with that name already exists"
        return str(error) or error.__class__.__name__

    @staticmethod
    def _find_trash_uri(gio: object, original: str) -> str | None:
        try:
            trash = gio.File.new_for_uri("trash:///")
            enumerator = trash.enumerate_children(
                "standard::name,trash::orig-path",
                gio.FileQueryInfoFlags.NONE,
                None,
            )
            try:
                while True:
                    info = enumerator.next_file(None)
                    if info is None:
                        break
                    if attribute_text(info, "trash::orig-path") == original:
                        return trash.get_child(info.get_name()).get_uri()
            finally:
                enumerator.close(None)
        except Exception:
            return None
        return None

    @staticmethod
    def _remove_path(path: str) -> None:
        if os.path.islink(path) or os.path.isfile(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

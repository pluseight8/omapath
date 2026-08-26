"""Capability-driven synchronous GIO operations for non-local locations.

The worker is deliberately independent from GTK widgets.  GVfs providers
implement the actual operation; Omapath only supplies Gio.File objects and
turns provider errors into the normal OperationResult contract.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .operations import ConflictPolicy, OperationKind, OperationProgress, OperationResult


ProgressEmitter = Callable[[OperationProgress], None]


def _describe_error(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "You don't have permission to modify this location"
    if isinstance(error, FileNotFoundError):
        return "The source or destination no longer exists"
    if isinstance(error, FileExistsError):
        return "A file with that name already exists"
    message = getattr(error, "message", None) or str(error)
    return str(message) or error.__class__.__name__


def _unique_child(parent: Gio.File, name: str, cancellable: Gio.Cancellable) -> Gio.File:
    stem, suffix = os.path.splitext(name)
    for number in range(2, 10000):
        candidate_name = f"{stem} (copy {number}){suffix}"
        candidate = parent.get_child(candidate_name)
        if not candidate.query_exists(cancellable):
            return candidate
    raise GLib.Error(f"Could not create a unique name for {name}")


def _emit(
    emit_progress: ProgressEmitter | None,
    operation_id: str,
    kind: OperationKind,
    completed: int,
    total: int,
    name: str,
    *,
    bytes_done: int = 0,
    bytes_total: int = 0,
    state: str = "running",
) -> None:
    if emit_progress is None:
        return
    emit_progress(
        OperationProgress(
            operation_id,
            kind,
            completed,
            total,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            current_name=name,
            state=state,
        )
    )


def run_gio_operation(
    operation_id: str,
    kind: OperationKind,
    sources: list[Gio.File],
    destination: Gio.File | str | None,
    policy: ConflictPolicy,
    cancel: threading.Event,
    emit_progress: ProgressEmitter | None = None,
) -> OperationResult:
    """Run one provider-backed operation without using a shell.

    Remote conflict handling is intentionally conservative: ``MERGE`` is
    treated like ``RENAME`` because providers differ in recursive merge
    semantics.  The UI only offers directory Merge for the local worker,
    where Omapath can guarantee its behavior.
    """

    result = OperationResult(operation_id, kind, 0)
    cancellable = Gio.Cancellable()
    total = len(sources)
    parent = destination if isinstance(destination, Gio.File) else None

    def progress(current: int, total_bytes: int, _data: object = None) -> None:
        if cancel.is_set():
            cancellable.cancel()
        _emit(
            emit_progress,
            operation_id,
            kind,
            result.completed,
            total,
            "",
            bytes_done=int(current),
            bytes_total=int(total_bytes),
        )

    try:
        if kind in {OperationKind.COPY, OperationKind.MOVE, OperationKind.DUPLICATE}:
            if parent is None or not parent.query_exists(cancellable):
                result.errors.append("Destination is unavailable")
                return result
            for source in sources:
                if cancel.is_set():
                    result.cancelled = True
                    break
                name = source.get_basename() or source.get_uri()
                target = parent.get_child(name)
                try:
                    if target.query_exists(cancellable):
                        if policy is ConflictPolicy.SKIP:
                            result.skipped += 1
                            _emit(emit_progress, operation_id, kind, result.completed, total, name, state="skipped")
                            continue
                        if policy is ConflictPolicy.CANCEL:
                            result.errors.append(f"{name}: a file with that name already exists")
                            break
                        if policy in {ConflictPolicy.RENAME, ConflictPolicy.MERGE}:
                            target = _unique_child(parent, name, cancellable)
                        flags = Gio.FileCopyFlags.OVERWRITE if policy is ConflictPolicy.REPLACE else Gio.FileCopyFlags.NONE
                    else:
                        flags = Gio.FileCopyFlags.NONE
                    if kind is OperationKind.MOVE:
                        source.move(target, flags, cancellable, progress, None)
                    else:
                        source.copy(target, flags, cancellable, progress, None)
                    result.completed += 1
                except GLib.Error as error:
                    if cancel.is_set() or cancellable.is_cancelled():
                        result.cancelled = True
                        break
                    result.errors.append(f"{name}: {_describe_error(error)}")
                _emit(emit_progress, operation_id, kind, result.completed, total, name)
            return result

        for source in sources:
            if cancel.is_set():
                result.cancelled = True
                break
            name = source.get_basename() or source.get_uri()
            try:
                if kind is OperationKind.RENAME:
                    if not isinstance(destination, str) or not destination:
                        result.errors.append("Rename requires a destination name")
                        break
                    source.set_display_name(destination, cancellable)
                elif kind is OperationKind.CREATE_FOLDER:
                    source.make_directory(cancellable)
                elif kind is OperationKind.CREATE_FILE:
                    stream = source.create(Gio.FileCreateFlags.NONE, cancellable)
                    stream.close(cancellable)
                elif kind is OperationKind.TRASH:
                    source.trash(cancellable)
                elif kind is OperationKind.DELETE:
                    source.delete(cancellable)
                else:
                    result.errors.append(f"Unsupported remote operation: {kind.value}")
                    break
                result.completed += 1
            except GLib.Error as error:
                if cancel.is_set() or cancellable.is_cancelled():
                    result.cancelled = True
                    break
                result.errors.append(f"{name}: {_describe_error(error)}")
            _emit(emit_progress, operation_id, kind, result.completed, total, name)
    except (GLib.Error, TypeError, ValueError, OSError) as error:
        result.errors.append(_describe_error(error))
    return result


__all__ = ["run_gio_operation"]

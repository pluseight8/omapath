"""Preflighted, collision-safe batch rename planning."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class RenamePlanError(ValueError):
    """The proposed batch rename cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class BatchRenameSpec:
    find: str = ""
    replace: str = ""
    regex: bool = False
    prefix: str = ""
    suffix: str = ""
    numbering: bool = False
    start_number: int = 1
    padding: int = 1
    placeholder: str = "#"
    preserve_extension: bool = True


@dataclass(frozen=True, slots=True)
class RenamePair:
    source: str
    target: str


def _split_extension(name: str, preserve: bool) -> tuple[str, str]:
    if not preserve or name.startswith(".") or "." not in name:
        return name, ""
    stem, extension = os.path.splitext(name)
    return stem, extension


def _replace(value: str, spec: BatchRenameSpec) -> str:
    if not spec.find:
        return value
    if spec.regex:
        try:
            return re.sub(spec.find, spec.replace, value)
        except re.error as error:
            raise RenamePlanError(f"Invalid rename regular expression: {error}") from error
    return value.replace(spec.find, spec.replace)


def build_rename_plan(paths: Iterable[str], spec: BatchRenameSpec) -> list[RenamePair]:
    sources = [os.path.abspath(os.fspath(path)) for path in paths]
    if not sources:
        return []
    if len(set(sources)) != len(sources):
        raise RenamePlanError("Duplicate source paths")
    pairs: list[RenamePair] = []
    for index, source in enumerate(sources):
        name = os.path.basename(source)
        stem, extension = _split_extension(name, spec.preserve_extension)
        stem = _replace(stem, spec)
        if spec.numbering:
            token = str(spec.start_number + index).zfill(max(1, int(spec.padding)))
            stem = stem.replace(spec.placeholder, token)
            if spec.placeholder not in stem and spec.placeholder == "#":
                stem = f"{stem} {token}"
        target_name = f"{spec.prefix}{stem}{spec.suffix}{extension}"
        if not target_name or target_name in {".", ".."} or os.path.basename(target_name) != target_name or "\x00" in target_name:
            raise RenamePlanError(f"Invalid target name: {target_name!r}")
        pairs.append(RenamePair(source, os.path.join(os.path.dirname(source), target_name)))

    targets = [pair.target for pair in pairs]
    if len(set(targets)) != len(targets):
        raise RenamePlanError("Batch rename produces duplicate target names")
    source_set = set(sources)
    for pair in pairs:
        if os.path.lexists(pair.target) and pair.target not in source_set:
            raise RenamePlanError(f"Target already exists: {os.path.basename(pair.target)}")
    return pairs


def apply_rename_plan(pairs: Iterable[RenamePair]) -> list[RenamePair]:
    """Apply a preflighted plan with temporary names for cycles."""

    plan = list(pairs)
    if not plan:
        return []
    temporary: list[tuple[str, str]] = []
    try:
        for pair in plan:
            temporary_name = os.path.join(
                os.path.dirname(pair.source),
                f".omapath-rename-{uuid.uuid4().hex}.tmp",
            )
            os.replace(pair.source, temporary_name)
            temporary.append((temporary_name, pair.target))
        for source, target in temporary:
            os.replace(source, target)
    except Exception:
        for source, target in reversed(temporary):
            if os.path.lexists(source) and not os.path.lexists(target):
                try:
                    os.replace(source, target)
                except OSError:
                    pass
        raise
    return plan


__all__ = ["BatchRenameSpec", "RenamePair", "RenamePlanError", "apply_rename_plan", "build_rename_plan"]

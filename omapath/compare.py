"""Safe file/folder comparison command selection."""

from __future__ import annotations

import difflib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CompareTool:
    name: str
    executable: str
    argv_prefix: tuple[str, ...] = ()

    def argv(self, paths: Iterable[str]) -> list[str]:
        return [self.executable, *self.argv_prefix, *[os.fspath(path) for path in paths]]


def detect_compare_tool() -> CompareTool | None:
    for name, executable, prefix in (
        ("Meld", "meld", ()),
        ("Kompare", "kompare", ()),
        ("Diffoscope", "diffoscope", ()),
    ):
        if shutil.which(executable):
            return CompareTool(name, executable, prefix)
    return None


def validate_compare_paths(paths: Iterable[str]) -> tuple[str, str]:
    values = [os.path.abspath(os.fspath(path)) for path in paths]
    if len(values) != 2 or values[0] == values[1]:
        raise ValueError("Compare requires exactly two different items")
    if any(not os.path.lexists(path) for path in values):
        raise FileNotFoundError("One of the selected items no longer exists")
    return values[0], values[1]


def text_diff(left: str, right: str, *, max_bytes: int = 4 * 1024 * 1024) -> str:
    left_text = Path(left).read_text(encoding="utf-8", errors="replace")[:max_bytes]
    right_text = Path(right).read_text(encoding="utf-8", errors="replace")[:max_bytes]
    return "".join(
        difflib.unified_diff(
            left_text.splitlines(keepends=True),
            right_text.splitlines(keepends=True),
            fromfile=left,
            tofile=right,
        )
    )


__all__ = ["CompareTool", "detect_compare_tool", "text_diff", "validate_compare_paths"]

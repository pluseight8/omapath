"""Optional, bounded Git status lookup for the Details view."""

from __future__ import annotations

import os
import shutil
import subprocess


def repository_root(location: str) -> str | None:
    current = os.path.abspath(location)
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        marker = os.path.join(current, ".git")
        if os.path.isdir(marker) or os.path.isfile(marker):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def parse_status(output: str, root: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if len(line) < 4:
            continue
        state = line[:2].strip() or "?"
        name = line[3:]
        if " -> " in name:
            name = name.rsplit(" -> ", 1)[-1]
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        result[os.path.abspath(os.path.join(root, name))] = state
    return result


def status_for_location(location: str, timeout: float = 0.8) -> dict[str, str]:
    """Return direct/descendant statuses without raising on non-Git folders."""

    executable = shutil.which("git")
    root = repository_root(location)
    if executable is None or root is None:
        return {}
    try:
        completed = subprocess.run(
            [executable, "-C", root, "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, timeout),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    return parse_status(completed.stdout, root)

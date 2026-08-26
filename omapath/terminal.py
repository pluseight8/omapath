"""Safe terminal command detection for Open Terminal Here."""

from __future__ import annotations

import os
import shlex
import shutil


def configured_terminal(value: str | None = None) -> list[str] | None:
    raw = str(value or os.environ.get("TERMINAL", "")).strip()
    if raw:
        try:
            parts = shlex.split(raw)
        except ValueError:
            return None
        return parts or None
    for candidate in ("omarchy-launch-or-focus", "kitty", "foot", "alacritty", "gnome-terminal", "kgx"):
        if shutil.which(candidate):
            return [candidate]
    return None


def terminal_argv(terminal: list[str], cwd: str, shell: str | None = None) -> list[str]:
    if not terminal or not os.path.isdir(cwd):
        raise ValueError("A valid terminal and working directory are required")
    executable = os.path.basename(terminal[0])
    argv = list(terminal)
    if executable in {"kitty", "foot"}:
        argv.extend(["--directory", os.path.abspath(cwd)])
    elif executable == "alacritty":
        argv.extend(["--working-directory", os.path.abspath(cwd)])
    elif executable in {"gnome-terminal", "kgx"}:
        argv.extend(["--working-directory", os.path.abspath(cwd)])
    if shell:
        argv.extend(["--", shell])
    return argv


__all__ = ["configured_terminal", "terminal_argv"]

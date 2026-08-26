"""Bounded optional VCS command adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable

from .git_status import repository_root, status_for_location


@dataclass(frozen=True, slots=True)
class VcsCommand:
    action_id: str
    label: str
    argv: tuple[str, ...]


class GitAdapter:
    COMMANDS = {
        "status": VcsCommand("git.status", "Git Status", ("status", "--short")),
        "diff": VcsCommand("git.diff", "Git Diff", ("diff",)),
        "log": VcsCommand("git.log", "Git Log", ("log", "--oneline", "-20")),
        "add": VcsCommand("git.add", "Git Add", ("add", "--")),
        "unstage": VcsCommand("git.unstage", "Git Unstage", ("reset", "HEAD", "--")),
        "restore": VcsCommand("git.restore", "Git Restore", ("restore", "--")),
        "commit": VcsCommand("git.commit", "Git Commit", ("commit", "-m")),
        "fetch": VcsCommand("git.fetch", "Git Fetch", ("fetch",)),
        "pull": VcsCommand("git.pull", "Git Pull", ("pull",)),
        "push": VcsCommand("git.push", "Git Push", ("push",)),
    }

    def __init__(self, location: str, timeout: float = 2.0) -> None:
        self.root = repository_root(location)
        self.timeout = max(0.2, float(timeout))

    @property
    def available(self) -> bool:
        return self.root is not None and shutil.which("git") is not None

    def status(self) -> dict[str, str]:
        return status_for_location(self.root or "", timeout=self.timeout) if self.available else {}

    def argv(self, action: str, paths: Iterable[str] = ()) -> list[str]:
        if not self.available or action not in self.COMMANDS:
            raise ValueError("Git action is unavailable")
        command = self.COMMANDS[action]
        values = [os.path.relpath(os.path.abspath(os.fspath(path)), self.root or os.getcwd()) for path in paths]
        if action in {"add", "unstage", "restore"}:
            return ["git", "-C", self.root or os.getcwd(), *command.argv, *values]
        return ["git", "-C", self.root or os.getcwd(), *command.argv]

    def run(
        self,
        action: str,
        paths: Iterable[str] = (),
        *,
        message: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = self.argv(action, paths)
        if action == "commit":
            clean_message = str(message or "").strip()
            if not clean_message:
                raise ValueError("Commit message is empty")
            argv.append(clean_message)
        return subprocess.run(
            argv,
            cwd=self.root or None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            check=False,
        )


__all__ = ["GitAdapter", "VcsCommand"]

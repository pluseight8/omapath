"""Declarative, argv-only user services for context menus."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ServiceAction:
    name: str
    argv: tuple[str, ...]
    icon: str = "system-run-symbolic"
    mime_types: tuple[str, ...] = ()
    applies_to: str = "both"
    min_selection: int = 1
    max_selection: int | None = None
    terminal: bool = False

    def applies(self, paths: Iterable[str], mime_types: Iterable[str] = ()) -> bool:
        selected = list(paths)
        if len(selected) < self.min_selection or (self.max_selection is not None and len(selected) > self.max_selection):
            return False
        if self.applies_to != "both":
            has_folder = any(os.path.isdir(path) for path in selected)
            if self.applies_to == "folders" and not has_folder:
                return False
            if self.applies_to == "files" and has_folder:
                return False
        allowed = {value.casefold() for value in self.mime_types}
        if allowed:
            actual = {value.casefold() for value in mime_types}
            if not any(value in allowed or any(value.startswith(prefix.rstrip("*")) for prefix in allowed if prefix.endswith("*")) for value in actual):
                return False
        return True


def expand_argv(template: Iterable[str], paths: Iterable[str], directory: str | None = None) -> list[str]:
    """Expand placeholders as argv tokens, never through a shell."""

    values = [os.path.abspath(os.fspath(path)) for path in paths]
    uris = [Path(path).as_uri() for path in values]
    parent = directory or (os.path.dirname(values[0]) if values else os.getcwd())
    result: list[str] = []
    for token in template:
        token = str(token)
        if token == "{files}":
            result.extend(values)
        elif token == "{uris}":
            result.extend(uris)
        else:
            result.append(
                token.replace("{file}", values[0] if values else "")
                .replace("{uri}", uris[0] if uris else "")
                .replace("{directory}", os.path.abspath(parent))
            )
    return result


def _from_mapping(raw: dict[str, Any]) -> ServiceAction | None:
    name = raw.get("name")
    argv = raw.get("argv", raw.get("command"))
    if not isinstance(name, str) or not name.strip() or not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
        return None
    mime = raw.get("mime_types", raw.get("mime", []))
    if isinstance(mime, str):
        mime = [mime]
    if not isinstance(mime, list):
        mime = []
    try:
        minimum = max(0, int(raw.get("min_selection", 1)))
        maximum_raw = raw.get("max_selection")
        maximum = None if maximum_raw in {None, ""} else max(minimum, int(maximum_raw))
    except (TypeError, ValueError):
        return None
    return ServiceAction(
        name.strip(),
        tuple(argv),
        str(raw.get("icon", "system-run-symbolic")),
        tuple(value for value in mime if isinstance(value, str)),
        str(raw.get("applies_to", "both")),
        minimum,
        maximum,
        bool(raw.get("terminal", False)),
    )


def load_service(path: str | Path) -> ServiceAction | None:
    target = Path(path)
    try:
        if target.suffix.casefold() == ".toml":
            raw = tomllib.loads(target.read_text(encoding="utf-8"))
        else:
            raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError):
        return None
    return _from_mapping(raw) if isinstance(raw, dict) else None


def discover_services(directory: str | Path | None = None, limit: int = 64) -> list[ServiceAction]:
    root = Path(directory or Path.home() / ".local/share/omapath/services")
    try:
        files = sorted(
            [path for path in root.iterdir() if path.is_file() and path.suffix.casefold() in {".json", ".toml"}],
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return []
    result: list[ServiceAction] = []
    for path in files[: max(1, limit)]:
        action = load_service(path)
        if action is not None:
            result.append(action)
    return result


def run_service(action: ServiceAction, paths: Iterable[str], directory: str | None = None) -> subprocess.Popen[bytes]:
    argv = expand_argv(action.argv, paths, directory)
    if not argv or shutil.which(argv[0]) is None:
        raise FileNotFoundError(argv[0] if argv else "service command")
    return subprocess.Popen(argv, cwd=directory or None, shell=False)


__all__ = ["ServiceAction", "discover_services", "expand_argv", "load_service", "run_service"]

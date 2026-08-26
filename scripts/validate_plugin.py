#!/usr/bin/env python3
"""Validate Omapath's repository-local Omarchy plugin contract.

This mirrors the stable checks performed by Omarchy's plugin validator while
remaining runnable in ordinary CI where the Omarchy command may not exist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NoReturn


PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENTRY_POINT_BY_KIND = {
    "bar": "bar",
    "bar-widget": "barWidget",
    "menu": "menu",
    "overlay": "overlay",
    "panel": "panel",
    "service": "service",
}


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def validate(root: Path) -> dict[str, object]:
    if not root.is_dir():
        fail(f"plugin folder not found: {root}")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        fail(f"missing manifest.json: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")
    if not isinstance(manifest, dict):
        fail("manifest.json must contain an object")

    if manifest.get("schemaVersion") != 1:
        fail("schemaVersion must be the number 1")
    for field in ("id", "name", "version", "kinds", "entryPoints"):
        if field not in manifest:
            fail(f"manifest missing required field: {field}")

    plugin_id = manifest["id"]
    if not isinstance(plugin_id, str) or not PLUGIN_ID_RE.fullmatch(plugin_id):
        fail(f"invalid plugin id: {plugin_id!r}")
    if plugin_id.startswith("omarchy."):
        fail("the omarchy.* namespace is reserved")

    kinds = manifest["kinds"]
    entry_points = manifest["entryPoints"]
    if not isinstance(kinds, list) or not kinds or not all(isinstance(kind, str) for kind in kinds):
        fail("kinds must be a non-empty array of strings")
    if not isinstance(entry_points, dict):
        fail("entryPoints must be an object")

    for entry_name, relative_path in entry_points.items():
        if not isinstance(entry_name, str) or not isinstance(relative_path, str) or not relative_path:
            fail("entry point names and paths must be non-empty strings")
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or ".." in relative_path:
            fail(f"unsafe entry point: {relative_path!r}")
        target = root / path
        if not target.is_file():
            fail(f"entry point file not found: {relative_path}")

    for kind, entry_name in ENTRY_POINT_BY_KIND.items():
        if kind in kinds and entry_name not in entry_points:
            fail(f"kind {kind!r} requires entryPoints.{entry_name}")

    bar_widget = manifest.get("barWidget")
    if isinstance(bar_widget, dict) and "defaultSection" in bar_widget:
        if bar_widget["defaultSection"] not in {"left", "center", "right"}:
            fail("barWidget.defaultSection must be left, center, or right")

    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            fail(f"symlinks are not allowed: {path}")

    for required_file in ("README.md", "LICENSE"):
        if not (root / required_file).is_file():
            fail(f"marketplace-required file is missing: {required_file}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_folder", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        manifest = validate(args.plugin_folder.resolve())
    except ValueError as error:
        print(f"validate_plugin: {error}", file=sys.stderr)
        return 1
    print(f"valid Omarchy plugin: {manifest['id']} v{manifest['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Per-folder presentation properties stored in Omapath settings only."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .nautilus_features import canonical_location


@dataclass(slots=True)
class FolderViewProperties:
    view_mode: str = "grid"
    sort_mode: str = "name"
    sort_reverse: bool = False
    folders_first: bool = True
    show_hidden: bool = False
    show_previews: bool = True
    show_groups: bool = False
    group_criterion: str = "none"
    icon_zoom: int = 48
    additional_columns: tuple[str, ...] = ("name", "size", "type", "modified")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["additional_columns"] = list(self.additional_columns)
        return result


class FolderViewSettings:
    """Small validated store keyed by canonical URI/path."""

    def __init__(self, raw: object | None = None) -> None:
        self._values: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for location, value in raw.items():
                if isinstance(location, str) and isinstance(value, dict):
                    self._values[canonical_location(location)] = self._sanitize(value)

    @staticmethod
    def _sanitize(value: dict[str, Any]) -> dict[str, Any]:
        defaults = FolderViewProperties().to_dict()
        result: dict[str, Any] = {}
        if value.get("view_mode") in {"grid", "icons", "compact", "details", "list"}:
            result["view_mode"] = value["view_mode"]
        if value.get("sort_mode") in {"name", "size", "type", "modified"}:
            result["sort_mode"] = value["sort_mode"]
        if value.get("group_criterion") in {"none", "name", "type", "date", "size"}:
            result["group_criterion"] = value["group_criterion"]
        for key in ("sort_reverse", "folders_first", "show_hidden", "show_previews", "show_groups"):
            if isinstance(value.get(key), bool):
                result[key] = value[key]
        try:
            icon_zoom = int(value["icon_zoom"])
            if 32 <= icon_zoom <= 96:
                result["icon_zoom"] = icon_zoom
        except (KeyError, TypeError, ValueError):
            pass
        columns = value.get("additional_columns")
        if isinstance(columns, list):
            result["additional_columns"] = [column for column in columns if isinstance(column, str)][:16]
        return {key: result[key] for key in defaults if key in result}

    def get(self, location: str, defaults: FolderViewProperties | None = None) -> FolderViewProperties:
        base = (defaults or FolderViewProperties()).to_dict()
        key = location if location in {"*"} or str(location).startswith("prefix:") else canonical_location(location)
        value = self._values.get(key, {})
        base.update(value)
        if isinstance(base.get("additional_columns"), list):
            base["additional_columns"] = tuple(base["additional_columns"])
        return FolderViewProperties(**base)

    def set(self, location: str, properties: FolderViewProperties) -> None:
        canonical = canonical_location(location)
        if canonical:
            self._values[canonical] = self._sanitize(properties.to_dict())

    def apply_to_current(self, location: str, properties: FolderViewProperties) -> None:
        self.set(location, properties)

    def apply_to_all(self, properties: FolderViewProperties) -> None:
        self._values["*"] = self._sanitize(properties.to_dict())

    def apply_to_subfolders(self, location: str, properties: FolderViewProperties) -> None:
        canonical = canonical_location(location).rstrip("/")
        defaults = FolderViewProperties().to_dict()
        values = properties.to_dict()
        # A subfolder rule is an overlay.  Fields left at their normal default
        # must not erase a more specific All Folders rule.  ``set`` remains the
        # explicit full-property operation for Current Folder.
        overlay = {key: value for key, value in values.items() if value != defaults.get(key)}
        self._values[f"prefix:{canonical}"] = self._sanitize(overlay)

    def resolve(self, location: str, defaults: FolderViewProperties | None = None) -> FolderViewProperties:
        canonical = canonical_location(location).rstrip("/")
        base = defaults or FolderViewProperties()
        if "*" in self._values:
            base = self.get("*", base)
        prefix_matches = [
            (key.removeprefix("prefix:"), value)
            for key, value in self._values.items()
            if key.startswith("prefix:") and canonical.startswith(key.removeprefix("prefix:").rstrip("/") + "/")
        ]
        for prefix, value in sorted(prefix_matches, key=lambda item: len(item[0])):
            merged = base.to_dict()
            merged.update(value)
            base = FolderViewProperties(**{**merged, "additional_columns": tuple(merged["additional_columns"])})
        return self.get(canonical, base)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._values.items()}


__all__ = ["FolderViewProperties", "FolderViewSettings"]

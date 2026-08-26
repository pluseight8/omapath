"""Portable Places, Recent and Starred state without desktop-specific files."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .nautilus_features import canonical_location, push_recent, remove_location


@dataclass(slots=True)
class PlaceEntry:
    location: str
    label: str
    icon: str = "folder-symbolic"
    hidden: bool = False


class PlacesStore:
    def __init__(self, *, pinned: object | None = None, starred: object | None = None, recent: object | None = None) -> None:
        self.pinned: list[PlaceEntry] = []
        if isinstance(pinned, list):
            for raw in pinned:
                if isinstance(raw, str):
                    self.add_pinned(raw)
                elif isinstance(raw, dict) and isinstance(raw.get("location"), str):
                    self.add_pinned(raw["location"], str(raw.get("label") or ""), str(raw.get("icon") or "folder-symbolic"))
        self.starred: list[str] = self._clean_list(starred, 64)
        self.recent: list[str] = self._clean_list(recent, 24)

    @staticmethod
    def _clean_list(raw: object | None, limit: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                continue
            normalized = canonical_location(value)
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) >= limit:
                break
        return result

    def add_pinned(self, location: str, label: str = "", icon: str = "folder-symbolic") -> bool:
        normalized = canonical_location(location)
        if not normalized or any(entry.location == normalized for entry in self.pinned):
            return False
        self.pinned.append(PlaceEntry(normalized, label or normalized.rsplit("/", 1)[-1] or normalized, icon))
        return True

    def remove_pinned(self, location: str) -> None:
        normalized = canonical_location(location)
        self.pinned = [entry for entry in self.pinned if entry.location != normalized]

    def rename_pinned(self, location: str, label: str) -> bool:
        normalized = canonical_location(location)
        for entry in self.pinned:
            if entry.location == normalized:
                entry.label = str(label).strip() or entry.label
                return True
        return False

    def reorder_pinned(self, location: str, target_index: int) -> None:
        normalized = canonical_location(location)
        current = next((index for index, entry in enumerate(self.pinned) if entry.location == normalized), None)
        if current is None:
            return
        entry = self.pinned.pop(current)
        self.pinned.insert(max(0, min(int(target_index), len(self.pinned))), entry)

    def add_starred(self, location: str) -> None:
        normalized = canonical_location(location)
        self.starred = push_recent(self.starred, normalized, 64)

    def remove_starred(self, location: str) -> None:
        self.starred = remove_location(self.starred, location)

    def add_recent(self, location: str) -> None:
        self.recent = push_recent(self.recent, location, 24)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pinned_places": [asdict(entry) for entry in self.pinned],
            "starred_places": list(self.starred),
            "recent_locations": list(self.recent),
        }


__all__ = ["PlaceEntry", "PlacesStore"]

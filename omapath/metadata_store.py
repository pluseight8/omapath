"""Portable Omapath tags/rating/comments and bounded checksum helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .nautilus_features import canonical_location


@dataclass(slots=True)
class FileMetadataTags:
    tags: list[str]
    rating: int = 0
    comment: str = ""


class MetadataStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path.home() / ".config/omapath/metadata.json"
        self._values: dict[str, FileMetadataTags] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for location, value in raw.items():
            if not isinstance(location, str) or not isinstance(value, dict):
                continue
            tags = value.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            try:
                rating = max(0, min(5, int(value.get("rating", 0))))
            except (TypeError, ValueError):
                rating = 0
            self._values[canonical_location(location)] = FileMetadataTags(
                sorted({str(tag).strip() for tag in tags if str(tag).strip()})[:64],
                rating,
                str(value.get("comment", ""))[:4096],
            )

    def get(self, location: str) -> FileMetadataTags:
        value = self._values.get(canonical_location(location))
        if value is None:
            return FileMetadataTags([])
        return FileMetadataTags(list(value.tags), value.rating, value.comment)

    def set(self, location: str, *, tags: list[str] | None = None, rating: int | None = None, comment: str | None = None) -> None:
        key = canonical_location(location)
        current = self.get(key)
        self._values[key] = FileMetadataTags(
            sorted({str(tag).strip() for tag in (current.tags if tags is None else tags) if str(tag).strip()})[:64],
            current.rating if rating is None else max(0, min(5, int(rating))),
            current.comment if comment is None else str(comment)[:4096],
        )

    def save(self) -> bool:
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({key: asdict(value) for key, value in self._values.items()}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return True
        except (OSError, TypeError, ValueError):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            return False

    def search_tag(self, tag: str) -> list[str]:
        needle = str(tag).casefold()
        return [location for location, value in self._values.items() if any(needle == item.casefold() for item in value.tags)]

    def tag_counts(self) -> dict[str, int]:
        """Return stable, case-folded tag counts for a compact sidebar view."""

        counts: dict[str, int] = {}
        for value in self._values.values():
            for tag in value.tags:
                key = str(tag).strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (item[0].casefold(), item[0])))


def checksum(path: str, algorithm: str = "sha256", chunk_size: int = 1024 * 1024) -> str:
    if algorithm not in hashlib.algorithms_available:
        raise ValueError(f"Unsupported checksum algorithm: {algorithm}")
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(max(1024, int(chunk_size)))
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["FileMetadataTags", "MetadataStore", "checksum"]

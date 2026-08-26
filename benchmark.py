#!/usr/bin/env python3
"""Small repeatable headless benchmark for model/sort/filter costs."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from omapath.filesystem import filter_items, sort_items
from omapath.model import FileItem


class FakeFile:
    def __init__(self, path: str) -> None:
        self.path = path

    def get_path(self) -> str:
        return self.path

    def get_uri(self) -> str:
        return Path(self.path).as_uri()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1000, choices=(1000, 10000, 50000))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="omapath-benchmark-") as root:
        directory = Path(root)
        started = time.perf_counter()
        items = [
            FileItem(FakeFile(str(directory / f"file{index}.txt")), f"file{index}.txt", f"file{index}.txt", False, index, index, "text/plain", False, False)
            for index in range(args.entries)
        ]
        model_time = time.perf_counter() - started
        started = time.perf_counter()
        sorted_items = sort_items(items, mode="name")
        sort_time = time.perf_counter() - started
        started = time.perf_counter()
        filtered = filter_items(sorted_items, "file9")
        filter_time = time.perf_counter() - started
        print(f"entries={args.entries}")
        print(f"model_creation_seconds={model_time:.6f}")
        print(f"natural_sort_seconds={sort_time:.6f}")
        print(f"filter_seconds={filter_time:.6f}")
        print(f"matches={len(filtered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

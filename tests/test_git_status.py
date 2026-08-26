from __future__ import annotations

import unittest

from omapath.git_status import parse_status


class GitStatusTests(unittest.TestCase):
    def test_porcelain_status_maps_paths_and_renames(self) -> None:
        result = parse_status(" M file.txt\n?? new.txt\nR  old.txt -> renamed.txt\n", "/tmp/repo")
        self.assertEqual(result["/tmp/repo/file.txt"], "M")
        self.assertEqual(result["/tmp/repo/new.txt"], "??")
        self.assertEqual(result["/tmp/repo/renamed.txt"], "R")

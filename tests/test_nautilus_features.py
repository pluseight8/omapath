from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from omapath.nautilus_features import (
    canonical_location,
    push_recent,
    remove_location,
    script_paths,
    template_paths,
)


class NautilusFeatureTests(unittest.TestCase):
    def test_location_history_uses_canonical_identity(self) -> None:
        self.assertEqual(canonical_location("trash:///"), "trash:///")
        self.assertEqual(canonical_location("file:///tmp/a%20b"), "/tmp/a b")
        history = push_recent(["/tmp", "file:///tmp"], "~/Work", limit=3)
        self.assertEqual(history, [os.path.abspath(os.path.expanduser("~/Work")), "/tmp"])
        self.assertEqual(remove_location(history, "file:///tmp"), [history[0]])

    def test_templates_and_scripts_are_bounded_and_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            templates = home / "Templates"
            templates.mkdir()
            (templates / "note.txt").write_text("hello", encoding="utf-8")
            (templates / ".hidden").write_text("hidden", encoding="utf-8")
            (templates / "folder").mkdir()
            self.assertEqual([path.name for path in template_paths(str(home))], ["note.txt"])

            data_home = home / "data"
            scripts = data_home / "nautilus" / "scripts"
            scripts.mkdir(parents=True)
            runnable = scripts / "format.sh"
            runnable.write_text("#!/bin/sh\n", encoding="utf-8")
            runnable.chmod(0o755)
            (scripts / "not-runnable.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual([path.name for path in script_paths(str(home), str(data_home))], ["format.sh"])

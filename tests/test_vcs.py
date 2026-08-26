from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omapath.vcs import GitAdapter


class VcsTests(unittest.TestCase):
    def test_git_adapter_builds_argv_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / ".git").mkdir()
            adapter = GitAdapter(str(base))
            with patch("omapath.vcs.shutil.which", return_value="/usr/bin/git"):
                self.assertEqual(adapter.argv("add", [str(base / "a;file.txt")])[-1], "a;file.txt")
            self.assertNotIn("shell", adapter.argv("status"))

    def test_unknown_action_is_rejected(self) -> None:
        adapter = GitAdapter("/tmp")
        with patch("omapath.vcs.shutil.which", return_value="/usr/bin/git"):
            with self.assertRaises(ValueError):
                adapter.argv("unknown")


if __name__ == "__main__":
    unittest.main()

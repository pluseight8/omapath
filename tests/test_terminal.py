from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.terminal import terminal_argv


class TerminalTests(unittest.TestCase):
    def test_terminal_working_directory_is_an_argv_value(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            cwd = Path(root) / "folder with spaces"
            cwd.mkdir()
            argv = terminal_argv(["kitty"], str(cwd), "/bin/bash")
            self.assertEqual(argv, ["kitty", "--directory", str(cwd), "--", "/bin/bash"])

    def test_invalid_directory_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            terminal_argv(["kitty"], "/path/that/does/not/exist")


if __name__ == "__main__":
    unittest.main()

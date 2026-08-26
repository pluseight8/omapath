from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    from omapath.settings import validate_settings
    from omapath.ui.simple_window import DirectoryEntry, OmapathApplication, visible_directory_entries

    HIDDEN_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gio = None  # type: ignore[assignment]
    DirectoryEntry = None  # type: ignore[assignment]
    OmapathApplication = None  # type: ignore[assignment]
    visible_directory_entries = None  # type: ignore[assignment]
    validate_settings = None  # type: ignore[assignment]
    HIDDEN_IMPORT_ERROR = error


class HiddenFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if HIDDEN_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GIO unavailable: {HIDDEN_IMPORT_ERROR}")

    def test_hidden_presentation_filter_handles_unicode_directories_and_symlinks(self) -> None:
        assert DirectoryEntry is not None and Gio is not None and visible_directory_entries is not None
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            target = root / "обычная папка"
            target.mkdir()
            link = root / "ссылка"
            link.symlink_to(target, target_is_directory=True)
            entries = [
                DirectoryEntry(Gio.File.new_for_path(str(root / ".hidden")), ".hidden", ".hidden", False, "text/plain", True),
                DirectoryEntry(Gio.File.new_for_path(str(root / "Документы")), "Документы", "Документы", True, "inode/directory"),
                DirectoryEntry(Gio.File.new_for_path(str(link)), "ссылка", "ссылка", False, "inode/symlink"),
            ]
            self.assertEqual([entry.name for entry in visible_directory_entries(entries, False)], ["Документы", "ссылка"])
            self.assertEqual(
                [entry.name for entry in visible_directory_entries(entries, True)],
                [".hidden", "Документы", "ссылка"],
            )

    def test_corrupt_hidden_setting_falls_back_to_false(self) -> None:
        assert validate_settings is not None
        settings = validate_settings({"show_hidden": "yes"})
        self.assertFalse(settings["show_hidden"])

    def test_toggle_updates_state_without_reenumeration(self) -> None:
        assert OmapathApplication is not None
        application = OmapathApplication.__new__(OmapathApplication)
        application.show_hidden = False
        application.settings = {"show_hidden": False}
        application.file_view = None
        application.directory_entries_ready = False
        application.show_hidden_check = None
        application._sync_view_menu = Mock()

        application.toggle_hidden(persist=False)

        self.assertTrue(application.show_hidden)
        application._sync_view_menu.assert_called_once_with()

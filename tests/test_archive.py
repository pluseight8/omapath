from __future__ import annotations

import tempfile
import tarfile
import unittest
import zipfile
from pathlib import Path

from omapath.archive import ArchiveSafetyError, create_archive, extract_archive


class ArchiveTests(unittest.TestCase):
    def test_create_and_extract_zip(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            source = base / "source"
            source.mkdir()
            (source / "hello.txt").write_text("hello", encoding="utf-8")
            archive = base / "files.zip"
            create_archive([str(source)], str(archive))
            destination = base / "out"
            extract_archive(str(archive), str(destination))
            self.assertEqual((destination / "source" / "hello.txt").read_text(encoding="utf-8"), "hello")

    def test_zip_path_traversal_is_rejected_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            archive = base / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("../../outside.txt", "no")
            destination = base / "out"
            with self.assertRaises(ArchiveSafetyError):
                extract_archive(str(archive), str(destination))
            self.assertFalse(destination.exists())
            self.assertFalse((base.parent / "outside.txt").exists())

    def test_tar_symlink_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            archive = base / "unsafe.tar"
            with tarfile.open(archive, "w") as source:
                member = tarfile.TarInfo("link")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                source.addfile(member)
            destination = base / "out"
            with self.assertRaises(ArchiveSafetyError):
                extract_archive(str(archive), str(destination))
            self.assertFalse(destination.exists())

    def test_extraction_limits_reject_bomb_like_members(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            archive = base / "large.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("large.txt", b"x" * 32)
            destination = base / "out"
            with self.assertRaises(ArchiveSafetyError):
                extract_archive(str(archive), str(destination), max_member_bytes=8)
            self.assertFalse(destination.exists())

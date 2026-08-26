"""Small, safe ZIP/tar helpers used by the optional archive actions."""

from __future__ import annotations

import os
import posixpath
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path


class ArchiveSafetyError(ValueError):
    """An archive contains a path or entry type that must not be extracted."""


DEFAULT_MAX_MEMBER_BYTES = 1 << 30
DEFAULT_MAX_TOTAL_BYTES = 4 << 30


def archive_kind(path: str) -> str | None:
    name = os.path.basename(path).casefold()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    return None


def _safe_destination(root: str, member_name: str) -> str:
    """Resolve an archive member under root without allowing traversal."""

    if "\x00" in member_name or "\\" in member_name:
        raise ArchiveSafetyError("archive member contains an unsafe path")
    normalized = posixpath.normpath(member_name)
    if normalized in {"", "."}:
        return os.path.abspath(root)
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ArchiveSafetyError(f"archive member escapes extraction folder: {member_name}")
    root_real = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_real, *normalized.split("/")))
    if os.path.commonpath((root_real, candidate)) != root_real:
        raise ArchiveSafetyError(f"archive member escapes extraction folder: {member_name}")
    return candidate


def _ensure_no_symlink_parent(root: str, path: str) -> None:
    root_real = os.path.abspath(root)
    current = root_real
    relative = os.path.relpath(os.path.dirname(path), root_real)
    if relative == ".":
        return
    for component in Path(relative).parts:
        current = os.path.join(current, component)
        if os.path.islink(current):
            raise ArchiveSafetyError("archive extraction would follow a symlink")


def _safe_member_name(name: str) -> str:
    if name.endswith("/"):
        return name[:-1]
    return name


def _extract_zip(archive: str, destination: str, max_member_bytes: int, max_total_bytes: int) -> None:
    total_bytes = 0
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            member_name = _safe_member_name(info.filename)
            target = _safe_destination(destination, member_name)
            if info.file_size > max_member_bytes or total_bytes + info.file_size > max_total_bytes:
                raise ArchiveSafetyError("archive extraction exceeds configured size limits")
            mode = (info.external_attr >> 16) & 0o170000
            if mode and mode not in {stat.S_IFREG, stat.S_IFDIR}:
                raise ArchiveSafetyError(f"unsupported unsafe archive entry: {info.filename}")
            if info.is_dir() or info.filename.endswith("/"):
                _ensure_no_symlink_parent(destination, target + os.sep + "placeholder")
                os.makedirs(target, exist_ok=True)
                continue
            _ensure_no_symlink_parent(destination, target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with source.open(info, "r") as input_stream, open(target, "xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            total_bytes += info.file_size
            permissions = (info.external_attr >> 16) & 0o777
            if permissions:
                os.chmod(target, permissions)


def _extract_tar(archive: str, destination: str, max_member_bytes: int, max_total_bytes: int) -> None:
    total_bytes = 0
    with tarfile.open(archive, mode="r:*") as source:
        for member in source.getmembers():
            target = _safe_destination(destination, member.name)
            if member.isdir():
                _ensure_no_symlink_parent(destination, target + os.sep + "placeholder")
                os.makedirs(target, exist_ok=True)
                continue
            if not member.isreg():
                raise ArchiveSafetyError(f"unsupported unsafe archive entry: {member.name}")
            if member.size > max_member_bytes or total_bytes + member.size > max_total_bytes:
                raise ArchiveSafetyError("archive extraction exceeds configured size limits")
            _ensure_no_symlink_parent(destination, target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            input_stream = source.extractfile(member)
            if input_stream is None:
                raise ArchiveSafetyError(f"could not read archive entry: {member.name}")
            with input_stream, open(target, "xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            total_bytes += member.size
            os.chmod(target, member.mode & 0o777)


def extract_archive(
    archive: str,
    destination: str,
    *,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> str:
    """Extract a supported archive into an existing empty destination."""

    kind = archive_kind(archive)
    if kind is None:
        raise ArchiveSafetyError("unsupported archive format")
    os.makedirs(destination, exist_ok=False)
    try:
        if kind == "zip":
            _extract_zip(archive, destination, max_member_bytes, max_total_bytes)
        else:
            _extract_tar(archive, destination, max_member_bytes, max_total_bytes)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def create_archive(paths: list[str], target: str, kind: str = "zip") -> str:
    """Create a ZIP or tar archive with paths stored relative to their parent."""

    if kind not in {"zip", "tar"}:
        raise ValueError("unsupported archive format")
    if not paths:
        raise ValueError("no archive sources")
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            def add_path(path: str, arcname: str) -> None:
                if os.path.isdir(path) and not os.path.islink(path):
                    with os.scandir(path) as entries:
                        children = sorted(entries, key=lambda entry: entry.name.casefold())
                    if not children:
                        archive.writestr(arcname.rstrip("/") + "/", "")
                    for child in children:
                        add_path(child.path, f"{arcname.rstrip('/')}/{child.name}")
                    return
                archive.write(path, arcname=arcname)

            for path in paths:
                add_path(path, os.path.basename(os.path.normpath(path)))
    else:
        with tarfile.open(target, "w") as archive:
            for path in paths:
                archive.add(path, arcname=os.path.basename(os.path.normpath(path)), recursive=True)
    return target

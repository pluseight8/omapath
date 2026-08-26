"""Capability-detected GIO/GVfs remote-location helpers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse


SUPPORTED_SCHEMES = {"sftp", "smb", "dav", "davs", "ftp", "mtp", "afc", "gdrive", "google-drive"}


@dataclass(frozen=True, slots=True)
class RemoteLocation:
    scheme: str
    host: str
    path: str = "/"
    user: str = ""

    @property
    def uri(self) -> str:
        authority = f"{quote(self.user)}@" if self.user else ""
        authority += self.host
        return f"{self.scheme}://{authority}{self.path if self.path.startswith('/') else '/' + self.path}"


def supported_scheme(value: str) -> bool:
    try:
        scheme = urlparse(str(value)).scheme.casefold()
    except ValueError:
        return False
    return scheme in SUPPORTED_SCHEMES


def parse_remote_uri(value: str) -> RemoteLocation | None:
    try:
        parsed = urlparse(str(value).strip())
    except ValueError:
        return None
    if parsed.scheme.casefold() not in SUPPORTED_SCHEMES or not parsed.netloc:
        return None
    return RemoteLocation(parsed.scheme.casefold(), parsed.hostname or parsed.netloc, parsed.path or "/", parsed.username or "")


def build_remote_uri(scheme: str, host: str, path: str = "/", user: str = "") -> str:
    normalized_scheme = str(scheme).casefold().strip()
    if normalized_scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"Unsupported remote scheme: {scheme}")
    clean_host = str(host).strip()
    if not clean_host or any(character in clean_host for character in "\r\n/@"):
        raise ValueError("Invalid remote host")
    return RemoteLocation(normalized_scheme, clean_host, str(path or "/"), str(user or "")).uri


__all__ = ["RemoteLocation", "SUPPORTED_SCHEMES", "build_remote_uri", "parse_remote_uri", "supported_scheme"]

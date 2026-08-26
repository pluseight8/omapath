"""Capability-detected removable-device integration through Gio.VolumeMonitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    name: str
    uri: str | None
    mounted: bool
    can_mount: bool
    can_eject: bool
    icon: Gio.Icon | None = None
    volume: Gio.Volume | None = None
    drive: Gio.Drive | None = None
    can_stop: bool = False


def device_info(volume: Gio.Volume) -> DeviceInfo:
    mount = volume.get_mount()
    root = mount.get_root() if mount is not None else None
    drive = volume.get_drive()
    can_eject = volume.can_eject() or (mount is not None and mount.can_eject()) or (drive is not None and drive.can_eject())
    can_stop = drive is not None and drive.can_stop()
    return DeviceInfo(
        name=volume.get_name(),
        uri=root.get_uri() if root is not None else None,
        mounted=mount is not None,
        can_mount=volume.can_mount(),
        can_eject=can_eject,
        icon=volume.get_symbolic_icon(),
        volume=volume,
        drive=drive,
        can_stop=can_stop,
    )


class DeviceMonitor:
    def __init__(self, on_changed: Callable[[], None]) -> None:
        self.monitor = Gio.VolumeMonitor.get()
        self.on_changed = on_changed
        self._handlers: list[int] = []

    def start(self) -> None:
        if self._handlers:
            return
        for signal in ("mount-added", "mount-removed", "volume-added", "volume-removed", "drive-connected", "drive-disconnected"):
            self._handlers.append(self.monitor.connect(signal, self._on_signal))

    def stop(self) -> None:
        for handler in self._handlers:
            try:
                self.monitor.disconnect(handler)
            except (TypeError, ValueError):
                pass
        self._handlers.clear()

    def list_devices(self) -> list[DeviceInfo]:
        return [device_info(volume) for volume in self.monitor.get_volumes()]

    def _on_signal(self, *_args: object) -> None:
        try:
            self.on_changed()
        except Exception:
            # Device hotplug must not take down the file browser.
            return


def mount(volume: Gio.Volume, on_complete: Callable[[bool, str | None], None]) -> None:
    if not volume.can_mount():
        on_complete(False, "Device cannot be mounted")
        return
    volume.mount(
        Gio.MountMountFlags.NONE,
        None,
        None,
        lambda source, result: _finish_mount(source, result, on_complete),
    )


def _finish_mount(volume: Gio.Volume, result: Gio.AsyncResult, on_complete: Callable[[bool, str | None], None]) -> None:
    try:
        volume.mount_finish(result)
    except GLib.Error as error:
        on_complete(False, error.message)
    else:
        on_complete(True, None)


def unmount(mount_point: Gio.Mount, on_complete: Callable[[bool, str | None], None]) -> None:
    mount_point.unmount_with_operation(
        Gio.MountUnmountFlags.NONE,
        None,
        None,
        lambda source, result: _finish_unmount(source, result, on_complete),
    )


def eject(mount_point: Gio.Mount, on_complete: Callable[[bool, str | None], None]) -> None:
    """Eject a mounted volume when the provider exposes that operation."""

    if not mount_point.can_eject():
        on_complete(False, "Device cannot be ejected")
        return
    mount_point.eject_with_operation(
        Gio.MountUnmountFlags.NONE,
        None,
        None,
        lambda source, result: _finish_eject(source, result, on_complete),
    )


def stop_drive(drive: Gio.Drive, on_complete: Callable[[bool, str | None], None]) -> None:
    """Safely stop a drive when eject is unavailable but stop is supported."""

    if not drive.can_stop():
        on_complete(False, "Device cannot be safely removed")
        return
    drive.stop(
        Gio.MountUnmountFlags.NONE,
        None,
        None,
        lambda source, result: _finish_stop(source, result, on_complete),
    )


def eject_drive(drive: Gio.Drive, on_complete: Callable[[bool, str | None], None]) -> None:
    if not drive.can_eject():
        on_complete(False, "Drive cannot be ejected")
        return
    drive.eject_with_operation(
        Gio.MountUnmountFlags.NONE,
        None,
        None,
        lambda source, result: _finish_drive_eject(source, result, on_complete),
    )


def _finish_eject(mount_point: Gio.Mount, result: Gio.AsyncResult, on_complete: Callable[[bool, str | None], None]) -> None:
    try:
        mount_point.eject_with_operation_finish(result)
    except GLib.Error as error:
        on_complete(False, error.message)
    else:
        on_complete(True, None)


def _finish_stop(drive: Gio.Drive, result: Gio.AsyncResult, on_complete: Callable[[bool, str | None], None]) -> None:
    try:
        drive.stop_finish(result)
    except GLib.Error as error:
        on_complete(False, error.message)
    else:
        on_complete(True, None)


def _finish_drive_eject(drive: Gio.Drive, result: Gio.AsyncResult, on_complete: Callable[[bool, str | None], None]) -> None:
    try:
        drive.eject_with_operation_finish(result)
    except GLib.Error as error:
        on_complete(False, error.message)
    else:
        on_complete(True, None)


def _finish_unmount(mount_point: Gio.Mount, result: Gio.AsyncResult, on_complete: Callable[[bool, str | None], None]) -> None:
    try:
        mount_point.unmount_with_operation_finish(result)
    except GLib.Error as error:
        on_complete(False, error.message)
    else:
        on_complete(True, None)


__all__ = ["DeviceInfo", "DeviceMonitor", "device_info", "eject", "eject_drive", "mount", "stop_drive", "unmount"]

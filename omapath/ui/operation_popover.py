"""Compact progress surface for concurrent filesystem operations."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango


class OperationController:
    """Present operation progress without moving filesystem work into GTK."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.button: Gtk.MenuButton | None = None
        self.rows_box: Gtk.Box | None = None
        self.rows: dict[str, Gtk.Box] = {}
        self.labels: dict[str, Gtk.Label] = {}
        self.detail_labels: dict[str, Gtk.Label] = {}
        self.progress_bars: dict[str, Gtk.ProgressBar] = {}
        self.cancel_buttons: dict[str, Gtk.Button] = {}
        self.finished: set[str] = set()
        self.undoable: set[str] = set()

    def build(self) -> Gtk.MenuButton:
        if self.button is not None:
            return self.button
        self.button = Gtk.MenuButton(label="Operations")
        self.button.add_css_class("toolbar-button")
        self.button.set_visible(False)
        popover = Gtk.Popover()
        popover.add_css_class("simple-context-popover")
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        shell.add_css_class("simple-context-menu")
        shell.set_margin_top(8)
        shell.set_margin_bottom(8)
        shell.set_margin_start(8)
        shell.set_margin_end(8)
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        shell.append(self.rows_box)
        popover.set_child(shell)
        self.button.set_popover(popover)
        self.host.operation_button = self.button
        return self.button

    def track(self, handle: Any, kind: Any) -> None:
        operation_id = str(getattr(handle, "operation_id", ""))
        if not operation_id:
            return
        self._ensure_row(operation_id, str(getattr(kind, "value", kind)))
        self._set_visible(True)

    def _ensure_row(self, operation_id: str, kind: str) -> Gtk.Box | None:
        if self.rows_box is None:
            return None
        if operation_id in self.rows:
            return self.rows[operation_id]
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.add_css_class("operation-row")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon_names = {
            "copy": "edit-copy-symbolic",
            "move": "go-jump-symbolic",
            "trash": "user-trash-symbolic",
            "delete": "user-trash-full-symbolic",
            "rename": "document-edit-symbolic",
            "create-file": "document-new-symbolic",
            "create-folder": "folder-new-symbolic",
        }
        icon = Gtk.Image.new_from_icon_name(icon_names.get(kind, "system-run-symbolic"))
        icon.set_pixel_size(18)
        header.append(icon)
        label = Gtk.Label(label=kind.replace("-", " ").title())
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        header.append(label)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("flat")
        cancel.connect("clicked", lambda _button, token=operation_id: self._on_row_button(token))
        header.append(cancel)
        bar = Gtk.ProgressBar()
        bar.set_show_text(True)
        details = Gtk.Expander(label="Details")
        details.set_visible(False)
        details_label = Gtk.Label()
        details_label.set_wrap(True)
        details_label.set_selectable(True)
        details_label.set_halign(Gtk.Align.START)
        details.set_child(details_label)
        row.append(header)
        row.append(bar)
        row.append(details)
        self.rows_box.append(row)
        self.rows[operation_id] = row
        self.labels[operation_id] = label
        self.detail_labels[operation_id] = details_label
        self.progress_bars[operation_id] = bar
        self.cancel_buttons[operation_id] = cancel
        return row

    def on_progress(self, progress: Any) -> None:
        operation_id = str(getattr(progress, "operation_id", ""))
        if not operation_id:
            return
        row = self._ensure_row(operation_id, str(getattr(getattr(progress, "kind", ""), "value", "operation")))
        if row is None:
            return
        current_name = str(getattr(progress, "current_name", "") or "")
        fraction = max(0.0, min(1.0, float(getattr(progress, "fraction", 0.0) or 0.0)))
        completed = int(getattr(progress, "completed", 0) or 0)
        total = int(getattr(progress, "total", 0) or 0)
        bytes_done = int(getattr(progress, "bytes_done", 0) or 0)
        bytes_total = int(getattr(progress, "bytes_total", 0) or 0)
        byte_text = f" · {self._format_bytes(bytes_done)}/{self._format_bytes(bytes_total)}" if bytes_total else ""
        state = str(getattr(progress, "state", "running") or "running").replace("_", " ").title()
        text = f"{current_name} · {completed}/{total}{byte_text}" if current_name and total else f"{completed}/{total}{byte_text}"
        if state.casefold() not in {"running", "done"}:
            text = f"{state} · {text}"
        label = self.labels.get(operation_id)
        if label is not None:
            label.set_text(text)
        bar = self.progress_bars.get(operation_id)
        if bar is not None:
            bar.set_fraction(fraction)
            bar.set_text(f"{int(fraction * 100)}%")
        self._set_visible(True)

    def on_complete(self, result: Any) -> None:
        operation_id = str(getattr(result, "operation_id", ""))
        row = self.rows.get(operation_id)
        if row is None:
            return
        errors = list(getattr(result, "errors", []) or [])
        completed = int(getattr(result, "completed", 0) or 0)
        cancelled = bool(getattr(result, "cancelled", False))
        self.finished.add(operation_id)
        label = self.labels.get(operation_id)
        bar = self.progress_bars.get(operation_id)
        button = self.cancel_buttons.get(operation_id)
        if errors:
            if label is not None:
                label.set_text("Failed: " + "; ".join(str(error) for error in errors))
                label.set_tooltip_text("\n".join(str(error) for error in errors))
            detail = self.detail_labels.get(operation_id)
            if detail is not None:
                detail.set_text("\n".join(str(error) for error in errors))
                parent = detail.get_parent()
                if isinstance(parent, Gtk.Expander):
                    parent.set_visible(True)
                    parent.set_expanded(False)
            if button is not None:
                button.set_label("Dismiss")
            if bar is not None:
                bar.set_fraction(0.0)
            self._set_visible(True)
            return
        if label is not None:
            label.set_text("Cancelled" if cancelled else f"Completed · {completed} item(s)")
        undo = getattr(result, "undo", None)
        can_undo = bool(
            undo is not None
            and any(
                bool(getattr(undo, field, ()))
                for field in ("undo_paths", "created_paths", "trash_paths")
            )
        )
        if button is not None:
            if can_undo:
                button.set_label("Undo")
                self.undoable.add(operation_id)
            else:
                button.set_label("Dismiss")
        if bar is not None:
            bar.set_fraction(0.0 if cancelled else 1.0)
            bar.set_text("Cancelled" if cancelled else "Done")
        # Keep reversible completions visible until the user explicitly takes
        # Undo or Dismiss; a 1.5s timeout made the action effectively
        # unreachable for ordinary file copies.  Irreversible completions can
        # still leave the compact operation center automatically.
        if not can_undo:
            GLib.timeout_add(2500, self._remove_if_finished, operation_id)

    def cancel(self, operation_id: str) -> None:
        self.host.operations.cancel(operation_id)
        label = self.labels.get(operation_id)
        if label is not None:
            label.set_text("Cancelling…")

    def _on_row_button(self, operation_id: str) -> None:
        if operation_id in self.finished:
            if operation_id in self.undoable:
                self.host.undo_last()
            self.dismiss(operation_id)
        else:
            self.cancel(operation_id)

    def dismiss(self, operation_id: str) -> None:
        row = self.rows.pop(operation_id, None)
        if row is not None and self.rows_box is not None:
            self.rows_box.remove(row)
        self.labels.pop(operation_id, None)
        self.detail_labels.pop(operation_id, None)
        self.progress_bars.pop(operation_id, None)
        self.cancel_buttons.pop(operation_id, None)
        self.finished.discard(operation_id)
        self.undoable.discard(operation_id)
        self._set_visible(bool(self.rows))

    def _remove_if_finished(self, operation_id: str) -> bool:
        if operation_id in self.rows:
            self.dismiss(operation_id)
        return False

    def _set_visible(self, visible: bool) -> None:
        if self.button is not None:
            self.button.set_visible(visible)

    def shutdown(self) -> None:
        for operation_id in list(self.rows):
            self.dismiss(operation_id)
        if self.button is not None:
            self.button.set_visible(False)

    @staticmethod
    def _format_bytes(value: int) -> str:
        amount = float(max(0, value))
        for suffix in ("B", "KiB", "MiB", "GiB"):
            if amount < 1024 or suffix == "GiB":
                return f"{amount:.0f} {suffix}" if suffix == "B" else f"{amount:.1f} {suffix}"
            amount /= 1024


__all__ = ["OperationController"]

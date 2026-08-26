"""Small GTK4 dialog presenters used by the active Omapath window.

The presenters own widget construction and response lifecycle only.  Filesystem
operations remain on the host application and are supplied as callbacks.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango

from ..filesystem import format_date, format_size
from ..operations import ConflictPolicy


class DialogController:
    """Present reusable, host-backed dialogs without owning filesystem state."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def prompt_for_name(
        self,
        title: str,
        initial: str,
        action_label: str,
        on_submit: Callable[[str], None],
    ) -> None:
        host = self.host
        window = getattr(host, "window", None)
        if window is None:
            return
        current = getattr(host, "name_dialog", None)
        if current is not None:
            current.close()

        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title(title)
        dialog.set_default_size(360, -1)
        dialog.set_modal(True)
        dialog.set_transient_for(window)
        dialog.set_destroy_with_parent(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        label = Gtk.Label(label="Name")
        label.add_css_class("simple-property")
        label.set_halign(Gtk.Align.START)
        entry = Gtk.Entry()
        entry.add_css_class("simple-dialog-entry")
        entry.set_width_chars(28)
        entry.set_text(initial)
        entry.set_activates_default(True)
        content.append(label)
        content.append(entry)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button(action_label, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if getattr(host, "name_dialog", None) is dialog:
                host.name_dialog = None
            dialog.close()
            if response_id == Gtk.ResponseType.OK:
                on_submit(entry.get_text())

        dialog.connect("response", on_response)
        host.name_dialog = dialog
        dialog.present()
        entry.grab_focus()
        entry.select_region(0, len(initial))

    def confirm_action(
        self,
        title: str,
        message: str,
        confirm_label: str,
        on_confirm: Callable[[], None],
    ) -> None:
        """Ask before a destructive action without making it the default."""

        host = self.host
        window = getattr(host, "window", None)
        if window is None:
            return
        current = getattr(host, "confirmation_dialog", None)
        if current is not None:
            current.close()

        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title(title)
        dialog.set_default_size(420, -1)
        dialog.set_modal(True)
        dialog.set_transient_for(window)
        dialog.set_destroy_with_parent(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        label = Gtk.Label(label=message)
        label.set_wrap(True)
        label.set_halign(Gtk.Align.START)
        content.append(label)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        confirm = dialog.add_button(confirm_label, Gtk.ResponseType.OK)
        confirm.add_css_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if getattr(host, "confirmation_dialog", None) is dialog:
                host.confirmation_dialog = None
            dialog.close()
            if response_id == Gtk.ResponseType.OK:
                on_confirm()

        dialog.connect("response", on_response)
        host.confirmation_dialog = dialog
        dialog.present()

    def choose_conflict(
        self,
        existing_path: str,
        incoming_path: str,
        remaining: int,
        on_choice: Callable[[ConflictPolicy, bool], None],
        *,
        directory_conflict: bool = False,
    ) -> None:
        """Present a non-destructive-by-default file/folder conflict decision."""

        host = self.host
        window = getattr(host, "window", None)
        if window is None:
            on_choice(ConflictPolicy.RENAME, True)
            return
        current = getattr(host, "conflict_dialog", None)
        if current is not None:
            current.close()
        dialog = Gtk.Dialog()
        dialog.add_css_class("simple-dialog")
        dialog.set_title("Folder conflict" if directory_conflict else "File conflict")
        dialog.set_default_size(520, -1)
        dialog.set_modal(True)
        dialog.set_transient_for(window)
        dialog.set_destroy_with_parent(True)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        heading = Gtk.Label(label=(f"{remaining} conflicts remain" if remaining > 1 else "A file with this name already exists"))
        heading.set_halign(Gtk.Align.START)
        heading.set_wrap(True)
        content.append(heading)

        comparison = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for title, path in (("Existing", existing_path), ("Incoming", incoming_path)):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            card.add_css_class("conflict-card")
            title_label = Gtk.Label(label=title)
            title_label.add_css_class("sidebar-section-label")
            title_label.set_halign(Gtk.Align.START)
            card.append(title_label)
            name = Gtk.Label(label=os.path.basename(path) or path)
            name.set_halign(Gtk.Align.START)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            card.append(name)
            try:
                info = os.stat(path)
                details = f"{format_size(info.st_size)} · {format_date(int(info.st_mtime))}"
            except OSError:
                details = "Metadata unavailable"
            meta = Gtk.Label(label=details)
            meta.add_css_class("simple-property")
            meta.set_halign(Gtk.Align.START)
            card.append(meta)
            comparison.append(card)
        content.append(comparison)
        apply_all = Gtk.CheckButton(label="Apply this action to equivalent conflicts")
        content.append(apply_all)

        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Skip", Gtk.ResponseType.OK)
        if directory_conflict:
            dialog.add_button("Merge", Gtk.ResponseType.YES)
            rename = dialog.add_button("Rename Incoming", Gtk.ResponseType.APPLY)
            rename.add_css_class("suggested-action")
        else:
            dialog.add_button("Keep Both", Gtk.ResponseType.YES)
            replace = dialog.add_button("Replace", Gtk.ResponseType.REJECT)
            replace.add_css_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if getattr(host, "conflict_dialog", None) is dialog:
                host.conflict_dialog = None
            dialog.close()
            if directory_conflict:
                policy = {
                    Gtk.ResponseType.OK: ConflictPolicy.SKIP,
                    Gtk.ResponseType.YES: ConflictPolicy.MERGE,
                    Gtk.ResponseType.APPLY: ConflictPolicy.RENAME,
                    Gtk.ResponseType.CANCEL: ConflictPolicy.CANCEL,
                }.get(response_id, ConflictPolicy.CANCEL)
            else:
                policy = {
                    Gtk.ResponseType.OK: ConflictPolicy.SKIP,
                    Gtk.ResponseType.YES: ConflictPolicy.RENAME,
                    Gtk.ResponseType.REJECT: ConflictPolicy.REPLACE,
                    Gtk.ResponseType.CANCEL: ConflictPolicy.CANCEL,
                }.get(response_id, ConflictPolicy.CANCEL)
            on_choice(policy, apply_all.get_active())

        dialog.connect("response", on_response)
        host.conflict_dialog = dialog
        dialog.present()


__all__ = ["DialogController"]

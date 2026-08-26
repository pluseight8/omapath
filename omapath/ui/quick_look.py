"""Quick Look window presentation for the active Omapath window."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from ..preview import PreviewResult


class QuickLookController:
    """Own the preview window while delegating rows and preview service to host."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def show(self, row: Any) -> None:
        host = self.host
        if host.window is None:
            return
        if not bool(host.settings.get("quick_look_enabled", True)):
            host._set_status("Quick Look is disabled in Settings")
            return
        if not self._preview_type_enabled(row):
            host._set_status("This preview type is disabled in Settings")
            return
        if host.preview_window is None:
            preview = Gtk.Window(title="Quick Look", transient_for=host.window, modal=False)
            preview.add_css_class("quick-look")
            parent_width = host.window.get_width() or 720
            parent_height = host.window.get_height() or 520
            preview.set_default_size(
                max(360, min(720, parent_width - 40)),
                max(280, min(520, parent_height - 80)),
            )
            preview.set_resizable(True)
            shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            shell.set_margin_top(14)
            shell.set_margin_bottom(14)
            shell.set_margin_start(14)
            shell.set_margin_end(14)
            title = Gtk.Label()
            title.add_css_class("preview-title")
            title.set_halign(Gtk.Align.START)
            shell.append(title)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            content.set_hexpand(True)
            content.set_vexpand(True)
            shell.append(content)
            hint = Gtk.Label(label="←/→ adjacent · Space/Escape close")
            hint.add_css_class("item-count")
            hint.set_halign(Gtk.Align.END)
            shell.append(hint)
            preview.set_child(shell)
            controller = Gtk.EventControllerKey()
            controller.connect("key-pressed", self.on_key)
            preview.add_controller(controller)
            preview.connect("close-request", self.on_closed)
            host.preview_window = preview
            host.preview_content_box = content
            host.preview_title_label = title

        host.preview_generation += 1
        generation = host.preview_generation
        host.preview_uri = row.location
        if host.preview_title_label is not None:
            host.preview_title_label.set_text(row.display_name)
        self._clear_content()
        path = row.file.get_path()
        if path is None:
            self._show_text("Preview is available for local files only")
        else:
            self._show_text("Loading preview…")
            host.preview_manager.request(
                path,
                lambda result, token=generation, uri=row.location: self.on_result(token, uri, result),
                scheduler=GLib.idle_add,
            )
        host.preview_window.present()

    def _preview_type_enabled(self, row: Any) -> bool:
        content_type = str(getattr(row, "content_type", "") or "").casefold()
        settings = self.host.settings
        if content_type.startswith("image/"):
            return bool(settings.get("image_preview", True))
        if content_type.startswith("text/"):
            return bool(settings.get("text_preview", True))
        if content_type.startswith("audio/") or content_type.startswith("video/"):
            return bool(settings.get("media_preview", True))
        return True

    def _clear_content(self) -> None:
        content = self.host.preview_content_box
        if content is None:
            return
        child = content.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            content.remove(child)
            child = next_child

    def _show_text(self, value: str) -> None:
        content = self.host.preview_content_box
        if content is None:
            return
        label = Gtk.Label(label=value)
        label.set_wrap(True)
        label.set_selectable(True)
        label.set_halign(Gtk.Align.START)
        label.set_valign(Gtk.Align.START)
        content.append(label)

    def on_result(self, generation: int, uri: str, result: PreviewResult) -> None:
        host = self.host
        if generation != host.preview_generation or uri != host.preview_uri or host.preview_window is None:
            return
        self._clear_content()
        if result.error:
            self._show_text(result.error)
            return
        content = host.preview_content_box
        if content is None:
            return
        if result.kind == "text" and isinstance(result.content, str):
            text_view = Gtk.TextView()
            text_view.set_editable(False)
            text_view.set_cursor_visible(False)
            text_view.set_wrap_mode(Gtk.WrapMode.NONE)
            text_view.get_buffer().set_text(result.content)
            text_view.set_hexpand(True)
            text_view.set_vexpand(True)
            content.append(text_view)
            if result.truncated:
                open_full = Gtk.Button(label="Open full file")
                open_full.set_halign(Gtk.Align.START)
                open_full.connect(
                    "clicked",
                    lambda _button, path=result.path: self.host.open_default(
                        Gio.File.new_for_path(path).get_uri()
                    ),
                )
                content.append(open_full)
            return
        if result.kind in {"folder", "archive"} and isinstance(result.content, str):
            self._show_text(result.content)
            return
        if result.kind == "image" and result.content is not None:
            try:
                texture = Gdk.Texture.new_for_pixbuf(result.content)
                picture = Gtk.Picture.new_for_paintable(texture)
                picture.set_can_shrink(True)
                picture.set_hexpand(True)
                picture.set_vexpand(True)
                content.append(picture)
                return
            except Exception:
                pass
        self._show_text(f"{result.kind.title()} preview is not available\n\n{result.subtitle}")

    def on_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval in {Gdk.KEY_Escape, Gdk.KEY_space}:
            self.close()
            return True
        if keyval in {Gdk.KEY_Left, Gdk.KEY_Right}:
            self.adjacent(-1 if keyval == Gdk.KEY_Left else 1)
            return True
        return False

    def adjacent(self, delta: int) -> None:
        file_view = self.host._active_file_view() if hasattr(self.host, "_active_file_view") else self.host.file_view
        if file_view is None:
            return
        rows = [
            row
            for position in range(file_view.store.get_n_items())
            if (row := file_view.store.get_item(position)) is not None
            and hasattr(row, "location")
        ]
        current = next((index for index, row in enumerate(rows) if row.location == self.host.preview_uri), None)
        if current is not None and rows:
            self.show(rows[(current + delta) % len(rows)])

    def on_closed(self, _window: Gtk.Window) -> bool:
        host = self.host
        host.preview_generation += 1
        host.preview_uri = None
        host.preview_manager.cancel()
        host.preview_window = None
        host.preview_content_box = None
        host.preview_title_label = None
        return False

    def close(self) -> None:
        if self.host.preview_window is not None:
            self.host.preview_window.close()


__all__ = ["QuickLookController"]

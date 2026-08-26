# Contributing to Omapath

Omapath is a GTK4/PyGObject file manager for Omarchy. The active production
entry point is `omapath.ui.simple_window.OmapathApplication`; the older
`omapath/ui/window.py` and `omapath/ui/file_view.py` implementations are
quarantined and must not be re-enabled casually.

## Development gate

Run these commands from the project root after every cohesive change:

```text
python3 -m compileall -q omapath tests
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest tests.test_gtk_api -v
python3 scripts/validate_plugin.py
bash -n scripts/omapath-plugin scripts/desktop-entry.sh install.sh update.sh uninstall.sh
```

The GTK display smoke test is skipped when no display is available. In a real
Omarchy/Hyprland session use `python3 scripts/gui_smoke.py --checklist` and
complete `docs/MANUAL_QA.md`; never report a headless run as visual success.

## Architecture rules

- Keep one `Gio.ListStore[FileRow]` and one selection model per active file
  view. Do not create a parallel model for a presentation controller.
- Create list-item children in `setup`, populate them in `bind`, and leave
  recycled children alone in `unbind`/`teardown`.
- Use documented GTK4/PyGObject signatures. For GIcons, call
  `set_from_gicon(gicon)` and set the pixel size separately.
- GTK callbacks must catch worker/provider failures at their boundary.
- File paths are values, never shell fragments. Do not use `shell=True` or
  `os.system` for filenames.
- Make one extraction or behavior change at a time and stop at the first
  failing gate.

The Omarchy integration is a service-only QML launcher entry for the native GTK
application. Keep `manifest.json`, `Service.qml` and the marker-guarded
`scripts/desktop-entry.sh` compatible with Omarchy's plugin validator; do not
move the file view into the shell process or add a bar widget. The project is
installed only through Omarchy's plugin lifecycle, never through the Python or
Arch packaging metadata in a user session.

## Plugins

Plugins are ordinary user Python code loaded from
`~/.local/share/omapath/plugins/<id>/plugin.toml`. They are not sandboxed.
Use `omapath --safe-mode` to disable all third-party plugins for a session.
Keep plugin manifests explicit about the v1 API and requested capabilities.

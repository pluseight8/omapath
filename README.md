# Omapath

> The native file manager for Omarchy.

Omapath is a GTK4/GIO file manager designed for Omarchy, Hyprland and
keyboard-first workflows. Its visual language follows the Omarchy Plugin
Marketplace: quiet dark surfaces, thin rules, rectangular geometry and the
active Omarchy accent, while the content area stays dense and desktop-native.

## Features

- Virtualized `Gtk.GridView` and `Gtk.ListView` models: no widget-per-file
  explosion for large directories.
- GIO asynchronous directory enumeration with generation IDs and cancellation.
- Icons, Compact and Details modes, per-folder view settings, zoom, grouping,
  natural sorting, folders-first ordering, hidden files and debounced local or
  recursive background search, with an opt-in bounded text-content search.
- Tabs with independent history, hidden while only one tab exists, `Ctrl+T`,
  middle-click folder opening, middle-click close and session restore.
- A content-first default layout: Back/Forward/Up, Home-style breadcrumbs,
  icon-first search, grid/list view controls, optional tabs, a bounded Places
  sidebar and the file view; Properties is opened on demand.
- Optional split view (`F3`) with a second location and DnD.
- Folders panel (`F7`), external terminal launch (`F4`), live removable-device
  mounts, separate windows (`Ctrl+N`) and cross-application URI clipboard.
- `Ctrl+K` fuzzy command palette and keyboard-first navigation.
- Editable shortcuts in Settings with conflict confirmation, reset/clear and
  persistence by stable action ID.
- `Space` Quick Look with asynchronous text/image loading and arrow-key
  navigation between neighbouring entries; it is a temporary window, never a
  permanent file inspector.
- Properties with exact/human-readable size, MIME, timestamps, permissions and
  symlink target.
- Central cancellable local operation queue for copy, move, duplicate, rename,
  Trash, permanent delete, restore and create operations, with progress and
  safe undo metadata.
- Compare selected files, batch rename, safe ZIP/tar create/extract, optional
  bounded Git status probing and detailed multi-selection properties.
- Conflict policies: rename, skip or replace; no silent overwrite by default.
- Drag-and-drop between folders, sidebar places, pinned targets, split panel and
  external file managers; modifier-selected copy/move/link actions are
  preserved.
- Controlled Starred and Recent sidebar actions, manually pinned local
  folders, drag-to-star, template creation from `~/Templates`, executable scripts from
  `~/.local/share/nautilus/scripts`, and a GIO Connect to Server dialog for
  SFTP/SMB/WebDAV-style locations.
- System default applications through GIO, Open With, Trash and optional
  terminal integration without `shell=True`.
- Persistent pinned/starred/recent locations, XDG Places, a filesystem device
  entry and live folder/theme monitoring.

## Installation: Omarchy plugin only

Omapath is installed and managed only as an Omarchy shell plugin. System
dependencies are GTK4, GIO/GVfs and PyGObject (`python3-gi`); on Omarchy these
are normally provided by the system. The official plugin lifecycle is:

```bash
omarchy plugin add https://github.com/pluseight8/omapath.git --enable --yes
omarchy plugin update io.github.pluseight8.omapath --yes
omarchy plugin remove io.github.pluseight8.omapath --yes
```

The repository wrappers `./install.sh`, `./update.sh` and `./uninstall.sh`
invoke those same official commands. They do not install a system package,
Python package, desktop entry or separate launcher. The only Omarchy entry
point is the `io.github.pluseight8.omapath` bar widget, which starts the GTK4
window from the managed plugin checkout.

For source-level development only, `./run.sh` can launch the current checkout;
that is not an installation path.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+L` | Editable location |
| `Ctrl+K` | Command Palette |
| `Ctrl+F` | Search |
| `Ctrl+T` / `Ctrl+W` | New/close tab |
| `Ctrl+N` / `Ctrl+Q` | New window / quit |
| `Ctrl+Shift+T` | Reopen closed tab |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Next/previous tab |
| `Ctrl+C`, `Ctrl+X`, `Ctrl+V` | Copy/cut/paste |
| `Ctrl+Z` | Undo the latest safe operation |
| `Ctrl+A` | Select all |
| `Ctrl+Shift+A` | Invert selection |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Icons / Compact / Details |
| `Ctrl+Plus` / `Ctrl+Minus` / `Ctrl+0` | Zoom in / out / reset |
| `F2` | Rename |
| `F3` | Toggle split view |
| `F4` / `F7` | Terminal / Folders panel |
| `F5` | Refresh |
| `Ctrl+H` | Toggle hidden files |
| `Space` | Temporary Quick Look |
| `Alt+Enter` | Properties |
| `Enter` | Open |
| Middle-click folder | Open folder in a new tab |
| `Alt+Left/Right/Up` | Back/forward/parent |
| `Delete` / `Shift+Delete` | Trash/permanent delete |
| `Esc` | Close temporary UI or clear selection |

One click selects an item; double-click or `Enter` opens it. Properties are
available from the context menu, `Alt+Enter` or `Ctrl+K`; Quick Look is opened
with `Space` and closes with `Space`/`Escape`. Text fields keep their normal editing semantics; global shortcuts are ignored
while a text entry is actively editing unless they are navigation commands.

## Omarchy integration

Omapath reads the active palette and font from Omarchy's current-theme state,
then watches those files through GIO. Changing the theme updates the app
without a restart. If the state is unavailable or malformed, a neutral
accessible fallback is used; no particular theme is assumed. The setting
“Follow Omarchy theme” can disable live reload.

### Omarchy shell plugin

The repository also contains the official Omarchy shell-plugin wrapper. The
plugin is intentionally a small bar launcher: Omapath remains a native GTK4
application with its own window, while `omarchy-shell` loads only the QML bar
widget. It does not replace or embed the working `SimpleFileView`.

Complete install, update and removal from the public repository:

```bash
./install.sh
./update.sh
./uninstall.sh
```

The equivalent official Omarchy lifecycle commands are:

```bash
omarchy plugin add https://github.com/pluseight8/omapath.git --enable --yes
omarchy plugin update io.github.pluseight8.omapath --yes
omarchy plugin remove io.github.pluseight8.omapath --yes
```

The convenience scripts call only those commands. No repository install hook,
desktop file or system-wide file is created. Omarchy's official installer never
executes repository install hooks; the shell plugin is therefore fully
described by `manifest.json` and `BarWidget.qml`.

Before enabling a local checkout, validate it with:

```bash
omarchy plugin validate .
python3 scripts/validate_plugin.py
```

The bar entry is installed disabled unless `--enable` is supplied; the helper
enables it in the default left bar section. Plugin code runs unsandboxed inside
the Omarchy shell, so review the repository before enabling it.

Settings are stored atomically at `~/.config/omapath/settings.json`. The first
run migrates compatible values from the previous
`~/.config/omarchy-files/settings.json` location without deleting the old file.
Corrupt JSON falls back to validated defaults.

`OMAPATH_DEBUG=1 ./run.sh` enables bounded startup, cancellation and worker
diagnostics for operations, preview and search. Normal startup remains quiet.
Use `omapath --safe-mode` to disable all third-party plugins for one session.
The manual verification checklist is in [`docs/MANUAL_QA.md`](docs/MANUAL_QA.md);
`python3 scripts/gui_smoke.py --checklist` assists the same real-display pass.

Optional plugins are ordinary user Python code loaded from
`~/.local/share/omapath/plugins/<id>/plugin.toml`. They are not sandboxed;
each plugin is isolated behind a load/provider exception boundary and can
register namespaced actions, context-menu items and provider hooks.

## Preview and network behavior

Text-like files and common images are previewed without reading the whole text
file into memory; preview size is bounded in settings. PDF/audio/video entries
show a useful fallback unless the local GTK/GStreamer stack is extended with a
safe preview backend. GIO/GVfs URIs such as `sftp://` and `smb://` are accepted
for browsing/opening when their system backend is installed. The current
operation queue, DnD destinations and sidebar pinning use local paths; remote
locations expose no misleading create/paste/delete controls. Unsupported
backends report an error instead of pretending to support the protocol.

Archive actions use bounded Python stdlib ZIP/tar backends with traversal and
special-file checks. Omapath does not depend on KDE/KIO, Qt, Electron or a web
runtime.

## Architecture

```text
omapath/
├── app.py              GTK application entry point
├── ui/simple_window.py active GTK4 composition and interaction layer
├── ui/window.py        legacy, not imported by the production entry point
├── ui/file_view.py     legacy, not imported by the production entry point
├── ui/palette.py       fuzzy command palette
├── model.py            file/tab data contracts
├── filesystem.py       sorting, filtering, metadata and safety checks
├── navigation.py       pure navigation history
├── operations.py       cancellable operation queue and undo metadata
├── archive.py          safe ZIP/tar create and extract helpers
├── nautilus_features.py bounded Starred/Recent/template/script discovery
├── git_status.py       optional bounded Git decoration lookup
├── preview.py          bounded asynchronous preview service
├── search.py           cancellable recursive search
├── settings.py         schema, migration and atomic persistence
├── actions.py          command registry and shortcut normalization
├── plugins.py          versioned optional plugin API and safe mode
├── theme.py            active Omarchy theme/font integration
├── compat.py           narrow GTK4/PyGObject compatibility helpers
└── diagnostics.py      opt-in OMAPATH_DEBUG logging
```

The GTK adapter is deliberately isolated from the testable filesystem and
operation modules. All worker callbacks are marshalled back to the GTK main
context, and shutdown cancels monitors, searches, preview work and operations.

## Development and tests

Run the headless suite from this directory:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q omapath tests
python3 -m unittest tests.test_gtk_api -v
python3 benchmark.py --entries 1000
```

`tests/test_gtk_api.py` checks the GTK4/PyGObject surface without creating
widgets; its display-dependent `GtkRuntimeSmokeTests` run only when a real
graphical display is available and otherwise skip explicitly. `OMAPATH_DEBUG=1 ./run.sh`
logs Python, PyGObject, GTK and GLib runtime versions before startup.

The tests use temporary directories only and cover Unicode/special filenames,
natural sorting, migration, symlinks, transfer safety, failed Replace moves,
conflicts, history, cancellation, stale preview/search callbacks and the
operation manager. Run the benchmark at each supported size when collecting a
performance report:

```bash
for size in 1000 10000 50000; do PYTHONPATH=. python3 benchmark.py --entries "$size"; done
```

It reports model/sort/filter timings for synthetic 1k/10k/50k entry sets; it
does not invent FPS or compositor numbers.

## Verification limits

The GTK runtime requires a graphical display/compositor. In a headless
environment syntax, imports, CSS parsing, model operations and filesystem
integration can be verified, but visual interaction and real compositor FPS
cannot honestly be claimed as tested.

GUI interaction was not visually verified in this environment. See the
[Dolphin parity matrix](docs/DOLPHIN_PARITY.md) for the exact implemented and
partial scope.

Remaining parity work is backend-specific: KDE custom services/KIO protocols
and advanced archive backends (Ark/7z/RAR) are optional and not required for
normal browsing. Standard GTK/GIO file-list DnD and
`text/uri-list` clipboard interop are implemented; destructive file operations
remain local-only unless a GIO backend is explicitly added.

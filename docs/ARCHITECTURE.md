# Omapath active architecture

This document describes the production GTK4 path, not the historical
implementation.

## Runtime ownership

`omapath/app.py` imports `OmapathApplication` from
`omapath/ui/simple_window.py`. The legacy `ui/window.py` and `ui/file_view.py`
modules remain quarantined and are not imported by the production entry point.

The active filesystem pipeline has one model identity from enumeration to
paint:

```text
GIO async enumeration
  -> DirectoryEntry snapshots
  -> hidden/filter/sort/group policy
  -> one Gio.ListStore[FileRow]
  -> one Gtk.MultiSelection
  -> active Gtk.GridView / Gtk.ListView
  -> Gtk.SignalListItemFactory setup/bind
  -> per-list-item widget
```

`SimpleFileView` owns this chain. Group headers are presentation `FileRow`s in
the same store; they are non-selectable/non-activatable and never become
filesystem entries. Collapse state is held by the active tab or split pane.

## Active controller boundaries

These modules own widget construction and lifecycle while delegating state and
filesystem work to the host:

| Boundary | Responsibility |
|---|---|
| `ui/context_menu.py` | Capability-aware file/background/Git menu and reusable popover |
| `ui/dialogs.py` | Name prompts, destructive confirmation and conflict queue dialog |
| `ui/sidebar.py` | Places, Starred, one virtual Recent row, tags, devices and Trash rendering/DnD |
| `ui/folders.py` | Lazy folders-only `TreeListModel`, activation and DnD |
| `ui/tab_bar.py` | Tab activation, close, reorder, delayed file-drop activation and context menu |
| `ui/quick_look.py` | Preview window, adjacent navigation and stale-result guard |
| `ui/operation_popover.py` | Concurrent progress, cancellation, failure details and Undo |
| `ui/panel_controls.py` | One discoverable Panels control and live-state popover |
| `ui/topbar.py` | Navigation, breadcrumbs, view/sort/group/zoom controls |
| `ui/search_controls.py` | Filter/search controls and host signal wiring |
| `ui/terminal_panel.py` | Optional VTE surface; absence means no embedded panel is built |
| `navigation.py` / `tabs.py` / `split.py` | Toolkit-neutral history, tab and pane state |
| `actions.py` | Stable commands, shortcut validation and predicates |
| `capabilities.py` | Conservative local/GVfs capability snapshots |
| `gio_operations.py` | Provider-backed GIO mutations |
| `operations.py` | Local worker operations, conflict policy, progress and undo/redo |
| `plugins.py` | Versioned optional plugin loading and safe mode |

`OmapathApplication` is still the coordination layer (about 6.6k source lines
in this pass). That is known debt, not a reason to move methods mechanically:
the active model contract and callback guards are more important than a lower
line count. Further extraction must move responsibility, preserve model
identity, and pass the full regression gate.

## Lifecycle and safety rules

- Worker threads never mutate GTK; callbacks are marshalled to the GTK main
  context.
- Enumeration, monitor, search, thumbnail and Quick Look callbacks verify a
  generation/token/URI before changing visible state.
- Recycled list-item children are created in `setup` and populated in `bind`.
  `unbind`/`teardown` unregister state but do not manually unparent children or
  call `set_child(None)`.
- GTK4/PyGObject calls use GTK4 signatures; icon size is configured separately
  from `Gtk.Image.set_from_gicon`.
- Popovers have a stable parent and are reused; no manual `.unparent()` is
  required in the active controllers.
- The active shell starts with one resizable sidebar pane and a direct file
  area. Folders, Information and the VTE-only terminal pane are attached
  lazily when requested; hidden optional children are removed from their pane
  rather than left as reserved content. `SimpleFileView` attaches exactly one
  active virtualized view directly to its owning `Gtk.ScrolledWindow`.
- `PanelControlsController` is the single topbar surface for Sidebar, Folders,
  Information and Terminal. It exposes one live-state Panels popover at every
  responsive mode. Controls execute ActionRegistry commands and reflect
  actual responsive presentation, including drawers and VTE-unavailable
  external-terminal fallback.
- Recent history is bounded in state but rendered as one `recent:///` virtual
  location, never as one sidebar widget per navigation.
- Local file operations use path APIs/argv values. Remote mutations use GIO.
  No file-operation path uses `shell=True` or `os.system`.
- Capability checks are conservative. A provider feature that cannot be
  established is not advertised as available.

## Verification commands

```text
python3 -m compileall -q omapath tests
python3 -m unittest discover -s tests -v
```

Display-dependent runtime tests are explicit smoke tests: they measure GTK
geometry when a display is available and skip when it is not. They do not
prove Wayland focus, cursor feedback, VTE painting or real pointer activation.
Those checks must still be repeated on the user's Omarchy/Hyprland session.

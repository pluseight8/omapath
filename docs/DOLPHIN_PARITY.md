# Omapath interactive parity

This matrix describes the active GTK4 runtime. `PARTIAL` is deliberate: it
means the active UI has a safe, usable subset but does not claim the complete
Dolphin surface. `PLATFORM-SPECIFIC` means that the result depends on GTK,
GVfs, Omarchy, or an optional program installed on the host.

The production entry point is
`omapath.ui.simple_window.OmapathApplication`. The legacy
`omapath/ui/window.py` and `omapath/ui/file_view.py` modules are not imported
by it.

## Matrix

| Area | Status | Active behavior and boundary |
|---|---|---|
| Action registry | IMPLEMENTED | Namespaced actions, shortcuts, predicates, command palette and conflict-safe shortcut editing. |
| Icons / Compact / Details | IMPLEMENTED | Three virtualized views share one `Gio.ListStore` and one `Gtk.MultiSelection`; Details shows name, size, type and modified time. |
| Filtering | IMPLEMENTED | Plain, glob and regex filtering with visible invalid-regex feedback; filtering does not re-enumerate the filesystem. |
| Grouping | IMPLEMENTED | Type, date, size and name headers are real rows, show counts, collapse per tab/pane, and update after refresh. Grouping remains independent from sorting. |
| Selection / rename | PARTIAL | Multi-selection, select-all, invert, marker, tooltip metadata, inline local rename, F2 rename and batch rename work. Rubber-band selection and all Dolphin rename tokens are not claimed. |
| Operations / conflicts / undo | IMPLEMENTED (bounded) | Worker-backed copy/move/link/create/rename/archive/trash operations, sequential conflict queue, file Replace/Keep Both/Skip/Cancel, directory Merge/Rename/Skip/Cancel, progress, cancellation and real undo/redo metadata. |
| Operation center | IMPLEMENTED (bounded) | Active operations expose current item, item/byte progress, cancellation and expandable failure details. Reversible completion stays available for Undo; irreversible rows dismiss automatically. |
| Trash | PARTIAL | Browse, restore, Move to Trash, permanent delete and Empty Trash are wired through GIO/operations. Automatic cleanup and full Trash metadata editing are not included. |
| Drag and drop | IMPLEMENTED (bounded) | File rows in Icons/Compact/Details and Search/Recent/Starred results publish URI data. Folder rows, empty backgrounds, breadcrumbs, Places, pinned rows, devices, folder tree, tabs, split pane and Trash accept drops where capabilities permit. Same-device default Move, cross-device/provider default Copy, Ctrl Copy, Shift Move and Alt Link for local files. Archive import is not advertised. |
| Split view | PARTIAL | Each pane has independent location, history, selection, view, zoom, sort, hidden policy, groups, filter, search, monitor, generation and scroll state. Pane tabs, drag-out windows and every Dolphin pane preference are not implemented. |
| Tabs | PARTIAL | Activate, middle-click close, duplicate, reorder by drag, delayed file-drop activation, Move Left/Right, Close/Close Others/Left/Right, session restore and scroll/selection state work. Move to New Window/drag-out is intentionally omitted because it is not safe to claim without a real Wayland window test. |
| Per-folder view properties | IMPLEMENTED | Canonical folder/default/subfolder overlays persist view, sort, hidden, folders-first, grouping, previews and zoom. |
| Places / bookmarks | PARTIAL | Pinned, Starred and Recent locations persist, render and accept capability-checked drops; pinned reorder persists and middle/right click works. Rename/custom icons/hide and full XDG bookmark import are not claimed. |
| Devices | PARTIAL / PLATFORM-SPECIFIC | GIO VolumeMonitor hotplug, mount, navigate, unmount, eject and drive stop are present. Encrypted-volume prompts and complete device properties depend on GVfs/platform support. |
| Search | PARTIAL | Cancellable local recursive search, hidden policy, case sensitivity, bounded content search, `tag:` and `rating:>=N` predicates work. Indexed/remote search and a full advanced query form are not claimed. |
| Quick Look | PARTIAL | Space opens a generation-guarded window with adjacent navigation, bounded text/code, image, folder and archive summaries. PDF/media playback, Markdown rendering, image zoom/EXIF controls and full keyboard parity need optional backends. |
| Thumbnails / previews | PARTIAL | Bounded URI+mtime image thumbnail cache and GdkPixbuf decoding are used for visible virtualized rows. PDF/video/audio cover-art decoders are optional and fall back to an icon/summary without an error. |
| Folders panel | PARTIAL | F7 opens a lazy GTK4 `TreeListModel`; folder activation and drop targets work. Full tree context menus, middle-click tabs, persisted expansion and hover-expand are not claimed. |
| Information panel / Properties | PARTIAL | F11 shows selection count, size, MIME, path, timestamps, permissions, symlink target and editable local tags/rating/comment. Owner/group, mount/free-space and recursive folder-size UI are not claimed. |
| Embedded terminal | PLATFORM-SPECIFIC / PARTIAL | F4 uses a resizable VTE pane when installed; without VTE it opens the configured external terminal and creates no embedded fallback panel or reserved height. |
| Archives | PARTIAL | Safe ZIP/tar create/extract and bounded Quick Look summaries work through the operation manager. Writable archive-as-folder, Ark/7z/RAR/password and archive drop-import are not included. |
| Open With / MIME | IMPLEMENTED | GIO default application launch and a GTK4 Open With chooser use enumerated MIME types. |
| Services | PARTIAL | Declarative user services and safe argv execution work; KDE service-menu compatibility is not claimed. |
| Custom shortcuts / palette | IMPLEMENTED | Capture, Clear, Default, Reset All, explicit conflict replacement, fuzzy search, Enter and Escape use one ActionRegistry. |
| Git / VCS | PARTIAL | One bounded Git status snapshot per directory decorates rows; context actions and a visible status/diff/log/fetch/pull/push/commit surface use argv-only subprocesses. Async jobs, other VCS backends and a full staging UI are not claimed. |
| Tags / rating / comment | IMPLEMENTED (bounded) | Information panel and context dialog edit local metadata; sidebar tag counts and `tag:` / `rating:>=N` search are active. Metadata remains Omapath-side and is not written into files. |
| Remote / GVfs | PLATFORM-SPECIFIC / PARTIAL | URI navigation/opening plus capability-driven Gio copy/move/create/rename/trash/delete/drop are active when the provider supports them. Remote mutations have no fake local undo; provider-specific dialogs and KIO parity are not claimed. |
| Compare | PLATFORM-SPECIFIC / PARTIAL | Exactly-two validation and safe external compare launch are available when Meld/Kompare/Diffoscope exists; no integrated diff editor is included. |
| Settings | PARTIAL | Resizable categorized GTK4 settings, atomic validated persistence, shortcut editing, conflict behavior, theme, view/sidebar/panel/refresh and preview controls are live. Unsupported/deferred options remain persisted for migration compatibility but are not exposed as dead controls. |
| Startup / session | PARTIAL | Validated startup location, tabs, history, selection, scroll, view state, split position, window size and maximized state persist. Crash recovery/workspace restore is not claimed. |
| Plugins | PARTIAL | Versioned manifests, actions/context/provider registration, failure isolation and safe mode work. Plugins are user Python code, not a sandbox. |
| Performance / hardening | PARTIAL | Async chunked enumeration, virtualized rows, generation/cancellation guards, bounded previews/search/archive work, safe argv launches and operation error handling are active. FPS/RSS profiling still requires a real Omarchy session. |

## Active model contract

The filesystem path remains a single-source pipeline:

```text
GIO async enumeration
  -> DirectoryEntry snapshots
  -> hidden/filter/sort/group presentation rows
  -> one Gio.ListStore[FileRow]
  -> one Gtk.MultiSelection
  -> active Gtk.GridView or Gtk.ListView
  -> Gtk.SignalListItemFactory setup/bind
  -> per-list-item widget
```

Navigation, monitor, preview, thumbnail and worker callbacks use generation or
URI guards. GTK is only touched from the GTK main context. List-item children
are created in `setup`, populated in `bind`, and are not manually unparented
or replaced with `set_child(None)` during `unbind`.

Local mutations use `omapath/operations.py`; provider mutations use GIO. File
names are values passed to APIs/argv, never shell fragments.

## Verification boundary

Headless checks cover imports, GTK API signatures, model identity, filesystem
safety, operation conflicts/undo, GIO local smoke operations, stale callbacks,
capabilities and bounded preview/search behavior. This environment has no
graphical display, so pointer rendering, drag cursors, focus, Wayland window
activation, VTE painting and actual GUI clicks still require a real Omarchy
session.

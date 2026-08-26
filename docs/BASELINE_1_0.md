# Omapath 1.0 Baseline

Baseline captured before the Phase 1 architecture extraction requested in the
Omapath 1.0 plan. No production source was changed while collecting these
measurements.

Date: 2026-08-26
Active project: `/home/pluseight/Documents/Codex/2026-08-24/cl/outputs/omapath`

## Regression gate

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_*.py'` | **126 tests OK, 4 skipped** |
| `python3 -m compileall -q omapath tests` | **passed** |
| Production entry point | `omapath.ui.simple_window.OmapathApplication` |
| Legacy imports from active production modules | No active import found; `ui/window.py` only imports its own legacy `file_view.py` |
| GTK runtime smoke | Skipped because no graphical display is available |
| `OMAPATH_DEBUG=1 ./run.sh` | Correctly reports missing display and exits `2` |

The four skipped tests are display/environment-dependent GTK tests. The suite
also emits two sandbox-specific GIO proxy connection messages in the device
test; they do not fail the test run.

## Benchmark baseline

Values are from the existing `benchmark.py` synthetic model benchmark on the
current environment. The script measures model creation, natural sorting and
filtering; it does not measure compositor FPS or GTK realization.

| Synthetic entries | Model creation | Natural sort | Filter |
|---:|---:|---:|---:|
| 1,000 | 0.002844 s | 0.002197 s | 0.000059 s |
| 10,000 | 0.029526 s | 0.010129 s | 0.000540 s |
| 50,000 | 0.142703 s | 0.057304 s | 0.002741 s |

The current benchmark CLI accepts only `1000`, `10000` and `50000`; a 100,000
entry measurement is therefore not yet part of the baseline.

## Size and responsibility baseline

| Unit | Measurement |
|---|---:|
| `omapath/ui/simple_window.py` | 4,822 LOC |
| `SimpleFileView` | 685 LOC / 36 methods |
| `OmapathApplication` | 3,556 LOC / 206 methods |
| Active `omapath` Python modules | 16,551 LOC total |
| Legacy `omapath/ui/window.py` | 6,075 LOC |
| Legacy `omapath/ui/file_view.py` | 647 LOC |

The large class owns lifecycle, navigation, tabs, split view, sidebar,
context menus, settings application, search, Quick Look, operations, Trash,
DnD, devices, archive actions, services, VCS, and presentation state. This is
the primary architecture debt for Phase 1.

## Action inventory

The active Action Registry contains 55 namespaced actions. 33 have default
shortcuts and 22 are currently unassigned. Shortcut conflict detection returns
an empty conflict set at baseline.

### Assigned shortcuts

| Shortcut | Action |
|---|---|
| `Alt+Left` | Back |
| `Alt+Right` | Forward |
| `Alt+Up` | Up |
| `Ctrl+L` | Editable Location |
| `Ctrl+K` | Command Palette |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | Copy / Cut / Paste |
| `Ctrl+A` | Select All |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / Redo |
| `Ctrl+F` | Search |
| `Ctrl+I` | Filter Bar |
| `Ctrl+H` | Show Hidden Files |
| `Ctrl+T` / `Ctrl+W` / `Ctrl+Shift+T` | New / Close / Reopen Tab |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Next / Previous Tab |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Icons / Compact / Details |
| `Ctrl+Plus` / `Ctrl+Minus` / `Ctrl+0` | Zoom In / Out / Reset |
| `Delete` | Move to Trash |
| `Enter` | Open |
| `F2` | Rename |
| `F3` | Split View |
| `F4` | External Terminal |
| `F5` | Refresh |
| `F7` | Folders Panel |
| `F11` | Information Panel |
| `Space` | Quick Look |
| `Alt+Enter` | Properties |

The current settings UI displays shortcut values but does not yet provide an
editable shortcut editor.

## Settings inventory

There are 68 validated settings keys in `DEFAULT_SETTINGS`. The Settings
window currently exposes 34 of them. The remaining keys are persisted or used
by specific subsystems but do not all have a dedicated live settings control.
This gap is tracked for the settings audit rather than being hidden.

### Settings without a dedicated Settings UI control

`schema_version`, `zoom_level`, `icon_zoom`, `icon_size`, `thumbnail_size`,
`show_thumbnails`, `show_folder_sizes`, `show_status_bar`, `compact_mode`,
`remember_location`, `remember_window_size`, `window_width`, `window_height`,
`window_maximized`, `split_position`, `sidebar_width`, `quick_look_enabled`,
`preview_max_bytes`, `thumbnail_cache_limit`, `sort_mode`, `sort_reverse`,
`search_recursive`, `search_contents`, `search_case_sensitive`,
`search_show_hidden`, `conflict_behavior`, `terminal_shell`,
`folder_view_settings`, `pinned_places`, `starred_places`,
`recent_locations`, `last_location`, `session_tabs`, `active_tab`.

## Active runtime modules

The production import chain is:

```text
omapath/app.py
  -> omapath.ui.simple_window
      -> actions, archive, devices, directory_monitor, dnd,
         filtering, folder_settings, grouping, operations, places,
         preview, search, services, settings, tabs, terminal, theme,
         view_modes, vcs and GTK UI helpers
```

The stable file pipeline remains:

```text
GIO enumeration
  -> DirectoryEntry
  -> filtering/sorting/grouping
  -> Gio.ListStore[FileRow]
  -> Gtk.MultiSelection
  -> Gtk.GridView / Gtk.ListView
  -> Gtk.SignalListItemFactory
  -> item widget
```

## Current parity status

The detailed feature matrix is maintained in
[`docs/DOLPHIN_PARITY.md`](DOLPHIN_PARITY.md). At baseline, the major partial
areas are:

- god-object architecture and mixed UI/controller responsibilities;
- visible group headers and collapse/expand;
- inline rename;
- interactive conflict resolution and operation UI;
- complete DnD targets and tab-hover activation;
- fully independent split-pane state;
- optional PDF/media previews and embedded terminal;
- VCS decorations/workflows;
- tags/rating/comment UI;
- remote mutations with per-action capability detection;
- editable shortcuts;
- complete device/Places/Trash properties and settings live application;
- plugin API and release packaging artifacts.

## Baseline conclusion

The working GTK4 model/view core is suitable for incremental extraction. The
next change must extract one cohesive responsibility behind a narrow boundary,
run the full regression gate, and preserve the active production entry point.
No legacy UI deletion or big-bang rewrite is justified before that sequence is
complete.

## Post-baseline verification (2026-08-26)

The measurements above remain the historical pre-extraction baseline. After
the controller/state/shortcut/plugin batches, the active runtime reports:

| Check | Current result |
|---|---|
| Full unittest discovery | **141 tests OK, 4 skipped** |
| `compileall` | **passed** |
| `tests.test_gtk_api` | **5 tests OK, 1 display smoke skipped** |
| Active `simple_window.py` | 4,939 LOC; `SimpleFileView` 1,021 LOC / 59 methods; `OmapathApplication` 3,245 LOC / 234 methods |
| Registered base actions | 58; 36 default shortcuts; no conflicts |
| Validated settings keys | 69, including persisted `shortcuts` overrides |

Current synthetic benchmark values (same environment and script):

| Synthetic entries | Model creation | Natural sort | Filter |
|---:|---:|---:|---:|
| 1,000 | 0.003118 s | 0.003264 s | 0.000086 s |
| 10,000 | 0.030898 s | 0.010576 s | 0.000613 s |
| 50,000 | 0.142778 s | 0.057041 s | 0.002719 s |

No graphical display was available for this verification pass; these results
do not replace real Omarchy/Hyprland GUI smoke testing.

The latest interaction batch keeps the same single model pipeline and adds
realized-item marker/tooltip/inline-rename hooks, breadcrumb and sidebar
middle/right-click actions, destructive confirmations, natural-sort and
startup/window/split settings application. Full GTK realization still needs
the user's graphical Omarchy session.

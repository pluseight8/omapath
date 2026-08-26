# Omapath GTK4 layout and GridView geometry audit

Status: implemented in the active `simple_window.py` path. This document is
the geometry contract for the current shell; it is not a feature roadmap.

## Scope and runtime path

The production entry point is:

```text
run.sh
  -> omapath/app.py
  -> omapath.ui.simple_window.OmapathApplication
```

`ui/window.py` and `ui/file_view.py` are legacy modules and are not part of
this audit. The audit deliberately does not change filesystem enumeration,
`FileRow`, `Gio.ListStore`, `Gtk.MultiSelection`, navigation semantics,
operations, theme monitoring, or directory monitoring.

The relevant model identity remains:

```text
enumeration result
  -> DirectoryEntry snapshots
  -> presentation rows
  -> one Gio.ListStore[FileRow]
  -> one Gtk.MultiSelection
  -> active Gtk.GridView or Gtk.ListView
  -> SignalListItemFactory setup/bind
  -> item widget
```

## Minimal geometry probe

`scripts/grid_geometry_probe.py` is intentionally independent of Omapath. It
constructs only:

```text
Gtk.ApplicationWindow
  -> Gtk.ScrolledWindow
    -> Gtk.GridView
      -> Gtk.MultiSelection
        -> Gio.ListStore[ProbeRow]
```

The probe uses the same important constraints as the production icon view:
one-to-twenty columns, a bounded icon cell, a natural-height item, two-line
text, and a direct `ScrolledWindow -> GridView` parent relationship. It tests
10, 20, 100, 1000 and 4000 rows and reports:

- allocated window, scroller and view size;
- vertical adjustment lower/upper/page/value;
- realized/bound list-item count;
- estimated columns and rows;
- sample item allocation sizes;
- the unconstrained `GridView.measure()` result for comparison.

The display-dependent regression wrapper is
`tests/test_grid_geometry_probe.py`. It skips when no display exists and does
not claim to prove Wayland pointer input.

On the GTK4/Wayland display used for this audit, the final probe produced:

| rows | allocated width | columns | estimated rows | adjustment upper | page | realized items | sample item |
|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 937 | 7 | 2 | 1,022 | 1,022 | 10 | 103 × 98 |
| 20 | 937 | 7 | 3 | 1,022 | 1,022 | 20 | 103 × 98 |
| 100 | 937 | 7 | 15 | 1,920 | 1,022 | 100 | 103 × 98 |
| 1,000 | 937 | 7 | 143 | 18,304 | 1,022 | 641 | 103 × 98 |
| 4,000 | 937 | 7 | 572 | 73,216 | 1,022 | 641 | 103 × 98 |

The exact window width is compositor-dependent. The important invariants are
that the extent follows rows and columns, cells remain around 100 px high,
and 4,000 rows do not all remain realized. The probe still reports a large
one-column natural height from `GridView.measure()` (512,000 for 4,000 rows);
that is expected GTK measurement data. The direct scroller uses the allocated
width-aware grid layout for its actual adjustment upper value.

## Root cause found in the old FileView chain

Before this pass the active file area was effectively:

```text
Gtk.ScrolledWindow
  -> Gtk.Overlay (content overlay)
    -> Gtk.Stack (icons/compact/details)
      -> Gtk.GridView
```

The active item was also wrapped in an overlay/box whose default vertical
fill behavior allowed the recycled `GtkListItemWidget` allocation to feed
the viewport size back into its own natural measurement. On a large directory
the chain was observed at runtime as:

```text
GridView allocation: approximately 643,364 px high
vertical adjustment upper: approximately 643,364
item allocation: approximately 21,445 px high
```

This was not an enumeration or model identity problem. The model contained
the correct rows and virtualization was binding items, but the intermediate
`Stack`/`Overlay` asked GTK for an unconstrained natural height. GTK chose its
one-column natural measurement, then the fill-aligned item root amplified the
result into a giant per-row extent. The visible result was a slow open,
excessive empty space and a broken-looking interface.

## Current parent and measurement chain

At launch, the active shell is:

```text
Gtk.ApplicationWindow
  -> root Gtk.Box
    -> topbar
    -> optional tab bar (only when the existing tab state needs it)
    -> workspace overlay (drawer/backdrop owner only)
      -> workspace Gtk.Paned, horizontal
        -> left Gtk.Paned
          -> sidebar Gtk.ScrolledWindow
          -> optional folders tree (not attached by default)
        -> file_area Gtk.Box
          -> SimpleFileView Gtk.ScrolledWindow
            -> active Gtk.GridView (Icons)
              or active Gtk.GridView (Compact)
              or active Gtk.ListView (Details)
    -> status bar
```

The file view has no `Gtk.Stack` or `Gtk.Overlay` between its owning
`Gtk.ScrolledWindow` and the active virtualized view. The item-level overlay
still exists for the selection marker and inline rename editor, but it is
natural-height and `valign=START`; it is not a file-area measurement wrapper.

The remaining `Gtk.Stack` in the active application is the topbar location
field/search switch. It is unrelated to FileView geometry.

Responsive composition has three states only: `WIDE`, `MEDIUM` and `NARROW`.
The former fourth responsive `COMPACT` state was removed because it repeated
the narrow drawer behavior and caused an unnecessary extra breakpoint. This
does not remove the existing user-selectable `ViewMode.COMPACT` file
presentation; it removes only the window-width state.

Optional panels are lazy:

```text
default: workspace end -> file_area -> SimpleFileView
F11:     workspace end -> content Gtk.Paned(file_area | info_panel)
F4:      workspace end -> terminal Gtk.Paned(base | terminal_panel)
```

The optional information/terminal child is removed from its pane when hidden;
the default launch path does not construct those pane wrappers. Existing F3
split behavior is also not part of the default geometry chain.

## Geometry constraint inventory

The following is the complete set of constraints that directly affect the
active file-area geometry.

### `SimpleFileView` and model-backed views

| Location | Constraint | Decision and reason |
|---|---|---|
| `ui/simple_window.py:385` | ScrolledWindow vertical policy `AUTOMATIC`, horizontal `NEVER` | KEEP: one vertical extent, no horizontal file-area drift |
| `ui/simple_window.py:386-387` | ScrolledWindow `hexpand/vexpand=True` | KEEP: owns all available file-area space |
| `ui/simple_window.py:398-405` | Icon GridView min columns 1, max columns 20, expand both axes | KEEP: width-aware virtualization and narrow-window fallback |
| `ui/simple_window.py:422-429` | Compact GridView min columns 1, max columns 12, expand both axes | KEEP: same model chain, bounded compact layout |
| `ui/simple_window.py:437-441` | Details ListView expand both axes | KEEP: direct virtualized list |
| `ui/simple_window.py:468` | Active view is the direct ScrolledWindow child | KEEP: removes unconstrained Stack/Overlay measurement feedback |
| `ui/simple_window.py:638-646` | View-mode change replaces the ScrolledWindow child | KEEP: one active view, no second model and no hidden view in measurement |
| `ui/simple_window.py:1519-1532` | One `ListStore.splice()` for a replacement; optional adjustment reset | KEEP: bounded notifications, no row-by-row churn, reset stale scroll on navigation |
| `ui/simple_window.py:1561-1571` | Empty label replaces active view directly | KEEP: empty state cannot reserve a second file-area layout |
| `ui/simple_window.py:1647-1693` | Delayed post-layout log of width, columns, model count, bound count and adjustment | KEEP: diagnostics observe final GTK geometry, not pre-layout guesses |

### Icon item and recycled widget geometry

| Location | Constraint | Decision and reason |
|---|---|---|
| `ui/simple_window.py:1024-1034` | Vertical box, spacing 8, `halign=CENTER`, `valign=START`, `vexpand=False`, width `cell_width(icon_size)`, height `-1`, margins 8/8/4/4 | KEEP: bounded natural cell, no viewport-height feedback |
| `ui/simple_window.py:1036-1048` | Icon pixel size follows zoom; label wraps to two lines with ellipsis and bounded width chars | KEEP: stable natural height for Unicode and long names |
| `ui/simple_window.py:780-792` | Item overlay is centered for icons, fill-aligned only for compact/details, `valign=START`, `vexpand=False` | KEEP: marker/editor remains available without making icon rows fill the viewport |
| `ui/simple_window.py:794-823` | Marker/editor overlays have explicit small margins and editor width chars | KEEP: editor affordance only; does not set file-area height |
| `ui/simple_window.py:1210-1224` | Compact row is horizontal, fill-width, `valign=START`, `vexpand=False`, 4 px vertical margins | KEEP: row fills width but not height |
| `ui/simple_window.py:1290-1300` | Details row is horizontal, `valign=START`, `vexpand=False`, 4 px vertical margins | KEEP: list rows have natural bounded height |
| `ui/simple_window.py:1305-1320` | Details labels use zero minimum width; name expands, auxiliary columns can hide | KEEP: no fixed-width overflow on narrow windows |
| `ui/simple_window.py:1092-1107` | Group headers opt into fill width but retain natural height | KEEP: presentation rows cannot become giant cells |

### Shell, sidebar and optional panels

| Location | Constraint | Decision and reason |
|---|---|---|
| `ui/simple_window.py:3532-3648` | Workspace and file area expand; sidebar is the start child, file area is the end child | KEEP: simple default shell and one file-area owner |
| `ui/simple_window.py:4400-4455` | Content/terminal panes are created only when requested | KEEP: optional surfaces cannot affect initial GridView measurement |
| `ui/simple_window.py:3180-3207` | Narrow sidebar moves to the existing workspace overlay drawer | KEEP: sidebar minimum width does not squeeze the file view |
| `ui/sidebar.py:123-126` | Sidebar scroller minimum content width 160, no horizontal expansion, vertical expansion | KEEP: practical sidebar floor; drawer/pane owns its width |
| `ui/sidebar.py` row builders | Sidebar rows are `hexpand=True`, section labels are only rendered for non-empty sections | KEEP: no empty PINNED/STARRED headers and no horizontal clipping |
| `ui/folders.py` tree | Folders surface is lazy and has its own scroller | KEEP: it is not part of the default file-view measurement chain |
| `ui/simple_window.py:4520-4528` | Empty info state is centered `No selection`, both-axis expansion | KEEP: no information heading or technical block when nothing is selected |

### CSS size and spacing constraints

`build_css()` in `ui/simple_window.py` is the active theme-bound stylesheet.
The geometry-affecting rules are:

| Selector | Values | Purpose |
|---|---|---|
| `window, .omapath-window, .simple-shell` | `min-width:0; min-height:0` | Permit panes and the file view to shrink |
| `.topbar` | `min-height:32px; padding:6px 10px` | About 44 px normal topbar including padding/border |
| `.toolbar-button`, `.panel-menu-button` | 34 × 34 minimum | Stable keyboard/pointer targets |
| `.breadcrumbs`, `.search-mode`, `.location-entry` | 34 px minimum; location entry horizontal padding 8 | Keep path/search accessible without fixed width |
| `.status-bar` | `min-height:22px; padding:2px 10px` | Compact status area, roughly 26 px including border |
| `.tab-bar` / `.tab-row` | 32 / 28 px minimum | Existing tabs do not inflate the file view when present |
| `.sidebar` | `min-width:160px; padding:10px 8px 14px` | Bounded sidebar floor |
| `.sidebar-row` | `min-height:36px` | Compact places rows |
| `.workspace-overlay`, `.workspace-paned`, `.left-panel-paned`, `.content-paned`, `.terminal-paned`, `.split-paned` | `min-width:0; min-height:0` | Prevent pane minimums from propagating into the file view |
| `.sidebar-spacer` | `min-height:12px` | Small section separation only |
| `.file-grid` | `padding:16px` | Normal icon-view inset; responsive rules reduce it at narrow widths |
| `.file-item`, `.file-item-overlay`, `.file-icon` | `min-width:0; min-height:0` | No CSS minimum-size feedback loop |
| `.compact-file-grid` | `padding:8px 12px 12px` | Compact view inset |
| `.details-list` | `padding:8px 12px 12px` | Details view inset |
| `.details-row` | `min-height:36px` | Bounded details row |
| `.responsive-medium .file-grid` / `.responsive-narrow .file-grid` | 14/8 px primary inset | Responsive density, not a fixed viewport height |
| `.responsive-medium .compact-file-grid` / `.responsive-narrow .compact-file-grid` | 7/5 px primary inset | Responsive density, not a fixed viewport height |
| `.responsive-medium .details-list` / `.responsive-narrow .details-list` | 6 px horizontal inset | Details remains usable at narrow widths |

Dead expanded panel-button CSS was removed. The live panel control is the one
`.panel-menu-button` with a stateful popover; there is no permanent four-button
row affecting topbar width.

## Loading and scroll behavior

Enumeration still runs through the existing asynchronous backend. The active
UI now uses a bounded presentation path:

1. The first enumeration chunk (up to 128 entries) is painted once as soon as
   it is available. The log is `directory first-visible` with elapsed time.
2. Remaining entries accumulate off the GTK model; no growing model splice or
   full sort occurs for each chunk.
3. The final result is sorted/grouped once and committed by one `splice()`.
4. New navigation resets the file-area adjustment to zero. A refresh can
   restore its captured scroll position after the final commit.
5. The final `layout event=commit` is delayed until GTK has processed the
   model mutation, so its numbers describe the actual allocated view.

This keeps a large directory responsive while preserving the existing backend
and navigation semantics. It does not add a new file-management capability.

## Runtime diagnostics

With `OMAPATH_DEBUG=1`, the active path logs model identity and geometry. A
normal commit includes lines like:

```text
backend items=...
gtk store items=...
selection model items=...
view model items=...
active view=grid
file view visible=True
layout event=commit location=... window=... file_area=... grid_columns=... model_items=... bound_items=... visible_rows=... vadj_upper=... vadj_page=... vadj_value=...
```

With `OMAPATH_TRACE_ITEMS=1`, factory setup/bind lines identify recycled item
indexes and names. A small model with a very large `vadj_upper` emits a
`suspicious grid geometry` warning for direct investigation.

## Verification record

The following checks were run after the geometry changes:

```text
python3 -m compileall -q omapath scripts tests
python3 -m unittest tests.test_panel_controls -q
python3 -m unittest tests.test_simple_file_view -q
python3 -m unittest tests.test_grid_geometry_probe -q
OMAPATH_DEBUG=1 timeout 8s ./run.sh
```

The targeted test results were 3/3, 17/17 and 1/1. The runtime launch showed
first-visible Home content and a normal post-commit layout with no GTK
warning, Gtk-CRITICAL or traceback in the application process. The probe
reported 641 realized items for both 1,000 and 4,000 rows.

The direct runtime audit also opened `/lib64`: the first bounded chunk became
visible in about 53 ms in the recorded run, the completed model contained
3,872 entries, and the final adjustment upper was about 164k px at the tested
zoom/window size. Returning to Home restored a small normal extent; no stale
large-directory extent remained.

These are local display measurements, not a substitute for the user's real
Omarchy session. Final manual checks must repeat Home, `/lib64` (or another
large directory), resize, Back/Forward/Up, selection and pointer activation
with `OMAPATH_DEBUG=1 ./run.sh`.

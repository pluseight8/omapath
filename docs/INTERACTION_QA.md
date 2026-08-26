# Omapath interaction QA

This matrix covers the active GTK4 production path. `not claimed` is a scope
boundary, not a hidden feature. Every real action should have visible hover,
pressed/focus and disabled feedback.

| Surface | Pointer interaction | Keyboard | Drag source | Drop target / feedback |
|---|---|---|---|---|
| File item | Left selects; double/Enter opens; middle opens a folder in a new tab; right-click preserves multi-selection and opens actions | Arrows, Enter, Space, Ctrl+A/C/X/V, F2, Delete | Selected local/remote URI list in Icons, Compact and Details | Folder item; valid destination receives accent highlight, invalid destination does not |
| Search result | Same file-row behavior; Open Parent Folder is available from context | Same file-row actions | Same URI provider | Current folder, folder rows, split pane |
| Group header | Click collapses/expands; right-click exposes Group By/Ungroup; it is not selectable/openable | File navigation skips header as an item | none | none |
| Empty view | Right-click New Folder/New File/Paste/Refresh | Ctrl+V, F5 | none | External URI drops; background highlights only when capability allows |
| Breadcrumb | Left navigates; middle opens new tab; right-click shows Open New Tab/Copy/Places/Terminal/Properties | Tab/Enter | none | Drop target for local/provider URIs with visible highlight |
| Sidebar Place / Pinned / Starred / Recent | Left navigates; Recent opens one virtual location; middle opens new tab; right-click context menu | Tab/Enter | Local place URI where it exists | Drop target; pinned rows also support reorder with insertion position |
| Device row | Left mounts/navigates; trailing eject/stop/unmount affordance where GIO permits | Tab/Enter | Local mounted URI | Drop target when mounted and writable |
| Folder tree (F7) | Click/activate navigates; lazy expansion comes from `TreeListModel` | Tab/Enter | Folder URI | Drop target on folder node with highlight |
| Tab | Left activates; middle closes; right-click duplicate/move/close groups; tab drag reorders | Ctrl+T/W/Shift+T/Tab, palette actions | Tab index for reorder | File URI drop target; inactive tab activates after 700 ms, not immediately |
| Split pane | Click activates pane; all commands route to active pane; separator is draggable | Navigation, paste, create, search and view actions use active pane | File URI provider | Other pane and its folder items; capability highlight |
| Details rows | Same selection/open/context/DnD behavior as other views | Same file-row actions | URI list | Row or empty details surface |
| Operation center | Topbar button opens progress popover; each row offers Cancel, Undo or Dismiss; failure Details expands | Focusable controls; action shortcuts remain available | none | none |
| Quick Look | Space opens; image/text/folder/archive content is visible where supported | Left/Right adjacent, Space/Escape close | none | none |
| Information / Properties | F11 panel metadata fields and Save metadata button; Alt+Enter Properties | Tab focus; action shortcuts | none | none |
| Settings / palette | Switches, combos, shortcut capture and action rows are clickable | Tab/Space/Enter/Escape/arrow navigation | none | none |

## DnD action policy

For local files, same-device drops default to Move and cross-device drops to
Copy. Ctrl forces Copy, Shift forces Move and Alt requests Link. Remote Gio
providers use Copy by default and expose Move/Trash/Delete only when their
capabilities report support. A folder cannot be dropped into itself or one of
its descendants. The active UI publishes both `Gdk.FileList` and
`text/uri-list` data for external applications.

## Conflict and operation QA

1. Copy or move a file onto an existing file. The default is Skip, with Keep
   Both, Replace and Cancel available; Replace is never the default.
2. Copy or move a directory onto an existing directory. Verify Merge preserves
   existing children, Rename Incoming and Skip are available, and Cancel does
   no I/O.
3. Use “Apply to equivalent conflicts” across mixed file/directory conflicts;
   a file decision must not silently apply to a directory conflict.
4. Observe current item, item/byte progress, Cancel, failure Details and the
   completion Undo action. Permanent delete/Empty Trash must not create fake
   undo metadata.

## Real Omarchy smoke sequence

Run in a graphical Omarchy/Hyprland session; the current development
environment has no display and cannot validate these pointer paths.

1. Launch with `OMAPATH_DEBUG=1 ./run.sh`, verify Home renders its entries, then
   navigate Documents → nested folder → Back/Forward/Up.
2. Repeat selection, double-click, Enter, F2, right-click and Space in Icons,
   Compact and Details. Include spaces, Cyrillic and Unicode names.
3. Enable Groups, click a header, refresh externally, and verify counts/header
   visibility update without losing the active selection.
4. DnD a file to a folder item, empty background, breadcrumb, pinned Place,
   folder tree, another tab, split pane and Trash. Check hover highlight,
   modifiers and the invalid self-drop.
5. Open F4/F7/F11, Settings and Quick Look; verify optional VTE/media fallbacks
   are quiet and do not change the stable file-view model.

Record failures with the complete debug log. Do not mark visual or pointer
verification complete from unit tests alone.

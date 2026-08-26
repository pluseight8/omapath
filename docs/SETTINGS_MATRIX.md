# Omapath settings matrix

This is an audit of the validated schema and the active
`SimpleFileView`/`OmapathApplication` runtime. `Live UI` means that the active
Settings window exposes a working control; a persisted migration key without
a live control is not presented as a completed feature.

| Key | Persisted | Active runtime | Live UI | Status |
|---|---:|---:|---:|---|
| `default_view` | yes | yes | yes | complete |
| `icon_zoom` | yes | yes | view popover | complete |
| `zoom_level`, `icon_size`, `thumbnail_size` | yes | no | no | deferred compatibility keys |
| `start_location`, `startup_location`, `last_location` | yes | yes | yes / runtime | complete |
| `show_hidden`, `folders_first`, `natural_sort` | yes | yes | yes | complete |
| `sort_mode`, `sort_reverse` | yes | yes | view popover | complete |
| `single_click_open` | yes | yes | yes | complete |
| `confirm_delete`, `confirm_permanent_delete`, `confirm_empty_trash` | yes | yes | yes | complete |
| `conflict_behavior` | yes | yes | yes | complete |
| `auto_refresh` | yes | yes | yes | complete |
| `show_previews`, `image_preview`, `text_preview`, `media_preview` | yes | yes | yes | complete / graceful fallback |
| `show_thumbnails`, `show_folder_sizes` | yes | no | no | deferred until matching active UI exists |
| `show_additional_info` | yes | yes | yes | complete |
| `show_git_status` | yes | yes | yes | complete |
| `show_groups`, `group_criterion` | yes | yes | yes | complete |
| `show_selection_marker`, `show_tooltips`, `inline_rename` | yes | yes | yes | complete |
| `archive_as_folder` | yes | no | no | deferred; no dead archive virtual-location control |
| `services_enabled` | yes | yes | yes | complete |
| `new_tab_inherit_view` | yes | yes | yes | complete |
| `trash_auto_cleanup` | yes | no | no | deferred; no automatic destructive cleanup is advertised |
| `show_sidebar`, `show_folders_panel`, `show_inspector`, `show_terminal_panel`, `show_status_bar` | yes | yes | yes (VTE-gated) | complete |
| `compact_mode` | yes | no | no | deferred compatibility key |
| `remember_location`, `remember_tabs`, `remember_window_size` | yes | yes | tabs/startup/geometry | complete |
| `window_width`, `window_height`, `window_maximized` | yes | yes | runtime | complete |
| `split_view`, `split_position` | yes | yes | split separator/startup | complete |
| `sidebar_width`, `folders_panel_width`, `info_panel_width`, `terminal_panel_height` | yes | yes | yes (VTE-gated for terminal) | complete |
| `follow_omarchy_theme`, `animations` | yes | yes | yes | complete |
| `quick_look_enabled` | yes | yes | no | active compatibility key; Quick Look is exposed by Space/action |
| `preview_max_bytes`, `thumbnail_cache_limit` | yes | yes | no | bounded defaults; no dead numeric editor |
| `search_recursive`, `search_contents`, `search_case_sensitive`, `search_show_hidden` | yes | yes | topbar search options/settings | complete |
| `terminal_shell` | yes | yes | no | active config key; shell is validated at launch and passed as argv |
| `external_clipboard` | yes | yes | yes | complete |
| `folder_view_settings` | yes | yes | view popover | complete |
| `pinned_places`, `starred_places`, `recent_locations` | yes | yes | sidebar/actions | complete |
| `session_tabs`, `active_tab` | yes | yes | runtime | complete |
| `shortcuts` | yes | yes | yes | complete |

The schema deliberately retains a small number of deferred keys for migration
compatibility with earlier Omapath versions. They are not exposed as clickable
controls in the active settings window and therefore cannot be mistaken for
working features. All visible settings controls are checked by
`tests.test_architecture` against the persisted schema.

## Applying settings

The active window applies view, grouping, Git, sidebar, panel, theme, refresh,
animation, terminal, conflict and preview changes without replacing the
filesystem model. Current-folder and all-folder view properties remain
separate from global defaults. Settings writes are validated and atomic.

## Verification boundary

The settings schema and live-application paths are headlessly tested. Actual
focus, switch rendering, resizable dialog layout and Wayland theme changes
must be checked in a real Omarchy session.

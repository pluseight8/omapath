# Changelog

## 0.2.0 — unreleased

- Preserved the stable single-model GTK4 `SimpleFileView` pipeline while
  extracting context menus, dialogs, sidebar, tabs, topbar, search controls,
  Quick Look and operation presentation into host-backed controllers.
- Added concurrent operation progress/cancellation presentation and stronger
  tab state restoration for filter, grouping, previews and selection metadata.
- Added canonical, persisted ActionRegistry shortcut editing with conflict
  confirmation, Clear/Default/Reset All and runtime dispatch.
- Added the versioned v1 optional plugin API, per-plugin failure isolation and
  `--safe-mode`.
- Added live application of core sidebar, status bar, refresh, theme and
  single-click settings.
- Added an Omarchy shell-plugin package with a bar launcher and official
  install/update/remove lifecycle wrappers for the public GitHub repository.

This is not an Omapath 1.0 release. Real Omarchy/Hyprland GUI verification and
the remaining parity items in `docs/DOLPHIN_PARITY.md` are still required.

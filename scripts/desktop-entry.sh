#!/usr/bin/env bash
set -euo pipefail

# This file is deliberately owned by the Omarchy plugin checkout. It creates
# only Omapath's user-local launcher entry and removes it only when the marker
# proves that the file belongs to this plugin.

readonly PLUGIN_ID="io.github.pluseight8.omapath"
readonly DESKTOP_ID="${PLUGIN_ID}.desktop"
readonly MARKER="X-Omapath-Plugin-Id=${PLUGIN_ID}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PLUGIN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

if [[ -z "${HOME:-}" ]]; then
  echo "desktop-entry: HOME is not set" >&2
  exit 1
fi

if [[ -n "${XDG_DATA_HOME:-}" && "$XDG_DATA_HOME" == /* ]]; then
  DATA_HOME="$XDG_DATA_HOME"
else
  DATA_HOME="$HOME/.local/share"
fi

DESKTOP_DIR="$DATA_HOME/applications"
DESKTOP_FILE="$DESKTOP_DIR/$DESKTOP_ID"

die() {
  echo "desktop-entry: $*" >&2
  exit 1
}

desktop_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\`/\\\`}
  value=${value//\$/\\\$}
  printf '%s' "$value"
}

install_entry() {
  [[ -x "$PLUGIN_DIR/run.sh" ]] || die "missing executable launcher: $PLUGIN_DIR/run.sh"
  [[ -f "$PLUGIN_DIR/data/icons/hicolor/scalable/apps/dev.omarchy.Omapath.svg" ]] || \
    die "missing Omapath icon in plugin checkout"

  if [[ -e "$DESKTOP_FILE" || -L "$DESKTOP_FILE" ]]; then
    [[ -f "$DESKTOP_FILE" ]] || die "refusing to replace non-regular file: $DESKTOP_FILE"
    grep -Fqx -- "$MARKER" "$DESKTOP_FILE" || \
      die "refusing to replace an unowned desktop file: $DESKTOP_FILE"
  fi

  mkdir -p -- "$DESKTOP_DIR"
  local exec_path icon_path temporary
  exec_path="$(desktop_escape "$PLUGIN_DIR/run.sh")"
  icon_path="$PLUGIN_DIR/data/icons/hicolor/scalable/apps/dev.omarchy.Omapath.svg"
  temporary="$(mktemp --tmpdir="$DESKTOP_DIR" ".${DESKTOP_ID}.XXXXXX")"
  trap 'rm -f -- "$temporary"' RETURN

  cat >"$temporary" <<EOF
[Desktop Entry]
Type=Application
Name=Omapath
GenericName=File Manager
Comment=The native file manager for Omarchy.
Exec="$exec_path" %U
Icon=$icon_path
Terminal=false
Categories=FileManager;System;FileTools;
MimeType=inode/directory;application/x-7z-compressed;application/zip;application/x-tar;application/pdf;text/plain;
StartupNotify=true
StartupWMClass=dev.omarchy.Omapath
Actions=new-window;
$MARKER

[Desktop Action new-window]
Name=New window
Exec="$exec_path"
EOF
  chmod 0644 -- "$temporary"
  mv -f -- "$temporary" "$DESKTOP_FILE"
  trap - RETURN
}

remove_entry() {
  [[ -e "$DESKTOP_FILE" || -L "$DESKTOP_FILE" ]] || return 0
  [[ -f "$DESKTOP_FILE" ]] || die "refusing to remove non-regular file: $DESKTOP_FILE"
  grep -Fqx -- "$MARKER" "$DESKTOP_FILE" || \
    die "refusing to remove an unowned desktop file: $DESKTOP_FILE"
  rm -f -- "$DESKTOP_FILE"
}

case "${1:-}" in
  install) install_entry ;;
  remove|uninstall) remove_entry ;;
  -h|--help)
    printf 'Usage: %s {install|remove}\n' "$(basename -- "$0")"
    ;;
  *)
    echo "Usage: $0 {install|remove}" >&2
    exit 2
    ;;
esac

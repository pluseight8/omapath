import QtQuick
import Quickshell.Io

// Omapath is a native GTK application. The Omarchy service has no visible
// panel surface; it only keeps the launcher entry synchronized with the
// enabled plugin checkout.
Item {
  id: root

  // Injected by omarchy-shell after the service object is created.
  property var manifest: null
  property bool desktopInstallStarted: false

  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : ""

  function ensureDesktopEntry() {
    if (root.desktopInstallStarted || root.pluginDir === "" || desktopInstaller.running) {
      return
    }
    root.desktopInstallStarted = true
    desktopInstaller.command = [
      "bash",
      root.pluginDir + "/scripts/desktop-entry.sh",
      "install"
    ]
    desktopInstaller.running = true
  }

  Process {
    id: desktopInstaller

    onExited: function(exitCode) {
      if (exitCode !== 0) {
        console.warn("Omapath could not create the application launcher entry (exit " + exitCode + ")")
      }
    }
  }

  // manifest is injected immediately after createObject(); the deferred call
  // and onManifestChanged cover both loader timing paths.
  Component.onCompleted: Qt.callLater(root.ensureDesktopEntry)
  onManifestChanged: Qt.callLater(root.ensureDesktopEntry)
}

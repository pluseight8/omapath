import QtQuick
import Quickshell
import Quickshell.Io

// Omapath is a native GTK application. This headless Omarchy service owns only
// the user-local launcher integration. It never adds a bar or panel surface.
Item {
  id: root

  // Injected by omarchy-shell after the service object is created.
  property var manifest: null
  property bool installRequested: false
  property int installAttempts: 0
  property string lastInstallError: ""

  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : ""
  readonly property string helperPath: pluginDir === ""
    ? ""
    : pluginDir + "/scripts/desktop-entry.sh"

  function installLauncher() {
    if (root.helperPath === "" || desktopInstaller.running)
      return

    root.installRequested = true
    root.installAttempts++
    desktopInstaller.command = ["bash", root.helperPath, "install"]
    desktopInstaller.running = true
  }

  function scheduleInstall() {
    if (root.helperPath === "")
      return
    installRetry.restart()
  }

  Process {
    id: desktopInstaller

    property string stderrText: ""
    stderr: SplitParser {
      onRead: function(line) {
        desktopInstaller.stderrText += String(line) + "\n"
      }
    }

    onStarted: stderrText = ""
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.lastInstallError = ""
        console.log("Omapath launcher integration ready")
        return
      }

      root.lastInstallError = desktopInstaller.stderrText.trim()
      console.warn(
        "Omapath launcher integration failed (exit " + exitCode + ")" +
        (root.lastInstallError ? ": " + root.lastInstallError : "")
      )

      // A freshly added/updated plugin can race the shell's plugin reload.
      // Retry a few times instead of silently leaving the user without a launcher.
      if (root.installAttempts < 4)
        installRetry.restart()
    }
  }

  Timer {
    id: installRetry
    interval: root.installAttempts === 0 ? 50 : Math.min(1500, 200 * root.installAttempts)
    repeat: false
    onTriggered: root.installLauncher()
  }

  Component.onCompleted: Qt.callLater(root.scheduleInstall)
  onManifestChanged: Qt.callLater(root.scheduleInstall)

  // `omarchy plugin remove` disables the service before deleting its checkout.
  // Clean the launcher artifacts while the helper still exists. Detached cleanup
  // is used because this object is being destroyed at this point.
  Component.onDestruction: {
    if (root.helperPath !== "")
      Quickshell.execDetached(["bash", root.helperPath, "remove"])
  }
}

import QtQuick
import Quickshell
import qs.Ui

// Omapath is a GTK4 application, so the Omarchy shell integration is a small
// bar launcher. The file manager keeps its own native window and lifecycle.
BarWidget {
  id: root
  moduleName: "io.github.pluseight8.omapath"

  readonly property string home: Quickshell.env("HOME")
  readonly property string pluginDir: home + "/.config/omarchy/plugins/" + moduleName
  readonly property string launcherPath: pluginDir + "/run.sh"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function openOmapath() {
    // Keep the plugin path as one argv element. No shell interpolation is
    // needed, and a user path containing spaces remains safe.
    Quickshell.execDetached(["bash", launcherPath])
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰉋"
    tooltipText: "Omapath"

    onPressed: function(button) {
      if (button === Qt.LeftButton) root.openOmapath()
    }
  }
}

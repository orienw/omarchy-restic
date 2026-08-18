import QtQuick
import qs.Ui
import "Model.js" as Model

// qmllint disable missing-property

BarWidget {
  id: root
  moduleName: "io.github.orienw.restic"

  readonly property var resticService: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null
  readonly property string status: resticService ? resticService.overallStatus : "unknown"
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = button
    target.hostWidget = root
  }

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function togglePanel() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.status === "running" ? "󰑐" : "󰁯"
    active: root.status === "attention"
    dimmed: root.status === "unknown"
    tooltipText: Model.statusLabel(root.status)

    RotationAnimation on textRotation {
      from: 0
      to: 360
      duration: 900
      loops: Animation.Infinite
      running: root.status === "running"
    }

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) {
        if (root.resticService) root.resticService.refresh(false)
      } else if (buttonCode === Qt.RightButton) {
        if (root.resticService) root.resticService.refresh(true)
      } else {
        root.togglePanel()
      }
    }
  }
}

// qmllint enable missing-property

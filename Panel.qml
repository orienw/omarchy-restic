pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// qmllint disable missing-property

Panel {
  id: root
  moduleName: "io.github.orienw.restic"

  property var anchorItem: null
  property var hostWidget: null
  property double nowMs: Date.now()

  readonly property var barIdentity: hostWidget || root
  readonly property var resticService: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null
  readonly property var report: resticService ? resticService.report : null
  readonly property var jobs: resticService ? resticService.jobs : []
  readonly property string status: resticService ? resticService.overallStatus : "unknown"
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    controller.show()
    nowMs = Date.now()
    if (resticService) resticService.refresh(false)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  function refreshRepositories() {
    if (resticService) resticService.refresh(true)
  }

  function jobCard(jobId) {
    for (var i = 0; i < jobRepeater.count; i++) {
      var card = jobRepeater.itemAt(i)
      if (card && String(card.job && card.job.id || "") === String(jobId)) return card
    }
    return null
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onActivateRequested: root.refreshRepositories()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") root.refreshRepositories()
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: content
          width: parent.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "Restic"
            meta: Model.reportMeta(root.report, root.resticService && root.resticService.refreshing)
            detail: root.jobs.length > 0 ? String(root.jobs.length) + (root.jobs.length === 1 ? " JOB" : " JOBS") : ""
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.status === "unknown" ? 0.55 : 1
            iconComponent: Component {
              Text {
                text: Model.statusGlyph(root.status)
                color: root.status === "attention" ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            visible: root.resticService && root.resticService.lastError !== ""
            width: parent.width
            text: root.resticService ? root.resticService.lastError : ""
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          BorderSurface {
            visible: root.resticService && root.resticService.configError !== ""
            width: parent.width
            implicitHeight: configErrorText.implicitHeight + Style.space(20)
            color: Style.normalFillFor(root.foreground, Color.accent)
            borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
            radius: Style.cornerRadius

            Text {
              id: configErrorText
              anchors.fill: parent
              anchors.margins: Style.space(10)
              text: root.resticService ? root.resticService.configError : ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              verticalAlignment: Text.AlignVCenter
            }
          }

          Repeater {
            id: jobRepeater
            model: root.jobs

            delegate: BorderSurface {
              id: jobCard
              required property var modelData
              property var job: modelData
              property bool detailsExpanded: Model.detailsDefaultOpen(job)

              function toggleDetails() {
                detailsExpanded = !detailsExpanded
              }

              width: content.width
              implicitHeight: jobContent.implicitHeight + Style.space(20)
              color: "transparent"
              borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
              radius: Style.cornerRadius

              Column {
                id: jobContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Style.space(10)
                spacing: Style.space(7)

                Row {
                  width: parent.width
                  spacing: Style.space(8)

                  Text {
                    text: String(jobCard.job.name || jobCard.job.id || "Restic job")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }

                  Item {
                    width: Math.max(0, parent.width - parent.children[0].implicitWidth - statusText.implicitWidth - parent.spacing * 2)
                    height: 1
                  }

                  Text {
                    id: statusText
                    text: Model.statusGlyph(jobCard.job.status) + "  " + String(jobCard.job.status || "unknown").toUpperCase()
                    color: jobCard.job.status === "attention" ? root.urgent : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                Text {
                  width: parent.width
                  text: String(jobCard.job.statusText || "Status unknown")
                  color: jobCard.job.status === "attention" ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                InfoPair { label: "Last run"; value: Model.lastRun(jobCard.job, root.nowMs) }
                InfoPair { label: "Next run"; value: Model.nextRun(jobCard.job, root.nowMs) }
                InfoPair { label: "Latest snapshot"; value: Model.latestSnapshot(jobCard.job, root.nowMs) }

                Text {
                  visible: jobCard.job.logTail && jobCard.job.logTail.length > 0
                  width: parent.width
                  text: jobCard.job.logTail ? jobCard.job.logTail.join("\n") : ""
                  color: root.urgent
                  opacity: 0.85
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WrapAnywhere
                }

                DetailsToggle {
                  objectName: "jobDetailsToggle-" + String(jobCard.job.id || "")
                  expanded: jobCard.detailsExpanded
                  onClicked: jobCard.toggleDetails()
                }

                Column {
                  id: jobDetails
                  objectName: "jobDetails-" + String(jobCard.job.id || "")
                  visible: jobCard.detailsExpanded
                  width: parent.width
                  spacing: Style.space(7)

                  Text {
                    visible: text !== ""
                    width: parent.width
                    text: Model.snapshotSummary(jobCard.job)
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }

                  InfoPair { label: "Snapshots"; value: Model.snapshotCount(jobCard.job) }
                  InfoPair { label: "Raw data"; value: Model.rawData(jobCard.job) }
                  InfoPair { label: "Repository"; value: Model.repositoryState(jobCard.job, root.nowMs) }
                  InfoPair { label: "Integrity"; value: Model.integrityState(jobCard.job, root.nowMs) }
                }
              }
            }
          }

          Text {
            visible: root.jobs.length === 0 && !(root.resticService && root.resticService.configError !== "")
            width: parent.width
            text: "No Restic backup timers were discovered"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
          }

          PanelSeparator {
            foreground: root.foreground
          }

          Button {
            width: parent.width
            text: root.resticService && root.resticService.refreshing ? "Refreshing repositories..." : "Refresh repositories"
            iconText: "󰑐"
            iconSpinning: root.resticService && root.resticService.refreshing
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            focusable: true
            enabled: root.resticService && !root.resticService.refreshing
            onClicked: root.refreshRepositories()
          }

          Text {
            width: parent.width
            text: root.resticService && root.resticService.generatedAt
              ? "Status updated " + Model.relativeTime(root.resticService.generatedAt, root.nowMs).toLowerCase()
              : "Status has not been collected yet"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }

  Timer {
    interval: 30000
    repeat: true
    running: root.opened
    onTriggered: root.nowMs = Date.now()
  }

  component DetailsToggle: Item {
    id: detailsToggle

    property bool expanded: false

    signal clicked()

    width: parent.width
    implicitHeight: detailsLabel.implicitHeight
    height: implicitHeight

    Row {
      anchors.fill: parent
      spacing: Style.space(8)

      Text {
        id: detailsLabel
        text: "Details"
        color: detailsHandler.containsMouse ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Item {
        width: Math.max(0, parent.width - detailsLabel.implicitWidth - detailsChevron.implicitWidth - parent.spacing * 2)
        height: 1
      }

      Text {
        id: detailsChevron
        text: "󰅀"
        color: detailsHandler.containsMouse ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        rotation: detailsToggle.expanded ? 180 : 0
        transformOrigin: Item.Center
      }
    }

    MouseArea {
      id: detailsHandler
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: detailsToggle.clicked()
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    Text {
      id: infoLabel
      text: parent.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Item {
      width: Math.max(0, parent.width - infoLabel.implicitWidth - infoValue.implicitWidth - parent.spacing * 2)
      height: 1
    }

    Text {
      id: infoValue
      width: Math.min(
        implicitWidth,
        Math.max(0, parent.width - infoLabel.implicitWidth - parent.spacing * 2)
      )
      text: parent.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideRight
    }
  }
}

// qmllint enable missing-property

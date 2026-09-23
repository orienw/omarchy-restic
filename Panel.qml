pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// qmllint disable missing-property

Panel {
  id: root
  objectName: "resticPanel"
  moduleName: "io.github.orienw.restic"

  property var anchorItem: null
  property var hostWidget: null
  property double nowMs: Date.now()
  property var detailsOverrides: ({})
  // The selected job by id, so a refresh that reorders or drops jobs can
  // never point a key at a different job. A selected job that disappears
  // targets nothing until the cursor moves.
  property string cursorJobId: ""
  readonly property int cursorIndex: {
    for (var i = 0; cursorJobId !== "" && i < jobs.length; i++) {
      if (String(jobs[i].id) === cursorJobId) return i
    }
    return -1
  }
  property var browsingJob: null
  property alias snapshotBrowser: browser

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
    browsingJob = null
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

  function targetJob() {
    if (cursorIndex >= 0) return jobs[cursorIndex]
    return cursorJobId === "" && jobs.length === 1 ? jobs[0] : null
  }

  function moveCursor(delta) {
    if (jobs.length === 0) return
    var next = cursorIndex < 0
      ? (delta > 0 ? 0 : jobs.length - 1)
      : Math.max(0, Math.min(jobs.length - 1, cursorIndex + delta))
    cursorJobId = String(jobs[next].id)
    var card = jobRepeater.itemAt(next)
    if (!card) return
    if (card.y < flick.contentY) flick.contentY = card.y
    else if (card.y + card.height > flick.contentY + flick.height)
      flick.contentY = card.y + card.height - flick.height
  }

  function browse(job) {
    if (!job || !resticService) return
    browsingJob = job
    browser.open(job)
  }

  function stopBrowsing() {
    browsingJob = null
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function activate() {
    if (browsingJob) browser.openSelected()
    else if (cursorIndex >= 0) browse(targetJob())
    else if (cursorJobId === "") refreshRepositories()
  }

  function backUp(job) {
    if (resticService && job) resticService.backupNow(job.id)
  }

  function backupBusy(job) {
    return Model.serviceActive(job) || job.status === "running"
      || (!!resticService && resticService.startingJob === String(job.id))
  }

  function backupLabel(job) {
    if (Model.serviceActive(job) || job.status === "running") return "Backing up..."
    return backupBusy(job) ? "Starting..." : "Back up now"
  }

  function jobCard(jobId) {
    for (var i = 0; i < jobRepeater.count; i++) {
      var card = jobRepeater.itemAt(i)
      if (card && String(card.job && card.job.id || "") === String(jobId)) return card
    }
    return null
  }

  function initialDetails(job) {
    var key = String(job && job.id || "")
    return key in detailsOverrides ? detailsOverrides[key] === true : Model.detailsDefaultOpen(job)
  }

  function rememberDetails(jobId, expanded) {
    detailsOverrides[String(jobId)] = expanded === true
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(
      root.browsingJob ? browser.implicitHeight : content.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onActivateRequested: root.activate()
      onCloseRequested: root.browsingJob ? root.stopBrowsing() : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (!root.browsingJob) {
          if (dy !== 0) root.moveCursor(dy)
        } else if (dy !== 0) {
          browser.moveSelection(dy)
        } else if (dx > 0) {
          browser.openSelected()
        } else if (dx < 0) {
          browser.goUp()
        }
      }
      onTextKey: function(text) {
        if (root.browsingJob) browser.handleText(text)
        else if (text === "r" || text === "R") root.refreshRepositories()
        else if (text === "b" || text === "B") root.backUp(root.targetJob())
      }

      SnapshotBrowser {
        id: browser
        anchors.fill: parent
        visible: !!root.browsingJob
        service: root.resticService
        foreground: root.foreground
        urgent: root.urgent
        dim: root.dim
        fontFamily: root.fontFamily
        onCloseRequested: root.stopBrowsing()
      }

      Flickable {
        id: flick
        anchors.fill: parent
        visible: !root.browsingJob
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
            meta: root.resticService && root.resticService.lastError !== ""
              ? "Verification incomplete"
              : Model.reportMeta(root.report, root.resticService && root.resticService.refreshing)
            detail: root.jobs.length > 0 ? String(root.jobs.length) + (root.jobs.length === 1 ? " JOB" : " JOBS") : ""
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.status === "unknown" ? 0.55 : 1
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Model.statusGlyph(root.status)
                color: root.status === "attention" ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.resticService && root.resticService.lastError !== ""
            width: parent.width
            text: root.resticService ? root.resticService.lastError : ""
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            text: root.resticService ? root.resticService.actionError : ""
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            text: root.resticService && root.resticService.restoreState === "running"
              ? "Restoring " + root.resticService.restoreName + "..."
              : root.resticService && root.resticService.restoreState === "error"
                ? root.resticService.restoreError
                : ""
            color: root.resticService && root.resticService.restoreState === "error" ? root.urgent : root.dim
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
              textFormat: Text.PlainText
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

            delegate: CursorSurface {
              id: jobCard
              required property var modelData
              required property int index
              property var job: modelData
              property bool detailsExpanded: root.initialDetails(job)

              function toggleDetails() {
                detailsExpanded = !detailsExpanded
                root.rememberDetails(job.id, detailsExpanded)
              }

              width: content.width
              implicitHeight: jobContent.implicitHeight + Style.space(20)
              bordered: true
              hasCursor: index === root.cursorIndex
              foreground: root.foreground

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
                    id: jobName
                    textFormat: Text.PlainText
                    width: Math.min(implicitWidth, parent.width - statusText.implicitWidth - parent.spacing * 2)
                    text: String(jobCard.job.name || jobCard.job.id || "Restic job")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  Item {
                    width: Math.max(0, parent.width - jobName.width - statusText.implicitWidth - parent.spacing * 2)
                    height: 1
                  }

                  Text {
                    textFormat: Text.PlainText
                    id: statusText
                    text: Model.statusGlyph(jobCard.job.status) + "  " + String(jobCard.job.status || "unknown").toUpperCase()
                    color: jobCard.job.status === "attention" ? root.urgent : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                Text {
                  textFormat: Text.PlainText
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
                  textFormat: Text.PlainText
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
                    textFormat: Text.PlainText
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

                Row {
                  spacing: Style.space(8)

                  Button {
                    objectName: "backupButton-" + String(jobCard.job.id || "")
                    text: root.backupLabel(jobCard.job)
                    iconText: "󰁯"
                    iconSpinning: root.backupBusy(jobCard.job)
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.bodySmall
                    bordered: true
                    enabled: Model.canBackUp(jobCard.job)
                      && !!root.resticService && root.resticService.startingJob === ""
                    opacity: enabled || root.backupBusy(jobCard.job) ? 1 : 0.5
                    onClicked: root.backUp(jobCard.job)
                  }

                  Button {
                    objectName: "browseButton-" + String(jobCard.job.id || "")
                    text: "Browse snapshots"
                    iconText: "󰉋"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.bodySmall
                    bordered: true
                    onClicked: root.browse(jobCard.job)
                  }
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
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
            textFormat: Text.PlainText
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
        textFormat: Text.PlainText
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
        textFormat: Text.PlainText
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
      textFormat: Text.PlainText
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
      textFormat: Text.PlainText
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

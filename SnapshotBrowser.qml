pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// qmllint disable missing-property

Item {
  id: root

  property var service: null
  property var job: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.45)
  property string fontFamily: Style.font.family

  property var snapshots: []
  property int snapshotIndex: 0
  property string path: "/"
  property var entries: []
  property bool folderExists: false
  property int selectedIndex: -1
  // What the user picked, by name, independent of the listing on screen, so
  // switching snapshots can neither lose it nor widen it to the folder.
  property string selectedName: ""
  property bool truncated: false
  property bool loading: false
  property string error: ""
  property var listings: ({})
  property int session: 0
  property string shownKey: ""
  // The one listing allowed to redirect a single-file snapshot to its folder:
  // the newest snapshot's root. A listing for any other snapshot or path,
  // including one that arrives after a quick switch, never redirects.
  property string openingKey: ""

  property var _active: null
  property var _pending: null
  property string _stdout: ""
  property bool _stdoutDone: false
  property bool _exited: false

  readonly property var snapshot: snapshotIndex >= 0 && snapshotIndex < snapshots.length
    ? snapshots[snapshotIndex]
    : null
  readonly property var selectedEntry: selectedIndex >= 0 && selectedIndex < entries.length
    ? entries[selectedIndex]
    : null
  // Restore only what the listing on screen proves exists in this snapshot.
  readonly property bool listingReady: !!snapshot && shownKey === listingKey(path)
  readonly property var restoreTarget: listingReady && selectedEntry && Model.exactPath(selectedEntry.path)
    ? selectedEntry
    : null
  readonly property bool folderRestorable: listingReady && folderExists && path !== "/" && Model.exactPath(path)
  readonly property bool restoring: !!service && service.restoreState === "running"

  signal closeRequested()

  implicitHeight: column.implicitHeight

  function open(target) {
    session++
    openingKey = ""
    job = target
    shownKey = ""
    snapshots = []
    snapshotIndex = 0
    listings = ({})
    path = "/"
    entries = []
    selectedIndex = -1
    truncated = false
    error = ""
    if (service) service.clearRestore()
    request({ kind: "snapshots", args: ["snapshots"] })
  }

  function request(item) {
    item.session = session
    if (lister.running) {
      _pending = item
      return
    }
    _active = item
    _stdout = ""
    _stdoutDone = false
    _exited = false
    loading = true
    lister.command = ["python3", service.browsePath].concat(
      item.args, ["--config=" + service.jobsFile, "--job=" + String(job.id)])
    lister.running = true
  }

  function _finalize() {
    if (!_stdoutDone || !_exited) return
    var item = _active
    _active = null
    loading = false
    handle(item, _stdout)
    if (_pending) {
      var next = _pending
      _pending = null
      request(next)
    }
  }

  // Results and errors apply only to the browser session and listing that
  // asked for them. Anything else arrived after the user moved on.
  function handle(item, output) {
    if (!item || item.session !== session) return
    var current = item.kind === "snapshots" || item.key === listingKey(path)
    var lines = String(output || "").trim().split("\n")
    var result = null
    try {
      result = JSON.parse(lines[lines.length - 1])
    } catch (e) {
      result = null
    }
    if (!result || result.type === "error") {
      if (current) error = result && result.error ? String(result.error) : "Could not read the repository"
      return
    }
    if (item.kind === "snapshots") {
      snapshots = Array.isArray(result.snapshots) ? result.snapshots : []
      snapshotIndex = 0
      if (!snapshot) return
      openingKey = listingKey(Model.browseRoot(snapshot))
      navigate(Model.browseRoot(snapshot), "")
    } else {
      listings[item.key] = result
      if (current) show(result)
    }
  }

  function listingKey(target) {
    return (snapshot ? snapshot.id : "") + "\n" + target
  }

  function show(listing) {
    // A snapshot of a single file starts at the file: open its folder instead.
    // Only when opening; elsewhere a path that is not a folder is simply not
    // in this snapshot, and the selection stays as it was.
    var opening = openingKey !== "" && openingKey === listingKey(path)
    if (opening) openingKey = ""
    if (opening && listing.kind && listing.kind !== "dir" && listing.kind !== "missing") {
      navigate(Model.parentPath(path), Model.baseName(path))
      return
    }
    error = ""
    shownKey = listingKey(path)
    entries = Array.isArray(listing.entries) ? listing.entries : []
    folderExists = listing.exists === true
    truncated = listing.truncated === true
    selectedIndex = -1
    for (var i = 0; selectedName && i < entries.length; i++) {
      if (entries[i].name === selectedName) {
        selectedIndex = i
        break
      }
    }
  }

  function navigate(target, selectName) {
    if (!snapshot) return
    if (target !== path || selectName !== undefined) selectedName = selectName || ""
    path = target
    var cached = listings[listingKey(target)]
    if (cached) {
      show(cached)
      return
    }
    error = ""
    shownKey = ""
    entries = []
    folderExists = false
    selectedIndex = -1
    truncated = false
    request({
      kind: "ls",
      key: listingKey(target),
      args: ["ls", "--snapshot=" + snapshot.id, "--path=" + target]
    })
  }

  function select(index) {
    selectedIndex = index
    selectedName = entries[index].name
  }

  function moveSelection(delta) {
    if (entries.length === 0) return
    select(selectedIndex < 0
      ? (delta > 0 ? 0 : entries.length - 1)
      : Math.max(0, Math.min(entries.length - 1, selectedIndex + delta)))
  }

  function openSelected() {
    if (selectedEntry && selectedEntry.type === "dir") navigate(selectedEntry.path, "")
  }

  function goUp() {
    if (path !== "/") navigate(Model.parentPath(path), Model.baseName(path))
  }

  // Snapshots are listed newest first, so +1 moves back in time.
  function switchSnapshot(delta) {
    var next = snapshotIndex + delta
    if (next < 0 || next >= snapshots.length) return
    snapshotIndex = next
    navigate(path)
  }

  function restoreSelected() {
    if (service && restoreTarget && !restoring)
      service.restoreEntry(job.id, snapshot, restoreTarget)
  }

  function restoreFolder() {
    if (service && folderRestorable && !restoring)
      service.restoreEntry(job.id, snapshot, { name: Model.baseName(path), path: path, type: "dir" })
  }

  function restoreLabel() {
    if (restoring) {
      var percent = service.restorePercent >= 0 ? " " + Math.round(service.restorePercent * 100) + "%" : ""
      return "Restoring " + service.restoreName + "..." + percent
    }
    if (!selectedName) return "Restore"
    if (!listingReady) return "Restore " + selectedName
    if (!selectedEntry) return selectedName + " is not in this snapshot"
    if (!restoreTarget) return "Restore the folder that contains it"
    return "Restore " + selectedName
  }

  function handleText(text) {
    if (text === "[") switchSnapshot(1)
    else if (text === "]") switchSnapshot(-1)
    else if (text === "r" || text === "R") restoreSelected()
    else if (text === "f" || text === "F") restoreFolder()
    else if (text === "\b") goUp()
  }

  Column {
    id: column
    width: parent.width
    spacing: Style.space(10)

    Row {
      width: parent.width
      spacing: Style.space(8)

      PanelActionButton {
        iconText: "󰁍"
        tooltipText: "Back to jobs"
        foreground: root.foreground
        fontFamily: root.fontFamily
        anchors.verticalCenter: parent.verticalCenter
        onClicked: root.closeRequested()
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width - parent.children[0].width - parent.spacing
        text: root.job ? String(root.job.name || root.job.id) : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        elide: Text.ElideRight
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    Row {
      width: parent.width
      spacing: Style.space(8)

      PanelActionButton {
        id: olderButton
        iconText: "󰅁"
        tooltipText: "Older snapshot  ["
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: root.snapshotIndex < root.snapshots.length - 1
        anchors.verticalCenter: parent.verticalCenter
        onClicked: root.switchSnapshot(1)
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width - olderButton.width - newerButton.width - parent.spacing * 2
        text: root.snapshots.length > 0
          ? Model.snapshotLabel(root.snapshot)
          : (root.loading ? "Loading snapshots..." : "No snapshots")
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        anchors.verticalCenter: parent.verticalCenter
      }

      PanelActionButton {
        id: newerButton
        iconText: "󰅂"
        tooltipText: "Newer snapshot  ]"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: root.snapshotIndex > 0
        anchors.verticalCenter: parent.verticalCenter
        onClicked: root.switchSnapshot(-1)
      }
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: Model.tildePath(root.path, root.service ? root.service.home : "")
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideMiddle
    }

    PanelSeparator {
      foreground: root.foreground
    }

    Item {
      width: parent.width
      height: Style.space(300)

      ListView {
        id: entryList
        anchors.fill: parent
        spacing: Style.space(2)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        model: root.entries
        currentIndex: root.selectedIndex
        onCurrentIndexChanged: if (currentIndex >= 0) Qt.callLater(keepCurrentVisible)
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        function keepCurrentVisible() {
          if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
        }

        delegate: CursorSurface {
          id: row
          required property var modelData
          required property int index

          width: ListView.view.width
          implicitHeight: rowContent.implicitHeight + Style.space(10)
          hasCursor: index === root.selectedIndex
          foreground: root.foreground

          Row {
            id: rowContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.space(8)
            anchors.rightMargin: Style.space(8)
            spacing: Style.space(8)

            Text {
              id: entryIcon
              textFormat: Text.PlainText
              text: row.modelData.type === "dir" ? "󰉋" : (row.modelData.type === "symlink" ? "󰌷" : "󰈔")
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width - entryIcon.width - entryDetail.width - parent.spacing * 2
              text: String(row.modelData.name || "")
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              elide: Text.ElideMiddle
            }

            Text {
              id: entryDetail
              textFormat: Text.PlainText
              text: row.modelData.type === "dir" ? "󰅂" : Model.formatBytes(row.modelData.size)
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.select(row.index)
              root.openSelected()
            }
          }
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: text !== ""
        anchors.centerIn: parent
        width: parent.width - Style.space(20)
        text: root.error !== "" ? root.error
          : root.snapshot && !root.listingReady ? "Loading..."
          : root.listingReady && !root.folderExists ? "This folder is not in this snapshot"
          : root.listingReady && root.entries.length === 0 ? "This folder is empty"
          : ""
        color: root.error !== "" ? root.urgent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
      }
    }

    Text {
      textFormat: Text.PlainText
      visible: root.truncated
      width: parent.width
      text: "Showing the first 5,000 entries"
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    PanelSeparator {
      foreground: root.foreground
    }

    Row {
      width: parent.width
      spacing: Style.space(8)

      // The kit's Button does not elide, and an overflowing file name would
      // spill over the folder button next to it.
      TextMetrics {
        id: restoreMetrics
        font.family: root.fontFamily
        font.pixelSize: restoreButton.fontSize
        elide: Qt.ElideMiddle
        elideWidth: Math.max(0, restoreButton.width - restoreButton.horizontalPadding * 2
          - restoreButton.iconSize - Style.spacing.controlGap - Style.space(8))
        text: root.restoreLabel()
      }

      Button {
        id: restoreButton
        objectName: "restoreButton"
        width: parent.width - folderButton.width - parent.spacing
          - (cancelButton.visible ? cancelButton.width + parent.spacing : 0)
        text: restoreMetrics.elidedText
        iconText: "󰦛"
        iconSpinning: root.restoring
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.restoring && !!root.restoreTarget
        opacity: enabled || root.restoring ? 1 : 0.5
        onClicked: root.restoreSelected()
      }

      Button {
        id: folderButton
        objectName: "restoreFolderButton"
        visible: !root.restoring
        width: visible ? implicitWidth : 0
        text: "Restore folder"
        iconText: "󰉋"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: root.folderRestorable
        opacity: enabled ? 1 : 0.5
        onClicked: root.restoreFolder()
      }

      Button {
        id: cancelButton
        visible: root.restoring
        text: "Cancel"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        onClicked: root.service.cancelRestore()
      }
    }

    Row {
      visible: !!root.service && root.service.restoreState !== "" && root.service.restoreState !== "running"
      width: parent.width
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        width: parent.width - (openButton.visible ? openButton.width + parent.spacing : 0)
        text: !root.service ? ""
          : root.service.restoreState === "done"
            ? "Restored to " + Model.tildePath(root.service.restorePath, root.service.home)
          : root.service.restoreState === "cancelled" ? "Restore cancelled"
          : root.service.restoreError
        color: root.service && root.service.restoreState === "error" ? root.urgent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        anchors.verticalCenter: parent.verticalCenter
      }

      Button {
        id: openButton
        visible: !!root.service && root.service.restoreState === "done"
        text: "Open folder"
        foreground: root.foreground
        fontFamily: root.fontFamily
        fontSize: Style.font.caption
        bordered: true
        anchors.verticalCenter: parent.verticalCenter
        onClicked: root.service.openRestoreFolder()
      }
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: "⏎ open · ⌫ up · [ ] snapshot · R restore · F folder"
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
    }
  }

  Process {
    id: lister
    running: false
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root._stdout = text
        root._stdoutDone = true
        root._finalize()
      }
    }
    onExited: {
      root._exited = true
      root._finalize()
    }
  }
}

// qmllint enable missing-property

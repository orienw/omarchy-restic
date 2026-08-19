import QtQuick
import QtTest
import Quickshell

ShellRoot {
  id: root

  property bool finished: false
  property int stage: 0
  property var barButton: null
  property real rotationStart: 0

  function fail(message) {
    console.error("TEST FAILURE:", message)
    finished = true
    Qt.exit(1)
  }

  function findBarButton(parent) {
    if (!parent) return null
    if (typeof parent.triggerPress === "function" && "textRotation" in parent) return parent
    for (var i = 0; parent.children && i < parent.children.length; i++) {
      var match = findBarButton(parent.children[i])
      if (match) return match
    }
    return null
  }

  function findByName(parent, name) {
    if (!parent) return null
    if (parent.objectName === name) return parent
    var i
    if (parent.children) {
      for (i = 0; i < parent.children.length; i++) {
        var childMatch = findByName(parent.children[i], name)
        if (childMatch) return childMatch
      }
    }
    if (parent.contentItem && parent.contentItem !== parent) {
      var contentMatch = findByName(parent.contentItem, name)
      if (contentMatch) return contentMatch
    }
    return null
  }

  TestEvent {
    id: events
  }

  QtObject {
    id: fakeService

    property bool refreshing: false
    property string overallStatus: "attention"
    property string lastError: ""
    property string configError: ""
    property string generatedAt: "2026-08-17T12:00:00Z"
    property int refreshCalls: 0
    property var refreshForces: []
    property var report: ({
      schemaVersion: 1,
      generatedAt: generatedAt,
      overallStatus: overallStatus,
      summary: { jobs: 2, healthy: 1, running: 0, attention: 1, unknown: 0 },
      jobs: jobs
    })
    property var jobs: [
      {
        id: "home",
        name: "Home",
        status: "healthy",
        statusText: "Last run completed successfully",
        service: { lastRun: { finishedAt: "2026-08-17T11:00:00Z", durationSec: 3, result: "success" } },
        timer: { nextRunAt: "2026-08-18T03:30:00Z" },
        repository: {
          status: "ready",
          checkedAt: "2026-08-17T11:59:00Z",
          snapshotCount: 4,
          latestSnapshot: {
            id: "a36afc8f",
            time: "2026-08-17T11:00:00Z",
            summary: { totalFilesProcessed: 11520, totalBytesProcessed: 2039661056, dataAdded: 48007632 }
          },
          stats: { totalSize: 604758931 }
        },
        integrity: { status: "not-configured", lastSuccessAt: null },
        logTail: []
      },
      {
        id: "dropbox",
        name: "Dropbox",
        status: "attention",
        statusText: "Last run failed",
        service: { lastRun: { finishedAt: "2026-08-17T10:55:00Z", durationSec: 4, result: "failed" } },
        timer: { nextRunAt: "2026-08-18T03:50:00Z" },
        repository: { status: "stale", checkedAt: "2026-08-17T10:00:00Z", snapshotCount: 2, latestSnapshot: null, stats: {} },
        integrity: { status: "not-configured", lastSuccessAt: null },
        logTail: ["repository unavailable"]
      }
    ]

    function refresh(forceRepositories) {
      refreshCalls++
      refreshForces = refreshForces.concat([forceRepositories === true])
      return forceRepositories ? "forced" : "started"
    }
  }

  QtObject {
    id: fakeShell
    function serviceFor(pluginId) {
      return pluginId === "io.github.orienw.restic" ? fakeService : null
    }
  }

  QtObject {
    id: fakeBar

    property var shell: fakeShell
    property color foreground: "#f0f0f0"
    property color barForeground: foreground
    property color urgent: "#ff6666"
    property color background: "#111111"
    property string fontFamily: "sans-serif"
    property string position: "top"
    property int barSize: 30
    property int sizeHorizontal: 30
    property bool vertical: false
    property bool foregroundAnimationEnabled: false
    property var activePopout: null
    property var clickTargets: []

    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { if (activePopout === owner) activePopout = null }
    function switchPanelFrom(owner, direction) { return false }
    function targetBelongsToWindow(target, window) { return true }
    function showTooltip(item, text) {}
    function hideTooltip(item) {}
    function registerClickTarget(item) {}
    function unregisterClickTarget(item) {}
    function moduleWidgets(moduleName) { return [] }
  }

  FloatingWindow {
    id: testWindow
    visible: true
    color: "transparent"
    implicitWidth: 60
    implicitHeight: 30

    Item {
      id: anchor
      anchors.fill: parent
    }

    Loader {
      id: widgetLoader
      anchors.fill: parent
      source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/BarWidget.qml"
      onLoaded: item.bar = fakeBar
    }
  }

  Loader {
    id: panelLoader
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/Panel.qml"
    onLoaded: {
      item.bar = fakeBar
      item.anchorItem = anchor
    }
  }

  Timer {
    interval: 100
    running: true
    repeat: true
    onTriggered: {
      if (panelLoader.status === Loader.Error || widgetLoader.status === Loader.Error) {
        root.fail("a UI entry point failed to load")
        return
      }
      if (panelLoader.status !== Loader.Ready || widgetLoader.status !== Loader.Ready) return

      var panel = panelLoader.item
      var widget = widgetLoader.item
      if (root.stage === 0) {
        if (panel.jobs.length !== 2 || widget.resticService !== fakeService || widget.status !== "attention") {
          root.fail("UI did not resolve the service state")
          return
        }
        root.barButton = root.findBarButton(widget)
        if (!root.barButton) {
          root.fail("bar button was not created")
          return
        }
        if (!events.mouseClick(
            root.barButton,
            root.barButton.width / 2,
            root.barButton.height / 2,
            Qt.LeftButton,
            Qt.NoModifier,
            -1
        )) {
          root.fail("the bar window did not accept pointer input")
          return
        }
        if (!widget.opened || fakeService.refreshCalls !== 1 || fakeService.refreshForces[0] !== false) {
          root.fail("the bar button did not open and refresh the panel")
          return
        }
        var homeCard = panel.jobCard("home")
        var dropboxCard = panel.jobCard("dropbox")
        if (!homeCard) {
          root.fail("healthy job card was not created")
          return
        }
        if (homeCard.detailsExpanded) {
          root.fail("healthy job details should start collapsed")
          return
        }
        if (!dropboxCard || !dropboxCard.detailsExpanded) {
          root.fail("attention job details should start open")
          return
        }
        var homeToggle = root.findByName(homeCard, "jobDetailsToggle-home")
        if (!homeToggle || homeToggle.height < 1 || homeToggle.width < 1) {
          root.fail("healthy job details toggle was not laid out")
          return
        }
        homeCard.toggleDetails()
        if (!homeCard.detailsExpanded) {
          root.fail("toggling details did not expand the healthy job")
          return
        }
        root.stage = 1
        return
      }

      if (root.stage === 1) {
        fakeService.jobs = fakeService.jobs.slice()
        root.stage = 2
        return
      }

      if (root.stage === 2) {
        if (!panel.jobCard("home") || panel.jobCard("home").detailsExpanded !== true) {
          root.fail("healthy job details did not survive a jobs refresh")
          return
        }
        if (!panel.jobCard("dropbox") || panel.jobCard("dropbox").detailsExpanded !== true) {
          root.fail("attention job details did not survive a jobs refresh")
          return
        }
        root.stage = 3
        return
      }

      if (root.stage === 3) {
        if (!events.keyClickChar("R", Qt.NoModifier, -1)) {
          root.fail("the panel window did not accept keyboard input")
          return
        }
        root.stage = 4
        return
      }

      if (root.stage === 4) {
        if (fakeService.refreshCalls !== 2 || fakeService.refreshForces[1] !== true) {
          root.fail("the R key did not force a repository refresh")
          return
        }
        fakeService.overallStatus = "running"
        root.rotationStart = root.barButton.textRotation
        root.stage = 5
        return
      }

      if (root.stage === 5) {
        if (widget.status !== "running"
            || Math.abs(root.barButton.textRotation - root.rotationStart) < 1) {
          root.fail("the running state did not animate the bar icon")
          return
        }
        if (!events.keyClick(Qt.Key_Escape, Qt.NoModifier, -1)) {
          root.fail("the panel window did not accept Escape")
          return
        }
        root.stage = 6
        return
      }

      if (widget.opened) {
        root.fail("Escape did not close the panel")
        return
      }

      console.log("UI smoke tests passed")
      root.finished = true
      stop()
      Qt.quit()
    }
  }

  Timer {
    interval: 5000
    running: true
    repeat: false
    onTriggered: if (!root.finished) root.fail("UI smoke test timed out")
  }
}

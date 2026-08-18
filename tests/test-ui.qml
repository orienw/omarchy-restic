import QtQuick
import Quickshell

ShellRoot {
  id: root

  property bool finished: false

  function fail(message) {
    console.error("TEST FAILURE:", message)
    finished = true
    Qt.exit(1)
  }

  QtObject {
    id: fakeService

    property bool refreshing: false
    property string overallStatus: "attention"
    property string lastError: ""
    property string configError: ""
    property string generatedAt: "2026-08-17T12:00:00Z"
    property int refreshCalls: 0
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

  Item {
    id: anchor
    width: 30
    height: 30
  }

  Loader {
    id: panelLoader
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/Panel.qml"
    onLoaded: {
      item.bar = fakeBar
      item.anchorItem = anchor
    }
  }

  Loader {
    id: widgetLoader
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/BarWidget.qml"
    onLoaded: item.bar = fakeBar
  }

  Timer {
    interval: 50
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
      if (panel.jobs.length !== 2 || widget.resticService !== fakeService || widget.status !== "attention") {
        root.fail("UI did not resolve the service state")
        return
      }
      panel.refreshRepositories()
      if (fakeService.refreshCalls !== 1) {
        root.fail("panel refresh did not reach the service")
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

import QtQuick
import Quickshell

ShellRoot {
  id: root

  property bool finished: false
  property int stage: 0

  function fail(message) {
    console.error("TEST FAILURE:", message)
    finished = true
    Qt.exit(1)
  }

  QtObject {
    id: scopedShell
    property var barConfig: ({
      layout: { right: [{
        id: "io.github.orienw.restic", jobsFile: "~/custom jobs.json",
        refreshIntervalSec: 45, repositoryRefreshMinutes: 7, logLines: 5
      }] }
    })
  }

  QtObject {
    id: legacyShell
    property var shellConfig: ({
      bar: { layout: { left: [{
        id: "io.github.orienw.restic", jobsFile: "~/legacy bar.json",
        refreshIntervalSec: 30, repositoryRefreshMinutes: 4, logLines: 3
      }] } },
      plugins: [{
        id: "io.github.orienw.restic", jobsFile: "~/legacy service.json",
        refreshIntervalSec: 75, repositoryRefreshMinutes: 3, logLines: 2
      }]
    })
  }

  Loader {
    id: serviceLoader
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR").split("/").map(encodeURIComponent).join("/") + "/Service.qml"
    onLoaded: {
      item.shell = scopedShell
      item.manifest = { id: "io.github.orienw.restic" }
    }
  }

  Timer {
    interval: 25
    running: true
    repeat: true
    onTriggered: {
      if (serviceLoader.status === Loader.Error) {
        root.fail("service failed to load from a path containing URL characters")
        return
      }
      var service = serviceLoader.item
      if (!service) return
      if (service.lastError !== "") {
        root.fail("startup collector failed: " + service.lastError)
        return
      }
      if (!service.generatedAt || service.refreshing) return

      var expected = [
        ["~/custom jobs.json", 45, 420, "5"],
        ["~/updated jobs.json", 90, 660, "8"],
        ["~/legacy bar.json", 30, 240, "3"],
        ["~/legacy service.json", 75, 180, "2"]
      ][root.stage]
      var options = service.report.config.options
      if (options["--config"] !== expected[0]) return
      if (!service.initialized || service.overallStatus !== "healthy" || service.jobs.length !== 1
          || service.helperPath !== Quickshell.env("RESTIC_PLUGIN_DIR") + "/scripts/restic_status.py"
          || service.refreshIntervalSec !== expected[1]
          || options["--repository-cache-seconds"] !== String(expected[2])
          || options["--log-lines"] !== expected[3]) {
        root.fail("startup or settings did not reach the collector at stage " + root.stage)
        return
      }

      if (root.stage === 0) {
        scopedShell.barConfig = { layout: { center: [{
          id: "io.github.orienw.restic", jobsFile: "~/updated jobs.json",
          refreshIntervalSec: 90, repositoryRefreshMinutes: 11, logLines: 8
        }] } }
      } else if (root.stage === 1) {
        service.shell = legacyShell
      } else if (root.stage === 2) {
        legacyShell.shellConfig = { plugins: legacyShell.shellConfig.plugins }
      } else {
        console.log("startup tests passed")
        root.finished = true
        Qt.quit()
      }
      root.stage++
    }
  }

  Timer {
    interval: 5000
    running: true
    repeat: false
    onTriggered: if (!root.finished) root.fail("collector never completed startup or settings refresh at stage " + root.stage)
  }
}

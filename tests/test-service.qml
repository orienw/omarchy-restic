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

  Loader {
    id: serviceLoader
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/Service.qml"
    onLoaded: {
      var report = {
        schemaVersion: 1,
        generatedAt: "2026-08-17T12:00:00Z",
        overallStatus: "attention",
        summary: { jobs: 2, healthy: 1, running: 0, attention: 1, unknown: 0 },
        config: { status: "ready", error: "" },
        jobs: [
          { id: "home", name: "Home", status: "healthy" },
          { id: "dropbox", name: "Dropbox", status: "attention" }
        ]
      }
      if (!item.applyReport(JSON.stringify(report))) {
        root.fail("service rejected a valid report")
        return
      }
      checkTimer.start()
    }
  }

  Timer {
    id: checkTimer
    interval: 20
    repeat: false
    onTriggered: {
      var service = serviceLoader.item
      if (!service || service.overallStatus !== "attention"
          || service.attentionJobs !== 1 || service.jobs.length !== 2
          || service.generatedAt !== "2026-08-17T12:00:00Z") {
        root.fail("service properties did not follow the report")
        return
      }
      if (service.applyReport("not-json") || service.lastError === "") {
        root.fail("service accepted invalid collector output")
        return
      }
      console.log("service tests passed")
      root.finished = true
      Qt.quit()
    }
  }

  Timer {
    interval: 3000
    running: true
    repeat: false
    onTriggered: if (!root.finished) root.fail("service test timed out")
  }
}

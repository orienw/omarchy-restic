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
        generatedAt: new Date().toISOString(),
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
          || service.generatedAt === "") {
        root.fail("service properties did not follow the report")
        return
      }
      var healthy = JSON.stringify({
        schemaVersion: 1, generatedAt: new Date().toISOString(), overallStatus: "healthy",
        summary: {jobs: 1, healthy: 1, running: 0, attention: 0, unknown: 0},
        config: {status: "ready", error: ""}, jobs: [{id: "home", status: "healthy"}]
      })
      service.applyReport(healthy)
      if (service.overallStatus !== "healthy" || service.applyReport("not-json")
          || service.lastError === "" || service.overallStatus !== "unknown"
          || service.jobs.length !== 1) {
        root.fail("invalid output did not invalidate healthy status while retaining cached jobs")
        return
      }
      service.applyReport(healthy)
      service.refreshing = true
      service._stdout = healthy
      service._exitCode = 1
      service._stdoutDone = true
      service._exited = true
      service._finalize()
      if (!service.refreshing) {
        root.fail("collector finalized before stderr arrived")
        return
      }
      service._stderr = "Collector failed"
      service._stderrDone = true
      service._finalize()
      if (service.refreshing || service.overallStatus !== "unknown"
          || service.lastError !== "Collector failed") {
        root.fail("failed collector exit preserved healthy status")
        return
      }
      service.applyReport(healthy)
      service.nowMs += 121000
      service.refreshing = true
      if (service.overallStatus !== "unknown" || !service.reportStale || service.lastError === "") {
        root.fail("stale status remained healthy during a hung refresh")
        return
      }
      service.applyReport(healthy)
      if (service.overallStatus !== "healthy" || service.lastError !== "") {
        root.fail("fresh status did not recover after collection failure or expiry")
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

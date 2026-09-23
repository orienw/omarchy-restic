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
      service.notificationsEnabled = false
      var failing = function(finishedAt) {
        return JSON.stringify({
          schemaVersion: 1, generatedAt: new Date().toISOString(), overallStatus: "attention",
          summary: {jobs: 1, healthy: 0, running: 0, attention: 1, unknown: 0},
          config: {status: "ready", error: ""},
          jobs: [{id: "home", name: "Home", status: "attention",
            service: {unit: "restic-home.service", lastRun: {finishedAt: finishedAt, result: "failed"}},
            issues: [{code: "last-run-failed", message: "Backup failed", severity: "critical"}]}]
        })
      }
      service.applyReport(failing("2026-08-17T11:00:00Z"))
      var firstAlert = Object.keys(service.alertedKeys)
      service.applyReport(failing("2026-08-17T11:00:00Z"))
      if (firstAlert.length !== 1 || Object.keys(service.alertedKeys).join() !== firstAlert.join()) {
        root.fail("a persisting failure was not tracked as one alert")
        return
      }
      service.applyReport(healthy)
      if (Object.keys(service.alertedKeys).length !== 0) {
        root.fail("recovery did not clear the alert so a new failure can notify")
        return
      }

      service.restoreState = "running"
      service.restoreName = "notes.md"
      service.handleRestoreLine("not json")
      service.handleRestoreLine(JSON.stringify({type: "progress", percent: 0.25}))
      if (service.restoreState !== "running" || service.restorePercent !== 0.25) {
        root.fail("restore progress was not tracked")
        return
      }
      service.handleRestoreLine(JSON.stringify({type: "done", path: "/tmp/r/notes.md", folder: "/tmp/r"}))
      service.handleRestoreLine(JSON.stringify({type: "error", error: "late"}))
      if (service.restoreState !== "done" || service.restoreFolder !== "/tmp/r" || service.restoreError !== "") {
        root.fail("restore completion was not kept: " + service.restoreState)
        return
      }
      service.clearRestore()
      service.restoreState = "running"
      service.handleRestoreLine(JSON.stringify({type: "error", error: "Wrong repository password"}))
      if (service.restoreState !== "error" || service.restoreError !== "Wrong repository password") {
        root.fail("restore failure was not reported")
        return
      }
      service.clearRestore()
      if (service.restoreState !== "" || service.restoreEntry("home", null, null) !== "busy") {
        root.fail("restore state did not clear or accepted a missing entry")
        return
      }

      service.applyReport(JSON.stringify({
        schemaVersion: 1, generatedAt: new Date().toISOString(), overallStatus: "healthy",
        summary: {jobs: 2, healthy: 1, running: 1, attention: 0, unknown: 0},
        config: {status: "ready", error: ""},
        jobs: [
          {id: "home", name: "Home", status: "healthy",
           service: {unit: "omarchy-restic-test-missing.service", active: false}},
          {id: "busy", name: "Busy", status: "running",
           service: {unit: "omarchy-restic-test-busy.service", active: true}}
        ]
      }))
      if (service.backupNow("missing") !== "unavailable" || service.backupNow("busy") !== "unavailable") {
        root.fail("backup started for a missing or running job")
        return
      }
      if (service.backupNow("home") !== "started" || service.startingJob !== "home"
          || service.backupNow("home") !== "unavailable") {
        root.fail("backup start was not tracked or allowed a duplicate start")
        return
      }
      startTimer.start()
    }
  }

  Timer {
    id: startTimer
    interval: 50
    repeat: true
    onTriggered: {
      var service = serviceLoader.item
      if (service.startingJob !== "") return
      stop()
      if (service.actionError.indexOf("Could not start Home") !== 0) {
        root.fail("failed backup start was not reported: " + service.actionError)
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

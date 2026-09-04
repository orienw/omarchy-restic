import QtQuick
import Quickshell
import "Model.js" as Model

ShellRoot {
  id: root

  function fail(message) {
    console.error("TEST FAILURE:", message)
    Qt.exit(1)
  }

  Timer {
    interval: 20
    running: true
    repeat: false
    onTriggered: {
      var parsed = Model.parseReport(JSON.stringify({
        schemaVersion: 1,
        jobs: [],
        overallStatus: "healthy"
      }))
      if (!parsed.ok || parsed.report.overallStatus !== "healthy") {
        root.fail("report parsing failed")
        return
      }
      if (Model.formatBytes(1073741824) !== "1.00 GiB") {
        root.fail("byte formatting failed: " + Model.formatBytes(1073741824))
        return
      }
      var nowMs = Date.parse("2026-08-17T12:00:00Z")
      if (Model.relativeTime("2026-08-17T11:00:00Z", nowMs) !== "1 hour ago") {
        root.fail("relative time formatting failed")
        return
      }
      if (Model.relativeTime(new Date(nowMs - 115000).toISOString(), nowMs) !== "2 minutes ago") {
        root.fail("115s relative time should round to 2 minutes")
        return
      }
      if (Model.relativeTime(new Date(nowMs - 5400000).toISOString(), nowMs) !== "2 hours ago") {
        root.fail("5400s relative time should round to 2 hours")
        return
      }
      if (Model.relativeTime(new Date(nowMs - 138240000).toISOString(), nowMs) !== "2 days ago") {
        root.fail("138240s relative time should round to 2 days")
        return
      }
      if (Model.formatDuration(3599) !== "1h 0m") {
        root.fail("3599s should format as 1h 0m: " + Model.formatDuration(3599))
        return
      }
      if (Model.formatDuration(7170) !== "2h 0m") {
        root.fail("7170s should format as 2h 0m: " + Model.formatDuration(7170))
        return
      }
      if (Model.futureTime("2026-08-17T15:00:00Z", Date.parse("2026-08-17T12:00:00Z")) !== "In 3 hours") {
        root.fail("future time formatting failed")
        return
      }
      if (Model.repositoryState({ repository: { status: "partial" } }, Date.now())
          !== "Snapshots ready, stats unavailable") {
        root.fail("partial repository status was not described as informational")
        return
      }
      if (Model.detailsDefaultOpen(null) !== false) {
        root.fail("missing jobs should stay collapsed")
        return
      }
      if (Model.detailsDefaultOpen({ status: "healthy" }) !== false) {
        root.fail("healthy jobs should stay collapsed")
        return
      }
      if (Model.detailsDefaultOpen({ status: "running" }) !== false) {
        root.fail("running jobs should stay collapsed")
        return
      }
      if (Model.detailsDefaultOpen({ status: "unknown" }) !== false) {
        root.fail("unknown jobs should stay collapsed")
        return
      }
      if (Model.detailsDefaultOpen({ status: "attention" }) !== true) {
        root.fail("attention jobs should start expanded")
        return
      }
      if (Model.detailsDefaultOpen({
        status: "healthy",
        repository: { status: "partial" }
      }) !== false) {
        root.fail("missing stats should not force details open")
        return
      }
      if (Model.detailsDefaultOpen({
        status: "healthy",
        repository: { status: "stale" }
      }) !== true) {
        root.fail("stale repositories should start expanded")
        return
      }
      if (Model.detailsDefaultOpen({
        status: "healthy",
        repository: { status: "unavailable" }
      }) !== true) {
        root.fail("unavailable repositories should start expanded")
        return
      }
      if (Model.detailsDefaultOpen({
        status: "healthy",
        integrity: { status: "attention" }
      }) !== true) {
        root.fail("integrity failures should start expanded")
        return
      }
      if (Model.integrityState({integrity: {
        status: "unknown", statusText: "Integrity-check service status is unavailable",
        lastSuccessAt: "2026-08-17T11:00:00Z"
      }}, nowMs) !== "Integrity-check service status is unavailable") {
        root.fail("unverifiable integrity status must not display a past check as passed")
        return
      }
      if (Model.reportMeta(null, true) !== "Refreshing status") {
        root.fail("null report while refreshing should say refreshing")
        return
      }
      if (Model.reportMeta({schemaVersion: 1, generatedAt: null, summary: {jobs: 0}}, true) !== "Refreshing status") {
        root.fail("stub report while refreshing should say refreshing")
        return
      }
      if (Model.reportMeta({
        schemaVersion: 1,
        generatedAt: "2026-08-17T12:00:00Z",
        overallStatus: "healthy",
        summary: { jobs: 2, healthy: 2, running: 0, attention: 0, unknown: 0 }
      }, true) !== "2 jobs checked") {
        root.fail("real report while refreshing should keep job counts")
        return
      }
      console.log("model tests passed")
      Qt.quit()
    }
  }
}

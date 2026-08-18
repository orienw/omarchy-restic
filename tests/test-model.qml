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
      if (Model.relativeTime("2026-08-17T11:00:00Z", Date.parse("2026-08-17T12:00:00Z")) !== "1 hour ago") {
        root.fail("relative time formatting failed")
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
      console.log("model tests passed")
      Qt.quit()
    }
  }
}

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
      console.log("model tests passed")
      Qt.quit()
    }
  }
}

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
      var backupJobs = [
        { id: "home", status: "healthy", service: { unit: "restic-home.service", active: false } },
        { id: "busy", status: "running", service: { unit: "restic-busy.service", active: true } },
        { id: "bad", status: "healthy", service: { unit: "--now evil.service" } }
      ]
      if (Model.findJob(backupJobs, "busy") !== backupJobs[1] || Model.findJob(backupJobs, "missing") !== null) {
        root.fail("job lookup did not match by id")
        return
      }
      if (!Model.canBackUp(backupJobs[0]) || Model.canBackUp(backupJobs[1]) || Model.canBackUp(null)) {
        root.fail("backup availability did not follow the service state")
        return
      }
      if (Model.backupUnit(backupJobs[2]) !== "" || Model.canBackUp(backupJobs[2])) {
        root.fail("an invalid unit name was accepted for a backup start")
        return
      }
      var alertsFor = function(previous, jobs, degraded) {
        return Model.nextAlerts(previous || {}, jobs, degraded === true)
      }
      var failedJob = function(finishedAt, extraIssues) {
        return {
          id: "home", name: "Home", status: "attention",
          service: { unit: "restic-home.service", lastRun: { finishedAt: finishedAt, result: "failed" } },
          issues: (extraIssues || []).concat([
            { code: "last-run-failed", message: "Fatal: <b>wrong</b> password & key", severity: "critical" },
            { code: "repository-stale", message: "Repository unreachable", severity: "warning" }
          ])
        }
      }
      var first = alertsFor({}, [failedJob("2026-08-17T11:00:00Z")])
      var repeat = alertsFor(first.known, [failedJob("2026-08-17T11:00:00Z")])
      var nextRun = alertsFor(repeat.known, [failedJob("2026-08-18T11:00:00Z")])
      if (first.notices.length !== 1 || repeat.notices.length !== 0 || nextRun.notices.length !== 1) {
        root.fail("failed runs did not notify exactly once each")
        return
      }
      var command = first.notices[0]
      if (command[0] !== "omarchy-notification-send" || command[7] !== "Home backup needs attention"
          || command[8] !== "Fatal: &lt;b&gt;wrong&lt;/b&gt; password &amp; key"
          || command.indexOf("restic-home.service") === -1) {
        root.fail("alert command was not built safely: " + JSON.stringify(command))
        return
      }
      var offline = { id: "nas", status: "attention", issues: [
        { code: "repository-unavailable", message: "Repository unreachable", severity: "warning" }
      ] }
      var offlineAlerts = alertsFor({}, [offline])
      if (offlineAlerts.notices.length !== 0 || Object.keys(offlineAlerts.known).length !== 0) {
        root.fail("a transient repository outage raised an alert")
        return
      }
      var overdue = function(hours) {
        return { id: "home", status: "attention", issues: [
          { code: "run-overdue", message: "No successful run in " + hours + " hours", severity: "critical" }
        ] }
      }
      var overdueAlerts = alertsFor({}, [overdue(40)])
      if (overdueAlerts.notices.length !== 1 || alertsFor(overdueAlerts.known, [overdue(41)]).notices.length !== 0) {
        root.fail("an overdue job would alert again every hour")
        return
      }
      var timerOff = { code: "timer-disabled", message: "Timer is disabled", severity: "critical" }
      var blocked = alertsFor({}, [failedJob("2026-08-17T11:00:00Z", [timerOff])])
      var laterFailure = alertsFor(blocked.known, [failedJob("2026-08-18T11:00:00Z", [timerOff])])
      if (blocked.notices.length !== 1 || laterFailure.notices.length !== 1
          || laterFailure.notices[0][8].indexOf("Timer is disabled") !== -1) {
        root.fail("an open schedule problem hid a later failed run")
        return
      }
      var timerJob = { id: "home", name: "Home", status: "attention", issues: [timerOff] }
      var unverified = { id: "home", name: "Home", status: "unknown", issues: [
        { code: "service-unavailable", message: "Systemd service status is unavailable", severity: "unknown" },
        { code: "timer-unavailable", message: "Systemd timer status is unavailable", severity: "unknown" }
      ] }
      var partial = { id: "home", name: "Home", status: "attention",
        service: { lastRun: { finishedAt: "2026-08-17T11:00:00Z" } },
        issues: [
          { code: "last-run-failed", message: "Backup failed", severity: "critical" },
          { code: "timer-unavailable", message: "Systemd timer status is unavailable", severity: "unknown" }
        ] }
      var timerAlert = alertsFor({}, [timerJob])
      var afterGap = alertsFor(alertsFor(timerAlert.known, [unverified]).known, [timerJob])
      var afterPartial = alertsFor(alertsFor(timerAlert.known, [partial]).known, [timerJob])
      if (timerAlert.notices.length !== 1 || afterGap.notices.length !== 0 || afterPartial.notices.length !== 0) {
        root.fail("an incomplete report made an unchanged problem notify again")
        return
      }
      var degraded = alertsFor(timerAlert.known, [], true)
      if (Object.keys(degraded.known).length !== 1 || Object.keys(alertsFor(timerAlert.known, []).known).length !== 0) {
        root.fail("alert history did not follow report completeness for missing jobs")
        return
      }
      var integrityUnknown = { code: "integrity-unknown", message: "Integrity-check service status is unavailable", severity: "unknown" }
      var timerFixed = alertsFor(timerAlert.known, [{ id: "home", name: "Home", status: "unknown", issues: [integrityUnknown] }])
      var timerBroken = alertsFor(timerFixed.known, [{ id: "home", name: "Home", status: "attention", issues: [timerOff, integrityUnknown] }])
      if (Object.keys(timerFixed.known).length !== 0 || timerBroken.notices.length !== 1) {
        root.fail("an unrelated unknown component kept a repaired alert from rearming")
        return
      }
      var overdueCheck = { code: "integrity-attention", message: "Integrity check is overdue", severity: "warning" }
      var checkJob = function(status, active) {
        return { id: "home", name: "Home", status: status,
          service: { unit: "restic-home.service", active: active },
          integrity: { status: "attention", lastRun: { finishedAt: "2026-08-01T03:00:00Z" } },
          issues: [overdueCheck] }
      }
      var checkAlert = alertsFor({}, [checkJob("attention", false)])
      var duringBackup = alertsFor(checkAlert.known, [checkJob("running", true)])
      var afterBackup = alertsFor(duringBackup.known, [checkJob("attention", false)])
      if (checkAlert.notices.length !== 1 || duringBackup.notices.length !== 0 || afterBackup.notices.length !== 0) {
        root.fail("a running backup made an unresolved integrity alert notify again")
        return
      }
      var retryFailed = alertsFor(first.known, [{ id: "home", name: "Home", status: "attention",
        service: { unit: "restic-home.service", active: true,
          lastRun: { finishedAt: "2026-08-17T11:00:00Z", result: "failed" } },
        issues: [{ code: "last-run-failed", message: "Backup failed", severity: "critical" }] }])
      if (retryFailed.notices.length !== 0 || Object.keys(retryFailed.known).length !== 1) {
        root.fail("a retry in progress changed the alert for the run it is replacing")
        return
      }
      var recovered = alertsFor(timerAlert.known, [{ id: "home", status: "healthy", issues: [] }])
      if (Object.keys(recovered.known).length !== 0) {
        root.fail("a verified recovery did not clear the alert")
        return
      }
      var hostile = alertsFor({}, [{ id: "home", name: "--urgency=critical", status: "attention", issues: [
        { code: "run-overdue", message: "--exec", severity: "critical" }
      ] }]).notices[0]
      var failedRestore = Model.restoreNotice({ type: "error", error: "-u critical" }, "-g", "/home/test")
      if (hostile[7].charAt(0) === "-" || hostile[8].charAt(0) === "-" || failedRestore[8].charAt(0) === "-") {
        root.fail("notification text could be parsed as an option: " + JSON.stringify([hostile, failedRestore]))
        return
      }
      if (Model.browseRoot({ paths: ["/home/test/Documents", "/home/test/Pictures"] }) !== "/home/test"
          || Model.browseRoot({ paths: ["/home/test"] }) !== "/home/test"
          || Model.browseRoot({ paths: ["/home/test", "/etc"] }) !== "/"
          || Model.browseRoot({ paths: ["/home/ab", "/home/abc"] }) !== "/home"
          || Model.browseRoot(null) !== "/") {
        root.fail("browse root did not find the shared backed-up folder")
        return
      }
      if (Model.parentPath("/home/test/notes.md") !== "/home/test" || Model.parentPath("/home") !== "/"
          || Model.baseName("/home/test/notes.md") !== "notes.md") {
        root.fail("path helpers split paths incorrectly")
        return
      }
      if (!Model.exactPath("/home/test/notes.md") || Model.exactPath("/home/test/name-\ufffd")) {
        root.fail("lossy restic paths were treated as exact")
        return
      }
      if (Model.tildePath("/home/test/Restored/a", "/home/test") !== "~/Restored/a"
          || Model.tildePath("/home/tester/a", "/home/test") !== "/home/tester/a"
          || Model.tildePath("/etc/a", "/") !== "/etc/a") {
        root.fail("home paths were not shortened safely")
        return
      }
      if (Model.snapshotLabel({ time: "not a time", shortId: "abcd1234" }) !== "Unknown time · abcd1234") {
        root.fail("an unparseable snapshot time was not labeled")
        return
      }
      var done = Model.restoreNotice({ type: "done", path: "/home/test/Restored/<i>x</i>", folder: "/home/test/Restored" },
        "<i>x</i>", "/home/test")
      if (done[7] !== "Restored <i>x</i>" || done[8] !== "~/Restored/&lt;i&gt;x&lt;/i&gt;"
          || done[done.length - 1] !== "/home/test/Restored") {
        root.fail("restore notice was not built safely: " + JSON.stringify(done))
        return
      }
      var failed = Model.restoreNotice({ type: "error", error: "Wrong repository password" }, "notes.md", "/home/test")
      if (failed[7] !== "Could not restore notes.md" || failed.indexOf("--exec") !== -1) {
        root.fail("failed restore notice was wrong: " + JSON.stringify(failed))
        return
      }
      console.log("model tests passed")
      Qt.quit()
    }
  }

  Timer {
    interval: 5000
    running: true
    repeat: false
    onTriggered: root.fail("model tests did not finish; a check threw before reporting")
  }
}

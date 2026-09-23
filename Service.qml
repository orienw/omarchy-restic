import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string pluginId: manifest && manifest.id
    ? String(manifest.id)
    : "io.github.orienw.restic"
  readonly property var settings: findSettings()
  readonly property string jobsFile: String(setting("jobsFile", "~/.config/omarchy-restic/jobs.json"))
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 60, 15, 3600)
  readonly property int repositoryRefreshMinutes: intSetting("repositoryRefreshMinutes", 15, 1, 1440)
  readonly property int logLines: intSetting("logLines", 12, 1, 100)
  property bool notificationsEnabled: setting("notifications", true) !== false
  readonly property string restoreDirectory: String(setting("restoreDirectory", "~/Restored"))
  readonly property string helperPath: scriptPath("restic_status.py")
  readonly property string browsePath: scriptPath("restic_browse.py")
  readonly property string home: Quickshell.env("HOME") || ""

  property bool initialized: false
  property bool refreshing: false
  property bool forcePending: false
  property bool refreshPending: false
  property string startingJob: ""
  property string actionError: ""
  property var alertedKeys: ({})
  property string restoreState: ""
  property string restoreName: ""
  property real restorePercent: -1
  property string restorePath: ""
  property string restoreFolder: ""
  property string restoreError: ""
  property var report: ({
    schemaVersion: 1,
    generatedAt: null,
    overallStatus: "unknown",
    summary: { jobs: 0, healthy: 0, running: 0, attention: 0, unknown: 0 },
    config: { status: "unknown", error: "" },
    jobs: []
  })
  property double nowMs: Date.now()
  property string _collectorError: ""
  readonly property bool reportStale: !isFinite(Date.parse(generatedAt))
    || nowMs - Date.parse(generatedAt) > Math.max(120, refreshIntervalSec * 2) * 1000
  readonly property string lastError: _collectorError
    || (reportStale && generatedAt !== "" ? "Backup status is out of date" : "")

  readonly property var jobs: report && Array.isArray(report.jobs) ? report.jobs : []
  readonly property string overallStatus: lastError !== "" || reportStale
    ? "unknown" : String(report.overallStatus || "unknown")
  readonly property int attentionJobs: report && report.summary ? Number(report.summary.attention || 0) : 0
  readonly property int runningJobs: report && report.summary ? Number(report.summary.running || 0) : 0
  readonly property int activeJobs: Model.activeCount(jobs)
  readonly property int refreshTimerInterval: activeJobs > 0 ? 10000 : refreshIntervalSec * 1000
  readonly property string generatedAt: report ? String(report.generatedAt || "") : ""
  readonly property string configError: report && report.config ? String(report.config.error || "") : ""

  property string _stdout: ""
  property string _stderr: ""
  property bool _stdoutDone: false
  property bool _stderrDone: false
  property bool _exited: false
  property int _exitCode: 0
  property string _startStderr: ""
  property bool _startStderrDone: false
  property bool _startExited: false
  property int _startExitCode: 0

  function scriptPath(name) {
    return decodeURIComponent(String(Qt.resolvedUrl("scripts/" + name)).replace(/^file:\/\//, ""))
  }

  function findSettings() {
    var config = shell && shell.shellConfig ? shell.shellConfig : null
    var barConfig = shell && shell.barConfig ? shell.barConfig : config && config.bar
    var layout = barConfig && barConfig.layout ? barConfig.layout : null
    var sections = ["left", "center", "right"]
    for (var s = 0; s < sections.length; s++) {
      var widgets = layout && Array.isArray(layout[sections[s]]) ? layout[sections[s]] : []
      for (var w = 0; w < widgets.length; w++) {
        if (widgets[w] && String(widgets[w].id || "") === pluginId) return widgets[w]
      }
    }

    var plugins = config && Array.isArray(config.plugins) ? config.plugins : []
    for (var p = 0; p < plugins.length; p++) {
      if (plugins[p] && String(plugins[p].id || "") === pluginId) return plugins[p]
    }
    return ({})
  }

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, minimum, maximum) {
    var value = Math.floor(Number(setting(name, fallback)))
    if (!isFinite(value)) value = fallback
    return Math.max(minimum, Math.min(maximum, value))
  }

  function initialize() {
    if (initialized || !shell || !manifest) return
    initialized = true
    refresh(false)
  }

  function refresh(forceRepositories) {
    if (!initialized) return "not ready"
    if (collector.running) {
      if (forceRepositories === true) forcePending = true
      return "busy"
    }

    refreshPending = false
    _stdout = ""
    _stderr = ""
    _stdoutDone = false
    _stderrDone = false
    _exited = false
    refreshing = true
    var command = [
      "python3", helperPath,
      "--config", jobsFile,
      "--repository-cache-seconds", String(repositoryRefreshMinutes * 60),
      "--log-lines", String(logLines)
    ]
    if (forceRepositories === true) command.push("--force-repositories")
    collector.command = command
    collector.running = true
    return "started"
  }

  function applyReport(raw) {
    var parsed = Model.parseReport(raw)
    if (!parsed.ok) {
      _collectorError = parsed.error
      return false
    }
    report = parsed.report
    nowMs = Date.now()
    _collectorError = ""
    notifyNewAlerts()
    return true
  }

  function notifyNewAlerts() {
    var degraded = !!(report.config && report.config.error)
    var next = Model.nextAlerts(alertedKeys, jobs, degraded)
    alertedKeys = next.known
    if (!notificationsEnabled) return
    for (var i = 0; i < next.notices.length; i++) Quickshell.execDetached(next.notices[i])
  }

  function elideError(value) {
    var text = String(value || "").replace(/\s+/g, " ").trim()
    return text.length > 240 ? text.substring(0, 237) + "..." : text
  }

  function _finalize() {
    if (!_stdoutDone || !_stderrDone || !_exited) return
    refreshing = false
    if (_exitCode !== 0)
      _collectorError = elideError(_stderr || "Restic status collector failed with exit " + _exitCode)
    else if (!applyReport(_stdout) && _stderr !== "")
      _collectorError = elideError(_stderr)
    if (forcePending || refreshPending) {
      var force = forcePending
      forcePending = false
      Qt.callLater(function() { root.refresh(force) })
    }
  }

  function backupNow(jobId) {
    var job = Model.findJob(jobs, jobId)
    if (!Model.canBackUp(job) || starter.running) return "unavailable"
    actionError = ""
    startingJob = String(job.id)
    _startStderr = ""
    _startStderrDone = false
    _startExited = false
    starter.command = ["systemctl", "--user", "start", "--no-block", "--", Model.backupUnit(job)]
    starter.running = true
    return "started"
  }

  function restoreEntry(jobId, snapshot, entry) {
    if (restorer.running || !snapshot || !entry) return "busy"
    restoreState = "running"
    restoreName = String(entry.name || Model.baseName(entry.path))
    restorePercent = -1
    restorePath = ""
    restoreFolder = ""
    restoreError = ""
    restorer.command = [
      "python3", browsePath, "restore",
      "--config", jobsFile,
      "--job", String(jobId),
      "--snapshot", String(snapshot.id),
      "--snapshot-time", String(snapshot.time || ""),
      "--path", String(entry.path),
      "--type", String(entry.type || "file"),
      "--target-root", restoreDirectory
    ]
    restorer.running = true
    return "started"
  }

  function cancelRestore() {
    if (restorer.running) restorer.signal(15)
  }

  function clearRestore() {
    if (restoreState !== "running") restoreState = ""
  }

  function openRestoreFolder() {
    if (restoreFolder !== "") Quickshell.execDetached(["uwsm-app", "--", "xdg-open", restoreFolder])
  }

  function handleRestoreLine(line) {
    var event
    try {
      event = JSON.parse(line)
    } catch (error) {
      return
    }
    if (!event || restoreState !== "running") return
    if (event.type === "progress") {
      restorePercent = Number(event.percent)
      return
    }
    if (event.type === "done") {
      restorePath = String(event.path || "")
      restoreFolder = String(event.folder || "")
    } else if (event.type === "error") {
      restoreError = elideError(event.error)
    } else if (event.type !== "cancelled") {
      return
    }
    restoreState = event.type
    if (notificationsEnabled && event.type !== "cancelled")
      Quickshell.execDetached(Model.restoreNotice(event, restoreName, home))
  }

  function _finishStart() {
    if (!_startStderrDone || !_startExited) return
    if (_startExitCode !== 0) {
      var job = Model.findJob(jobs, startingJob)
      var name = job ? String(job.name || job.id) : startingJob
      actionError = elideError("Could not start " + name + ": "
        + (_startStderr || "systemctl exited with " + _startExitCode))
    }
    startingJob = ""
    refreshPending = true
    refresh(false)
  }

  onManifestChanged: Qt.callLater(initialize)
  onShellChanged: Qt.callLater(initialize)
  onJobsFileChanged: if (initialized) delayedRefresh.restart()
  onRepositoryRefreshMinutesChanged: if (initialized) delayedRefresh.restart()
  Component.onCompleted: Qt.callLater(initialize)

  Timer {
    interval: root.refreshTimerInterval
    repeat: true
    running: root.initialized
    onTriggered: root.refresh(false)
  }

  Timer {
    interval: 15000
    repeat: true
    running: root.generatedAt !== ""
    onTriggered: root.nowMs = Date.now()
  }

  Timer {
    id: delayedRefresh
    interval: 100
    repeat: false
    onTriggered: root.refresh(false)
  }

  Process {
    id: collector
    running: false
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root._stdout = text
        root._stdoutDone = true
        root._finalize()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root._stderr = text
        root._stderrDone = true
        root._finalize()
      }
    }
    onExited: function(exitCode) {
      root._exitCode = exitCode
      root._exited = true
      root._finalize()
    }
  }

  Process {
    id: restorer
    running: false
    command: []
    stdout: SplitParser {
      onRead: function(line) { root.handleRestoreLine(line) }
    }
    // The final JSON line can arrive after the exit signal, so give the
    // parser a moment before calling a silent exit a failure.
    onExited: restoreExitCheck.restart()
  }

  Timer {
    id: restoreExitCheck
    interval: 500
    repeat: false
    onTriggered: {
      if (root.restoreState !== "running" || restorer.running) return
      root.restoreState = "error"
      root.restoreError = "Restore stopped unexpectedly"
    }
  }

  Process {
    id: starter
    running: false
    command: []
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root._startStderr = text
        root._startStderrDone = true
        root._finishStart()
      }
    }
    onExited: function(exitCode) {
      root._startExitCode = exitCode
      root._startExited = true
      root._finishStart()
    }
  }
}

import QtQuick
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string pluginId: manifest && manifest.id
    ? String(manifest.id)
    : "io.github.orienw.restic"
  readonly property string sourceDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : ""
  readonly property var settings: findSettings()
  readonly property string jobsFile: String(setting("jobsFile", "~/.config/omarchy-restic/jobs.json"))
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 60, 15, 3600)
  readonly property int repositoryRefreshMinutes: intSetting("repositoryRefreshMinutes", 15, 1, 1440)
  readonly property int logLines: intSetting("logLines", 12, 1, 100)
  readonly property string helperPath: sourceDir + "/scripts/restic_status.py"

  property bool initialized: false
  property bool refreshing: false
  property bool forcePending: false
  property var report: ({
    schemaVersion: 1,
    generatedAt: null,
    overallStatus: "unknown",
    summary: { jobs: 0, healthy: 0, running: 0, attention: 0, unknown: 0 },
    config: { status: "unknown", error: "" },
    jobs: []
  })
  property string lastError: ""

  readonly property var jobs: report && Array.isArray(report.jobs) ? report.jobs : []
  readonly property string overallStatus: report ? String(report.overallStatus || "unknown") : "unknown"
  readonly property int attentionJobs: report && report.summary ? Number(report.summary.attention || 0) : 0
  readonly property int runningJobs: report && report.summary ? Number(report.summary.running || 0) : 0
  readonly property string generatedAt: report ? String(report.generatedAt || "") : ""
  readonly property string configError: report && report.config ? String(report.config.error || "") : ""

  property string _stdout: ""
  property string _stderr: ""
  property bool _stdoutDone: false
  property bool _exited: false
  property int _exitCode: 0

  function findSettings() {
    var config = shell && shell.shellConfig ? shell.shellConfig : null
    var layout = config && config.bar && config.bar.layout ? config.bar.layout : null
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
    if (initialized || !shell || sourceDir === "") return
    initialized = true
    refresh(false)
  }

  function refresh(forceRepositories) {
    if (!initialized || helperPath === "/scripts/restic_status.py") return "not ready"
    if (collector.running) {
      if (forceRepositories === true) forcePending = true
      return "busy"
    }

    _stdout = ""
    _stderr = ""
    _stdoutDone = false
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
      lastError = parsed.error
      return false
    }
    report = parsed.report
    lastError = ""
    return true
  }

  function elideError(value) {
    var text = String(value || "").replace(/\s+/g, " ").trim()
    return text.length > 240 ? text.substring(0, 237) + "..." : text
  }

  function _finalize() {
    if (!_stdoutDone || !_exited) return
    refreshing = false
    if (!applyReport(_stdout))
      lastError = elideError(_stderr || _stdout || "Restic status collector failed with exit " + _exitCode)
    if (forcePending) {
      forcePending = false
      Qt.callLater(function() { root.refresh(true) })
    }
  }

  onManifestChanged: Qt.callLater(initialize)
  onShellChanged: Qt.callLater(initialize)
  onJobsFileChanged: if (initialized) delayedRefresh.restart()
  onRepositoryRefreshMinutesChanged: if (initialized) delayedRefresh.restart()
  Component.onCompleted: Qt.callLater(initialize)

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: root.initialized
    onTriggered: root.refresh(false)
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
      onStreamFinished: root._stderr = text
    }
    onExited: function(exitCode) {
      root._exitCode = exitCode
      root._exited = true
      root._finalize()
    }
  }
}

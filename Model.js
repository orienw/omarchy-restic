.pragma library

function parseReport(raw) {
  var text = String(raw || "").trim()
  if (text === "") return { ok: false, error: "Collector returned no data" }
  try {
    var report = JSON.parse(text)
    if (!report || report.schemaVersion !== 1 || !Array.isArray(report.jobs))
      return { ok: false, error: "Collector returned an unsupported report" }
    return { ok: true, report: report }
  } catch (error) {
    return { ok: false, error: "Collector returned invalid JSON" }
  }
}

function statusLabel(status) {
  if (status === "healthy") return "All jobs healthy"
  if (status === "running") return "Backup in progress"
  if (status === "attention") return "Backup needs attention"
  return "Backup status unknown"
}

function statusGlyph(status) {
  if (status === "healthy") return "󰄬"
  if (status === "running") return "󰑐"
  if (status === "attention") return "󰅚"
  return "󰋗"
}

function relativeTime(value, nowMs) {
  if (!value) return "Never"
  var timestamp = Date.parse(String(value))
  if (!isFinite(timestamp)) return "Unknown"
  var delta = Math.max(0, (Number(nowMs || Date.now()) - timestamp) / 1000)
  if (delta < 45) return "Just now"
  if (delta < 90) return "1 minute ago"
  if (delta < 3600) return Math.round(delta / 60) + " minutes ago"
  if (delta < 5400) return "1 hour ago"
  if (delta < 86400) return Math.round(delta / 3600) + " hours ago"
  if (delta < 129600) return "1 day ago"
  if (delta < 604800) return Math.round(delta / 86400) + " days ago"
  if (delta < 1209600) return "1 week ago"
  return Math.floor(delta / 604800) + " weeks ago"
}

function futureTime(value, nowMs) {
  if (!value) return "Not scheduled"
  var timestamp = Date.parse(String(value))
  if (!isFinite(timestamp)) return "Unknown"
  var delta = (timestamp - Number(nowMs || Date.now())) / 1000
  if (delta <= 0) return "Due now"
  if (delta < 90) return "In 1 minute"
  if (delta < 3600) return "In " + Math.ceil(delta / 60) + " minutes"
  if (delta < 5400) return "In 1 hour"
  if (delta < 86400) return "In " + Math.round(delta / 3600) + " hours"
  if (delta < 129600) return "In 1 day"
  return "In " + Math.round(delta / 86400) + " days"
}

function formatBytes(value) {
  var bytes = Number(value)
  if (!isFinite(bytes) || bytes < 0) return "Unknown"
  var rounded = Math.round(bytes)
  if (rounded < 1024) return rounded + " B"
  var units = ["KiB", "MiB", "GiB", "TiB", "PiB"]
  var size = bytes
  var index = -1
  do {
    size /= 1024
    index++
  } while (size >= 1024 && index < units.length - 1)
  var digits = size >= 100 ? 0 : size >= 10 ? 1 : 2
  return size.toFixed(digits) + " " + units[index]
}

function formatDuration(value) {
  var seconds = Number(value)
  if (!isFinite(seconds) || seconds < 0) return "Unknown"
  if (seconds < 60) return Math.max(1, Math.round(seconds)) + "s"
  var totalMinutes = Math.round(seconds / 60)
  if (totalMinutes < 60) return totalMinutes + "m"
  var hours = Math.floor(totalMinutes / 60)
  return hours + "h " + (totalMinutes % 60) + "m"
}

function count(value, singular, plural) {
  var amount = Number(value)
  if (!isFinite(amount)) return "Unknown"
  return Math.round(amount).toLocaleString() + " " + (amount === 1 ? singular : plural)
}

function lastRun(job, nowMs) {
  var service = job && job.service ? job.service : null
  var run = service && service.lastRun ? service.lastRun : null
  if (!run) return "No completed run"
  var text = relativeTime(run.finishedAt, nowMs)
  if (run.durationSec !== null && run.durationSec !== undefined)
    text += " · " + formatDuration(run.durationSec)
  return text
}

function nextRun(job, nowMs) {
  return futureTime(job && job.timer ? job.timer.nextRunAt : null, nowMs)
}

function latestSnapshot(job, nowMs) {
  var repository = job && job.repository ? job.repository : null
  var snapshot = repository && repository.latestSnapshot ? repository.latestSnapshot : null
  if (!snapshot) return "No snapshot found"
  var text = relativeTime(snapshot.time, nowMs)
  if (snapshot.id) text += " · " + snapshot.id
  return text
}

function snapshotSummary(job) {
  var repository = job && job.repository ? job.repository : null
  var snapshot = repository && repository.latestSnapshot ? repository.latestSnapshot : null
  var summary = snapshot && snapshot.summary ? snapshot.summary : null
  if (!summary || summary.totalFilesProcessed === undefined) return ""
  var parts = [count(summary.totalFilesProcessed, "file", "files")]
  if (summary.totalBytesProcessed !== undefined) parts.push(formatBytes(summary.totalBytesProcessed) + " scanned")
  if (summary.dataAdded !== undefined) parts.push(formatBytes(summary.dataAdded) + " added")
  return parts.join(" · ")
}

function rawData(job) {
  var stats = job && job.repository && job.repository.stats ? job.repository.stats : null
  return stats && stats.totalSize !== undefined ? formatBytes(stats.totalSize) : "Not available"
}

function snapshotCount(job) {
  var repository = job && job.repository ? job.repository : null
  return count(repository ? repository.snapshotCount : 0, "snapshot", "snapshots")
}

function repositoryState(job, nowMs) {
  var repository = job && job.repository ? job.repository : null
  if (!repository) return "Not configured"
  if (repository.status === "deferred") return "Deferred while backup runs"
  if (repository.status === "busy") return "Repository busy"
  if (repository.status === "unavailable") return "Unavailable"
  if (repository.status === "stale") return "Cached, refresh failed"
  if (repository.status === "partial") return "Snapshots ready, stats unavailable"
  return repository.checkedAt ? "Checked " + relativeTime(repository.checkedAt, nowMs).toLowerCase() : "Ready"
}

function integrityState(job, nowMs) {
  var integrity = job && job.integrity ? job.integrity : null
  if (!integrity || integrity.status === "not-configured") return "Not configured"
  if (integrity.status === "running") return "Check in progress"
  if (integrity.status === "unknown") return String(integrity.statusText || "Check status unavailable")
  if (integrity.status === "attention") return String(integrity.statusText || "Needs attention")
  if (!integrity.lastSuccessAt) return String(integrity.statusText || "No completed check")
  return "Passed " + relativeTime(integrity.lastSuccessAt, nowMs).toLowerCase()
}

function detailsDefaultOpen(job) {
  if (!job) return false
  if (job.status === "attention") return true
  var repository = job.repository ? job.repository : null
  if (repository) {
    var repositoryStatus = String(repository.status || "")
    if (repositoryStatus === "unavailable" || repositoryStatus === "stale") return true
  }
  var integrity = job.integrity ? job.integrity : null
  return !!(integrity && integrity.status === "attention")
}

function reportMeta(report, refreshing) {
  if (!report || !report.summary || !report.generatedAt)
    return refreshing ? "Refreshing status" : "Waiting for status"
  var jobs = Number(report.summary.jobs || 0)
  if (jobs === 0) return "No Restic jobs found"
  if (report.overallStatus === "attention")
    return count(report.summary.attention, "job", "jobs") + " need attention"
  if (report.overallStatus === "running")
    return count(report.summary.running, "job", "jobs") + " running"
  if (report.overallStatus === "healthy")
    return count(jobs, "job", "jobs") + " checked"
  return "Verification incomplete"
}

function findJob(jobs, jobId) {
  var list = Array.isArray(jobs) ? jobs : []
  for (var i = 0; i < list.length; i++) {
    if (list[i] && String(list[i].id || "") === String(jobId)) return list[i]
  }
  return null
}

function backupUnit(job) {
  var unit = job && job.service ? String(job.service.unit || "") : ""
  return /^[A-Za-z0-9_.:@-]+\.service$/.test(unit) ? unit : ""
}

function canBackUp(job) {
  return backupUnit(job) !== "" && job.status !== "running" && !(job.service && job.service.active)
}

function alertIssue(job) {
  if (!job || job.status !== "attention") return null
  var issues = Array.isArray(job.issues) ? job.issues : []
  for (var i = 0; i < issues.length; i++) {
    var entry = issues[i]
    if (!entry || String(entry.code || "").indexOf("repository-") === 0) continue
    if (entry.severity === "critical" || entry.severity === "warning") return entry
  }
  return null
}

// Identifies one alert-worthy problem, so a job alerts once per failed run
// or new problem rather than on every refresh while it stays broken.
function alertKey(job) {
  var entry = alertIssue(job)
  if (!entry) return ""
  var run = null
  if (entry.code === "last-run-failed") run = job.service ? job.service.lastRun : null
  else if (entry.code === "integrity-attention") run = job.integrity ? job.integrity.lastRun : null
  return [job.id, entry.code, run && run.finishedAt ? run.finishedAt : ""].join("|")
}

function alertCommand(job) {
  var entry = alertIssue(job)
  if (!entry) return []
  var command = [
    "omarchy-notification-send", "--app-name", "Restic", "-g", "󰁯", "-u", "normal",
    String(job.name || job.id) + " backup needs attention",
    // Notification bodies are StyledText, summaries are plain.
    escapeMarkup(entry.message || job.statusText || "Open the Restic panel for details")
  ]
  var unit = backupUnit(job)
  if (unit !== "")
    command.push("--exec", "uwsm-app", "--", "xdg-terminal-exec",
      "journalctl", "--user", "--unit", unit, "--pager-end")
  return command
}

function escapeMarkup(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

function parentPath(path) {
  var value = String(path || "/")
  var index = value.lastIndexOf("/")
  return index <= 0 ? "/" : value.substring(0, index)
}

// Undecodable filename bytes arrive from restic as U+FFFD, so the path may
// name a different file than the one shown.
function exactPath(path) {
  return String(path || "").indexOf("\ufffd") === -1
}

function baseName(path) {
  var value = String(path || "")
  return value.substring(value.lastIndexOf("/") + 1)
}

// The deepest folder shared by every backed-up path, so browsing starts
// where the files are rather than at the filesystem root.
function browseRoot(snapshot) {
  var paths = snapshot && Array.isArray(snapshot.paths) ? snapshot.paths : []
  if (paths.length === 0) return "/"
  var common = String(paths[0]).split("/")
  for (var i = 1; i < paths.length; i++) {
    var parts = String(paths[i]).split("/")
    var shared = 0
    while (shared < common.length && shared < parts.length && common[shared] === parts[shared]) shared++
    common = common.slice(0, shared)
  }
  var root = common.join("/")
  return root.charAt(0) === "/" && root.length > 1 ? root : "/"
}

function snapshotLabel(snapshot) {
  if (!snapshot) return "No snapshot"
  var time = new Date(Date.parse(String(snapshot.time || "")))
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  var pad = function(value) { return value < 10 ? "0" + value : String(value) }
  var label = isFinite(time.getTime())
    ? months[time.getMonth()] + " " + time.getDate() + ", " + time.getFullYear()
      + " " + pad(time.getHours()) + ":" + pad(time.getMinutes())
    : "Unknown time"
  return label + " · " + String(snapshot.shortId || "")
}

function tildePath(path, home) {
  var value = String(path || "")
  var prefix = String(home || "")
  if (prefix === "" || prefix === "/") return value
  if (value === prefix) return "~"
  return value.indexOf(prefix + "/") === 0 ? "~" + value.substring(prefix.length) : value
}

function restoreNotice(event, name, home) {
  var base = ["omarchy-notification-send", "--app-name", "Restic", "-g", "󰁯", "-u", "normal"]
  if (event.type === "done")
    return base.concat([
      "Restored " + name,
      escapeMarkup(tildePath(event.path, home)),
      "--exec", "uwsm-app", "--", "xdg-open", String(event.folder)
    ])
  return base.concat(["Could not restore " + name, escapeMarkup(event.error || "Restore failed")])
}

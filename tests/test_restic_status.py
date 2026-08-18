from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import restic_status  # noqa: E402


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def epoch(value: datetime) -> float:
    return value.timestamp()


def journal_entry(
    timestamp: datetime,
    message_id: str | None,
    message: str,
    invocation: str = "run-1",
    result: str | None = None,
) -> str:
    entry = {
        "__REALTIME_TIMESTAMP": str(int(timestamp.timestamp() * 1_000_000)),
        "USER_INVOCATION_ID": invocation,
        "MESSAGE": message,
    }
    if message_id:
        entry["MESSAGE_ID"] = message_id
    if result:
        entry["JOB_RESULT"] = result
    return json.dumps(entry)


class FakeRunner:
    def __init__(
        self,
        *,
        active: bool = False,
        failed: bool = False,
        restic_error: int = 0,
        stats_error: bool = False,
        systemd_unavailable: bool = False,
    ):
        self.active = active
        self.failed = failed
        self.restic_error = restic_error
        self.stats_error = stats_error
        self.systemd_unavailable = systemd_unavailable
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(self, command: list[str], *, timeout: int, env: dict[str, str] | None = None):
        self.calls.append((command, env))
        if command[0] == "systemctl":
            unit = command[command.index("show") + 1]
            return self._systemctl(command, unit)
        if command[0] == "journalctl":
            if any(argument.startswith("USER_UNIT=") for argument in command):
                return self._history()
            return self._tail()
        if "snapshots" in command:
            return self._snapshots()
        if "stats" in command:
            if self.stats_error:
                return subprocess.CompletedProcess(command, 1, "", "stats unavailable")
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "total_size": 987654321,
                "total_file_count": 321,
                "snapshots_count": 2,
                "compression_ratio": 1.42,
            }), "")
        raise AssertionError(f"Unexpected command: {command}")

    def _systemctl(self, command: list[str], unit: str):
        if self.systemd_unavailable:
            return subprocess.CompletedProcess(command, 1, "", "Failed to connect to user bus")
        if unit.endswith(".timer"):
            output = "\n".join([
                "LoadState=loaded",
                "ActiveState=active",
                "SubState=waiting",
                "UnitFileState=enabled",
                f"LastTriggerUSec=@{epoch(NOW - timedelta(hours=1))}",
                f"NextElapseUSecRealtime=@{epoch(NOW + timedelta(hours=23))}",
                "Persistent=yes",
            ])
            return subprocess.CompletedProcess(command, 0, output, "")

        output = "\n".join([
            "LoadState=loaded",
            f"ActiveState={'active' if self.active else 'inactive'}",
            f"SubState={'running' if self.active else 'dead'}",
            f"Result={'exit-code' if self.failed else 'success'}",
            "ExecMainCode=exited",
            f"ExecMainStatus={1 if self.failed else 0}",
            f"ExecMainStartTimestamp=@{epoch(NOW - timedelta(hours=1, minutes=2))}",
            f"ExecMainExitTimestamp={' ' if self.active else '@' + str(epoch(NOW - timedelta(hours=1)))}",
        ])
        return subprocess.CompletedProcess(command, 0, output, "")

    def _history(self):
        started = journal_entry(
            NOW - timedelta(hours=1, minutes=2),
            restic_status.UNIT_STARTING,
            "Starting backup",
        )
        if self.active:
            return subprocess.CompletedProcess([], 0, started + "\n", "")
        if self.failed:
            finished = journal_entry(
                NOW - timedelta(hours=1),
                restic_status.UNIT_FAILED,
                "Backup failed",
                result="failed",
            )
        else:
            finished = journal_entry(
                NOW - timedelta(hours=1),
                restic_status.UNIT_STARTED,
                "Backup completed",
                result="done",
            )
        return subprocess.CompletedProcess([], 0, started + "\n" + finished + "\n", "")

    def _tail(self):
        output = "\n".join([
            json.dumps({"MESSAGE": "repository password=super-secret"}),
            json.dumps({"MESSAGE": "request to https://alice:hunter2@backup.example failed"}),
        ])
        return subprocess.CompletedProcess([], 0, output, "")

    def _snapshots(self):
        if self.restic_error:
            return subprocess.CompletedProcess([], self.restic_error, "", "repository unavailable")
        snapshots = [
            {
                "id": "a" * 64,
                "short_id": "aaaaaaaa",
                "time": "2026-08-01T03:30:00Z",
                "hostname": "forge",
                "paths": ["/home/test"],
                "tags": ["home"],
            },
            {
                "id": "b" * 64,
                "short_id": "bbbbbbbb",
                "time": "2026-08-07T03:30:00Z",
                "hostname": "forge",
                "paths": ["/home/test"],
                "tags": ["home"],
                "summary": {
                    "files_new": 5,
                    "files_changed": 2,
                    "total_files_processed": 100,
                    "total_bytes_processed": 4096,
                    "data_added": 512,
                    "total_duration": 4.25,
                },
            },
        ]
        return subprocess.CompletedProcess([], 0, json.dumps(snapshots), "")


class DiscoveryRunner(FakeRunner):
    def __init__(self, script: Path):
        super().__init__()
        self.script = script

    def run(self, command: list[str], *, timeout: int, env: dict[str, str] | None = None):
        if command[0] == "systemctl" and "list-unit-files" in command:
            self.calls.append((command, env))
            output = "\n".join([
                "restic-documents.timer enabled enabled",
                "systemd-tmpfiles-clean.timer enabled enabled",
            ])
            return subprocess.CompletedProcess(command, 0, output, "")

        if command[0] == "systemctl" and "show" in command:
            unit = command[command.index("show") + 1]
            properties = {
                command[index + 1]
                for index, argument in enumerate(command[:-1])
                if argument == "--property"
            }
            if properties == {"Triggers"}:
                self.calls.append((command, env))
                trigger = (
                    "restic-documents.service"
                    if unit == "restic-documents.timer"
                    else "systemd-tmpfiles-clean.service"
                )
                return subprocess.CompletedProcess(command, 0, f"Triggers={trigger}\n", "")
            if "Description" in properties:
                self.calls.append((command, env))
                if unit == "restic-documents.service":
                    output = "\n".join([
                        "LoadState=loaded",
                        "Description=restic backup of Documents to archive",
                        "FragmentPath=",
                        f"ExecStart={{ path={self.script} ; argv[]={self.script} ; ignore_errors=no ; }}",
                        "Environment=",
                        "EnvironmentFiles=",
                    ])
                else:
                    output = "\n".join([
                        "LoadState=loaded",
                        "Description=Daily temporary-file cleanup",
                        "FragmentPath=",
                        "ExecStart={ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no ; }",
                        "Environment=",
                        "EnvironmentFiles=",
                    ])
                return subprocess.CompletedProcess(command, 0, output, "")

        return super().run(command, timeout=timeout, env=env)


class ResticStatusTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.password = self.root / "password"
        self.repository.write_text("rest:http://backup.example/home\n", encoding="utf-8")
        self.password.write_text("secret\n", encoding="utf-8")
        self.config = self.root / "jobs.json"
        self.config.write_text(json.dumps({
            "schemaVersion": 1,
            "jobs": [{
                "id": "home",
                "name": "Home",
                "service": "restic-home.service",
                "timer": "restic-home.timer",
                "repositoryFile": str(self.repository),
                "passwordFile": str(self.password),
                "restic": sys.executable,
                "tag": "home",
                "maxRunAgeHours": 36,
            }],
        }), encoding="utf-8")
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temporary.cleanup()

    def collect(self, runner: FakeRunner, *, force: bool = False):
        return restic_status.collect_report(
            self.config,
            cache_dir=self.cache,
            cache_seconds=900,
            force=force,
            log_lines=10,
            timeout=30,
            runner=runner,
            now=NOW,
        )

    def test_successful_unchanged_run_is_healthy_even_with_old_snapshot(self):
        runner = FakeRunner()
        report = self.collect(runner)
        job = report["jobs"][0]

        self.assertEqual(report["overallStatus"], "healthy")
        self.assertEqual(job["service"]["lastRun"]["result"], "success")
        self.assertEqual(job["repository"]["snapshotCount"], 2)
        self.assertEqual(job["repository"]["latestSnapshot"]["id"], "bbbbbbbb")
        self.assertEqual(job["repository"]["latestSnapshot"]["summary"]["dataAdded"], 512)
        self.assertEqual(job["repository"]["stats"]["totalSize"], 987654321)
        self.assertFalse(any("--no-lock" in command for command, _ in runner.calls))

        snapshot_call = next(call for call in runner.calls if "snapshots" in call[0])
        self.assertIn("--repository-file", snapshot_call[0])
        self.assertIn("--password-file", snapshot_call[0])
        self.assertIn("--cache-dir", snapshot_call[0])
        self.assertNotIn("RESTIC_PASSWORD", snapshot_call[1])
        self.assertFalse(any("list-unit-files" in call[0] for call in runner.calls))

    def test_missing_config_discovers_user_systemd_restic_jobs(self):
        script = self.root / "documents-backup.sh"
        script.write_text(
            "\n".join([
                "#!/bin/bash",
                f'export RESTIC_REPOSITORY_FILE="{self.repository}"',
                f'export RESTIC_PASSWORD_FILE="{self.password}"',
                f'RESTIC="{sys.executable}"',
                '"$RESTIC" backup --tag documents /home/test',
            ]),
            encoding="utf-8",
        )
        runner = DiscoveryRunner(script)
        report = restic_status.collect_report(
            self.root / "missing.json",
            cache_dir=self.cache,
            cache_seconds=900,
            force=False,
            log_lines=10,
            timeout=30,
            runner=runner,
            now=NOW,
        )

        self.assertEqual(report["config"]["status"], "discovered")
        self.assertEqual(report["config"]["source"], "systemd")
        self.assertEqual(report["overallStatus"], "healthy")
        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "documents")
        self.assertEqual(report["jobs"][0]["name"], "Documents")
        self.assertEqual(report["jobs"][0]["source"], "systemd")
        self.assertEqual(report["jobs"][0]["repository"]["snapshotCount"], 2)
        snapshot_call = next(call for call in runner.calls if "snapshots" in call[0])
        self.assertIn("documents", snapshot_call[0])

    def test_active_backup_defers_repository_refresh(self):
        self.collect(FakeRunner())
        runner = FakeRunner(active=True)
        report = self.collect(runner, force=True)
        job = report["jobs"][0]

        self.assertEqual(report["overallStatus"], "running")
        self.assertEqual(job["repository"]["status"], "deferred")
        self.assertEqual(job["repository"]["snapshotCount"], 2)
        self.assertFalse(any("snapshots" in command or "stats" in command for command, _ in runner.calls))

    def test_failed_run_is_attention_and_logs_are_redacted(self):
        report = self.collect(FakeRunner(failed=True))
        job = report["jobs"][0]

        self.assertEqual(report["overallStatus"], "attention")
        self.assertEqual(job["service"]["lastRun"]["result"], "failed")
        self.assertTrue(any(issue["code"] == "last-run-failed" for issue in job["issues"]))
        combined = " ".join(job["logTail"])
        self.assertNotIn("super-secret", combined)
        self.assertNotIn("hunter2", combined)
        self.assertIn("[redacted]", combined)

    def test_failed_refresh_keeps_cached_repository_data(self):
        self.collect(FakeRunner())
        report = self.collect(FakeRunner(restic_error=1), force=True)
        repository = report["jobs"][0]["repository"]

        self.assertEqual(report["overallStatus"], "attention")
        self.assertEqual(repository["status"], "stale")
        self.assertEqual(repository["source"], "cache")
        self.assertEqual(repository["snapshotCount"], 2)
        self.assertEqual(report["jobs"][0]["logTail"], [])

        cached_runner = FakeRunner()
        cached_report = self.collect(cached_runner)
        self.assertEqual(cached_report["jobs"][0]["repository"]["status"], "stale")
        self.assertFalse(any("snapshots" in command or "stats" in command for command, _ in cached_runner.calls))

    def test_partial_stats_failure_persists_in_fresh_cache(self):
        report = self.collect(FakeRunner(stats_error=True))
        self.assertEqual(report["jobs"][0]["repository"]["status"], "partial")

        cached_runner = FakeRunner()
        cached_report = self.collect(cached_runner)
        self.assertEqual(cached_report["jobs"][0]["repository"]["status"], "partial")
        self.assertIn("stats unavailable", cached_report["jobs"][0]["repository"]["error"])
        self.assertFalse(any("snapshots" in command or "stats" in command for command, _ in cached_runner.calls))

    def test_unavailable_systemd_is_unknown_not_a_failure(self):
        report = self.collect(FakeRunner(systemd_unavailable=True))
        job = report["jobs"][0]

        self.assertEqual(report["overallStatus"], "unknown")
        self.assertEqual(job["status"], "unknown")
        self.assertEqual(job["repository"]["status"], "ready")
        self.assertTrue(all(issue["severity"] == "unknown" for issue in job["issues"]))

    def test_configured_integrity_service_is_reported_separately(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["jobs"][0].update({
            "checkService": "restic-home-check.service",
            "checkTimer": "restic-home-check.timer",
            "checkMaxAgeHours": 720,
        })
        self.config.write_text(json.dumps(config), encoding="utf-8")

        report = self.collect(FakeRunner())
        integrity = report["jobs"][0]["integrity"]
        self.assertEqual(report["overallStatus"], "healthy")
        self.assertEqual(integrity["status"], "healthy")
        self.assertEqual(integrity["statusText"], "Last integrity check passed")

    def test_invalid_config_returns_an_unknown_report(self):
        self.config.write_text('{"schemaVersion":1,"jobs":[]}', encoding="utf-8")
        report = self.collect(FakeRunner())

        self.assertEqual(report["overallStatus"], "unknown")
        self.assertEqual(report["summary"]["jobs"], 0)
        self.assertEqual(report["config"]["status"], "error")
        self.assertIn("at least one job", report["config"]["error"])


if __name__ == "__main__":
    unittest.main()

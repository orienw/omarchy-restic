from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


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
        monotonic_timer: str = "",
        duplicate_completion: bool = False,
        history_age_hours: float = 1,
        last_trigger_age_hours: float = 1,
        next_run_hours: float = 23,
        no_history: bool = False,
        calendar: str = "daily UTC",
    ):
        self.active = active
        self.failed = failed
        self.restic_error = restic_error
        self.stats_error = stats_error
        self.systemd_unavailable = systemd_unavailable
        self.monotonic_timer = monotonic_timer
        self.duplicate_completion = duplicate_completion
        self.history_age_hours = history_age_hours
        self.last_trigger_age_hours = last_trigger_age_hours
        self.next_run_hours = next_run_hours
        self.no_history = no_history
        self.calendar = calendar
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(self, command: list[str], *, timeout: int, env: dict[str, str] | None = None):
        self.calls.append((command, env))
        if command[0] == "systemd-analyze":
            return subprocess.run(command, timeout=timeout, env=env, text=True, capture_output=True)
        if command[0] == "systemctl":
            if "list-unit-files" in command:
                return subprocess.CompletedProcess(command, 0, "", "")
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
                f"LastTriggerUSec=@{epoch(NOW - timedelta(hours=self.last_trigger_age_hours))}",
                (
                    "NextElapseUSecRealtime="
                    if self.monotonic_timer
                    else f"NextElapseUSecRealtime=@{epoch(NOW + timedelta(hours=self.next_run_hours))}"
                ),
                f"NextElapseUSecMonotonic={self.monotonic_timer}",
                "Persistent=yes",
                "WakeSystem=no",
                f"TimersCalendar={{ OnCalendar={self.calendar} ; next_elapse=n/a }}" if self.calendar else "TimersCalendar=",
            ])
            return subprocess.CompletedProcess(command, 0, output, "")

        if self.no_history:
            start_timestamp = ""
            exit_timestamp = ""
        else:
            start_timestamp = f"@{epoch(NOW - timedelta(hours=self.history_age_hours, minutes=2))}"
            exit_timestamp = " " if self.active else f"@{epoch(NOW - timedelta(hours=self.history_age_hours))}"
        output = "\n".join([
            "LoadState=loaded",
            f"ActiveState={'active' if self.active else 'inactive'}",
            f"SubState={'running' if self.active else 'dead'}",
            f"Result={'exit-code' if self.failed else 'success'}",
            "ExecMainCode=exited",
            f"ExecMainStatus={1 if self.failed else 0}",
            f"ExecMainStartTimestamp={start_timestamp}",
            f"ExecMainExitTimestamp={exit_timestamp}",
        ])
        return subprocess.CompletedProcess(command, 0, output, "")

    def _history(self):
        if self.no_history:
            return subprocess.CompletedProcess([], 0, "", "")
        started = journal_entry(
            NOW - timedelta(hours=self.history_age_hours, minutes=2),
            restic_status.UNIT_STARTING,
            "Starting backup",
        )
        if self.active:
            return subprocess.CompletedProcess([], 0, started + "\n", "")
        if self.failed:
            finished = journal_entry(
                NOW - timedelta(hours=self.history_age_hours),
                restic_status.UNIT_FAILED,
                "Backup failed",
                result="failed",
            )
        else:
            finished = journal_entry(
                NOW - timedelta(hours=self.history_age_hours),
                restic_status.UNIT_STARTED,
                "Backup completed",
                result="done",
            )
        output = started + "\n" + finished + "\n"
        if self.duplicate_completion and not self.failed:
            output += journal_entry(
                NOW - timedelta(minutes=59),
                restic_status.UNIT_SUCCESS,
                "Backup deactivated",
                result="done",
            ) + "\n"
        return subprocess.CompletedProcess([], 0, output, "")

    def _tail(self):
        output = "\n".join([
            json.dumps({"MESSAGE": "repository password=super-secret"}),
            json.dumps({"MESSAGE": "request to https://alice:hunter2@backup.example failed"}),
            json.dumps({"MESSAGE": "AWS_SECRET_ACCESS_KEY=aws-secret"}),
            json.dumps({"MESSAGE": "B2_ACCOUNT_KEY: b2-secret"}),
            json.dumps({"MESSAGE": "Authorization: Bearer bearer-secret"}),
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
    def __init__(
        self,
        script: Path,
        *,
        description: str = "restic backup of Documents to archive",
        list_error: bool = False,
        empty_shows: bool = False,
        show_error: bool = False,
    ):
        super().__init__()
        self.script = script
        self.description = description
        self.list_error = list_error
        self.empty_shows = empty_shows
        self.show_error = show_error

    def run(self, command: list[str], *, timeout: int, env: dict[str, str] | None = None):
        if command[0] == "systemctl" and "list-unit-files" in command:
            self.calls.append((command, env))
            if self.list_error:
                return subprocess.CompletedProcess(command, 1, "", "Failed to connect to user bus")
            output = "\n".join([
                "restic-documents.timer enabled enabled",
                "systemd-tmpfiles-clean.timer enabled enabled",
            ])
            return subprocess.CompletedProcess(command, 0, output, "")

        if command[0] == "systemctl" and "show" in command:
            if self.empty_shows:
                self.calls.append((command, env))
                return subprocess.CompletedProcess(command, 0, "", "")
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
                if self.show_error and unit == "restic-documents.service":
                    return subprocess.CompletedProcess(command, 1, "", "Failed to get properties")
                if unit == "restic-documents.service":
                    output = "\n".join([
                        "LoadState=loaded",
                        f"Description={self.description}",
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

    def collect(
        self,
        runner: FakeRunner,
        *,
        force: bool = False,
        config_path: Path | None = None,
        now: datetime = NOW,
    ):
        return restic_status.collect_report(
            config_path if config_path is not None else self.config,
            cache_dir=self.cache,
            cache_seconds=900,
            force=force,
            log_lines=10,
            timeout=30,
            runner=runner,
            now=now,
        )

    def discovery_script(self) -> Path:
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
        return script

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
        self.assertTrue(any("list-unit-files" in call[0] for call in runner.calls))

    def test_missing_config_discovers_user_systemd_restic_jobs(self):
        script = self.discovery_script()
        runner = DiscoveryRunner(script)
        report = self.collect(
            runner,
            config_path=self.root / "missing.json",
        )

        self.assertEqual(report["config"]["status"], "discovered")
        self.assertEqual(report["config"]["source"], "systemd")
        self.assertEqual(report["overallStatus"], "healthy")
        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "restic-documents")
        self.assertEqual(report["jobs"][0]["name"], "Documents")
        self.assertEqual(report["jobs"][0]["source"], "systemd")
        self.assertEqual(report["jobs"][0]["repository"]["snapshotCount"], 2)
        snapshot_call = next(call for call in runner.calls if "snapshots" in call[0])
        self.assertIn("documents", snapshot_call[0])

    def test_configured_jobs_merge_with_unmentioned_discovered_jobs(self):
        report = self.collect(DiscoveryRunner(self.discovery_script()))

        self.assertEqual(report["config"]["status"], "ready")
        self.assertEqual(report["config"]["source"], "file")
        self.assertEqual(
            [(job["id"], job["source"]) for job in report["jobs"]],
            [("home", "config"), ("restic-documents", "systemd")],
        )
        self.assertEqual(report["overallStatus"], "healthy")

    def test_configured_job_replaces_discovery_for_the_same_service(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["jobs"][0].update({
            "id": "documents",
            "name": "Documents override",
            "service": "restic-documents.service",
            "timer": "restic-documents.timer",
            "tag": "documents",
        })
        self.config.write_text(json.dumps(config), encoding="utf-8")

        report = self.collect(DiscoveryRunner(self.discovery_script()))

        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "documents")
        self.assertEqual(report["jobs"][0]["name"], "Documents override")
        self.assertEqual(report["jobs"][0]["source"], "config")

    def test_configured_jobs_survive_discovery_failure_without_cache(self):
        report = self.collect(
            DiscoveryRunner(self.discovery_script(), list_error=True)
        )

        self.assertEqual(report["overallStatus"], "unknown")
        self.assertEqual(report["config"]["status"], "degraded")
        self.assertEqual(report["config"]["source"], "file")
        self.assertIn("Could not list user timers", report["config"]["error"])
        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "home")
        self.assertEqual(report["jobs"][0]["status"], "healthy")

    def test_discovered_identity_survives_description_changes(self):
        config = self.root / "missing.json"
        first = self.collect(
            DiscoveryRunner(self.discovery_script()),
            config_path=config,
        )
        renamed_runner = DiscoveryRunner(
            self.discovery_script(),
            description="restic backup of Personal files to archive",
        )
        second = self.collect(renamed_runner, config_path=config)

        self.assertEqual(first["jobs"][0]["id"], "restic-documents")
        self.assertEqual(second["jobs"][0]["id"], "restic-documents")
        self.assertEqual(second["jobs"][0]["name"], "Personal files")
        self.assertFalse(
            any("snapshots" in command or "stats" in command for command, _ in renamed_runner.calls)
        )

    def test_discovery_failure_uses_last_discovered_jobs(self):
        config = self.root / "missing.json"
        self.collect(
            DiscoveryRunner(self.discovery_script()),
            config_path=config,
        )
        failed_runner = DiscoveryRunner(self.discovery_script(), list_error=True)

        report = self.collect(failed_runner, config_path=config)

        self.assertEqual(report["overallStatus"], "unknown")
        self.assertEqual(report["config"]["status"], "degraded")
        self.assertEqual(report["config"]["source"], "systemd")
        self.assertIn("Could not list user timers", report["config"]["error"])
        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "restic-documents")
        self.assertEqual(report["jobs"][0]["source"], "systemd-cache")

    def test_unchanged_discovery_cache_is_not_rewritten(self):
        config = self.root / "missing.json"
        runner = DiscoveryRunner(self.discovery_script())
        self.collect(runner, config_path=config)
        cache = restic_status.discovery_cache_file(self.cache, config)
        inode = cache.stat().st_ino

        self.collect(DiscoveryRunner(runner.script), config_path=config)

        self.assertEqual(cache.stat().st_ino, inode)

    def test_empty_discovery_preserves_nonempty_cache_without_merging_it(self):
        config = self.root / "missing.json"
        script = self.discovery_script()
        self.collect(DiscoveryRunner(script), config_path=config)

        empty = self.collect(
            DiscoveryRunner(script, empty_shows=True),
            config_path=config,
        )
        recovered = self.collect(
            DiscoveryRunner(script, list_error=True),
            config_path=config,
        )

        self.assertEqual(empty["jobs"], [])
        self.assertEqual(recovered["jobs"][0]["id"], "restic-documents")
        self.assertEqual(recovered["jobs"][0]["source"], "systemd-cache")

    def test_per_service_show_failure_reuses_cached_discovery(self):
        config = self.root / "missing.json"
        script = self.discovery_script()
        self.collect(DiscoveryRunner(script), config_path=config)

        report = self.collect(
            DiscoveryRunner(script, show_error=True),
            config_path=config,
        )

        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(report["jobs"][0]["id"], "restic-documents")
        self.assertEqual(report["jobs"][0]["source"], "systemd-cache")
        self.assertEqual(report["config"]["status"], "degraded")
        self.assertIn("restic-documents.service", report["config"]["error"])

        recovered = self.collect(
            DiscoveryRunner(script, list_error=True),
            config_path=config,
        )
        self.assertEqual(recovered["jobs"][0]["id"], "restic-documents")
        self.assertEqual(recovered["jobs"][0]["source"], "systemd-cache")

    def test_discovery_requires_a_restic_backup_invocation(self):
        direct_backup = (
            "{ path=/usr/bin/restic ; argv[]=/usr/bin/restic backup /home ; "
            "ignore_errors=no ; }"
        )
        flagged_backup = (
            "{ path=/usr/bin/restic ; argv[]=/usr/bin/restic --repo /tmp/repo "
            "--verbose backup /home ; ignore_errors=no ; }"
        )
        prune = (
            "{ path=/usr/bin/restic ; argv[]=/usr/bin/restic prune ; "
            "ignore_errors=no ; }"
        )
        multiple_commands = " ; ".join(
            (
                "{ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no ; }",
                "{ path=/usr/bin/restic ; argv[]=/usr/bin/restic backup /home ; "
                "ignore_errors=no ; }",
            )
        )
        shell_command = (
            "{ path=/bin/bash ; "
            "argv[]=/bin/bash -c restic backup /home ; "
            "ignore_errors=no ; }"
        )

        cases = [
            (direct_backup, "", True),
            (flagged_backup, "", True),
            (multiple_commands, "", True),
            (shell_command, "", True),
            ("", '"$RESTIC" backup --tag home /home', True),
            ("", "/bin/bash -c 'restic backup /home'", True),
            ("", "/bin/bash -lc 'exec nice -n19 restic backup /home'", True),
            ("", "/bin/bash -O extglob -c 'restic backup /home'", True),
            ("", "nice -n19 restic backup /home", True),
            ("", "nice -n 19 restic backup /home", True),
            ("", "ionice -c3 restic backup /home", True),
            ("", "chrt -i 0 restic backup /home", True),
            ("", "timeout 3600 restic backup /home", True),
            ("", "timeout --signal TERM 3600 restic backup /home", True),
            ("", "flock -n /tmp/restic.lock restic backup /home", True),
            ("", "flock --timeout 5 /tmp/restic.lock restic backup /home", True),
            ("", "systemd-inhibit --what=sleep restic backup /home", True),
            ("", "systemd-inhibit --what sleep restic backup /home", True),
            ("", "restic --verbose=2 backup /home", True),
            (prune, "", False),
            ("", "# restic backup\n/usr/bin/restic prune", False),
            (
                "{ path=/tmp/backup-prune.sh ; argv[]=/tmp/backup-prune.sh ; "
                "ignore_errors=no ; }",
                "/usr/bin/restic prune",
                False,
            ),
            ("", "/usr/bin/restic forget --tag backup", False),
            ("", 'echo "restic backup"', False),
            ("", "/bin/bash -c 'restic prune --tag backup'", False),
            ("", "/bin/bash -c 'echo restic backup'", False),
            ("", "/bin/bash -c \"bash -c 'restic backup /home'\"", False),
            ("", "flock /tmp/restic.lock -c 'restic backup /home'", False),
        ]

        for exec_text, script_text, expected in cases:
            with self.subTest(exec_text=exec_text, script_text=script_text):
                self.assertEqual(
                    restic_status.restic_backup_command(exec_text, script_text),
                    expected,
                )

    def test_missing_or_unreadable_wrapper_retains_cached_job(self):
        for broken in ("missing", "invalid-encoding"):
            with self.subTest(broken=broken):
                script = self.discovery_script()
                before = self.collect(DiscoveryRunner(script))
                if broken == "missing":
                    script.unlink()
                else:
                    script.write_bytes(b"#!/bin/sh\n\xff")
                report = self.collect(DiscoveryRunner(script))
                self.assertEqual([job["id"] for job in report["jobs"]], [job["id"] for job in before["jobs"]])
                self.assertEqual(report["overallStatus"], "unknown")
                self.assertEqual(report["config"]["status"], "degraded")
                self.assertIn("restic-documents.service", report["config"]["error"])
                self.assertEqual(report["jobs"][1]["source"], "systemd-cache")
                recovered = self.collect(DiscoveryRunner(self.discovery_script()))
                self.assertEqual(recovered["overallStatus"], "healthy")
                self.assertEqual(recovered["jobs"][1]["source"], "systemd")

    def test_wrapper_inspection_does_not_read_restic_option_paths(self):
        argv = " ".join(
            (
                "/usr/bin/restic backup",
                "--password-file",
                str(self.password),
                "--repository-file",
                str(self.repository),
                "/home/test",
            )
        )

        with patch.object(restic_status, "read_wrapper_script") as read_script:
            self.assertEqual(restic_status.wrapper_text("/usr/bin/restic", argv), "")

        read_script.assert_not_called()

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
        for secret in (
            "super-secret",
            "hunter2",
            "aws-secret",
            "b2-secret",
            "bearer-secret",
        ):
            self.assertNotIn(secret, combined)
        self.assertIn("[redacted]", combined)

    def test_sensitive_assignments_and_bearer_tokens_are_redacted(self):
        message = " ".join(
            (
                "AWS_SECRET_ACCESS_KEY=aws-secret",
                "B2_ACCOUNT_KEY: b2-secret",
                "api-key=api-secret",
                "Authorization: Bearer auth-secret",
                "Bearer standalone-secret",
                "ordinary=value",
                "RCLONE_CONFIG_PASS=rclone-secret",
                "passphrase: gpg-secret",
                "AZURE_STORAGE_SAS=sas-secret",
                "bypass=true",
            )
        )

        sanitized = restic_status.sanitize(message)

        for secret in (
            "aws-secret",
            "b2-secret",
            "api-secret",
            "auth-secret",
            "standalone-secret",
            "rclone-secret",
            "gpg-secret",
            "sas-secret",
        ):
            self.assertNotIn(secret, sanitized)
        self.assertIn("ordinary=value", sanitized)
        self.assertIn("bypass=true", sanitized)
        self.assertGreaterEqual(sanitized.count("[redacted]"), 8)

    def test_redaction_handles_unclosed_quoted_backslash_runs_in_linear_time(self):
        self.assertEqual(
            restic_status.sanitize(r'secret="sec \" ret"'),
            "secret=[redacted]",
        )
        hostile = 'AWS_SECRET_ACCESS_KEY="' + "\\" * 3000

        started = time.perf_counter()
        sanitized = restic_status.sanitize(hostile)
        elapsed = time.perf_counter() - started

        self.assertNotIn("\\", sanitized)
        self.assertIn("[redacted]", sanitized)
        self.assertLess(elapsed, 1)

    def test_monotonic_timer_deadline_is_converted_to_wall_clock_time(self):
        self.assertAlmostEqual(
            restic_status.systemd_timespan_seconds("1d 15min 4.152112s"),
            87304.152112,
        )
        self.assertAlmostEqual(
            restic_status.systemd_timespan_seconds("87304152112"),
            87304.152112,
        )
        self.assertIsNone(restic_status.systemd_timespan_seconds("not a deadline"))

        with patch.object(restic_status.time, "clock_gettime", return_value=100):
            report = self.collect(FakeRunner(monotonic_timer="2min"))

        self.assertEqual(
            report["jobs"][0]["timer"]["nextRunAt"],
            restic_status.isoformat(NOW + timedelta(seconds=20)),
        )

    def test_duplicate_completion_events_are_one_logical_run(self):
        runner = FakeRunner(duplicate_completion=True)
        runs = restic_status.parse_journal_runs(runner._history().stdout)
        history = restic_status.journal_history(
            "restic-home.service",
            runner,
            30,
        )

        self.assertEqual(len(runs), 1)
        self.assertNotIn("runs", history)
        self.assertEqual(history["lastRun"]["message"], "Backup deactivated")
        self.assertEqual(history["lastRun"]["durationSec"], 180)

    def test_newer_systemd_failure_overrides_older_journal_success(self):
        state = restic_status.service_state("restic-home.service", FakeRunner(failed=True), 30)
        state.update({"activeState": "failed", "finishedAt": restic_status.isoformat(NOW - timedelta(minutes=1))})
        with patch.object(restic_status, "service_state", return_value=state):
            job = self.collect(FakeRunner())["jobs"][0]
        self.assertEqual(job["status"], "attention")
        self.assertEqual(job["service"]["lastRun"]["result"], "failed")
        self.assertEqual(job["service"]["lastRun"]["finishedAt"], state["finishedAt"])
        self.assertTrue(job["logTail"])

    def test_newer_systemd_success_overrides_older_journal_failure(self):
        state = restic_status.service_state("restic-home.service", FakeRunner(), 30)
        state["finishedAt"] = restic_status.isoformat(NOW - timedelta(minutes=1))
        with patch.object(restic_status, "service_state", return_value=state):
            job = self.collect(FakeRunner(failed=True))["jobs"][0]
        self.assertEqual(job["status"], "healthy")
        self.assertEqual(job["service"]["lastSuccessAt"], state["finishedAt"])

    def test_failure_without_a_new_main_exit_uses_unit_state_change(self):
        for finished in (None, restic_status.isoformat(NOW - timedelta(hours=2))):
            with self.subTest(finished=finished):
                state = restic_status.service_state("restic-home.service", FakeRunner(failed=True), 30)
                state.update({
                    "activeState": "failed", "finishedAt": finished,
                    "stateChangedAt": restic_status.isoformat(NOW - timedelta(minutes=1)),
                })
                with patch.object(restic_status, "service_state", return_value=state):
                    job = self.collect(FakeRunner())["jobs"][0]
                self.assertEqual(job["status"], "attention")
                self.assertEqual(job["service"]["lastRun"]["finishedAt"], state["stateChangedAt"])

    def test_systemd_failure_wins_a_timestamp_tie(self):
        state = restic_status.service_state("restic-home.service", FakeRunner(failed=True), 30)
        with patch.object(restic_status, "service_state", return_value=state):
            job = self.collect(FakeRunner())["jobs"][0]
        self.assertEqual(job["status"], "attention")

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

    def test_failed_repository_refresh_backs_off_until_the_attempt_window_passes(self):
        self.collect(FakeRunner())
        self.collect(FakeRunner(restic_error=1), force=True, now=NOW + timedelta(minutes=20))

        backed_off = FakeRunner()
        stale = self.collect(backed_off, now=NOW + timedelta(minutes=21))
        self.assertEqual(stale["jobs"][0]["repository"]["status"], "stale")
        self.assertFalse(
            any("snapshots" in command or "stats" in command for command, _ in backed_off.calls)
        )

        recovered = FakeRunner()
        ready = self.collect(recovered, now=NOW + timedelta(minutes=40))
        self.assertTrue(any("snapshots" in command for command, _ in recovered.calls))
        self.assertEqual(ready["jobs"][0]["repository"]["status"], "ready")

    def test_forced_repository_refresh_bypasses_failure_backoff(self):
        self.collect(FakeRunner())
        self.collect(FakeRunner(restic_error=1), force=True, now=NOW + timedelta(minutes=20))

        forced = FakeRunner()
        self.collect(forced, force=True, now=NOW + timedelta(minutes=21))
        self.assertTrue(any("snapshots" in command for command, _ in forced.calls))

    def test_missing_run_history_uses_timer_trigger_as_deadline(self):
        overdue = self.collect(FakeRunner(no_history=True, last_trigger_age_hours=100))
        self.assertEqual(overdue["jobs"][0]["status"], "attention")
        self.assertTrue(
            any(issue["code"] == "run-unverified" for issue in overdue["jobs"][0]["issues"])
        )

        recent = self.collect(FakeRunner(no_history=True, last_trigger_age_hours=1))
        self.assertEqual(recent["jobs"][0]["status"], "unknown")

    def test_implicit_max_run_age_follows_timer_cadence(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        del config["jobs"][0]["maxRunAgeHours"]
        self.config.write_text(json.dumps(config), encoding="utf-8")
        runner = FakeRunner(
            history_age_hours=144,
            last_trigger_age_hours=144,
            next_run_hours=24,
            calendar="Tue *-*-* 12:00:00 UTC",
        )

        implicit = self.collect(runner)
        self.assertEqual(implicit["jobs"][0]["status"], "healthy")
        self.assertFalse(
            any(issue["code"] == "run-overdue" for issue in implicit["jobs"][0]["issues"])
        )

        config["jobs"][0]["maxRunAgeHours"] = 36
        self.config.write_text(json.dumps(config), encoding="utf-8")
        explicit = self.collect(runner)
        self.assertEqual(explicit["jobs"][0]["status"], "attention")
        self.assertTrue(
            any(issue["code"] == "run-overdue" for issue in explicit["jobs"][0]["issues"])
        )

    def test_missed_triggers_cannot_extend_implicit_deadlines(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        del config["jobs"][0]["maxRunAgeHours"]
        self.config.write_text(json.dumps(config), encoding="utf-8")
        for no_history in (False, True):
            with self.subTest(no_history=no_history):
                report = self.collect(FakeRunner(
                    history_age_hours=720, last_trigger_age_hours=720,
                    next_run_hours=23, no_history=no_history,
                ))
                self.assertEqual(report["jobs"][0]["status"], "attention")
                code = "run-unverified" if no_history else "run-overdue"
                self.assertTrue(any(issue["code"] == code for issue in report["jobs"][0]["issues"]))

    def test_calendar_deadlines_follow_irregular_schedules_and_timezones(self):
        cases = [
            (["Mon..Fri *-*-* 12:00:00 UTC"], "2026-09-04T12:01:00Z", 72 - 1 / 60),
            (["monthly UTC"], "2026-08-01T00:00:00Z", 31 * 24),
            (["*-*-* 03:30:00 America/Los_Angeles"], "2026-09-04T10:31:00Z", 24 - 1 / 60),
            (["weekly UTC", "daily UTC"], "2026-09-04T00:00:00Z", 24),
        ]
        for calendar, reference, expected in cases:
            with self.subTest(calendar=calendar):
                self.assertAlmostEqual(restic_status.timer_interval_hours(
                    {"calendar": calendar}, reference, restic_status.Runner(), 5,
                ), expected)

    def test_monotonic_cadence_and_calendar_failure_use_bounded_deadlines(self):
        timer = {"intervalsSec": [7 * 86400], "randomizedDelaySec": 600}
        self.assertAlmostEqual(restic_status.timer_interval_hours(
            timer, restic_status.isoformat(NOW), FakeRunner(), 5,
        ), 168 + 1 / 6)
        with patch.object(FakeRunner, "run", side_effect=OSError("not found")):
            self.assertIsNone(restic_status.timer_interval_hours(
                {"calendar": ["weekly UTC"]}, restic_status.isoformat(NOW), FakeRunner(), 5,
            ))

    def test_timer_schedule_preserves_repeated_systemd_properties(self):
        properties = restic_status.parse_properties("\n".join([
            "TimersMonotonic={ OnUnitActiveUSec=1d ; next_elapse=0 }",
            "TimersMonotonic={ OnStartupUSec=5min ; next_elapse=0 }",
            "TimersCalendar={ OnCalendar=weekly UTC ; next_elapse=n/a }",
            "TimersCalendar={ OnCalendar=daily UTC ; next_elapse=n/a }",
            "RandomizedDelayUSec=10min",
        ]))
        with patch.object(restic_status, "systemd_show", return_value={"available": True, "properties": properties}):
            timer = restic_status.timer_state("backup.timer", FakeRunner(), 5, NOW)
        self.assertEqual(timer["intervalsSec"], [86400])
        self.assertEqual(timer["calendar"], ["weekly UTC", "daily UTC"])
        self.assertEqual(timer["randomizedDelaySec"], 600)

    def test_partial_stats_failure_persists_in_fresh_cache(self):
        report = self.collect(FakeRunner(stats_error=True))
        job = report["jobs"][0]

        self.assertEqual(report["overallStatus"], "healthy")
        self.assertEqual(job["status"], "healthy")
        self.assertEqual(job["repository"]["status"], "partial")

        cached_runner = FakeRunner()
        cached_report = self.collect(cached_runner)
        self.assertEqual(cached_report["overallStatus"], "healthy")
        self.assertEqual(cached_report["jobs"][0]["repository"]["status"], "partial")
        self.assertIn("stats unavailable", cached_report["jobs"][0]["repository"]["error"])
        self.assertFalse(any("snapshots" in command or "stats" in command for command, _ in cached_runner.calls))

    def test_existing_cache_directories_are_hardened(self):
        job_cache = restic_status.cache_file(self.cache, self.config, "home")
        restic_parent = job_cache.parent / "restic"
        restic_parent.mkdir(parents=True)
        for directory in (self.cache, job_cache.parent, restic_parent):
            directory.chmod(0o755)

        self.collect(FakeRunner())

        private_directories = [
            self.cache,
            job_cache.parent,
            restic_parent,
            restic_parent / "home",
        ]
        self.assertTrue(
            all(
                directory.stat().st_mode & 0o777 == 0o700
                for directory in private_directories
            )
        )
        self.assertTrue(
            all(path.stat().st_mode & 0o777 == 0o600 for path in job_cache.parent.glob("*.json"))
        )

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

    def test_directory_config_path_returns_a_structured_error(self):
        config_path = restic_status.expanded_path("")
        self.assertTrue(config_path.is_dir())
        report = restic_status.collect_report(
            config_path,
            cache_dir=self.cache,
            cache_seconds=900,
            force=False,
            log_lines=10,
            timeout=30,
            runner=FakeRunner(),
            now=NOW,
        )

        self.assertEqual(report["config"]["status"], "error")
        self.assertIn("Jobs file is not readable", report["config"]["error"])
        self.assertNotIn("Traceback", report["config"]["error"])

    def test_numeric_config_error_names_the_job_and_field(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["jobs"][0]["checkMaxAgeHours"] = 9000
        self.config.write_text(json.dumps(config), encoding="utf-8")

        report = self.collect(FakeRunner())

        self.assertEqual(report["config"]["status"], "error")
        self.assertIn("jobs[0].checkMaxAgeHours", report["config"]["error"])

    def test_non_numeric_max_run_age_is_a_config_error(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["jobs"][0]["maxRunAgeHours"] = "thirty"
        self.config.write_text(json.dumps(config), encoding="utf-8")

        report = self.collect(FakeRunner())

        self.assertEqual(report["config"]["status"], "error")
        self.assertIn("jobs[0].maxRunAgeHours", report["config"]["error"])

    def test_runner_terminates_timed_out_commands(self):
        started = time.perf_counter()
        with self.assertRaises(subprocess.TimeoutExpired):
            restic_status.Runner().run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=1,
            )
        self.assertLess(time.perf_counter() - started, 10)


if __name__ == "__main__":
    unittest.main()

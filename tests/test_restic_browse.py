from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import restic_browse  # noqa: E402

SNAPSHOT = "a" * 64
FAKE_RESTIC = """#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path

args = sys.argv[1:]
Path(os.environ["FAKE_RESTIC_LOG"]).write_text(json.dumps(args))
mode = os.environ.get("FAKE_RESTIC_MODE", "ok")
if mode == "password":
    print(json.dumps({"message_type": "exit_error", "code": 12, "message": "Fatal: wrong password"}))
    sys.exit(12)
print(json.dumps({"message_type": "status", "percent_done": 0.5}), flush=True)
if mode == "slow":
    time.sleep(30)
target = Path(args[args.index("--target") + 1])
if "--include" in args:
    name = re.sub(r"\\\\(.)", r"\\1", args[args.index("--include") + 1].lstrip("/"))
    if mode != "empty":
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text("restored")
else:
    target.mkdir(parents=True, exist_ok=True)
    (target / "inside.txt").write_text("restored")
print(json.dumps({"message_type": "summary", "files_restored": 1, "bytes_restored": 8}))
"""


class ListRunner:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls: list[list[str]] = []

    def run(self, command, *, timeout, env=None):
        self.calls.append(command)
        return self.result


def node(path: str, kind: str = "file", size: int | None = 1) -> str:
    return json.dumps({
        "struct_type": "node", "name": Path(path).name, "type": kind, "path": path, "size": size,
    })


class ResticBrowseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.restic = self.root / "restic"
        self.restic.write_text(FAKE_RESTIC, encoding="utf-8")
        self.restic.chmod(0o755)
        self.log = self.root / "restic-args.json"
        self.folder = self.root / "Restored" / "Home 2026-08-17 0330"
        self.folder.mkdir(parents=True)
        self.events: list[dict] = []
        os.environ["FAKE_RESTIC_LOG"] = str(self.log)
        os.environ.pop("FAKE_RESTIC_MODE", None)

    def tearDown(self):
        os.environ.pop("FAKE_RESTIC_LOG", None)
        os.environ.pop("FAKE_RESTIC_MODE", None)
        self.temporary.cleanup()

    def restore(self, path: str, kind: str) -> dict:
        restic_browse.restore([str(self.restic), "--json"], SNAPSHOT, path, kind, self.folder, self.events.append)
        return self.events[-1]

    def restic_args(self) -> list[str]:
        return json.loads(self.log.read_text(encoding="utf-8"))

    def test_file_restore_includes_only_the_escaped_file_from_its_parent(self):
        event = self.restore("/home/test/x*y [1]?.txt", "file")

        args = self.restic_args()
        self.assertIn(f"{SNAPSHOT}:/home/test", args)
        self.assertEqual(args[args.index("--include") + 1], r"/x\*y \[1\]\?.txt")
        self.assertEqual(args[args.index("--target") + 1], str(self.folder))
        self.assertEqual(event["type"], "done")
        self.assertEqual(event["path"], str(self.folder / "x*y [1]?.txt"))
        self.assertIn({"type": "progress", "percent": 0.5}, self.events)

    def test_directory_restore_keeps_the_folder_name(self):
        event = self.restore("/home/test/Documents", "dir")

        args = self.restic_args()
        self.assertIn(f"{SNAPSHOT}:/home/test/Documents", args)
        self.assertNotIn("--include", args)
        self.assertEqual(event["path"], str(self.folder / "Documents"))
        self.assertTrue((self.folder / "Documents" / "inside.txt").is_file())

    def test_failed_restore_reports_the_error_and_removes_the_empty_folder(self):
        os.environ["FAKE_RESTIC_MODE"] = "password"
        event = self.restore("/home/test/notes.md", "file")

        self.assertEqual(event, {"type": "error", "error": "Wrong repository password", "folder": str(self.folder)})
        self.assertFalse(self.folder.exists())

    def test_restore_that_writes_nothing_is_an_error(self):
        os.environ["FAKE_RESTIC_MODE"] = "empty"
        event = self.restore("/home/test/notes.md", "file")

        self.assertEqual(event["type"], "error")
        self.assertEqual(event["error"], "Nothing was restored")
        self.assertFalse(self.folder.exists())

    def test_restore_folders_are_new_private_and_never_reused(self):
        root = self.root / "targets"
        first = restic_browse.unique_directory(root, "Home 2026-08-17 0330")
        (first / "keep.txt").write_text("mine")
        second = restic_browse.unique_directory(root, "Home 2026-08-17 0330")

        self.assertEqual(second.name, "Home 2026-08-17 0330 2")
        self.assertEqual(first.stat().st_mode & 0o777, 0o700)
        self.assertEqual((first / "keep.txt").read_text(), "mine")

    def test_restore_label_cannot_escape_the_target_root(self):
        label = restic_browse.restore_label({"id": "home", "name": "../.hidden/evil"}, "")
        self.assertNotIn("/", label)
        self.assertFalse(label.startswith("."))

    def test_invalid_snapshots_and_paths_are_rejected(self):
        for value in ("relative", "/home/../etc", "/home/", "/home\0x", "//home"):
            with self.subTest(path=value), self.assertRaises(restic_browse.BrowseError):
                restic_browse.snapshot_path(value)
        for value in ("abc", "latest", "A" * 64, "a" * 63):
            with self.subTest(snapshot=value), self.assertRaises(restic_browse.BrowseError):
                restic_browse.snapshot_id(value)

    def test_directory_listing_keeps_only_direct_children_with_folders_first(self):
        runner = ListRunner("\n".join([
            json.dumps({"struct_type": "snapshot", "id": SNAPSHOT}),
            node("/home/test", "dir", None),
            node("/home/test/zeta.txt"),
            node("/home/test/Alpha", "dir", None),
            node("/home/test/Alpha/nested.txt"),
            node("/home/test/beta.txt"),
        ]))

        entries, truncated = restic_browse.list_directory(["restic"], SNAPSHOT, "/home/test", runner, 30)

        self.assertEqual([entry["name"] for entry in entries], ["Alpha", "beta.txt", "zeta.txt"])
        self.assertFalse(truncated)
        self.assertEqual(runner.calls[0][-3:], ["ls", SNAPSHOT, "/home/test"])

    def test_snapshots_are_newest_first_and_filtered_by_tag(self):
        runner = ListRunner(json.dumps([
            {"id": "a" * 64, "time": "2026-08-01T03:30:00Z", "hostname": "forge", "paths": ["/home/test"]},
            {"id": "b" * 64, "time": "2026-08-07T03:30:00Z", "hostname": "forge", "paths": ["/home/test"]},
            {"id": "not-a-snapshot", "time": "2026-08-09T03:30:00Z"},
        ]))

        snapshots = restic_browse.list_snapshots({"tag": "home"}, ["restic"], runner, 30)

        self.assertEqual([snapshot["shortId"] for snapshot in snapshots], ["bbbbbbbb", "aaaaaaaa"])
        self.assertEqual(runner.calls[0][-3:], ["snapshots", "--tag", "home"])

    def test_restic_failures_become_readable_errors(self):
        runner = ListRunner("", 11)
        with self.assertRaisesRegex(restic_browse.BrowseError, "locked"):
            restic_browse.list_snapshots({"tag": ""}, ["restic"], runner, 30)

        runner = ListRunner("", 1, json.dumps({"message_type": "exit_error", "code": 1, "message": "path nope: not found"}))
        with self.assertRaisesRegex(restic_browse.BrowseError, "path nope: not found"):
            restic_browse.list_directory(["restic"], SNAPSHOT, "/nope", runner, 30)

    def config(self) -> Path:
        repository = self.root / "repository"
        password = self.root / "password"
        repository.write_text("/srv/restic\n", encoding="utf-8")
        password.write_text("secret\n", encoding="utf-8")
        config = self.root / "jobs.json"
        config.write_text(json.dumps({"schemaVersion": 1, "jobs": [{
            "id": "home", "name": "Home", "service": "restic-home.service", "timer": "restic-home.timer",
            "repositoryFile": str(repository), "passwordFile": str(password), "restic": str(self.restic),
        }]}), encoding="utf-8")
        return config

    def test_unknown_jobs_are_reported_without_running_restic(self):
        output = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "scripts" / "restic_browse.py"), "snapshots",
             "--config", str(self.config()), "--cache-dir", str(self.root / "cache"), "--job", "missing"],
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, XDG_RUNTIME_DIR=str(self.root), DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent"),
        )
        event = json.loads(output.stdout)
        self.assertEqual(event["type"], "error")
        self.assertFalse(self.log.exists())

    def test_cancel_stops_restic_and_reports_cancelled(self):
        process = subprocess.Popen(
            [sys.executable, str(PROJECT_DIR / "scripts" / "restic_browse.py"), "restore",
             "--config", str(self.config()), "--cache-dir", str(self.root / "cache"), "--job", "home",
             "--snapshot", SNAPSHOT, "--path", "/home/test/big.iso", "--target-root", str(self.root / "out")],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ, FAKE_RESTIC_MODE="slow"),
        )
        try:
            self.assertEqual(json.loads(process.stdout.readline())["type"], "progress")
            process.send_signal(signal.SIGTERM)
            event = json.loads(process.stdout.readline())
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            process.kill()
            process.stdout.close()

        self.assertEqual(event["type"], "cancelled")
        self.assertEqual(list((self.root / "out").iterdir()), [])


@unittest.skipUnless(shutil.which("restic"), "restic is not installed")
class ResticRoundTripTest(unittest.TestCase):
    def test_browse_and_restore_files_with_pattern_characters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "Docs" / "sub").mkdir(parents=True)
            for name in ("x*y.txt", "xay.txt", "[br].txt", "q?.txt"):
                (source / "Docs" / name).write_text(name)
            (source / "Docs" / "sub" / "b.txt").write_text("nested")
            (root / "repository").write_text(str(root / "repo"))
            (root / "password").write_text("secret")
            config = root / "jobs.json"
            config.write_text(json.dumps({"schemaVersion": 1, "jobs": [{
                "id": "docs", "name": "Docs", "service": "docs.service", "timer": "docs.timer",
                "repositoryFile": str(root / "repository"), "passwordFile": str(root / "password"),
            }]}))
            restic = ["restic", "-q", "--repository-file", str(root / "repository"),
                      "--password-file", str(root / "password"), "--cache-dir", str(root / "restic-cache")]
            subprocess.run(restic + ["init"], check=True, capture_output=True)
            subprocess.run(restic + ["backup", str(source)], check=True, capture_output=True)

            def browse(*args: str) -> dict:
                output = subprocess.run(
                    [sys.executable, str(PROJECT_DIR / "scripts" / "restic_browse.py"), *args,
                     "--config", str(config), "--cache-dir", str(root / "cache"), "--job", "docs"],
                    capture_output=True, text=True, timeout=60, check=True,
                )
                return json.loads(output.stdout.splitlines()[-1])

            snapshot = browse("snapshots")["snapshots"][0]
            docs = str(source / "Docs")
            listing = browse("ls", "--snapshot", snapshot["id"], "--path", docs)
            self.assertEqual(
                [entry["name"] for entry in listing["entries"]],
                ["sub", "[br].txt", "q?.txt", "x*y.txt", "xay.txt"],
            )

            target = root / "Restored"
            restored = browse("restore", "--snapshot", snapshot["id"], "--snapshot-time", snapshot["time"],
                              "--path", docs + "/x*y.txt", "--type", "file", "--target-root", str(target))
            self.assertEqual(restored["type"], "done")
            self.assertEqual(sorted(path.name for path in Path(restored["folder"]).iterdir()), ["x*y.txt"])

            folder = browse("restore", "--snapshot", snapshot["id"], "--path", docs + "/sub", "--type", "dir",
                            "--target-root", str(target))
            self.assertEqual(Path(folder["path"], "b.txt").read_text(), "nested")
            self.assertNotEqual(folder["folder"], restored["folder"])


if __name__ == "__main__":
    unittest.main()

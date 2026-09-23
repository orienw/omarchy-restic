#!/usr/bin/env python3

"""Browse snapshots and restore files for one monitored restic job."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import restic_status as status


SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}")
MAX_ENTRIES = 5000
CANCEL_GRACE_SECONDS = 10
RESTIC_EXIT_ERRORS = {
    10: "Repository not found",
    11: "Repository is locked by another operation",
    12: "Wrong repository password",
}


class BrowseError(RuntimeError):
    pass


def find_job(jobs: list[dict[str, Any]], job_id: str) -> dict[str, Any] | None:
    return next((job for job in jobs if job["id"] == job_id), None)


def resolve_job(
    config_path: Path,
    cache_dir: Path,
    job_id: str,
    runner: status.Runner,
    timeout: int,
) -> dict[str, Any]:
    try:
        configured = status.load_config(config_path) if config_path.exists() else []
    except status.ConfigError:
        configured = []
    cached = status.load_discovery_cache(status.discovery_cache_file(cache_dir, config_path))
    job = find_job(status.merge_jobs(cached or [], configured), job_id)
    if job is None:
        try:
            discovered, _ = status.discover_jobs(runner, timeout)
        except status.DiscoveryError as error:
            raise BrowseError(str(error)) from error
        job = find_job(status.merge_jobs(discovered, configured), job_id)
    if job is None:
        raise BrowseError(f"Unknown backup job: {job_id}")
    return job


def restic_error(returncode: int, output: str) -> str:
    if returncode in RESTIC_EXIT_ERRORS:
        return RESTIC_EXIT_ERRORS[returncode]
    for line in reversed(output.split("\n")):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("message_type") == "exit_error":
            return status.sanitize(entry.get("message"))
    return status.sanitize(output) or f"Restic exited with status {returncode}"


def run_restic(runner: status.Runner, command: list[str], timeout: int) -> str:
    try:
        result = runner.run(command, timeout=timeout, env=status.restic_environment())
    except subprocess.TimeoutExpired as error:
        raise BrowseError("The repository did not respond in time") from error
    except OSError as error:
        raise BrowseError(status.sanitize(error)) from error
    if result.returncode != 0:
        raise BrowseError(restic_error(result.returncode, result.stderr + "\n" + result.stdout))
    return result.stdout


def snapshot_id(value: str) -> str:
    if not SNAPSHOT_ID.fullmatch(value):
        raise BrowseError("Invalid snapshot id")
    return value


def snapshot_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\0" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise BrowseError("Invalid snapshot path")
    # restic's JSON replaces undecodable filename bytes with U+FFFD, so two
    # different names can arrive as the same path.
    if "\ufffd" in value:
        raise BrowseError("restic cannot show this name exactly. Restore the folder that contains it instead.")
    return value


def list_snapshots(
    job: dict[str, Any], base: list[str], runner: status.Runner, timeout: int
) -> list[dict[str, Any]]:
    command = base + ["snapshots"]
    if job["tag"]:
        command.append("--tag=" + job["tag"])
    try:
        raw = json.loads(run_restic(runner, command, timeout))
    except json.JSONDecodeError as error:
        raise BrowseError("Restic returned invalid snapshot JSON") from error
    if not isinstance(raw, list):
        raise BrowseError("Restic returned invalid snapshot JSON")

    snapshots = [
        {
            "id": item["id"],
            "shortId": item["id"][:8],
            "time": item.get("time"),
            "hostname": str(item.get("hostname") or ""),
            "paths": [path for path in item.get("paths") or [] if isinstance(path, str)],
        }
        for item in raw
        if isinstance(item, dict) and SNAPSHOT_ID.fullmatch(str(item.get("id", "")))
    ]
    oldest = datetime.min.replace(tzinfo=UTC)
    snapshots.sort(key=lambda snapshot: status.parse_iso(snapshot["time"]) or oldest, reverse=True)
    return snapshots


def list_directory(
    base: list[str], snapshot: str, path: str, runner: status.Runner, timeout: int
) -> tuple[list[dict[str, Any]], bool]:
    entries = []
    for line in run_restic(runner, base + ["ls", snapshot, path], timeout).split("\n"):
        try:
            node = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(node, dict) or node.get("struct_type") != "node":
            continue
        node_path = str(node.get("path") or "")
        if node_path == path or str(PurePosixPath(node_path).parent) != path:
            continue
        entries.append({
            "name": str(node.get("name") or PurePosixPath(node_path).name),
            "type": str(node.get("type") or "file"),
            "path": node_path,
            "size": node.get("size"),
            "mtime": node.get("mtime"),
        })
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].casefold()))
    return entries[:MAX_ENTRIES], len(entries) > MAX_ENTRIES


def escape_pattern(name: str) -> str:
    return re.sub(r"([\\*?\[\]])", r"\\\1", name)


def restore_label(job: dict[str, Any], snapshot_time: str) -> str:
    moment = status.parse_iso(snapshot_time)
    stamp = moment.astimezone().strftime("%Y-%m-%d %H%M") if moment else "snapshot"
    name = re.sub(r"[/\0]+", "-", job["name"]).strip().lstrip(".") or job["id"]
    return f"{name} {stamp}"


def unique_directory(root: Path, label: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        candidate = root / (label if number == 1 else f"{label} {number}")
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise BrowseError(f"Could not choose a restore folder in {root}")


def remove_if_empty(*paths: Path) -> None:
    for path in paths:
        try:
            path.rmdir()
        except OSError:
            pass


def restore(
    base: list[str],
    snapshot: str,
    path: str,
    kind: str,
    folder: Path,
    emit: Callable[[dict[str, Any]], None],
) -> None:
    name = PurePosixPath(path).name
    restored = folder / name
    if kind == "dir":
        command = base + ["restore", f"{snapshot}:{path}", "--target", str(restored)]
    else:
        # restic cannot restore a file as a subfolder, so restore its parent
        # and include only the file, escaped so its name is not a pattern.
        command = base + [
            "restore", f"{snapshot}:{PurePosixPath(path).parent}",
            "--target", str(folder),
            "--include", "/" + escape_pattern(name),
        ]

    env = status.restic_environment()
    env["RESTIC_PROGRESS_FPS"] = "1"
    output: list[str] = []
    summary: dict[str, Any] = {}

    def handle(raw: bytes) -> None:
        nonlocal summary
        line = raw.decode("utf-8", errors="replace")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            output.append(line + "\n")
            return
        if not isinstance(message, dict):
            return
        if message.get("message_type") == "status" and "percent_done" in message:
            emit({"type": "progress", "percent": message["percent_done"]})
        elif message.get("message_type") == "summary":
            summary = message
        else:
            output.append(line + "\n")

    cancelled_at: float | None = None
    with tempfile.TemporaryFile() as log:
        process = status.spawn(command, env=env, stdout=log, stderr=subprocess.STDOUT)

        def cancel(signum: int, frame: Any) -> None:
            nonlocal cancelled_at
            if cancelled_at is None:
                cancelled_at = time.monotonic()
                status.signal_group(process, signal.SIGTERM)

        previous = signal.signal(signal.SIGTERM, cancel)
        try:
            offset = 0
            pending = b""
            while True:
                running = process.poll() is None
                # pread keeps our read position apart from restic's write offset.
                chunk = os.pread(log.fileno(), 65536, offset)
                if chunk:
                    offset += len(chunk)
                    *lines, pending = (pending + chunk).split(b"\n")
                    for line in lines:
                        handle(line)
                    continue
                if not running:
                    break
                if cancelled_at is not None and time.monotonic() - cancelled_at > CANCEL_GRACE_SECONDS:
                    status.stop_process_group(process, 0)
                time.sleep(0.2)
            if pending:
                handle(pending)
            if cancelled_at is not None:
                status.signal_group(process, signal.SIGKILL)
        finally:
            signal.signal(signal.SIGTERM, previous)
    returncode = process.returncode
    cancelled = cancelled_at is not None

    if cancelled:
        remove_if_empty(restored, folder)
        emit({"type": "cancelled", "folder": str(folder)})
    elif returncode != 0:
        remove_if_empty(restored, folder)
        emit({"type": "error", "error": restic_error(returncode, "".join(output)), "folder": str(folder)})
    elif not restored.exists() and not restored.is_symlink():
        remove_if_empty(folder)
        emit({"type": "error", "error": "Nothing was restored", "folder": str(folder)})
    else:
        emit({
            "type": "done",
            "path": str(restored),
            "folder": str(folder),
            "files": summary.get("files_restored"),
            "bytes": summary.get("bytes_restored"),
        })


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", required=True, type=status.expanded_path)
    common.add_argument("--cache-dir", type=status.expanded_path, default=status.default_cache_dir())
    common.add_argument("--job", required=True)
    common.add_argument("--timeout-seconds", type=int, default=120)

    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshots", parents=[common])
    listing = commands.add_parser("ls", parents=[common])
    listing.add_argument("--snapshot", required=True)
    listing.add_argument("--path", required=True)
    restoring = commands.add_parser("restore", parents=[common])
    restoring.add_argument("--snapshot", required=True)
    restoring.add_argument("--snapshot-time", default="")
    restoring.add_argument("--path", required=True)
    restoring.add_argument("--type", default="file")
    restoring.add_argument("--target-root", required=True, type=status.expanded_path)
    return result


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    runner = status.Runner()
    try:
        job = resolve_job(args.config, args.cache_dir, args.job, runner, args.timeout_seconds)
        base = status.restic_base(job, args.config, args.cache_dir)
        if args.command == "snapshots":
            emit({"type": "snapshots", "snapshots": list_snapshots(job, base, runner, args.timeout_seconds)})
        elif args.command == "ls":
            path = snapshot_path(args.path)
            entries, truncated = list_directory(
                base, snapshot_id(args.snapshot), path, runner, args.timeout_seconds
            )
            emit({"type": "entries", "path": path, "entries": entries, "truncated": truncated})
        else:
            path = snapshot_path(args.path)
            if path == "/":
                raise BrowseError("Choose a file or folder to restore")
            snapshot = snapshot_id(args.snapshot)
            folder = unique_directory(args.target_root, restore_label(job, args.snapshot_time))
            restore(base, snapshot, path, args.type, folder, emit)
    except (BrowseError, status.RepositoryUnavailable) as error:
        emit({"type": "error", "error": str(error)})
    except OSError as error:
        emit({"type": "error", "error": status.sanitize(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

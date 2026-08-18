#!/usr/bin/env python3

"""Collect read-only restic and systemd health as a single JSON report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
CONFIG_SCHEMA_VERSION = 1

UNIT_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"
UNIT_STARTED = "39f53479d3a045ac8e11786248231fbf"
UNIT_FAILED = "be02cf6855d2428ba40df7e9d022f03d"
UNIT_SUCCESS = "7ad2d189f7e94e70a38c781354912448"
UNIT_FAILURE_RESULT = "d9b373ed55a64feb8242e02dbe79a49c"

UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]+\.(?:service|timer)$")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
URI_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*([^\s,;]+)"
)
SHELL_PARAMETER = re.compile(r"\$\{([^{}]+)\}")
SHELL_VARIABLE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
SHELL_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)
EXEC_PATH = re.compile(r"(?:^|[ {;])path=([^ ;}]+)")
EXEC_ARGV = re.compile(r"argv\[\]=(.*?)\s+;\s+(?:ignore_errors|start_time)=")
ENVIRONMENT_FILE = re.compile(
    r'(?:"([^"]+)"|(\S+))\s+\(ignore_errors=(?:yes|no)\)'
)


class ConfigError(ValueError):
    pass


class DiscoveryError(RuntimeError):
    pass


class Runner:
    def run(
        self,
        command: list[str],
        *,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            env=env,
            text=True,
            timeout=timeout,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_from_microseconds(value: Any) -> str | None:
    try:
        micros = int(str(value))
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    return isoformat(datetime.fromtimestamp(micros / 1_000_000, tz=UTC))


def iso_from_systemd_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"n/a", "[not set]"}:
        return None
    if text.startswith("@"):
        try:
            return isoformat(datetime.fromtimestamp(float(text[1:]), tz=UTC))
        except (OverflowError, ValueError):
            return None
    return iso_from_microseconds(text)


def elapsed_seconds(start: str | None, end: str | None) -> float | None:
    started = parse_iso(start)
    finished = parse_iso(end)
    if not started or not finished:
        return None
    return max(0.0, round((finished - started).total_seconds(), 3))


def sanitize(text: Any, limit: int = 360) -> str:
    value = " ".join(str(text or "").split())
    value = URI_USERINFO.sub(r"\1[redacted]@", value)
    value = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def number(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result < minimum or result > maximum:
        raise ConfigError(f"number must be between {minimum:g} and {maximum:g}")
    return result


def require_string(source: dict[str, Any], key: str, context: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def optional_unit(source: dict[str, Any], key: str, suffix: str, context: str) -> str | None:
    value = source.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not UNIT_PATTERN.fullmatch(value) or not value.endswith(suffix):
        raise ConfigError(f"{context}.{key} must be a {suffix} unit name")
    return value


def normalize_job(raw: Any, index: int) -> dict[str, Any]:
    context = f"jobs[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")

    job_id = require_string(raw, "id", context)
    if not ID_PATTERN.fullmatch(job_id):
        raise ConfigError(f"{context}.id must use lowercase letters, digits, dots, dashes, or underscores")

    service = require_string(raw, "service", context)
    timer = require_string(raw, "timer", context)
    if not UNIT_PATTERN.fullmatch(service) or not service.endswith(".service"):
        raise ConfigError(f"{context}.service must be a .service unit name")
    if not UNIT_PATTERN.fullmatch(timer) or not timer.endswith(".timer"):
        raise ConfigError(f"{context}.timer must be a .timer unit name")

    restic = raw.get("restic", "restic")
    if not isinstance(restic, str) or not restic.strip():
        raise ConfigError(f"{context}.restic must be a command name or path")

    tag = raw.get("tag", "")
    if not isinstance(tag, str):
        raise ConfigError(f"{context}.tag must be a string")

    return {
        "id": job_id,
        "name": str(raw.get("name") or job_id).strip(),
        "service": service,
        "timer": timer,
        "repositoryFile": str(expanded_path(require_string(raw, "repositoryFile", context))),
        "passwordFile": str(expanded_path(require_string(raw, "passwordFile", context))),
        "restic": restic.strip(),
        "tag": tag.strip(),
        "maxRunAgeHours": number(raw.get("maxRunAgeHours"), 36, 1, 8760),
        "checkService": optional_unit(raw, "checkService", ".service", context),
        "checkTimer": optional_unit(raw, "checkTimer", ".timer", context),
        "checkMaxAgeHours": number(raw.get("checkMaxAgeHours"), 720, 1, 8760),
    }


def load_config(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Jobs file not found: {path}") from error
    except PermissionError as error:
        raise ConfigError(f"Jobs file is not readable: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"Jobs file is not valid JSON: line {error.lineno}, column {error.colno}") from error

    if not isinstance(raw, dict):
        raise ConfigError("Jobs file must contain a JSON object")
    if raw.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"Jobs file schemaVersion must be {CONFIG_SCHEMA_VERSION}")
    jobs = raw.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError("Jobs file must contain at least one job")

    normalized = [normalize_job(job, index) for index, job in enumerate(jobs)]
    ids = [job["id"] for job in normalized]
    if len(ids) != len(set(ids)):
        raise ConfigError("Job ids must be unique")
    return normalized


def shell_token(raw: str) -> str | None:
    try:
        tokens = shlex.split(raw, comments=True, posix=True)
    except ValueError:
        return None
    return tokens[0] if len(tokens) == 1 else None


def expand_static_value(raw: str, variables: dict[str, str]) -> str | None:
    value = shell_token(raw)
    if value is None or "$(" in value or "`" in value:
        return None

    for _ in range(16):
        match = SHELL_PARAMETER.search(value)
        if not match:
            break
        expression = match.group(1)
        name, separator, fallback = expression.partition(":-")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return None
        replacement = variables.get(name, "")
        if separator and not replacement:
            replacement = fallback
        elif not separator and name not in variables:
            return None
        value = value[: match.start()] + replacement + value[match.end() :]
    if "${" in value:
        return None

    unresolved = False

    def replace_variable(match: re.Match[str]) -> str:
        nonlocal unresolved
        name = match.group(1)
        if name not in variables:
            unresolved = True
            return match.group(0)
        return variables[name]

    value = SHELL_VARIABLE.sub(replace_variable, value)
    if unresolved or "$" in value:
        return None
    return os.path.expanduser(value.replace("%h", str(Path.home())))


def static_variables() -> dict[str, str]:
    variables = dict(os.environ)
    variables.setdefault("HOME", str(Path.home()))
    variables.setdefault("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return variables


def parse_assignments(text: str, variables: dict[str, str]) -> None:
    for line in text.replace("\\\n", " ").splitlines():
        match = SHELL_ASSIGNMENT.match(line)
        if not match:
            continue
        value = expand_static_value(match.group(2), variables)
        if value is not None:
            variables[match.group(1)] = value


def parse_environment(value: str, variables: dict[str, str]) -> None:
    try:
        entries = shlex.split(value, comments=False, posix=True)
    except ValueError:
        return
    for entry in entries:
        key, separator, raw = entry.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        expanded = expand_static_value(shlex.quote(raw), variables)
        if expanded is not None:
            variables[key] = expanded


def read_static_text(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 1_048_576:
            return ""
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return "" if "\0" in text else text


def directive_values(text: str, directive: str) -> list[str]:
    prefix = directive + "="
    return [
        line.strip()[len(prefix) :].strip()
        for line in text.replace("\\\n", " ").splitlines()
        if line.strip().startswith(prefix)
    ]


def environment_file_paths(value: str, variables: dict[str, str]) -> list[Path]:
    paths: list[Path] = []
    for match in ENVIRONMENT_FILE.finditer(value):
        raw = match.group(1) or match.group(2) or ""
        expanded = expand_static_value(shlex.quote(raw.lstrip("-")), variables)
        if expanded:
            paths.append(expanded_path(expanded))
    return paths


def exec_details(value: str) -> tuple[str, str]:
    path_match = EXEC_PATH.search(value)
    argv_match = EXEC_ARGV.search(value)
    return (
        path_match.group(1) if path_match else "",
        argv_match.group(1) if argv_match else value,
    )


def wrapper_text(exec_path: str, argv: str) -> str:
    candidates = [exec_path]
    try:
        candidates.extend(shlex.split(argv, comments=False, posix=True))
    except ValueError:
        pass

    seen: set[Path] = set()
    for raw in candidates:
        if not raw.startswith("/"):
            continue
        path = Path(raw)
        if path in seen or path.name == "restic":
            continue
        seen.add(path)
        text = read_static_text(path)
        if text:
            return text
    return ""


def flag_value(text: str, flag: str, variables: dict[str, str]) -> str | None:
    pattern = re.compile(
        rf"(?:^|\s){re.escape(flag)}(?:=|\s+)(\"[^\"]*\"|'[^']*'|[^\s\\;]+)",
        re.MULTILINE,
    )
    match = pattern.search(text.replace("\\\n", " "))
    return expand_static_value(match.group(1), variables) if match else None


def restic_backup_command(
    service: str,
    timer: str,
    description: str,
    exec_text: str,
    script_text: str,
) -> bool:
    command = exec_text + "\n" + script_text
    identity = " ".join((service, timer, description, command)).lower()
    return bool(re.search(r"\bbackup\b", command, re.IGNORECASE) and "restic" in identity)


def discovered_name(description: str, service: str) -> str:
    match = re.search(r"\brestic\s+backup\s+of\s+(.+?)(?:\s+to\b|$)", description, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        return value[:1].upper() + value[1:]

    stem = service.removesuffix(".service")
    words = [word for word in re.split(r"[-_.]+", stem) if word.lower() not in {"restic", "backup"}]
    return " ".join(word[:1].upper() + word[1:] for word in words) or "Restic backup"


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return result or "restic-backup"


def parse_properties(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def systemd_show(
    unit: str,
    properties: list[str],
    runner: Runner,
    timeout: int,
) -> dict[str, Any]:
    command = [
        "systemctl",
        "--user",
        "--timestamp=unix",
        "show",
        unit,
        "--no-pager",
    ]
    for prop in properties:
        command.extend(["--property", prop])
    try:
        result = runner.run(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "error": sanitize(error)}
    if result.returncode != 0:
        return {"available": False, "error": sanitize(result.stderr or result.stdout)}
    return {"available": True, "properties": parse_properties(result.stdout)}


def list_user_timers(runner: Runner, timeout: int) -> list[str]:
    command = [
        "systemctl",
        "--user",
        "list-unit-files",
        "--type=timer",
        "--no-legend",
        "--no-pager",
    ]
    try:
        result = runner.run(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DiscoveryError(f"Could not list user timers: {sanitize(error)}") from error
    if result.returncode != 0:
        raise DiscoveryError(
            "Could not list user timers: " + sanitize(result.stderr or result.stdout)
        )
    return sorted(
        {
            line.split()[0]
            for line in result.stdout.splitlines()
            if line.split() and UNIT_PATTERN.fullmatch(line.split()[0]) and line.split()[0].endswith(".timer")
        }
    )


def timer_services(timer: str, runner: Runner, timeout: int) -> list[str]:
    shown = systemd_show(timer, ["Triggers"], runner, timeout)
    if shown["available"]:
        services = [
            unit
            for unit in shown["properties"].get("Triggers", "").split()
            if UNIT_PATTERN.fullmatch(unit) and unit.endswith(".service")
        ]
        if services:
            return services
    return [timer.removesuffix(".timer") + ".service"]


def discovered_job(
    service: str,
    timer: str,
    runner: Runner,
    timeout: int,
) -> dict[str, Any] | None:
    shown = systemd_show(
        service,
        ["LoadState", "Description", "FragmentPath", "ExecStart", "Environment", "EnvironmentFiles"],
        runner,
        timeout,
    )
    if not shown["available"]:
        return None

    properties = shown["properties"]
    description = properties.get("Description", "")
    fragment_text = read_static_text(Path(properties.get("FragmentPath", "")))
    exec_text = properties.get("ExecStart", "")
    exec_path, argv = exec_details(exec_text)
    script_text = wrapper_text(exec_path, argv)
    if not restic_backup_command(service, timer, description, exec_text, script_text):
        return None

    variables = static_variables()
    environment_files = environment_file_paths(properties.get("EnvironmentFiles", ""), variables)
    for directive in directive_values(fragment_text, "EnvironmentFile"):
        expanded = expand_static_value(directive.lstrip("-"), variables)
        if expanded:
            environment_files.append(expanded_path(expanded))
    for path in environment_files:
        parse_assignments(read_static_text(path), variables)

    parse_environment(properties.get("Environment", ""), variables)
    for directive in directive_values(fragment_text, "Environment"):
        parse_environment(directive, variables)
    parse_assignments(script_text, variables)

    command_text = exec_text + "\n" + script_text
    repository = flag_value(command_text, "--repository-file", variables)
    password = flag_value(command_text, "--password-file", variables)
    repository = repository or variables.get("RESTIC_REPOSITORY_FILE", "")
    password = password or variables.get("RESTIC_PASSWORD_FILE", "")
    tag = flag_value(command_text, "--tag", variables) or ""

    executable = "restic"
    if Path(exec_path).name == "restic":
        executable = exec_path
    elif variables.get("RESTIC"):
        executable = variables["RESTIC"]

    name = discovered_name(description, service)
    missing = []
    if not repository:
        missing.append("RESTIC_REPOSITORY_FILE")
    if not password:
        missing.append("RESTIC_PASSWORD_FILE")
    discovery_error = ""
    if missing:
        discovery_error = "Automatic discovery could not find " + " and ".join(missing)

    return {
        "id": slug(name),
        "name": name,
        "service": service,
        "timer": timer,
        "repositoryFile": str(expanded_path(repository)) if repository else "",
        "passwordFile": str(expanded_path(password)) if password else "",
        "restic": executable,
        "tag": tag,
        "maxRunAgeHours": 36,
        "checkService": None,
        "checkTimer": None,
        "checkMaxAgeHours": 720,
        "source": "systemd",
        "discoveryError": discovery_error,
    }


def discover_jobs(runner: Runner, timeout: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen_services: set[str] = set()
    for timer in list_user_timers(runner, timeout):
        for service in timer_services(timer, runner, timeout):
            if service in seen_services:
                continue
            seen_services.add(service)
            job = discovered_job(service, timer, runner, timeout)
            if job:
                jobs.append(job)

    used_ids: set[str] = set()
    for job in jobs:
        base = job["id"]
        suffix = 2
        while job["id"] in used_ids:
            job["id"] = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(job["id"])
    return jobs


def service_state(unit: str, runner: Runner, timeout: int) -> dict[str, Any]:
    shown = systemd_show(
        unit,
        [
            "LoadState",
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainCode",
            "ExecMainStatus",
            "ExecMainStartTimestamp",
            "ExecMainExitTimestamp",
        ],
        runner,
        timeout,
    )
    if not shown["available"]:
        return {
            "unit": unit,
            "available": False,
            "active": False,
            "error": shown.get("error", "systemd user manager unavailable"),
        }

    props = shown["properties"]
    active_state = props.get("ActiveState", "unknown")
    return {
        "unit": unit,
        "available": True,
        "loadState": props.get("LoadState", "unknown"),
        "activeState": active_state,
        "subState": props.get("SubState", "unknown"),
        "result": props.get("Result", ""),
        "exitCode": props.get("ExecMainCode", ""),
        "exitStatus": int(props.get("ExecMainStatus", "0") or 0),
        "startedAt": iso_from_systemd_timestamp(props.get("ExecMainStartTimestamp")),
        "finishedAt": iso_from_systemd_timestamp(props.get("ExecMainExitTimestamp")),
        "active": active_state in {"active", "activating", "reloading"},
    }


def timer_state(unit: str, runner: Runner, timeout: int) -> dict[str, Any]:
    shown = systemd_show(
        unit,
        [
            "LoadState",
            "ActiveState",
            "SubState",
            "UnitFileState",
            "LastTriggerUSec",
            "NextElapseUSecRealtime",
            "Persistent",
        ],
        runner,
        timeout,
    )
    if not shown["available"]:
        return {
            "unit": unit,
            "available": False,
            "error": shown.get("error", "systemd user manager unavailable"),
        }

    props = shown["properties"]
    return {
        "unit": unit,
        "available": True,
        "loadState": props.get("LoadState", "unknown"),
        "activeState": props.get("ActiveState", "unknown"),
        "subState": props.get("SubState", "unknown"),
        "unitFileState": props.get("UnitFileState", "unknown"),
        "lastTriggerAt": iso_from_systemd_timestamp(props.get("LastTriggerUSec")),
        "nextRunAt": iso_from_systemd_timestamp(props.get("NextElapseUSecRealtime")),
        "persistent": props.get("Persistent", "").lower() == "yes",
    }


def parse_journal_lines(output: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def journal_history(unit: str, runner: Runner, timeout: int) -> dict[str, Any]:
    command = [
        "journalctl",
        "--user",
        f"USER_UNIT={unit}",
        "--no-pager",
        "--output=json",
        "--lines=80",
    ]
    try:
        result = runner.run(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "lastRun": None, "lastSuccessAt": None, "error": sanitize(error)}
    if result.returncode != 0:
        return {
            "available": False,
            "lastRun": None,
            "lastSuccessAt": None,
            "error": sanitize(result.stderr or result.stdout),
        }

    invocations: dict[str, dict[str, Any]] = {}
    completions: list[dict[str, Any]] = []
    last_success: str | None = None

    for entry in parse_journal_lines(result.stdout):
        message_id = str(entry.get("MESSAGE_ID") or "")
        invocation_id = str(entry.get("USER_INVOCATION_ID") or "")
        timestamp = iso_from_microseconds(entry.get("__REALTIME_TIMESTAMP"))
        if not timestamp:
            continue
        if not invocation_id:
            invocation_id = f"journal-{entry.get('__CURSOR', timestamp)}"
        invocation = invocations.setdefault(invocation_id, {"invocationId": invocation_id})

        if message_id == UNIT_STARTING:
            invocation["startedAt"] = timestamp
            continue

        if message_id in {UNIT_STARTED, UNIT_SUCCESS, UNIT_FAILED, UNIT_FAILURE_RESULT}:
            successful = message_id in {UNIT_STARTED, UNIT_SUCCESS} and entry.get("JOB_RESULT", "done") == "done"
            invocation.update(
                {
                    "finishedAt": timestamp,
                    "result": "success" if successful else "failed",
                    "message": sanitize(entry.get("MESSAGE")),
                }
            )
            invocation["durationSec"] = elapsed_seconds(invocation.get("startedAt"), timestamp)
            completions.append(dict(invocation))
            if successful:
                last_success = timestamp

    completions.sort(key=lambda run: run.get("finishedAt", ""))
    return {
        "available": True,
        "lastRun": completions[-1] if completions else None,
        "lastSuccessAt": last_success,
        "error": "",
    }


def fallback_run_from_systemd(state: dict[str, Any]) -> dict[str, Any] | None:
    if not state.get("finishedAt"):
        return None
    successful = state.get("result") in {"", "success"} and state.get("exitStatus", 0) == 0
    return {
        "invocationId": "",
        "startedAt": state.get("startedAt"),
        "finishedAt": state.get("finishedAt"),
        "durationSec": elapsed_seconds(state.get("startedAt"), state.get("finishedAt")),
        "result": "success" if successful else "failed",
        "message": "Completed successfully" if successful else f"Service result: {state.get('result') or 'failed'}",
    }


def collect_unit(unit: str, runner: Runner, timeout: int) -> dict[str, Any]:
    state = service_state(unit, runner, timeout)
    history = journal_history(unit, runner, timeout)
    last_run = history.get("lastRun") or fallback_run_from_systemd(state)
    last_success = history.get("lastSuccessAt")
    if not last_success and last_run and last_run.get("result") == "success":
        last_success = last_run.get("finishedAt")
    state.update(
        {
            "historyAvailable": history.get("available", False),
            "historyError": history.get("error", ""),
            "lastRun": last_run,
            "lastSuccessAt": last_success,
        }
    )
    return state


def journal_tail(unit: str, lines: int, runner: Runner, timeout: int) -> list[str]:
    command = [
        "journalctl",
        "--user",
        "--unit",
        unit,
        "--no-pager",
        "--output=json",
        f"--lines={lines}",
    ]
    try:
        result = runner.run(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    messages = [sanitize(entry.get("MESSAGE"), 240) for entry in parse_journal_lines(result.stdout)]
    return [message for message in messages if message][-lines:]


def resolve_restic(command: str) -> str | None:
    if "/" in command:
        candidate = expanded_path(command)
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(command)


def restic_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "RESTIC_REPOSITORY",
        "RESTIC_REPOSITORY_FILE",
        "RESTIC_PASSWORD",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_PASSWORD_COMMAND",
    ):
        env.pop(key, None)
    env["LC_ALL"] = "C"
    return env


def restic_command(job: dict[str, Any], restic: str, cache_dir: Path) -> list[str]:
    return [
        restic,
        "--json",
        "--cache-dir",
        str(cache_dir),
        "--repository-file",
        job["repositoryFile"],
        "--password-file",
        job["passwordFile"],
    ]


def normalized_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = {
        "files_new": "filesNew",
        "files_changed": "filesChanged",
        "files_unmodified": "filesUnmodified",
        "total_files_processed": "totalFilesProcessed",
        "total_bytes_processed": "totalBytesProcessed",
        "data_added": "dataAdded",
        "data_added_packed": "dataAddedPacked",
        "total_duration": "totalDurationSec",
        "backup_start": "backupStartedAt",
        "backup_end": "backupFinishedAt",
    }
    return {target: raw[source] for source, target in keys.items() if source in raw}


def normalized_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = str(raw.get("short_id") or raw.get("id") or "")
    if len(snapshot_id) > 8:
        snapshot_id = snapshot_id[:8]
    return {
        "id": snapshot_id,
        "time": raw.get("time"),
        "hostname": str(raw.get("hostname") or ""),
        "paths": raw.get("paths") if isinstance(raw.get("paths"), list) else [],
        "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        "summary": normalized_summary(raw.get("summary")),
    }


def normalized_stats(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = {
        "total_size": "totalSize",
        "total_uncompressed_size": "totalUncompressedSize",
        "total_file_count": "totalFileCount",
        "snapshots_count": "snapshotsCount",
        "compression_ratio": "compressionRatio",
        "compression_progress": "compressionProgress",
        "compression_space_saving": "compressionSpaceSaving",
    }
    return {target: raw[source] for source, target in keys.items() if source in raw}


def cache_key(job: dict[str, Any]) -> str:
    relevant = {
        "repositoryFile": job["repositoryFile"],
        "passwordFile": job["passwordFile"],
        "restic": job["restic"],
        "tag": job["tag"],
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def cache_file(cache_dir: Path, config_path: Path, job_id: str) -> Path:
    config_hash = hashlib.sha256(str(config_path).encode()).hexdigest()[:12]
    return cache_dir / config_hash / f"{job_id}.json"


def load_cache(path: Path, expected_key: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("cacheKey") != expected_key:
        return None
    return value


def write_cache(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def cache_is_fresh(cache: dict[str, Any] | None, now: datetime, max_age_seconds: int) -> bool:
    if not cache:
        return False
    checked = parse_iso(cache.get("checkedAt"))
    return bool(checked and (now - checked).total_seconds() <= max_age_seconds)


def repository_from_cache(cache: dict[str, Any], status: str, source: str, error: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "checkedAt": cache.get("checkedAt"),
        "snapshotCount": cache.get("snapshotCount", 0),
        "latestSnapshot": cache.get("latestSnapshot"),
        "stats": cache.get("stats", {}),
        "error": error,
    }


def repository_cache_failure(
    path: Path,
    cached: dict[str, Any],
    status: str,
    error: str,
    now: datetime,
) -> dict[str, Any]:
    updated = dict(cached)
    updated.update({"status": status, "error": error, "lastAttemptAt": isoformat(now)})
    try:
        write_cache(path, updated)
    except OSError:
        pass
    return repository_from_cache(updated, status, "cache", error)


def restic_failure_status(returncode: int) -> str:
    return "busy" if returncode == 11 else "unavailable"


def collect_repository(
    job: dict[str, Any],
    *,
    active: bool,
    config_path: Path,
    cache_dir: Path,
    cache_seconds: int,
    force: bool,
    runner: Runner,
    timeout: int,
    now: datetime,
) -> dict[str, Any]:
    key = cache_key(job)
    path = cache_file(cache_dir, config_path, job["id"])
    cached = load_cache(path, key)

    if active:
        if cached:
            return repository_from_cache(cached, "deferred", "cache", "Refresh deferred while backup is active")
        return {
            "status": "deferred",
            "source": "none",
            "checkedAt": None,
            "snapshotCount": 0,
            "latestSnapshot": None,
            "stats": {},
            "error": "Refresh deferred while backup is active",
        }

    if not force and cache_is_fresh(cached, now, cache_seconds):
        return repository_from_cache(
            cached,
            str(cached.get("status") or "ready"),
            "cache",
            str(cached.get("error") or ""),
        )

    if job.get("discoveryError"):
        error = job["discoveryError"] + ". Add a jobs.json override for this service."
        return repository_cache_failure(path, cached, "stale", error, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": error,
        }

    repository_file = Path(job["repositoryFile"])
    password_file = Path(job["passwordFile"])
    if not repository_file.is_file():
        error = f"Repository file not found: {repository_file}"
        return repository_cache_failure(path, cached, "stale", error, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": error,
        }
    if not password_file.is_file():
        error = f"Password file not found: {password_file}"
        return repository_cache_failure(path, cached, "stale", error, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": error,
        }

    restic = resolve_restic(job["restic"])
    if not restic:
        error = f"Restic command not found: {job['restic']}"
        return repository_cache_failure(path, cached, "stale", error, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": error,
        }

    restic_cache_dir = path.parent / "restic" / job["id"]
    try:
        restic_cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        message = f"Could not create restic cache: {sanitize(error)}"
        return repository_cache_failure(path, cached, "stale", message, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": message,
        }

    base = restic_command(job, restic, restic_cache_dir)
    snapshot_command = base + ["snapshots"]
    if job["tag"]:
        snapshot_command.extend(["--tag", job["tag"]])
    try:
        snapshots_result = runner.run(snapshot_command, timeout=timeout, env=restic_environment())
    except (OSError, subprocess.TimeoutExpired) as error:
        message = sanitize(error)
        return repository_cache_failure(path, cached, "stale", message, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": message,
        }

    if snapshots_result.returncode != 0:
        status = restic_failure_status(snapshots_result.returncode)
        message = sanitize(snapshots_result.stderr or snapshots_result.stdout or "Restic snapshot query failed")
        if cached:
            if status == "busy":
                return repository_from_cache(cached, "deferred", "cache", message)
            return repository_cache_failure(path, cached, "stale", message, now)
        return {
            "status": status,
            "source": "none",
            "checkedAt": None,
            "snapshotCount": 0,
            "latestSnapshot": None,
            "stats": {},
            "error": message,
        }

    try:
        raw_snapshots = json.loads(snapshots_result.stdout)
    except json.JSONDecodeError:
        raw_snapshots = None
    if not isinstance(raw_snapshots, list):
        message = "Restic returned invalid snapshot JSON"
        return repository_cache_failure(path, cached, "stale", message, now) if cached else {
            "status": "unavailable", "source": "none", "checkedAt": None,
            "snapshotCount": 0, "latestSnapshot": None, "stats": {}, "error": message,
        }

    snapshots = [normalized_snapshot(item) for item in raw_snapshots if isinstance(item, dict)]
    snapshots.sort(
        key=lambda snapshot: (
            parse_iso(snapshot.get("time")) or datetime.min.replace(tzinfo=UTC)
        )
    )

    stats_command = base + ["stats", "--mode", "raw-data"]
    if job["tag"]:
        stats_command.extend(["--tag", job["tag"]])
    stats: dict[str, Any] = {}
    partial_error = ""
    try:
        stats_result = runner.run(stats_command, timeout=timeout, env=restic_environment())
        if stats_result.returncode == 0:
            raw_stats = json.loads(stats_result.stdout)
            if isinstance(raw_stats, dict):
                stats = normalized_stats(raw_stats)
            else:
                partial_error = "Restic returned invalid stats JSON"
        else:
            partial_error = sanitize(stats_result.stderr or stats_result.stdout or "Restic stats query failed")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        partial_error = sanitize(error or "Restic returned invalid stats JSON")

    checked_at = isoformat(now)
    payload = {
        "cacheKey": key,
        "checkedAt": checked_at,
        "lastAttemptAt": checked_at,
        "status": "partial" if partial_error else "ready",
        "error": partial_error,
        "snapshotCount": len(snapshots),
        "latestSnapshot": snapshots[-1] if snapshots else None,
        "stats": stats,
    }
    try:
        write_cache(path, payload)
    except OSError as error:
        partial_error = partial_error or f"Could not update cache: {sanitize(error)}"

    return {
        "status": "partial" if partial_error else "ready",
        "source": "live",
        "checkedAt": checked_at,
        "snapshotCount": len(snapshots),
        "latestSnapshot": snapshots[-1] if snapshots else None,
        "stats": stats,
        "error": partial_error,
    }


def age_hours(value: str | None, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600)


def issue(code: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def evaluate_integrity(
    job: dict[str, Any], runner: Runner, timeout: int, now: datetime
) -> dict[str, Any]:
    if not job.get("checkService"):
        return {
            "status": "not-configured",
            "statusText": "Not configured",
            "lastRun": None,
            "lastSuccessAt": None,
            "timer": None,
        }

    service = collect_unit(job["checkService"], runner, timeout)
    timer = timer_state(job["checkTimer"], runner, timeout) if job.get("checkTimer") else None
    last_run = service.get("lastRun")
    last_success = service.get("lastSuccessAt")
    if service.get("active"):
        status = "running"
        status_text = "Integrity check in progress"
    elif service.get("available") and service.get("loadState") != "loaded":
        status = "attention"
        status_text = "Integrity-check service is not loaded"
    elif last_run and last_run.get("result") == "failed":
        status = "attention"
        status_text = last_run.get("message") or "Last integrity check failed"
    elif timer and not timer.get("available"):
        status = "unknown"
        status_text = "Integrity-check timer status is unavailable"
    elif timer and (
        timer.get("loadState") != "loaded"
        or timer.get("activeState") != "active"
        or timer.get("unitFileState") in {"disabled", "masked", "bad"}
    ):
        status = "attention"
        status_text = "Integrity-check timer needs attention"
    elif not last_success:
        status = "unknown"
        status_text = "No completed integrity check"
    elif (age_hours(last_success, now) or 0) > job["checkMaxAgeHours"]:
        status = "attention"
        status_text = "Integrity check is overdue"
    else:
        status = "healthy"
        status_text = "Last integrity check passed"
    return {
        "status": status,
        "statusText": status_text,
        "lastRun": last_run,
        "lastSuccessAt": last_success,
        "timer": timer,
    }


def evaluate_job(
    job: dict[str, Any],
    *,
    config_path: Path,
    cache_dir: Path,
    cache_seconds: int,
    force: bool,
    log_lines: int,
    runner: Runner,
    timeout: int,
    now: datetime,
) -> dict[str, Any]:
    service = collect_unit(job["service"], runner, timeout)
    timer = timer_state(job["timer"], runner, timeout)
    repository = collect_repository(
        job,
        active=service.get("active", False),
        config_path=config_path,
        cache_dir=cache_dir,
        cache_seconds=cache_seconds,
        force=force,
        runner=runner,
        timeout=timeout,
        now=now,
    )
    integrity = evaluate_integrity(job, runner, timeout, now)
    issues: list[dict[str, str]] = []

    if service.get("available") and service.get("loadState") != "loaded":
        issues.append(issue("service-not-loaded", f"Service is {service.get('loadState', 'not loaded')}", "critical"))
    elif not service.get("available"):
        issues.append(issue("service-unavailable", "Systemd service status is unavailable", "unknown"))

    if timer.get("available"):
        if timer.get("loadState") != "loaded":
            issues.append(issue("timer-not-loaded", f"Timer is {timer.get('loadState', 'not loaded')}", "critical"))
        elif timer.get("unitFileState") in {"disabled", "masked", "bad"}:
            issues.append(issue("timer-disabled", f"Timer is {timer.get('unitFileState')}", "critical"))
        elif timer.get("activeState") != "active":
            issues.append(issue("timer-inactive", "Timer is not active", "critical"))
    else:
        issues.append(issue("timer-unavailable", "Systemd timer status is unavailable", "unknown"))

    last_run = service.get("lastRun")
    last_success = service.get("lastSuccessAt")
    if last_run and last_run.get("result") == "failed":
        issues.append(issue("last-run-failed", last_run.get("message") or "Last run failed", "critical"))
    elif last_success:
        hours = age_hours(last_success, now)
        if hours is not None and hours > job["maxRunAgeHours"]:
            issues.append(issue("run-overdue", f"No successful run in {int(hours)} hours", "critical"))

    if repository["status"] in {"unavailable", "stale", "partial"}:
        message = repository.get("error") or "Repository metadata is unavailable"
        issues.append(issue("repository-" + repository["status"], message))

    if integrity["status"] == "attention":
        issues.append(issue("integrity-attention", integrity["statusText"]))
    elif integrity["status"] == "unknown":
        issues.append(issue("integrity-unknown", integrity["statusText"], "unknown"))

    critical = any(entry["severity"] == "critical" for entry in issues)
    warnings = any(entry["severity"] == "warning" for entry in issues)
    if critical:
        status = "attention"
    elif service.get("active"):
        status = "running"
    elif warnings:
        status = "attention"
    elif issues or not last_success or not service.get("available") or not timer.get("available"):
        status = "unknown"
    else:
        status = "healthy"

    if status == "running":
        status_text = "Backup in progress"
    elif status == "attention":
        primary_issue = next(
            (entry for entry in issues if entry["severity"] in {"critical", "warning"}),
            issues[0],
        )
        status_text = primary_issue["message"]
    elif status == "unknown":
        unknown_issue = next((entry for entry in issues if entry["severity"] == "unknown"), None)
        status_text = unknown_issue["message"] if unknown_issue else "Waiting for a verified completed run"
    else:
        status_text = "Last run completed successfully"

    logs = (
        journal_tail(job["service"], log_lines, runner, timeout)
        if last_run and last_run.get("result") == "failed"
        else []
    )
    return {
        "id": job["id"],
        "name": job["name"],
        "source": job.get("source", "config"),
        "status": status,
        "statusText": status_text,
        "service": service,
        "timer": timer,
        "repository": repository,
        "integrity": integrity,
        "issues": issues,
        "logTail": logs,
    }


def empty_report(
    config_path: Path,
    now: datetime,
    error: str,
    *,
    source: str = "file",
) -> dict[str, Any]:
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": isoformat(now),
        "overallStatus": "unknown",
        "summary": {"jobs": 0, "healthy": 0, "running": 0, "attention": 0, "unknown": 0},
        "config": {"path": str(config_path), "status": "error", "source": source, "error": error},
        "jobs": [],
    }


def collect_report(
    config_path: Path,
    *,
    cache_dir: Path,
    cache_seconds: int,
    force: bool,
    log_lines: int,
    timeout: int,
    runner: Runner | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    runner = runner or Runner()
    now = now or utc_now()
    if config_path.exists():
        try:
            configured_jobs = load_config(config_path)
        except ConfigError as error:
            return empty_report(config_path, now, str(error))
        config_status = "ready"
        config_source = "file"
    else:
        try:
            configured_jobs = discover_jobs(runner, timeout)
        except DiscoveryError as error:
            return empty_report(config_path, now, str(error), source="systemd")
        if not configured_jobs:
            return empty_report(
                config_path,
                now,
                "No supported Restic backup timers were discovered. Add a jobs.json override for a dynamic setup.",
                source="systemd",
            )
        config_status = "discovered"
        config_source = "systemd"

    jobs = [
        evaluate_job(
            job,
            config_path=config_path,
            cache_dir=cache_dir,
            cache_seconds=cache_seconds,
            force=force,
            log_lines=log_lines,
            runner=runner,
            timeout=timeout,
            now=now,
        )
        for job in configured_jobs
    ]
    summary = {"jobs": len(jobs), "healthy": 0, "running": 0, "attention": 0, "unknown": 0}
    for job in jobs:
        summary[job["status"]] += 1

    if summary["attention"]:
        overall = "attention"
    elif summary["running"]:
        overall = "running"
    elif summary["healthy"] == summary["jobs"]:
        overall = "healthy"
    else:
        overall = "unknown"

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": isoformat(now),
        "overallStatus": overall,
        "summary": summary,
        "config": {
            "path": str(config_path),
            "status": config_status,
            "source": config_source,
            "error": "",
        },
        "jobs": jobs,
    }


def parser() -> argparse.ArgumentParser:
    default_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "omarchy-restic"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--config",
        required=True,
        type=expanded_path,
        help="Optional jobs.json override; systemd discovery is used when the file is absent",
    )
    result.add_argument("--cache-dir", type=expanded_path, default=default_cache)
    result.add_argument("--repository-cache-seconds", type=int, default=900)
    result.add_argument("--force-repositories", action="store_true")
    result.add_argument("--log-lines", type=int, default=12)
    result.add_argument("--timeout-seconds", type=int, default=30)
    result.add_argument("--pretty", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 60 <= args.repository_cache_seconds <= 86400:
        parser().error("--repository-cache-seconds must be between 60 and 86400")
    if not 1 <= args.log_lines <= 100:
        parser().error("--log-lines must be between 1 and 100")
    if not 5 <= args.timeout_seconds <= 300:
        parser().error("--timeout-seconds must be between 5 and 300")

    report = collect_report(
        args.config,
        cache_dir=args.cache_dir,
        cache_seconds=args.repository_cache_seconds,
        force=args.force_repositories,
        log_lines=args.log_lines,
        timeout=args.timeout_seconds,
    )
    json.dump(report, sys.stdout, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

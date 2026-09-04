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
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
CONFIG_SCHEMA_VERSION = 1
DISCOVERY_CACHE_SCHEMA_VERSION = 1

UNIT_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"
UNIT_STARTED = "39f53479d3a045ac8e11786248231fbf"
UNIT_FAILED = "be02cf6855d2428ba40df7e9d022f03d"
UNIT_SUCCESS = "7ad2d189f7e94e70a38c781354912448"
UNIT_FAILURE_RESULT = "d9b373ed55a64feb8242e02dbe79a49c"

UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]+\.(?:service|timer)$")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
URI_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
ASSIGNMENT = re.compile(
    r"(?P<name>\b[A-Za-z0-9_][A-Za-z0-9_-]{0,127})"
    r"(?P<separator>\s*[:=]\s*)"
    r'(?P<value>"(?:[^"\\]|\\.)*"|'
    r"'(?:[^'\\]|\\.)*'|[^\s,;]+)"
)
AUTHORIZATION = re.compile(
    r"(?i)\b(?P<name>(?:proxy[_-]?)?authorization)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?:(?P<scheme>bearer|basic)\s+)?"
    r"(?P<value>[^\s,;]+)"
)
BEARER_TOKEN = re.compile(
    r"(?i)\b(?P<scheme>bearer)\s+(?P<value>[A-Za-z0-9._~+/=-]+)"
)
SHELL_PARAMETER = re.compile(r"\$\{([^{}]+)\}")
SHELL_VARIABLE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
SHELL_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)
EXEC_PATH = re.compile(r"(?:^|[ {;])path=([^ ;}]+)")
EXEC_ARGV = re.compile(r"argv\[\]=(.*?)\s+;\s+(?:ignore_errors|start_time)=")
EXEC_ENTRY = re.compile(
    r"\{\s*path=(?P<path>[^ ;}]+)\s*;\s*"
    r"argv\[\]=(?P<argv>.*?)\s+;\s+(?:ignore_errors|start_time)=",
    re.DOTALL,
)
ENVIRONMENT_FILE = re.compile(
    r'(?:"([^"]+)"|(\S+))\s+\(ignore_errors=(?:yes|no)\)'
)
SYSTEMD_TIMESPAN_TOKEN = re.compile(
    r"\s*(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>usec|us|µs|msec|ms|seconds?|sec|s|minutes?|min|hours?|hr|h|days?|d|weeks?|w|months?|years?|yr|y)",
    re.IGNORECASE,
)
SYSTEMD_TIMESPAN_SECONDS = {
    "usec": 0.000001,
    "us": 0.000001,
    "µs": 0.000001,
    "msec": 0.001,
    "ms": 0.001,
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "s": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "h": 3600,
    "day": 86400,
    "days": 86400,
    "d": 86400,
    "week": 604800,
    "weeks": 604800,
    "w": 604800,
    "month": 2629800,
    "months": 2629800,
    "year": 31557600,
    "years": 31557600,
    "yr": 31557600,
    "y": 31557600,
}

RESTIC_GLOBAL_FLAGS_WITH_VALUE = {
    "--cache-dir",
    "--cacert",
    "--compression",
    "--key-hint",
    "--limit-download",
    "--limit-upload",
    "--option",
    "--pack-size",
    "--password-command",
    "--password-file",
    "--repo",
    "--repository-file",
    "--retry-lock",
    "--tls-client-cert",
    "-o",
    "-p",
    "-r",
}
RESTIC_GLOBAL_BOOLEAN_FLAGS = {
    "--cleanup-cache",
    "--help",
    "--insecure-tls",
    "--json",
    "--no-cache",
    "--no-extra-verify",
    "--no-lock",
    "--quiet",
    "--verbose",
    "--version",
    "-h",
    "-q",
    "-v",
    "-vv",
}

COMMAND_WRAPPERS = {
    "env": (
        {
            "-a",
            "--argv0",
            "-u",
            "--unset",
            "-C",
            "--chdir",
            "-S",
            "--split-string",
        },
        0,
    ),
    "nice": ({"-n", "--adjustment"}, 0),
    "ionice": (
        {
            "-c",
            "--class",
            "-n",
            "--classdata",
            "-p",
            "--pid",
            "-P",
            "--pgid",
            "-u",
            "--uid",
        },
        0,
    ),
    "chrt": (
        {
            "-T",
            "--sched-runtime",
            "-P",
            "--sched-period",
            "-D",
            "--sched-deadline",
        },
        1,
    ),
    "timeout": ({"-k", "--kill-after", "-s", "--signal"}, 1),
    "flock": (
        {
            "-w",
            "--timeout",
            "--wait",
            "-E",
            "--conflict-exit-code",
            "-c",
            "--command",
            "--start",
            "--length",
        },
        1,
    ),
    "systemd-inhibit": (
        {"--json", "--what", "--who", "--why", "--mode"},
        0,
    ),
}
SHELL_INTERPRETERS = {"ash", "bash", "dash", "ksh", "sh", "zsh"}
SHELL_OPTIONS_WITH_VALUE = {"-O", "+O", "-o", "+o", "--init-file", "--rcfile"}


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
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


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


def systemd_timespan_seconds(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0", "n/a", "[not set]", "infinity", "∞"}:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text) / 1_000_000

    position = 0
    total = 0.0
    matched = False
    for match in SYSTEMD_TIMESPAN_TOKEN.finditer(text):
        if text[position : match.start()].strip():
            return None
        total += float(match.group("value")) * SYSTEMD_TIMESPAN_SECONDS[match.group("unit").lower()]
        position = match.end()
        matched = True
    if not matched or text[position:].strip():
        return None
    return total if total > 0 else None


def iso_from_monotonic_deadline(
    value: Any,
    now: datetime,
    wake_system: bool,
) -> str | None:
    deadline = systemd_timespan_seconds(value)
    if deadline is None:
        return None
    clock = time.CLOCK_MONOTONIC
    if wake_system and hasattr(time, "CLOCK_BOOTTIME"):
        clock = time.CLOCK_BOOTTIME
    try:
        remaining = deadline - time.clock_gettime(clock)
    except (OSError, ValueError):
        return None
    return isoformat(now + timedelta(seconds=max(0.0, remaining)))


def elapsed_seconds(start: str | None, end: str | None) -> float | None:
    started = parse_iso(start)
    finished = parse_iso(end)
    if not started or not finished:
        return None
    return max(0.0, round((finished - started).total_seconds(), 3))


def credential_name(value: str) -> bool:
    lowered = value.lower()
    parts = [part for part in re.split(r"[_-]+", lowered) if part]
    compact = "".join(parts)
    return (
        any(
            part in {
                "password",
                "passwords",
                "passwd",
                "passwds",
                "pass",
                "passphrase",
                "passphrases",
                "credential",
                "credentials",
                "sas",
                "secret",
                "secrets",
                "token",
                "tokens",
                "key",
                "keys",
            }
            for part in parts
        )
        or bool(re.search(r"(?:password|passwd|passphrase|secret|token|key|credential|sas)s?$", compact))
    )


def redact_assignment(match: re.Match[str]) -> str:
    if not credential_name(match.group("name")):
        return match.group(0)
    return match.group("name") + match.group("separator") + "[redacted]"


def redact_authorization(match: re.Match[str]) -> str:
    scheme = match.group("scheme")
    prefix = match.group("name") + match.group("separator")
    return prefix + (scheme + " " if scheme else "") + "[redacted]"


def redact_bearer(match: re.Match[str]) -> str:
    return match.group("scheme") + " [redacted]"


def sanitize(text: Any, limit: int = 360) -> str:
    value = " ".join(str(text or "").split())
    value = URI_USERINFO.sub(r"\1[redacted]@", value)
    value = AUTHORIZATION.sub(redact_authorization, value)
    value = BEARER_TOKEN.sub(redact_bearer, value)
    value = ASSIGNMENT.sub(redact_assignment, value)
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def number(
    value: Any,
    fallback: float,
    minimum: float,
    maximum: float,
    context: str,
) -> float:
    if value is None:
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{context} must be a number")
    if result < minimum or result > maximum:
        raise ConfigError(f"{context} must be between {minimum:g} and {maximum:g}")
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
        "maxRunAgeHours": number(
            raw.get("maxRunAgeHours"), 36, 1, 8760, f"{context}.maxRunAgeHours"
        ),
        "maxRunAgeExplicit": "maxRunAgeHours" in raw,
        "checkService": optional_unit(raw, "checkService", ".service", context),
        "checkTimer": optional_unit(raw, "checkTimer", ".timer", context),
        "checkMaxAgeHours": number(
            raw.get("checkMaxAgeHours"), 720, 1, 8760, f"{context}.checkMaxAgeHours"
        ),
    }


def load_config(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Jobs file not found: {path}") from error
    except OSError as error:
        raise ConfigError(f"Jobs file is not readable: {path}: {sanitize(error)}") from error
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
    services = [job["service"] for job in normalized]
    if len(services) != len(set(services)):
        raise ConfigError("Job services must be unique")
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


def exec_entries(value: str) -> list[tuple[str, str]]:
    entries = [
        (match.group("path"), match.group("argv"))
        for match in EXEC_ENTRY.finditer(value)
    ]
    if entries:
        return entries

    path_match = EXEC_PATH.search(value)
    argv_match = EXEC_ARGV.search(value)
    return [
        (
            path_match.group(1) if path_match else "",
            argv_match.group(1) if argv_match else value,
        )
    ]


def read_wrapper_script(path: Path) -> str:
    try:
        with path.open("rb") as candidate:
            if candidate.read(2) != b"#!":
                return ""
    except OSError:
        return ""
    return read_static_text(path)


def wrapper_text(exec_path: str, argv: str) -> str:
    try:
        tokens = shlex.split(argv, comments=False, posix=True)
    except ValueError:
        tokens = []

    candidates = [exec_path]
    index = command_index(tokens)
    if index is not None:
        candidates.append(tokens[index])
        if Path(tokens[index]).name in SHELL_INTERPRETERS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            if index < len(tokens):
                candidates.append(tokens[index])

    seen: set[Path] = set()
    for raw in candidates:
        if not raw.startswith("/"):
            continue
        path = Path(raw)
        if path in seen or path.name == "restic":
            continue
        seen.add(path)
        text = read_wrapper_script(path)
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


def shell_command_segments(text: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for line in text.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens = list(lexer)
        except ValueError:
            continue

        segment: list[str] = []
        for token in tokens:
            if token and all(character in ";&|()" for character in token):
                if segment:
                    segments.append(segment)
                    segment = []
            else:
                segment.append(token)
        if segment:
            segments.append(segment)
    return segments


def option_end(
    tokens: list[str], index: int, options_with_values: set[str]
) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if token == "-" or not token.startswith("-"):
            break
        option = token.split("=", 1)[0]
        index += 1
        if option in options_with_values and "=" not in token:
            index += 1
    return index


def command_index(tokens: list[str]) -> int | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        if token in {"!", "command", "elif", "exec", "if", "then", "time"}:
            index += 1
            continue

        wrapper = COMMAND_WRAPPERS.get(Path(token).name)
        if wrapper is None:
            break
        options_with_values, positional_arguments = wrapper
        index = option_end(tokens, index + 1, options_with_values)
        if Path(token).name == "env":
            while index < len(tokens) and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]
            ):
                index += 1
        else:
            index += positional_arguments
        if index < len(tokens) and tokens[index] == "--":
            index += 1
    return index if index < len(tokens) else None


def restic_command_token(value: str) -> bool:
    return Path(value).name == "restic" or bool(
        re.fullmatch(r"\$(?:RESTIC|\{RESTIC\})", value)
    )


def restic_subcommand(tokens: list[str]) -> str | None:
    index = command_index(tokens)
    if index is None or not restic_command_token(tokens[index]):
        return None
    index += 1

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1] if index + 1 < len(tokens) else None
        if token in RESTIC_GLOBAL_FLAGS_WITH_VALUE:
            index += 2
            continue
        if any(
            token.startswith(flag + "=")
            for flag in RESTIC_GLOBAL_FLAGS_WITH_VALUE
            if flag.startswith("--")
        ):
            index += 1
            continue
        if (
            token in RESTIC_GLOBAL_BOOLEAN_FLAGS
            or re.fullmatch(r"-v+", token)
            or (
                "=" in token
                and token.split("=", 1)[0] in RESTIC_GLOBAL_BOOLEAN_FLAGS
            )
        ):
            index += 1
            continue
        if token.startswith("-"):
            return None
        return token
    return None


def shell_command_argument(tokens: list[str]) -> str | None:
    index = command_index(tokens)
    if index is None or Path(tokens[index]).name not in SHELL_INTERPRETERS:
        return None
    index += 1

    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            index += 1
            if option in SHELL_OPTIONS_WITH_VALUE and "=" not in token:
                index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            if "c" in token[1:]:
                return " ".join(tokens[index:]) if index < len(tokens) else None
            if token in SHELL_OPTIONS_WITH_VALUE:
                index += 1
            continue
        if token.startswith("+") and token != "+":
            index += 2 if token in SHELL_OPTIONS_WITH_VALUE else 1
            continue
        break
    return None


def backup_command_segment(tokens: list[str]) -> bool:
    if restic_subcommand(tokens) == "backup":
        return True
    command = shell_command_argument(tokens)
    if command is None:
        return False
    return any(
        restic_subcommand(nested) == "backup"
        for nested in shell_command_segments(command)
    )


def restic_backup_command(exec_text: str, script_text: str) -> bool:
    return any(
        backup_command_segment(tokens)
        for text in [argv for _, argv in exec_entries(exec_text)] + [script_text]
        for tokens in shell_command_segments(text)
    )


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


def discovered_id(service: str) -> str:
    stem = service.removesuffix(".service")
    base = slug(stem)
    if stem == base:
        return base
    digest = hashlib.sha256(service.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def parse_properties(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            if key in {"TimersCalendar", "TimersMonotonic"} and key in result:
                result[key] += "\n" + value
            else:
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
        raise DiscoveryError(f"Could not inspect {service}: {shown.get('error', '')}")

    properties = shown["properties"]
    description = properties.get("Description", "")
    fragment_text = read_static_text(Path(properties.get("FragmentPath", "")))
    exec_text = properties.get("ExecStart", "")
    entries = exec_entries(exec_text)
    script_text = "\n".join(
        text
        for path, arguments in entries
        if (text := wrapper_text(path, arguments))
    )
    if not restic_backup_command(exec_text, script_text):
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
    direct_restic = next(
        (path for path, _ in entries if Path(path).name == "restic"), ""
    )
    if direct_restic:
        executable = direct_restic
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
        "id": discovered_id(service),
        "name": name,
        "service": service,
        "timer": timer,
        "repositoryFile": str(expanded_path(repository)) if repository else "",
        "passwordFile": str(expanded_path(password)) if password else "",
        "restic": executable,
        "tag": tag,
        "maxRunAgeHours": 36,
        "maxRunAgeExplicit": False,
        "checkService": None,
        "checkTimer": None,
        "checkMaxAgeHours": 720,
        "source": "systemd",
        "discoveryError": discovery_error,
    }


def discover_jobs(runner: Runner, timeout: int) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    failed_services: list[str] = []
    seen_services: set[str] = set()
    for timer in list_user_timers(runner, timeout):
        for service in timer_services(timer, runner, timeout):
            if service in seen_services:
                continue
            seen_services.add(service)
            try:
                job = discovered_job(service, timer, runner, timeout)
            except DiscoveryError:
                failed_services.append(service)
                continue
            if job:
                jobs.append(job)
    return jobs, failed_services


def merge_jobs(
    discovered_jobs: list[dict[str, Any]],
    configured_jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    configured_services = {job["service"] for job in configured_jobs}
    merged = [dict(job, source="config") for job in configured_jobs]
    used_ids = {job["id"] for job in configured_jobs}
    for discovered in discovered_jobs:
        if discovered["service"] in configured_services:
            continue
        job = dict(discovered)
        if job["id"] in used_ids:
            base = job["id"]
            digest = hashlib.sha256(job["service"].encode()).hexdigest()[:8]
            job["id"] = f"{base}-{digest}"
            suffix = 2
            while job["id"] in used_ids:
                job["id"] = f"{base}-{digest}-{suffix}"
                suffix += 1
        merged.append(job)
        used_ids.add(job["id"])
    return merged


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
    try:
        exit_status = int(props.get("ExecMainStatus", "0") or 0)
    except (TypeError, ValueError):
        exit_status = 0
    return {
        "unit": unit,
        "available": True,
        "loadState": props.get("LoadState", "unknown"),
        "activeState": active_state,
        "subState": props.get("SubState", "unknown"),
        "result": props.get("Result", ""),
        "exitCode": props.get("ExecMainCode", ""),
        "exitStatus": exit_status,
        "startedAt": iso_from_systemd_timestamp(props.get("ExecMainStartTimestamp")),
        "finishedAt": iso_from_systemd_timestamp(props.get("ExecMainExitTimestamp")),
        "active": active_state in {"active", "activating", "reloading"},
    }


def timer_state(
    unit: str,
    runner: Runner,
    timeout: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    shown = systemd_show(
        unit,
        [
            "LoadState",
            "ActiveState",
            "SubState",
            "UnitFileState",
            "LastTriggerUSec",
            "NextElapseUSecMonotonic",
            "NextElapseUSecRealtime",
            "Persistent",
            "WakeSystem",
            "TimersCalendar",
            "TimersMonotonic",
            "RandomizedDelayUSec",
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
    wake_system = props.get("WakeSystem", "").lower() == "yes"
    next_run = iso_from_systemd_timestamp(props.get("NextElapseUSecRealtime"))
    if not next_run:
        next_run = iso_from_monotonic_deadline(
            props.get("NextElapseUSecMonotonic"), now or utc_now(), wake_system
        )
    return {
        "unit": unit,
        "available": True,
        "loadState": props.get("LoadState", "unknown"),
        "activeState": props.get("ActiveState", "unknown"),
        "subState": props.get("SubState", "unknown"),
        "unitFileState": props.get("UnitFileState", "unknown"),
        "lastTriggerAt": iso_from_systemd_timestamp(props.get("LastTriggerUSec")),
        "nextRunAt": next_run,
        "persistent": props.get("Persistent", "").lower() == "yes",
        "wakeSystem": wake_system,
        "calendar": re.findall(r"OnCalendar=(.*?)\s+;\s+next_elapse=", props.get("TimersCalendar", "")),
        "intervalsSec": [
            seconds
            for value in re.findall(
                r"OnUnit(?:Active|Inactive)USec=(.*?)\s+;",
                props.get("TimersMonotonic", ""),
            )
            if (seconds := systemd_timespan_seconds(value)) is not None
        ],
        "randomizedDelaySec": systemd_timespan_seconds(props.get("RandomizedDelayUSec")) or 0,
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


def parse_journal_runs(output: str) -> list[dict[str, Any]]:
    invocations: dict[str, dict[str, Any]] = {}
    completions: dict[str, dict[str, Any]] = {}

    for entry in parse_journal_lines(output):
        message_id = str(entry.get("MESSAGE_ID") or "")
        invocation_id = str(entry.get("USER_INVOCATION_ID") or "")
        timestamp = iso_from_microseconds(entry.get("__REALTIME_TIMESTAMP"))
        if not timestamp:
            continue
        if not invocation_id:
            invocation_id = f"journal-{entry.get('__CURSOR', timestamp)}"
        invocation = invocations.setdefault(
            invocation_id, {"invocationId": invocation_id}
        )

        if message_id == UNIT_STARTING:
            invocation["startedAt"] = timestamp
            continue

        if message_id in {
            UNIT_STARTED,
            UNIT_SUCCESS,
            UNIT_FAILED,
            UNIT_FAILURE_RESULT,
        }:
            successful = (
                message_id in {UNIT_STARTED, UNIT_SUCCESS}
                and entry.get("JOB_RESULT", "done") == "done"
            )
            invocation.update(
                {
                    "finishedAt": timestamp,
                    "result": "success" if successful else "failed",
                    "message": sanitize(entry.get("MESSAGE")),
                }
            )
            invocation["durationSec"] = elapsed_seconds(
                invocation.get("startedAt"), timestamp
            )
            completions[invocation_id] = dict(invocation)

    return sorted(completions.values(), key=lambda run: run.get("finishedAt", ""))


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
        return {
            "available": False,
            "lastRun": None,
            "lastSuccessAt": None,
            "error": sanitize(error),
        }
    if result.returncode != 0:
        return {
            "available": False,
            "lastRun": None,
            "lastSuccessAt": None,
            "error": sanitize(result.stderr or result.stdout),
        }

    completed = parse_journal_runs(result.stdout)
    successful = [run for run in completed if run.get("result") == "success"]
    return {
        "available": True,
        "lastRun": completed[-1] if completed else None,
        "lastSuccessAt": successful[-1]["finishedAt"] if successful else None,
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


def config_cache_directory(cache_dir: Path, config_path: Path) -> Path:
    config_hash = hashlib.sha256(str(config_path).encode()).hexdigest()[:12]
    return cache_dir / config_hash


def cache_file(cache_dir: Path, config_path: Path, job_id: str) -> Path:
    return config_cache_directory(cache_dir, config_path) / f"{job_id}.json"


def discovery_cache_file(cache_dir: Path, config_path: Path) -> Path:
    return config_cache_directory(cache_dir, config_path) / ".discovered-jobs.json"


def ensure_private_cache_directory(cache_dir: Path, directory: Path) -> None:
    relative = directory.relative_to(cache_dir)
    current = cache_dir
    current.mkdir(parents=True, exist_ok=True, mode=0o700)
    current.chmod(0o700)
    for part in relative.parts:
        current /= part
        current.mkdir(exist_ok=True, mode=0o700)
        current.chmod(0o700)


def load_cache(path: Path, expected_key: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("cacheKey") != expected_key:
        return None
    return value


def write_cache(path: Path, value: dict[str, Any]) -> None:
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


def read_discovery_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != DISCOVERY_CACHE_SCHEMA_VERSION
        or not isinstance(value.get("jobs"), list)
    ):
        return None

    required = {
        "id", "name", "service", "timer", "repositoryFile", "passwordFile",
        "restic", "tag", "maxRunAgeHours", "checkService", "checkTimer",
        "checkMaxAgeHours", "discoveryError",
    }
    if any(
        not isinstance(job, dict) or not required.issubset(job)
        for job in value["jobs"]
    ):
        return None
    return value


def load_discovery_cache(path: Path) -> list[dict[str, Any]] | None:
    value = read_discovery_cache(path)
    if value is None:
        return None
    return [dict(job, source="systemd-cache") for job in value["jobs"]]


def save_discovery_cache(
    path: Path,
    jobs: list[dict[str, Any]],
    cache_dir: Path,
) -> None:
    payload = {"schemaVersion": DISCOVERY_CACHE_SCHEMA_VERSION, "jobs": jobs}
    ensure_private_cache_directory(cache_dir, path.parent)
    current = read_discovery_cache(path)
    if current is not None and (current == payload or (not jobs and current["jobs"])):
        path.chmod(0o600)
        return
    write_cache(path, payload)


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


def repository_none(status: str, error: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": "none",
        "checkedAt": None,
        "snapshotCount": 0,
        "latestSnapshot": None,
        "stats": {},
        "error": error,
    }


def repository_uncached_failure(
    path: Path,
    key: str,
    status: str,
    error: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        write_cache(
            path,
            {
                "cacheKey": key,
                "checkedAt": None,
                "lastAttemptAt": isoformat(now),
                "status": status,
                "error": error,
                "snapshotCount": 0,
                "latestSnapshot": None,
                "stats": {},
            },
        )
    except OSError:
        pass
    return repository_none(status, error)


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
    try:
        ensure_private_cache_directory(cache_dir, path.parent)
    except OSError as error:
        return repository_uncached_failure(
            path, key, "unavailable", f"Could not secure plugin cache: {sanitize(error)}", now
        )
    cached = load_cache(path, key)

    if active:
        if cached:
            return repository_from_cache(cached, "deferred", "cache", "Refresh deferred while backup is active")
        return repository_none("deferred", "Refresh deferred while backup is active")

    if not force and cache_is_fresh(cached, now, cache_seconds):
        return repository_from_cache(
            cached,
            str(cached.get("status") or "ready"),
            "cache",
            str(cached.get("error") or ""),
        )

    attempted = parse_iso(cached.get("lastAttemptAt") if cached else None)
    if (
        not force
        and cached
        and attempted
        and (now - attempted).total_seconds() <= cache_seconds
    ):
        return repository_from_cache(
            cached,
            str(cached.get("status") or "stale"),
            "cache",
            str(cached.get("error") or ""),
        )

    if job.get("discoveryError"):
        error = job["discoveryError"] + ". Add a jobs.json override for this service."
        return (
            repository_cache_failure(path, cached, "stale", error, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", error, now)
        )

    repository_file = Path(job["repositoryFile"])
    password_file = Path(job["passwordFile"])
    if not repository_file.is_file():
        error = f"Repository file not found: {repository_file}"
        return (
            repository_cache_failure(path, cached, "stale", error, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", error, now)
        )
    if not password_file.is_file():
        error = f"Password file not found: {password_file}"
        return (
            repository_cache_failure(path, cached, "stale", error, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", error, now)
        )

    restic = resolve_restic(job["restic"])
    if not restic:
        error = f"Restic command not found: {job['restic']}"
        return (
            repository_cache_failure(path, cached, "stale", error, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", error, now)
        )

    restic_cache_dir = path.parent / "restic" / job["id"]
    try:
        ensure_private_cache_directory(cache_dir, restic_cache_dir)
    except OSError as error:
        message = f"Could not create restic cache: {sanitize(error)}"
        return (
            repository_cache_failure(path, cached, "stale", message, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", message, now)
        )

    base = restic_command(job, restic, restic_cache_dir)
    snapshot_command = base + ["snapshots"]
    if job["tag"]:
        snapshot_command.extend(["--tag", job["tag"]])
    try:
        snapshots_result = runner.run(snapshot_command, timeout=timeout, env=restic_environment())
    except (OSError, subprocess.TimeoutExpired) as error:
        message = sanitize(error)
        return (
            repository_cache_failure(path, cached, "stale", message, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", message, now)
        )

    if snapshots_result.returncode != 0:
        status = restic_failure_status(snapshots_result.returncode)
        message = sanitize(snapshots_result.stderr or snapshots_result.stdout or "Restic snapshot query failed")
        if cached:
            if status == "busy":
                return repository_from_cache(cached, "deferred", "cache", message)
            return repository_cache_failure(path, cached, "stale", message, now)
        return repository_uncached_failure(path, key, status, message, now)

    try:
        raw_snapshots = json.loads(snapshots_result.stdout)
    except json.JSONDecodeError:
        raw_snapshots = None
    if not isinstance(raw_snapshots, list):
        message = "Restic returned invalid snapshot JSON"
        return (
            repository_cache_failure(path, cached, "stale", message, now)
            if cached
            else repository_uncached_failure(path, key, "unavailable", message, now)
        )

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
        partial_error = sanitize(error)

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


def timer_interval_hours(
    timer: dict[str, Any], reference: str | None, runner: Runner, timeout: int
) -> float | None:
    last = parse_iso(reference)
    if not last:
        return None
    intervals = list(timer.get("intervalsSec", []))
    calendar = timer.get("calendar", [])
    if calendar:
        command = [
            "systemd-analyze", "calendar", f"--base-time=@{last.timestamp():.6f}",
            "--", *calendar,
        ]
        try:
            result = runner.run(command, timeout=timeout, env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            for timestamp in re.findall(
                r"^\s*(?:Next elapse|\(in UTC\)):\s+\w+\s+"
                r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) UTC\s*$",
                result.stdout,
                re.MULTILINE,
            ):
                upcoming = parse_iso(timestamp)
                if upcoming and upcoming > last:
                    intervals.append((upcoming - last).total_seconds())
    if not intervals:
        return None
    return (min(intervals) + timer.get("randomizedDelaySec", 0)) / 3600


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
    timer = (
        timer_state(job["checkTimer"], runner, timeout, now)
        if job.get("checkTimer")
        else None
    )
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
    timer = timer_state(job["timer"], runner, timeout, now)
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
    max_age = job["maxRunAgeHours"]
    if not job.get("maxRunAgeExplicit"):
        interval = timer_interval_hours(timer, last_success or timer.get("lastTriggerAt"), runner, timeout)
        if interval:
            max_age = max(max_age, interval + 12)
    if last_run and last_run.get("result") == "failed":
        issues.append(issue("last-run-failed", last_run.get("message") or "Last run failed", "critical"))
    elif last_success:
        hours = age_hours(last_success, now)
        if hours is not None and hours > max_age:
            issues.append(issue("run-overdue", f"No successful run in {int(hours)} hours", "critical"))
    elif timer.get("available") and timer.get("lastTriggerAt"):
        hours = age_hours(timer.get("lastTriggerAt"), now)
        if hours is not None and hours > max_age:
            issues.append(issue(
                "run-unverified",
                f"Timer last triggered {int(hours)} hours ago without a verified successful run",
                "warning",
            ))

    if repository["status"] in {"unavailable", "stale"}:
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
    config_present = config_path.exists()
    configured_jobs: list[dict[str, Any]] = []
    config_error = ""
    if config_present:
        try:
            configured_jobs = load_config(config_path)
        except ConfigError as error:
            config_error = str(error)

    discovery_path = discovery_cache_file(cache_dir, config_path)
    discovery_error = ""
    try:
        discovered_jobs, failed_services = discover_jobs(runner, timeout)
        if failed_services:
            cached_jobs = load_discovery_cache(discovery_path) or []
            cached_by_service = {job["service"]: job for job in cached_jobs}
            for service in failed_services:
                cached = cached_by_service.get(service)
                if cached:
                    discovered_jobs.append(cached)
            discovery_error = (
                "Could not inspect "
                + ", ".join(failed_services)
                + "; using cached discovery"
            )
        try:
            save_discovery_cache(discovery_path, discovered_jobs, cache_dir)
        except OSError:
            pass
    except DiscoveryError as error:
        discovery_error = str(error)
        cached_jobs = load_discovery_cache(discovery_path)
        if cached_jobs is None:
            discovered_jobs = []
        else:
            discovered_jobs = cached_jobs

    job_definitions = merge_jobs(discovered_jobs, configured_jobs)

    errors = "; ".join(error for error in (config_error, discovery_error) if error)
    if not job_definitions:
        if errors:
            source = "file" if config_present else "systemd"
            return empty_report(config_path, now, errors, source=source)
        return empty_report(
            config_path,
            now,
            "No supported Restic backup timers were discovered. Add a jobs.json override for a dynamic setup.",
            source="systemd",
        )

    if config_error:
        config_status = "error"
    elif discovery_error:
        config_status = "degraded"
    elif config_present:
        config_status = "ready"
    else:
        config_status = "discovered"
    config_source = "file" if config_present else "systemd"

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
        for job in job_definitions
    ]
    summary = {"jobs": len(jobs), "healthy": 0, "running": 0, "attention": 0, "unknown": 0}
    for job in jobs:
        summary[job["status"]] += 1

    if summary["attention"]:
        overall = "attention"
    elif summary["running"]:
        overall = "running"
    elif errors:
        overall = "unknown"
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
            "error": errors,
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
        help="Optional jobs.json overrides merged with systemd discovery",
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

from __future__ import annotations

import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Mapping, Optional

from .config import Config
from .models import BookingError


UNITS = {
    "monitor": "bk-monitor.service",
    "worker": "bk-worker.service",
}

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def default_user_unit_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return config_home / "systemd" / "user"


def unit_text(
    kind: str,
    python_executable: Optional[Path] = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    filename = _unit_filename(kind)
    executable = Path(python_executable or sys.executable)
    if not executable.is_absolute():
        raise BookingError("Python executable for systemd must be an absolute path")
    template = resources.files("bk").joinpath("data", "systemd", filename).read_text(encoding="utf-8")
    environment_text = "\n".join(
        _systemd_environment_line(name, value)
        for name, value in sorted((environment or {}).items())
    )
    return template.replace("@PYTHON_EXECUTABLE@", _quote_systemd_argument(str(executable))).replace(
        "@SERVICE_ENVIRONMENT@",
        environment_text,
    )


def install_user_unit(
    kind: str,
    target_dir: Optional[Path] = None,
    *,
    environment: Mapping[str, str],
    force: bool = False,
) -> Path:
    filename = _unit_filename(kind)
    directory = (target_dir or default_user_unit_dir()).expanduser()
    destination = directory / filename
    if destination.exists() and not force:
        raise BookingError(f"systemd unit already exists: {destination}; pass --force to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{filename}.{os.getpid()}.tmp"
    try:
        temporary.write_text(unit_text(kind, environment=environment), encoding="utf-8")
        temporary.chmod(0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def service_environment(config: Config, kind: str) -> dict[str, str]:
    _unit_filename(kind)
    environment = {
        "BK_DATA_DIR": str(_absolute_path(config.data_dir)),
        "PYTHONUNBUFFERED": "1",
    }
    if kind == "worker" and config.job_log_dir is not None:
        environment["BK_JOB_LOG_DIR"] = str(_absolute_path(config.job_log_dir))
    return environment


def _unit_filename(kind: str) -> str:
    try:
        return UNITS[kind]
    except KeyError as exc:
        raise BookingError(f"unknown service kind: {kind}") from exc


def _quote_systemd_argument(value: str) -> str:
    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise BookingError("invalid Python executable path for systemd")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _systemd_environment_line(name: str, value: str) -> str:
    if not _ENVIRONMENT_NAME.fullmatch(name):
        raise BookingError(f"invalid systemd environment name: {name}")
    text = str(value)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise BookingError(f"invalid control character in systemd environment value: {name}")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'Environment="{name}={escaped}"'


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))

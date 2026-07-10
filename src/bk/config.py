from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_PRIVATE_FILE_MODE = 0o600
DEFAULT_PRIVATE_DIR_MODE = 0o700


@dataclass(frozen=True)
class Config:
    data_dir: Path
    gpu_count: int = 1
    max_shared_users: int = 2
    queue_search_hours: int = 168
    lock_timeout_seconds: float = 10.0
    backup_keep: int = 10
    timeline_hours: int = 24
    require_shared_memory: bool = False
    shared_memory_reserve_mb: int = 512
    job_log_dir: Optional[Path] = None
    worker_poll_seconds: float = 1.0
    worker_claim_timeout_seconds: float = 30.0
    file_mode: int = DEFAULT_PRIVATE_FILE_MODE
    dir_mode: int = DEFAULT_PRIVATE_DIR_MODE


def _read_config_file(data_dir: Path) -> Dict[str, Any]:
    path = data_dir / "config.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def _int_value(raw: Dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be >= 1")
    return parsed


def _float_value(raw: Dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be > 0")
    return parsed


def _bool_value(raw: Dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{key} must be a boolean")


def _nonnegative_int_value(raw: Dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be >= 0")
    return parsed


def _mode_value(raw: Dict[str, Any], key: str, default: int, *, directory: bool) -> int:
    value = raw.get(key, default)
    try:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized.startswith("0o"):
                normalized = normalized[2:]
            parsed = int(normalized, 8)
        else:
            parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an octal mode such as 0600 or 2770") from exc
    allowed = 0o7777 if directory else 0o777
    if parsed < 0 or parsed & ~allowed:
        raise ValueError(f"invalid {key}")
    if not directory and parsed & 0o111:
        raise ValueError("file_mode must not contain executable bits")
    return parsed


def _default_data_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    return data_home / "bk"


def load_config() -> Config:
    data_dir = Path(os.environ["BK_DATA_DIR"]).expanduser() if "BK_DATA_DIR" in os.environ else _default_data_dir()
    raw = _read_config_file(data_dir)

    env_map = {
        "gpu_count": "BK_GPU_COUNT",
        "max_shared_users": "BK_MAX_SHARED_USERS",
        "queue_search_hours": "BK_QUEUE_SEARCH_HOURS",
        "lock_timeout_seconds": "BK_LOCK_TIMEOUT_SECONDS",
        "backup_keep": "BK_BACKUP_KEEP",
        "timeline_hours": "BK_TIMELINE_HOURS",
        "require_shared_memory": "BK_REQUIRE_SHARED_MEMORY",
        "shared_memory_reserve_mb": "BK_SHARED_MEMORY_RESERVE_MB",
        "worker_poll_seconds": "BK_WORKER_POLL_SECONDS",
        "worker_claim_timeout_seconds": "BK_WORKER_CLAIM_TIMEOUT_SECONDS",
        "file_mode": "BK_FILE_MODE",
        "dir_mode": "BK_DIR_MODE",
    }
    for key, env_name in env_map.items():
        if env_name in os.environ:
            raw[key] = os.environ[env_name]

    job_log_raw = os.environ.get("BK_JOB_LOG_DIR", raw.get("job_log_dir"))
    if job_log_raw:
        job_log_dir = Path(str(job_log_raw)).expanduser()
    else:
        state_home = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
        job_log_dir = state_home / "bk" / "jobs"

    return Config(
        data_dir=data_dir,
        gpu_count=_int_value(raw, "gpu_count", 1),
        max_shared_users=_int_value(raw, "max_shared_users", 2),
        queue_search_hours=_int_value(raw, "queue_search_hours", 168),
        lock_timeout_seconds=_float_value(raw, "lock_timeout_seconds", 10.0),
        backup_keep=_int_value(raw, "backup_keep", 10),
        timeline_hours=_int_value(raw, "timeline_hours", 24),
        require_shared_memory=_bool_value(raw, "require_shared_memory", False),
        shared_memory_reserve_mb=_nonnegative_int_value(raw, "shared_memory_reserve_mb", 512),
        job_log_dir=job_log_dir,
        worker_poll_seconds=_float_value(raw, "worker_poll_seconds", 1.0),
        worker_claim_timeout_seconds=_float_value(raw, "worker_claim_timeout_seconds", 30.0),
        file_mode=_mode_value(raw, "file_mode", DEFAULT_PRIVATE_FILE_MODE, directory=False),
        dir_mode=_mode_value(raw, "dir_mode", DEFAULT_PRIVATE_DIR_MODE, directory=True),
    )

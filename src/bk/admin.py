from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .config import (
    CONFIG_VERSION,
    MAX_GPU_COUNT,
    MAX_SHARED_UNITS,
    SYSTEM_CONFIG_FILE,
    Config,
)
from .fileio import fsync_directory, open_existing_regular
from .gpu import detect_gpu_count, snapshot
from .granularity import DEFAULT_SLOT_MINUTES, validate_slot_minutes
from .models import BookingError


ADMIN_SCHEMA_VERSION = "gpubk.admin.v1"
DEFAULT_SYSTEM_DATA_DIR = Path("/var/lib/gpubk")
CONFIG_DIRECTORY_MODE = 0o755
CONFIG_FILE_MODE = 0o644
ALL_USERS_FILE_MODE = 0o666
ALL_USERS_DIR_MODE = 0o777
GROUP_FILE_MODE = 0o660
GROUP_DIR_MODE = 0o2770


@dataclass(frozen=True)
class AdminIdentity:
    uid: int
    username: str
    primary_gid: int


@dataclass(frozen=True)
class AdminInitPlan:
    config_file: Path
    data_dir: Path
    access: str
    gpu_count: int
    slot_minutes: int
    max_shared_users: int
    require_shared_memory: bool
    monitor: AdminIdentity
    group_name: Optional[str]
    storage_gid: Optional[int]
    file_mode: int
    dir_mode: int

    def config_document(self) -> dict:
        document = {
            "config_version": CONFIG_VERSION,
            "data_dir": str(self.data_dir),
            "gpu_count": self.gpu_count,
            "slot_minutes": self.slot_minutes,
            "max_shared_users": self.max_shared_users,
            "queue_search_hours": 168,
            "require_shared_memory": self.require_shared_memory,
            "shared_memory_reserve_mb": 512,
            "monitor_uid": self.monitor.uid,
            "file_mode": f"{self.file_mode:04o}",
            "dir_mode": f"{self.dir_mode:04o}",
        }
        if self.storage_gid is not None:
            document["storage_gid"] = self.storage_gid
        return document

    def public_document(self, *, status: str) -> dict:
        warning = None
        if self.access == "all":
            warning = (
                "all local accounts are trusted participants and can modify the shared "
                "data directory outside GPUbk"
            )
        return {
            "schema_version": ADMIN_SCHEMA_VERSION,
            "kind": "admin-init",
            "status": status,
            "config_file": str(self.config_file),
            "data_dir": str(self.data_dir),
            "access": {
                "mode": self.access,
                "group": self.group_name,
                "file_mode": f"{self.file_mode:04o}",
                "dir_mode": f"{self.dir_mode:04o}",
                "warning": warning,
            },
            "gpu_count": self.gpu_count,
            "slot_minutes": self.slot_minutes,
            "max_shared_users": self.max_shared_users,
            "require_shared_memory": self.require_shared_memory,
            "monitor": {
                "uid": self.monitor.uid,
                "username": self.monitor.username,
            },
            "config": self.config_document(),
        }


@dataclass(frozen=True)
class AdminInspection:
    existing_config: Optional[dict]
    data_exists: bool
    data_nonempty: bool
    config_action: str
    data_action: str

    def public_document(self) -> dict:
        return {
            "config_action": self.config_action,
            "data_action": self.data_action,
            "data_exists": self.data_exists,
            "data_nonempty": self.data_nonempty,
        }


def run_admin_cli(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bk admin",
        description="Initialize a shared GPUbk server without editing JSON by hand.",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    init_parser = commands.add_parser(
        "init",
        help="preview or initialize shared server configuration",
    )
    init_parser.add_argument("--config-file", type=Path, default=SYSTEM_CONFIG_FILE)
    init_parser.add_argument("--data-dir", type=Path)
    init_parser.add_argument("--access", choices=("all", "group"))
    init_parser.add_argument("--group", help="existing Unix group used by --access group")
    init_parser.add_argument("--gpu-count", type=int)
    init_parser.add_argument("--slot-minutes", type=int)
    init_parser.add_argument("--max-shared-users", type=int)
    init_parser.add_argument("--monitor-user", help="non-root username or numeric UID")
    memory = init_parser.add_mutually_exclusive_group()
    memory.add_argument(
        "--require-shared-memory",
        dest="require_shared_memory",
        action="store_true",
    )
    memory.add_argument(
        "--allow-implicit-shared-memory",
        dest="require_shared_memory",
        action="store_false",
    )
    init_parser.set_defaults(require_shared_memory=None)
    init_parser.add_argument("--yes", action="store_true", help="apply without confirmation")
    init_parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="replace a different config only while the selected data directory is empty",
    )
    init_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv))

    interactive = sys.stdin.isatty() and not args.yes and not args.json
    detected_gpu_count = _detected_gpu_count(args.gpu_count)
    defaults = _default_monitor_identity(args.monitor_user)
    plan = _build_plan(args, detected_gpu_count, defaults, interactive=interactive)
    _validate_plan(plan)
    inspection = inspect_admin_init(plan, force=args.force, expected_owner=0)

    if not args.json:
        _print_plan(plan, inspection)

    if args.dry_run:
        if args.json:
            print(
                json.dumps(
                    {
                        **plan.public_document(status="dry-run"),
                        "inspection": inspection.public_document(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        return 0
    if not args.yes:
        if not interactive:
            if args.json:
                print(
                    json.dumps(
                        {
                            **plan.public_document(status="planned"),
                            "inspection": inspection.public_document(),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            print("bk: pass --yes to apply this administrator plan", file=sys.stderr)
            return 1
        answer = input("Apply this configuration? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No changes made.")
            return 1

    result = apply_admin_init(plan, force=args.force)
    if args.json:
        payload = plan.public_document(status="initialized")
        payload["inspection"] = inspection.public_document()
        payload["result"] = result
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"initialized config: {plan.config_file}")
        print(f"initialized data:   {plan.data_dir}")
        print("ready: local users with the bk command can make reservations")
        print(
            f"next: run 'bk doctor --probe --strict' as {plan.monitor.username}, "
            "then optionally install the monitor service"
        )
    return 0


def apply_admin_init(
    plan: AdminInitPlan,
    *,
    force: bool = False,
    require_root: bool = True,
) -> dict:
    if require_root and os.geteuid() != 0:
        raise BookingError("administrator initialization must run as root; use sudo bk admin init")

    expected_owner = 0 if require_root else os.geteuid()
    inspection = inspect_admin_init(plan, force=force, expected_owner=expected_owner)
    desired_config = plan.config_document()
    data_created = _prepare_data_directory(plan, nonempty=inspection.data_nonempty)
    config_changed = inspection.existing_config != desired_config
    backup = None
    if config_changed:
        backup = _atomic_write_config(
            plan.config_file,
            desired_config,
            previous=inspection.existing_config,
        )
    return {
        "config_changed": config_changed,
        "config_backup": str(backup) if backup is not None else None,
        "data_created": data_created,
    }


def inspect_admin_init(
    plan: AdminInitPlan,
    *,
    force: bool = False,
    expected_owner: int = 0,
) -> AdminInspection:
    _validate_plan(plan)
    _validate_config_destination(plan.config_file, expected_owner=expected_owner)
    existing_config = _read_existing_config(
        plan.config_file,
        expected_owner=expected_owner,
    )
    desired_config = plan.config_document()
    data_exists = os.path.lexists(plan.data_dir)
    data_nonempty = _directory_nonempty(plan.data_dir) if data_exists else False

    if data_exists:
        metadata = plan.data_dir.lstat()
        actual_mode = stat.S_IMODE(metadata.st_mode)
        gid_mismatch = plan.storage_gid is not None and metadata.st_gid != plan.storage_gid
        if data_nonempty and (actual_mode != plan.dir_mode or gid_mismatch):
            raise BookingError(
                "refusing to change mode or group of a non-empty data directory; "
                "use a reviewed migration instead"
            )
    elif not plan.data_dir.parent.is_dir():
        raise BookingError(f"data-directory parent does not exist: {plan.data_dir.parent}")

    if existing_config is None and data_nonempty:
        raise BookingError(
            "refusing to initialize an unconfigured non-empty data directory; "
            "use a reviewed migration instead"
        )
    if existing_config is not None and existing_config != desired_config:
        if not force:
            raise BookingError(
                f"configuration already exists and differs: {plan.config_file}; "
                "review it or pass --force while the selected data directory is empty"
            )
        if data_nonempty:
            raise BookingError(
                "refusing to replace configuration for a non-empty data directory; "
                "use a reviewed migration instead"
            )

    config_action = (
        "create"
        if existing_config is None
        else "unchanged"
        if existing_config == desired_config
        else "replace"
    )
    if not data_exists:
        data_action = "create"
    elif stat.S_IMODE(plan.data_dir.lstat().st_mode) != plan.dir_mode:
        data_action = "repair-empty-permissions"
    elif plan.storage_gid is not None and plan.data_dir.lstat().st_gid != plan.storage_gid:
        data_action = "repair-empty-group"
    else:
        data_action = "unchanged"
    return AdminInspection(
        existing_config=existing_config,
        data_exists=data_exists,
        data_nonempty=data_nonempty,
        config_action=config_action,
        data_action=data_action,
    )


def _build_plan(
    args: argparse.Namespace,
    detected_gpu_count: int,
    default_monitor: Optional[AdminIdentity],
    *,
    interactive: bool,
) -> AdminInitPlan:
    data_default = _absolute_path(DEFAULT_SYSTEM_DATA_DIR)
    data_dir = _ask_absolute_path(
        "Shared data directory",
        args.data_dir or data_default,
        enabled=interactive and args.data_dir is None,
    )
    access = _ask_choice(
        "Access mode",
        args.access or "all",
        ("all", "group"),
        enabled=interactive and args.access is None,
    )
    group_name = args.group
    storage_gid = None
    if access == "group":
        group_name, group_record = _ask_group(
            group_name or "gpuusers",
            enabled=interactive and args.group is None,
        )
        storage_gid = int(group_record.gr_gid)
        file_mode = GROUP_FILE_MODE
        dir_mode = GROUP_DIR_MODE
    else:
        if group_name:
            raise BookingError("--group is only valid with --access group")
        group_name = None
        file_mode = ALL_USERS_FILE_MODE
        dir_mode = ALL_USERS_DIR_MODE

    gpu_count = _ask_int(
        "GPU count",
        args.gpu_count if args.gpu_count is not None else detected_gpu_count,
        minimum=1,
        maximum=MAX_GPU_COUNT,
        enabled=interactive and args.gpu_count is None,
    )
    slot_minutes = _ask_slot_minutes(
        args.slot_minutes if args.slot_minutes is not None else DEFAULT_SLOT_MINUTES,
        enabled=interactive and args.slot_minutes is None,
    )
    max_shared_users = _ask_int(
        "Shared capacity units per GPU",
        args.max_shared_users if args.max_shared_users is not None else 2,
        minimum=1,
        maximum=MAX_SHARED_UNITS,
        enabled=interactive and args.max_shared_users is None,
    )
    require_shared_memory = (
        args.require_shared_memory
        if args.require_shared_memory is not None
        else _ask_bool(
            "Require expected VRAM for shared bookings",
            True,
            enabled=interactive,
        )
    )

    monitor_default = default_monitor.username if default_monitor is not None else ""
    monitor_value = _ask(
        "Monitor account",
        args.monitor_user or monitor_default,
        enabled=interactive and args.monitor_user is None,
    )
    if not monitor_value:
        raise BookingError("could not infer a non-root monitor account; pass --monitor-user USER")
    monitor = _ask_identity(
        monitor_value,
        enabled=interactive and args.monitor_user is None,
    )

    return AdminInitPlan(
        config_file=_absolute_path(args.config_file),
        data_dir=data_dir,
        access=access,
        gpu_count=gpu_count,
        slot_minutes=slot_minutes,
        max_shared_users=max_shared_users,
        require_shared_memory=require_shared_memory,
        monitor=monitor,
        group_name=group_name,
        storage_gid=storage_gid,
        file_mode=file_mode,
        dir_mode=dir_mode,
    )


def _validate_plan(plan: AdminInitPlan) -> None:
    if plan.config_file == plan.data_dir or plan.config_file.is_relative_to(plan.data_dir):
        raise BookingError("trusted configuration must be outside the shared data directory")
    if plan.access == "all":
        if (
            plan.group_name is not None
            or plan.storage_gid is not None
            or plan.file_mode != ALL_USERS_FILE_MODE
            or plan.dir_mode != ALL_USERS_DIR_MODE
        ):
            raise BookingError("all-user access must use modes 0666/0777 without a storage group")
    elif plan.access == "group":
        if not plan.group_name or plan.storage_gid is None:
            raise BookingError("group access requires an existing Unix group")
        try:
            group_record = grp.getgrnam(plan.group_name)
        except KeyError as exc:
            raise BookingError(f"Unix group does not exist: {plan.group_name}") from exc
        if int(group_record.gr_gid) != plan.storage_gid:
            raise BookingError("group name and storage GID do not match")
        if plan.file_mode != GROUP_FILE_MODE or plan.dir_mode != GROUP_DIR_MODE:
            raise BookingError("group access must use modes 0660/2770")
        memberships = set(os.getgrouplist(plan.monitor.username, plan.monitor.primary_gid))
        if plan.storage_gid not in memberships:
            raise BookingError(
                f"monitor account {plan.monitor.username} is not in group {plan.group_name}; "
                f"run: sudo usermod -aG {plan.group_name} {plan.monitor.username}"
            )
    else:
        raise BookingError(f"unknown access mode: {plan.access}")
    Config(
        data_dir=plan.data_dir,
        gpu_count=plan.gpu_count,
        slot_minutes=plan.slot_minutes,
        max_shared_users=plan.max_shared_users,
        require_shared_memory=plan.require_shared_memory,
        monitor_uid=plan.monitor.uid,
        storage_gid=plan.storage_gid,
        file_mode=plan.file_mode,
        dir_mode=plan.dir_mode,
    )


def _detected_gpu_count(explicit: Optional[int]) -> int:
    if explicit is not None:
        if explicit < 1 or explicit > MAX_GPU_COUNT:
            raise BookingError(f"--gpu-count must be between 1 and {MAX_GPU_COUNT}")
        return explicit
    detected = int(detect_gpu_count())
    if detected < 1:
        raise BookingError("no NVIDIA GPU detected; pass --gpu-count for a simulation setup")
    if detected > MAX_GPU_COUNT:
        raise BookingError(f"detected GPU count exceeds supported maximum {MAX_GPU_COUNT}")
    probe = snapshot(Config(DEFAULT_SYSTEM_DATA_DIR, gpu_count=detected))
    if not probe or all(device.source == "unknown" for device in probe):
        raise BookingError(
            "GPU hardware could not be verified; install gpubk[gpu] or make nvidia-smi "
            "available, otherwise pass --gpu-count explicitly for simulation"
        )
    return detected


def _default_monitor_identity(explicit: Optional[str]) -> Optional[AdminIdentity]:
    if explicit:
        return _resolve_identity(explicit)
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_uid and sudo_uid.isdigit() and int(sudo_uid) != 0:
        return _resolve_identity(sudo_uid)
    current_uid = os.geteuid()
    if current_uid != 0:
        return _resolve_identity(str(current_uid))
    return None


def _resolve_identity(value: str) -> AdminIdentity:
    text = str(value).strip()
    try:
        record = pwd.getpwuid(int(text)) if text.isdigit() else pwd.getpwnam(text)
    except KeyError as exc:
        raise BookingError(f"local account does not exist: {text}") from exc
    if int(record.pw_uid) == 0:
        raise BookingError("monitor must use a non-root account")
    return AdminIdentity(
        uid=int(record.pw_uid),
        username=str(record.pw_name),
        primary_gid=int(record.pw_gid),
    )


def _ask_identity(value: str, *, enabled: bool) -> AdminIdentity:
    candidate = value
    while True:
        try:
            return _resolve_identity(candidate)
        except BookingError as exc:
            if not enabled:
                raise
            print(f"Invalid monitor account: {exc}")
            candidate = _ask("Monitor account", "", enabled=True)


def _ask_group(value: str, *, enabled: bool) -> tuple[str, grp.struct_group]:
    candidate = _ask("Existing Unix group", value, enabled=True) if enabled else value
    while True:
        try:
            return candidate, grp.getgrnam(candidate)
        except KeyError as exc:
            if not enabled:
                raise BookingError(
                    f"Unix group does not exist: {candidate}; "
                    "create it first or use --access all"
                ) from exc
            print(f"Unix group does not exist: {candidate}")
            candidate = _ask("Existing Unix group", "", enabled=True)


def _ask_absolute_path(label: str, default: Path, *, enabled: bool) -> Path:
    candidate = _ask(label, str(default), enabled=True) if enabled else str(default)
    while True:
        try:
            return _absolute_path(Path(candidate))
        except BookingError as exc:
            if not enabled:
                raise
            print(f"Invalid path: {exc}")
            candidate = _ask(label, str(default), enabled=True)


def _prepare_data_directory(plan: AdminInitPlan, *, nonempty: bool) -> bool:
    path = plan.data_dir
    created = False
    if os.path.lexists(path):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise BookingError(f"data path is not a real directory: {path}")
        actual_mode = stat.S_IMODE(metadata.st_mode)
        gid_mismatch = plan.storage_gid is not None and metadata.st_gid != plan.storage_gid
        if nonempty and (actual_mode != plan.dir_mode or gid_mismatch):
            raise BookingError(
                "refusing to change mode or group of a non-empty data directory; "
                "use a reviewed migration instead"
            )
    else:
        parent = path.parent
        if not parent.is_dir():
            raise BookingError(f"data-directory parent does not exist: {parent}")
        os.mkdir(path, plan.dir_mode)
        created = True

    fd = os.open(
        str(path),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if plan.storage_gid is not None:
            os.fchown(fd, -1, plan.storage_gid)
        os.fchmod(fd, plan.dir_mode)
        metadata = os.fstat(fd)
        if stat.S_IMODE(metadata.st_mode) != plan.dir_mode:
            raise BookingError(f"failed to apply data-directory mode {plan.dir_mode:04o}: {path}")
        if plan.storage_gid is not None and metadata.st_gid != plan.storage_gid:
            raise BookingError(f"failed to apply data-directory GID {plan.storage_gid}: {path}")
    finally:
        os.close(fd)
    fsync_directory(path.parent)
    return created


def _validate_config_destination(path: Path, *, expected_owner: int) -> None:
    directory = path.parent
    if os.path.lexists(directory):
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise BookingError(f"configuration parent is not a real directory: {directory}")
        if stat.S_IMODE(metadata.st_mode) != CONFIG_DIRECTORY_MODE:
            raise BookingError(
                f"configuration directory mode must be {CONFIG_DIRECTORY_MODE:04o}: {directory}"
            )
        if metadata.st_uid != expected_owner:
            raise BookingError(
                f"configuration directory must be owned by UID {expected_owner}: {directory}"
            )
    else:
        parent = directory.parent
        if not parent.is_dir():
            raise BookingError(f"configuration-directory parent does not exist: {parent}")


def _read_existing_config(path: Path, *, expected_owner: int) -> Optional[dict]:
    if not os.path.lexists(path):
        return None
    fd = open_existing_regular(path)
    try:
        metadata = os.fstat(fd)
        if stat.S_IMODE(metadata.st_mode) != CONFIG_FILE_MODE:
            raise BookingError(
                f"existing configuration mode must be {CONFIG_FILE_MODE:04o}: {path}"
            )
        if metadata.st_uid != expected_owner:
            raise BookingError(
                f"existing configuration must be owned by UID {expected_owner}: {path}"
            )
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            payload = json.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict):
        raise BookingError(f"existing configuration must contain a JSON object: {path}")
    return payload


def _atomic_write_config(path: Path, document: dict, *, previous: Optional[dict]) -> Optional[Path]:
    directory = path.parent
    if os.path.lexists(directory):
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise BookingError(f"configuration parent is not a real directory: {directory}")
        if stat.S_IMODE(metadata.st_mode) != CONFIG_DIRECTORY_MODE:
            raise BookingError(
                f"configuration directory mode must be {CONFIG_DIRECTORY_MODE:04o}: {directory}"
            )
    else:
        parent = directory.parent
        if not parent.is_dir():
            raise BookingError(f"configuration-directory parent does not exist: {parent}")
        os.mkdir(directory, CONFIG_DIRECTORY_MODE)
        fsync_directory(parent)

    backup = None
    if previous is not None:
        backup = directory / f"{path.name}.bak"
        _write_new_file(
            backup,
            (json.dumps(previous, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            0o600,
            replace=True,
        )
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_new_file(path, payload, CONFIG_FILE_MODE, replace=True)
    return backup


def _write_new_file(path: Path, payload: bytes, mode: int, *, replace: bool) -> None:
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
    temporary = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while installing administrator configuration")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if not replace and os.path.lexists(path):
            raise FileExistsError(path)
        os.replace(temporary, path)
        fsync_directory(directory)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _directory_nonempty(path: Path) -> bool:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise BookingError(f"data path is not a real directory: {path}")
    return any(path.iterdir())


def _absolute_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise BookingError(f"administrator paths must be absolute: {path}")
    return Path(os.path.abspath(os.fspath(expanded)))


def _print_plan(plan: AdminInitPlan, inspection: AdminInspection) -> None:
    print("GPUbk administrator setup")
    print(f"  GPUs:       {plan.gpu_count}")
    print(f"  data:       {plan.data_dir}")
    print(f"  config:     {plan.config_file}")
    print(f"  time slice: {plan.slot_minutes} minutes")
    print(f"  sharing:    {plan.max_shared_users} capacity units per GPU")
    print(f"  monitor:    {plan.monitor.username} (UID {plan.monitor.uid})")
    print(
        f"  actions:    config={inspection.config_action}, "
        f"data={inspection.data_action}"
    )
    if plan.access == "all":
        print("  access:     all local users (open cooperative mode)")
        print(f"  modes:      files {plan.file_mode:04o}, directories {plan.dir_mode:04o}")
        print("  warning:    every local account is a trusted participant")
    else:
        print(f"  access:     Unix group {plan.group_name} (GID {plan.storage_gid})")
        print(f"  modes:      files {plan.file_mode:04o}, directories {plan.dir_mode:04o}")


def _ask(label: str, default: str, *, enabled: bool) -> str:
    if not enabled:
        return str(default)
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or str(default)


def _ask_choice(
    label: str,
    default: str,
    choices: Sequence[str],
    *,
    enabled: bool,
) -> str:
    if not enabled:
        return default
    allowed = {choice.lower(): choice for choice in choices}
    while True:
        value = _ask(f"{label} ({'/'.join(choices)})", default, enabled=True).lower()
        if value in allowed:
            return allowed[value]
        print(f"Please choose one of: {', '.join(choices)}")


def _ask_int(
    label: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    enabled: bool,
) -> int:
    if not enabled:
        value = int(default)
        if value < minimum or value > maximum:
            raise BookingError(f"{label} must be between {minimum} and {maximum}")
        return value
    while True:
        raw = _ask(label, str(default), enabled=True)
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if minimum <= value <= maximum:
            return value
        print(f"Please enter a value between {minimum} and {maximum}.")


def _ask_slot_minutes(default: int, *, enabled: bool) -> int:
    if not enabled:
        return validate_slot_minutes(default)
    while True:
        raw = _ask("Reservation slice in minutes", str(default), enabled=True)
        try:
            return validate_slot_minutes(int(raw))
        except (TypeError, ValueError) as exc:
            print(f"Invalid slice: {exc}")


def _ask_bool(label: str, default: bool, *, enabled: bool) -> bool:
    if not enabled:
        return default
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{marker}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")

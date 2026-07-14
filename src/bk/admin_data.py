from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .fileio import fsync_directory, open_existing_regular
from .models import BookingError


BACKUP_SCHEMA_VERSION = "gpubk.data-backup.v1"
BACKUP_MANIFEST = "manifest.json"
BACKUP_CONFIG = "config.json"
BACKUP_DATA = "data"
BACKUP_DIR_MODE = 0o700
BACKUP_FILE_MODE = 0o600
_MAINTENANCE_LOCKS = frozenset({"ledger.lock", "usage.lock"})


def create_data_backup(
    data_dir: Path,
    config_file: Path,
    destination: Path,
    *,
    service_uid: int,
    service_gid: int,
) -> dict:
    data_dir = _absolute(data_dir)
    config_file = _absolute(config_file)
    destination = _absolute(destination)
    _reject_nested_destination(data_dir, destination)
    _ensure_parent(destination.parent)
    if os.path.lexists(destination):
        raise BookingError(f"backup destination already exists: {destination}")

    config_payload = _read_regular(config_file)
    entries = _scan_source(data_dir, service_uid, service_gid)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    )
    os.chmod(staging, BACKUP_DIR_MODE)
    try:
        backup_data = staging / BACKUP_DATA
        backup_data.mkdir(mode=BACKUP_DIR_MODE)
        manifest_entries = _copy_source(entries, data_dir, backup_data)
        _write_file(staging / BACKUP_CONFIG, config_payload)
        manifest = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "data_dir": str(data_dir),
                "config_file": str(config_file),
                "service_uid": service_uid,
                "service_gid": service_gid,
                "config_sha256": _digest(config_payload),
            },
            "entries": manifest_entries,
        }
        _write_file(
            staging / BACKUP_MANIFEST,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        fsync_directory(backup_data)
        fsync_directory(staging)
        os.rename(staging, destination)
        fsync_directory(destination.parent)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_data_backup(destination)


def verify_data_backup(path: Path) -> dict:
    path = _absolute(path)
    root = _real_directory(path, "backup")
    manifest_payload = _read_backup_file(root / BACKUP_MANIFEST)
    config_payload = _read_backup_file(root / BACKUP_CONFIG)
    try:
        manifest = json.loads(manifest_payload)
    except json.JSONDecodeError as exc:
        raise BookingError(f"backup manifest is invalid JSON: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BookingError(f"unsupported or invalid GPUBK backup: {path}")
    root_entries = {item.name for item in _walk_tree(root) if item.parent == root}
    expected_root = {BACKUP_MANIFEST, BACKUP_CONFIG, BACKUP_DATA}
    if root_entries != expected_root:
        raise BookingError("backup root contains missing or unexpected entries")
    source = manifest.get("source")
    entries = manifest.get("entries")
    if not isinstance(source, dict) or not isinstance(entries, list):
        raise BookingError("backup manifest is missing source or entries")
    if source.get("config_sha256") != _digest(config_payload):
        raise BookingError("backup configuration checksum does not match")

    data_root = _real_directory(root / BACKUP_DATA, "backup data")
    expected: set[str] = set()
    for entry in entries:
        relative, kind, mode, size, digest = _validate_manifest_entry(entry)
        if relative in expected:
            raise BookingError(f"backup manifest contains a duplicate path: {relative}")
        expected.add(relative)
        target = data_root / relative
        if kind == "directory":
            _real_directory(target, f"backup directory {relative}")
            continue
        payload_digest, observed_size = _hash_backup_file(target)
        if observed_size != size or payload_digest != digest:
            raise BookingError(f"backup file checksum does not match: {relative}")
        if mode < 0 or mode > 0o777:
            raise BookingError(f"backup entry mode is invalid: {relative}")

    observed = {
        str(item.relative_to(data_root))
        for item in _walk_tree(data_root)
    }
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("unexpected=" + ",".join(extra))
        raise BookingError("backup contents differ from manifest: " + "; ".join(detail))
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "status": "verified",
        "path": str(path),
        "created_at": manifest.get("created_at"),
        "files": sum(1 for item in entries if item.get("kind") == "file"),
        "directories": sum(1 for item in entries if item.get("kind") == "directory"),
        "bytes": sum(int(item.get("size", 0)) for item in entries),
        "manifest": manifest,
    }


def clear_data_directory(
    data_dir: Path,
    *,
    service_uid: int,
    service_gid: int,
    directory_mode: int,
) -> None:
    data_dir = _absolute(data_dir)
    _atomic_replace_tree(
        data_dir,
        source=None,
        entries=(),
        service_uid=service_uid,
        service_gid=service_gid,
        directory_mode=directory_mode,
    )


def restore_data_backup(
    backup: Path,
    data_dir: Path,
    *,
    service_uid: int,
    service_gid: int,
    directory_mode: int,
) -> dict:
    verified = verify_data_backup(backup)
    data_dir = _absolute(data_dir)
    present = {item.name for item in data_dir.iterdir()}
    unexpected = sorted(present - _MAINTENANCE_LOCKS)
    if unexpected:
        raise BookingError(
            "restore requires an empty GPUBK data directory; found: "
            + ", ".join(unexpected)
        )
    manifest = verified["manifest"]
    _atomic_replace_tree(
        data_dir,
        source=_absolute(backup) / BACKUP_DATA,
        entries=manifest["entries"],
        service_uid=service_uid,
        service_gid=service_gid,
        directory_mode=directory_mode,
    )
    return {key: value for key, value in verified.items() if key != "manifest"}


def _scan_source(data_dir: Path, uid: int, gid: int) -> list[tuple[Path, os.stat_result]]:
    root = _real_directory(data_dir, "data")
    root_meta = root.lstat()
    if (root_meta.st_uid, root_meta.st_gid) != (uid, gid):
        raise BookingError(f"GPUBK data directory ownership drifted: {root}")
    entries = []
    for item in _walk_tree(root):
        metadata = item.lstat()
        if (metadata.st_uid, metadata.st_gid) != (uid, gid):
            raise BookingError(f"GPUBK data ownership drifted: {item}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode > 0o777:
            raise BookingError(f"refusing special permission bits in GPUBK data: {item}")
        if stat.S_ISDIR(metadata.st_mode):
            entries.append((item, metadata))
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            entries.append((item, metadata))
        else:
            raise BookingError(f"refusing symbolic, linked, or special GPUBK data: {item}")
    return entries


def _copy_source(
    entries: Iterable[tuple[Path, os.stat_result]],
    source: Path,
    destination: Path,
) -> list[dict]:
    documents = []
    for path, before in entries:
        relative = path.relative_to(source)
        target = destination / relative
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISDIR(before.st_mode):
            target.mkdir(mode=BACKUP_DIR_MODE)
            documents.append({"path": str(relative), "kind": "directory", "mode": mode})
            continue
        size, digest = _copy_regular(
            path,
            target,
            target_mode=BACKUP_FILE_MODE,
            target_uid=os.geteuid(),
            target_gid=os.getegid(),
            expected_source=before,
        )
        documents.append(
            {
                "path": str(relative),
                "kind": "file",
                "mode": mode,
                "size": size,
                "sha256": digest,
            }
        )
    return documents


def _atomic_replace_tree(
    data_dir: Path,
    *,
    source: Path | None,
    entries: Iterable[dict],
    service_uid: int,
    service_gid: int,
    directory_mode: int,
) -> None:
    parent = data_dir.parent
    staging = Path(tempfile.mkdtemp(prefix=f".{data_dir.name}.new-", dir=parent))
    retired = Path(tempfile.mkdtemp(prefix=f".{data_dir.name}.old-", dir=parent))
    retired.rmdir()
    try:
        os.chown(staging, service_uid, service_gid)
        os.chmod(staging, directory_mode)
        if source is not None:
            for entry in entries:
                relative, kind, mode, _size, _digest_value = _validate_manifest_entry(entry)
                target = staging / relative
                if kind == "directory":
                    target.mkdir(mode=mode)
                    os.chown(target, service_uid, service_gid)
                else:
                    size, digest = _copy_regular(
                        source / relative,
                        target,
                        target_mode=mode,
                        target_uid=service_uid,
                        target_gid=service_gid,
                    )
                    if size != _size or digest != _digest_value:
                        raise BookingError(
                            f"backup file changed while restoring: {relative}"
                        )
            for directory in sorted(
                (item for item in staging.rglob("*") if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                fsync_directory(directory)
        fsync_directory(staging)
        os.rename(data_dir, retired)
        try:
            os.rename(staging, data_dir)
            fsync_directory(parent)
        except BaseException:
            os.rename(retired, data_dir)
            fsync_directory(parent)
            raise
        shutil.rmtree(retired)
        fsync_directory(parent)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if retired.exists() and not data_dir.exists():
            os.rename(retired, data_dir)
            fsync_directory(parent)
        raise


def _validate_manifest_entry(entry: object) -> tuple[str, str, int, int, str | None]:
    if not isinstance(entry, dict):
        raise BookingError("backup manifest contains an invalid entry")
    relative = entry.get("path")
    kind = entry.get("kind")
    mode = entry.get("mode")
    if not isinstance(relative, str) or not relative or not _safe_relative(relative):
        raise BookingError("backup manifest contains an unsafe path")
    if kind not in {"file", "directory"}:
        raise BookingError(f"backup entry type is invalid: {relative}")
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
        raise BookingError(f"backup entry mode is invalid: {relative}")
    if kind == "directory":
        return relative, kind, mode, 0, None
    size = entry.get("size")
    digest = entry.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise BookingError(f"backup entry size is invalid: {relative}")
    if not isinstance(digest, str) or len(digest) != 64:
        raise BookingError(f"backup entry checksum is invalid: {relative}")
    return relative, kind, mode, size, digest


def _walk_tree(root: Path) -> Iterable[Path]:
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            path = Path(current) / name
            if path.is_symlink():
                raise BookingError(f"refusing symbolic link: {path}")
            yield path


def _read_regular(path: Path, expected: os.stat_result | None = None) -> bytes:
    try:
        fd = open_existing_regular(path)
    except OSError as exc:
        raise BookingError(f"cannot read regular file safely: {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if expected is not None and (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
            expected.st_mtime_ns,
        ):
            raise BookingError(f"GPUBK data changed during backup: {path}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read()
    finally:
        if fd >= 0:
            os.close(fd)
    if expected is not None and len(payload) != expected.st_size:
        raise BookingError(f"GPUBK data changed during backup: {path}")
    return payload


def _read_backup_file(path: Path) -> bytes:
    try:
        return _read_regular(path)
    except BookingError as exc:
        raise BookingError(f"backup file is unsafe or unreadable: {path}") from exc


def _hash_backup_file(path: Path) -> tuple[str, int]:
    try:
        fd = open_existing_regular(path)
    except OSError as exc:
        raise BookingError(f"backup file is unsafe or unreadable: {path}") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), size


def _copy_regular(
    source: Path,
    target: Path,
    *,
    target_mode: int,
    target_uid: int,
    target_gid: int,
    expected_source: os.stat_result | None = None,
) -> tuple[int, str]:
    try:
        source_fd = open_existing_regular(source)
    except OSError as exc:
        raise BookingError(f"cannot read regular file safely: {source}: {exc}") from exc
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    target_fd = -1
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(source_fd)
        if expected_source is not None and _file_identity(before) != _file_identity(expected_source):
            raise BookingError(f"GPUBK data changed during backup: {source}")
        target_fd = os.open(target, flags, target_mode)
        os.fchown(target_fd, target_uid, target_gid)
        os.fchmod(target_fd, target_mode)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(source_fd)
        if _file_identity(after) != _file_identity(before) or size != before.st_size:
            raise BookingError(f"source file changed while copying: {source}")
        os.fsync(target_fd)
    finally:
        os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)
    return size, digest.hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _write_file(path: Path, payload: bytes) -> None:
    _write_owned_file(path, payload, BACKUP_FILE_MODE, os.geteuid(), os.getegid())


def _write_owned_file(path: Path, payload: bytes, mode: int, uid: int, gid: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    fd = os.open(path, flags, mode)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def _real_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise BookingError(f"{label} directory is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BookingError(f"{label} path is not a real directory: {path}")
    return path


def _ensure_parent(path: Path) -> None:
    missing = []
    cursor = path
    while True:
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            missing.append(cursor)
            cursor = cursor.parent
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise BookingError(f"backup path contains a non-directory component: {cursor}")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    for directory in reversed(missing):
        os.mkdir(directory, BACKUP_DIR_MODE)
        os.chmod(directory, BACKUP_DIR_MODE)
        fsync_directory(directory.parent)


def _reject_nested_destination(data_dir: Path, destination: Path) -> None:
    try:
        destination.relative_to(data_dir)
    except ValueError:
        return
    raise BookingError("backup destination must be outside the GPUBK data directory")


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise BookingError(f"administrator data path must be absolute: {path}")
    return expanded.resolve(strict=False)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

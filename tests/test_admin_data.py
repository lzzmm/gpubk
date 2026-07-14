import json
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from bk.admin_data import (
    BACKUP_DATA,
    BACKUP_MANIFEST,
    clear_data_directory,
    create_data_backup,
    restore_data_backup,
    verify_data_backup,
)
from bk.admin import run_admin_cli
from bk.models import BookingError


class AdminDataTests(unittest.TestCase):
    def prepare(self, root: Path) -> tuple[Path, Path]:
        data = root / "data"
        data.mkdir(mode=0o755)
        (data / "usage").mkdir(mode=0o755)
        (data / "ledger.json").write_text('{"reservations": []}\n', encoding="utf-8")
        (data / "usage" / "rollups.jsonl").write_text(
            '{"schema_version": 1}\n', encoding="utf-8"
        )
        for path in data.rglob("*"):
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        config = root / "config.json"
        config.write_text(json.dumps({"data_dir": str(data)}) + "\n", encoding="utf-8")
        os.chmod(config, 0o644)
        return data, config

    def test_backup_verify_clear_and_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, config = self.prepare(root)
            backup = root / "backups" / "snapshot"
            uid, gid = os.geteuid(), os.getegid()

            created = create_data_backup(
                data,
                config,
                backup,
                service_uid=uid,
                service_gid=gid,
            )
            self.assertEqual(created["status"], "verified")
            self.assertEqual(created["files"], 2)
            self.assertTrue((backup / BACKUP_MANIFEST).is_file())

            clear_data_directory(
                data,
                service_uid=uid,
                service_gid=gid,
                directory_mode=0o755,
            )
            self.assertEqual(list(data.iterdir()), [])
            restored = restore_data_backup(
                backup,
                data,
                service_uid=uid,
                service_gid=gid,
                directory_mode=0o755,
            )
            self.assertEqual(restored["status"], "verified")
            self.assertEqual(
                (data / "ledger.json").read_text(encoding="utf-8"),
                '{"reservations": []}\n',
            )
            self.assertEqual(verify_data_backup(backup)["files"], 2)

    def test_verify_detects_tampered_file_and_unexpected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, config = self.prepare(root)
            backup = root / "backup"
            create_data_backup(
                data,
                config,
                backup,
                service_uid=os.geteuid(),
                service_gid=os.getegid(),
            )
            target = backup / BACKUP_DATA / "ledger.json"
            target.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(BookingError, "checksum"):
                verify_data_backup(backup)

            target.write_text('{"reservations": []}\n', encoding="utf-8")
            (backup / "unexpected").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(BookingError, "backup root"):
                verify_data_backup(backup)

    def test_backup_rejects_symlink_and_destination_inside_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, config = self.prepare(root)
            (data / "unsafe").symlink_to(data / "ledger.json")
            with self.assertRaisesRegex(BookingError, "symbolic"):
                create_data_backup(
                    data,
                    config,
                    root / "backup",
                    service_uid=os.geteuid(),
                    service_gid=os.getegid(),
                )
            (data / "unsafe").unlink()
            with self.assertRaisesRegex(BookingError, "outside"):
                create_data_backup(
                    data,
                    config,
                    data / "backup",
                    service_uid=os.geteuid(),
                    service_gid=os.getegid(),
                )

    def test_restore_refuses_nonempty_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, config = self.prepare(root)
            backup = root / "backup"
            uid, gid = os.geteuid(), os.getegid()
            create_data_backup(
                data,
                config,
                backup,
                service_uid=uid,
                service_gid=gid,
            )
            with self.assertRaisesRegex(BookingError, "requires an empty"):
                restore_data_backup(
                    backup,
                    data,
                    service_uid=uid,
                    service_gid=gid,
                    directory_mode=0o755,
                )


class AdminDataCliTests(unittest.TestCase):
    def test_data_commands_require_root(self):
        with (
            mock.patch("bk.admin.os.geteuid", return_value=1234),
            self.assertRaisesRegex(BookingError, "must run as root"),
        ):
            run_admin_cli(["data", "verify", "/tmp/backup"])

    def test_clear_creates_verified_backup_before_emptying_data(self):
        result = {
            "schema_version": "gpubk.data-backup.v1",
            "status": "verified",
            "path": "/backup/pre-clear",
            "created_at": "2030-01-01T00:00:00+00:00",
            "files": 3,
            "directories": 1,
            "bytes": 42,
            "manifest": {},
        }
        calls = []
        manifest = {
            "data_dir": "/data",
            "broker_socket": "/run/gpubk/broker.sock",
            "service_uid": 1003,
            "service_gid": 1003,
        }
        output = StringIO()
        with (
            mock.patch("bk.admin.os.geteuid", return_value=0),
            mock.patch("bk.admin._load_admin_services_manifest", return_value=(manifest, {})),
            mock.patch("bk.admin._validate_transfer_directory"),
            mock.patch("bk.admin._admin_service_blockers", return_value=[]),
            mock.patch("bk.admin._admin_service_guard", return_value=nullcontext()),
            mock.patch(
                "bk.admin.create_data_backup",
                side_effect=lambda *args, **kwargs: calls.append("backup") or result,
            ),
            mock.patch(
                "bk.admin.clear_data_directory",
                side_effect=lambda *args, **kwargs: calls.append("clear"),
            ),
            redirect_stdout(output),
        ):
            status = run_admin_cli(
                [
                    "data",
                    "clear",
                    "--backup-to",
                    "/backup/pre-clear",
                    "--yes",
                    "--json",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(calls, ["backup", "clear"])
        self.assertEqual(json.loads(output.getvalue())["status"], "cleared")


if __name__ == "__main__":
    unittest.main()

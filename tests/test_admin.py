import json
import grp
import os
import pwd
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from bk.admin import (
    ALL_USERS_DIR_MODE,
    ALL_USERS_FILE_MODE,
    GROUP_DIR_MODE,
    GROUP_FILE_MODE,
    AdminIdentity,
    AdminInitPlan,
    _detected_gpu_count,
    _validate_plan,
    apply_admin_init,
    inspect_admin_init,
    run_admin_cli,
)
from bk.cli import main as bk_main
from bk.gpu import GpuSnapshot
from bk.models import BookingError


def non_root_identity() -> AdminIdentity:
    current = pwd.getpwuid(os.getuid())
    if current.pw_uid != 0:
        return AdminIdentity(current.pw_uid, current.pw_name, current.pw_gid)
    for record in pwd.getpwall():
        if record.pw_uid > 0:
            return AdminIdentity(record.pw_uid, record.pw_name, record.pw_gid)
    raise unittest.SkipTest("no non-root account is available")


class TtyInput(StringIO):
    def isatty(self):
        return True


class AdminInitTests(unittest.TestCase):
    def plan(self, root: Path, **changes) -> AdminInitPlan:
        identity = non_root_identity()
        values = {
            "config_file": root / "etc" / "gpubk" / "config.json",
            "data_dir": root / "var" / "lib" / "gpubk",
            "access": "all",
            "gpu_count": 8,
            "slot_minutes": 5,
            "max_shared_users": 2,
            "require_shared_memory": True,
            "monitor": identity,
            "group_name": None,
            "storage_gid": None,
            "file_mode": ALL_USERS_FILE_MODE,
            "dir_mode": ALL_USERS_DIR_MODE,
        }
        values.update(changes)
        return AdminInitPlan(**values)

    def prepare_parents(self, root: Path) -> None:
        (root / "etc").mkdir(mode=0o755)
        (root / "var").mkdir(mode=0o755)
        (root / "var" / "lib").mkdir(mode=0o755)

    def test_open_mode_initialization_is_atomic_idempotent_and_group_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)

            first = apply_admin_init(plan, require_root=False)
            second = apply_admin_init(plan, require_root=False)

            self.assertTrue(first["config_changed"])
            self.assertTrue(first["data_created"])
            self.assertFalse(second["config_changed"])
            self.assertFalse(second["data_created"])
            self.assertEqual(stat.S_IMODE(plan.config_file.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(plan.data_dir.stat().st_mode), 0o777)
            document = json.loads(plan.config_file.read_text(encoding="utf-8"))
            self.assertEqual(document["file_mode"], "0666")
            self.assertEqual(document["dir_mode"], "0777")
            self.assertNotIn("storage_gid", document)

    def test_inspection_previews_clean_initialization_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)

            inspection = inspect_admin_init(
                plan,
                expected_owner=os.geteuid(),
            )

            self.assertEqual(inspection.config_action, "create")
            self.assertEqual(inspection.data_action, "create")
            self.assertFalse(inspection.data_exists)
            self.assertFalse(inspection.data_nonempty)
            self.assertFalse(plan.config_file.exists())
            self.assertFalse(plan.data_dir.exists())

    def test_unconfigured_nonempty_data_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            plan.data_dir.mkdir()
            plan.data_dir.chmod(plan.dir_mode)
            marker = plan.data_dir / "existing-data"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(BookingError, "unconfigured non-empty"):
                inspect_admin_init(plan, expected_owner=os.geteuid())

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(plan.config_file.exists())

    def test_group_mode_sets_group_policy_only_when_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            identity = non_root_identity()
            group_record = grp.getgrgid(identity.primary_gid)
            plan = self.plan(
                root,
                access="group",
                monitor=identity,
                group_name=group_record.gr_name,
                storage_gid=identity.primary_gid,
                file_mode=GROUP_FILE_MODE,
                dir_mode=GROUP_DIR_MODE,
            )

            apply_admin_init(plan, require_root=False)

            document = json.loads(plan.config_file.read_text(encoding="utf-8"))
            self.assertEqual(document["file_mode"], "0660")
            self.assertEqual(document["dir_mode"], "2770")
            self.assertEqual(document["storage_gid"], identity.primary_gid)
            self.assertEqual(stat.S_IMODE(plan.data_dir.stat().st_mode), 0o2770)

    def test_different_config_requires_force_and_nonempty_data_refuses_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            original = self.plan(root)
            apply_admin_init(original, require_root=False)
            changed = self.plan(root, max_shared_users=4)

            with self.assertRaisesRegex(BookingError, "configuration already exists"):
                apply_admin_init(changed, require_root=False)

            marker = original.data_dir / "existing-data"
            marker.write_text("keep", encoding="utf-8")
            marker.chmod(0o666)
            with self.assertRaisesRegex(BookingError, "non-empty data directory"):
                apply_admin_init(changed, force=True, require_root=False)

    def test_force_replaces_config_only_for_empty_data_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            original = self.plan(root)
            apply_admin_init(original, require_root=False)
            changed = self.plan(root, max_shared_users=4)

            result = apply_admin_init(changed, force=True, require_root=False)

            self.assertTrue(result["config_changed"])
            self.assertTrue(Path(result["config_backup"]).is_file())
            backup = json.loads(Path(result["config_backup"]).read_text(encoding="utf-8"))
            current = json.loads(changed.config_file.read_text(encoding="utf-8"))
            self.assertEqual(backup["max_shared_users"], 2)
            self.assertEqual(current["max_shared_users"], 4)

    def test_admin_dry_run_precedes_normal_config_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken_data = root / "broken"
            broken_data.mkdir()
            (broken_data / "config.json").write_text("{broken", encoding="utf-8")
            identity = non_root_identity()
            output = StringIO()
            errors = StringIO()
            argv = [
                "admin",
                "init",
                "--dry-run",
                "--json",
                "--gpu-count",
                "8",
                "--monitor-user",
                str(identity.uid),
                "--data-dir",
                str(root / "shared"),
                "--config-file",
                str(root / "config" / "config.json"),
            ]
            with mock.patch.dict(os.environ, {"BK_DATA_DIR": str(broken_data)}, clear=False):
                with redirect_stdout(output), redirect_stderr(errors):
                    status = bk_main(argv)

            self.assertEqual(status, 0, errors.getvalue())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["access"]["mode"], "all")
            self.assertEqual(payload["access"]["file_mode"], "0666")
            self.assertEqual(payload["access"]["dir_mode"], "0777")
            self.assertEqual(payload["inspection"]["config_action"], "create")
            self.assertEqual(payload["inspection"]["data_action"], "create")
            self.assertFalse((root / "shared").exists())
            self.assertFalse((root / "config").exists())

    def test_json_mode_never_emits_interactive_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = non_root_identity()
            output = StringIO()
            argv = [
                "init",
                "--dry-run",
                "--json",
                "--gpu-count",
                "8",
                "--monitor-user",
                str(identity.uid),
                "--data-dir",
                str(root / "shared"),
                "--config-file",
                str(root / "config" / "config.json"),
            ]

            with mock.patch("sys.stdin", TtyInput("unexpected input\n")):
                with redirect_stdout(output):
                    status = run_admin_cli(argv)

            self.assertEqual(status, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["access"]["mode"], "all")

    def test_apply_requires_root_but_dry_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            with mock.patch("bk.admin.os.geteuid", return_value=1234):
                with self.assertRaisesRegex(BookingError, "must run as root"):
                    apply_admin_init(plan)

    def test_json_apply_emits_one_final_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = non_root_identity()
            output = StringIO()
            argv = [
                "init",
                "--yes",
                "--json",
                "--gpu-count",
                "8",
                "--monitor-user",
                str(identity.uid),
                "--data-dir",
                str(root / "shared"),
                "--config-file",
                str(root / "config" / "config.json"),
            ]
            result = {
                "config_changed": True,
                "config_backup": None,
                "data_created": True,
            }
            with mock.patch("bk.admin.apply_admin_init", return_value=result):
                with redirect_stdout(output):
                    status = run_admin_cli(argv)

            self.assertEqual(status, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "initialized")
            self.assertEqual(payload["result"], result)

    def test_noninteractive_apply_requires_yes_and_emits_a_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = non_root_identity()
            output = StringIO()
            errors = StringIO()
            argv = [
                "init",
                "--json",
                "--gpu-count",
                "8",
                "--monitor-user",
                str(identity.uid),
                "--data-dir",
                str(root / "shared"),
                "--config-file",
                str(root / "config" / "config.json"),
            ]
            with mock.patch("sys.stdin", StringIO()):
                with redirect_stdout(output), redirect_stderr(errors):
                    status = run_admin_cli(argv)

            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "planned")
            self.assertIn("pass --yes", errors.getvalue())
            self.assertFalse((root / "shared").exists())

    def test_interactive_init_recovers_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = non_root_identity()
            answers = TtyInput(
                "relative\n"
                f"{root / 'shared'}\n"
                "invalid\n"
                "all\n"
                "many\n"
                "0\n"
                "8\n"
                "7\n"
                "5\n"
                "0\n"
                "4\n"
                "maybe\n"
                "yes\n"
                "\n"
            )
            output = StringIO()
            argv = [
                "init",
                "--dry-run",
                "--config-file",
                str(root / "config" / "config.json"),
            ]
            with mock.patch("sys.stdin", answers), mock.patch(
                "bk.admin._detected_gpu_count", return_value=8
            ), mock.patch("bk.admin._default_monitor_identity", return_value=identity):
                with redirect_stdout(output):
                    status = run_admin_cli(argv)

            text = output.getvalue()
            self.assertEqual(status, 0)
            self.assertIn("Invalid path", text)
            self.assertIn("Please choose one of", text)
            self.assertIn("Please enter a whole number", text)
            self.assertIn("Invalid slice", text)
            self.assertIn("Please answer y or n", text)
            self.assertIn("sharing:    4", text)
            self.assertFalse((root / "shared").exists())

    def test_gpu_detection_requires_real_telemetry_unless_count_is_explicit(self):
        self.assertEqual(_detected_gpu_count(8), 8)
        with self.assertRaisesRegex(BookingError, "between 1"):
            _detected_gpu_count(0)
        with mock.patch("bk.admin.detect_gpu_count", return_value=2), mock.patch(
            "bk.admin.snapshot",
            return_value=[
                GpuSnapshot(0, "unknown", source="unknown"),
                GpuSnapshot(1, "unknown", source="unknown"),
            ],
        ):
            with self.assertRaisesRegex(BookingError, "could not be verified"):
                _detected_gpu_count(None)
        with mock.patch("bk.admin.detect_gpu_count", return_value=2), mock.patch(
            "bk.admin.snapshot",
            return_value=[
                GpuSnapshot(0, "GPU 0", source="nvml"),
                GpuSnapshot(1, "GPU 1", source="nvml"),
            ],
        ):
            self.assertEqual(_detected_gpu_count(None), 2)

    def test_config_must_stay_outside_shared_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.plan(
                root,
                config_file=root / "var" / "lib" / "gpubk" / "config.json",
            )
            with self.assertRaisesRegex(BookingError, "outside the shared data"):
                _validate_plan(plan)

    def test_existing_permission_drift_and_unsafe_data_paths_are_not_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            plan.data_dir.mkdir(mode=0o700)
            marker = plan.data_dir / "keep"
            marker.write_text("data", encoding="utf-8")
            with self.assertRaisesRegex(BookingError, "refusing to change mode"):
                apply_admin_init(plan, require_root=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            plan.data_dir.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(BookingError, "not a real directory"):
                apply_admin_init(plan, require_root=False)

    def test_existing_config_permission_or_shape_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            apply_admin_init(plan, require_root=False)
            plan.config_file.chmod(0o600)
            with self.assertRaisesRegex(BookingError, "mode must be 0644"):
                apply_admin_init(plan, require_root=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prepare_parents(root)
            plan = self.plan(root)
            plan.config_file.parent.mkdir(mode=0o755)
            plan.config_file.write_text("[]", encoding="utf-8")
            plan.config_file.chmod(0o644)
            with self.assertRaisesRegex(BookingError, "JSON object"):
                apply_admin_init(plan, require_root=False)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from bk.config import Config
from bk.models import BookingError
from bk.systemd import install_user_unit, service_environment, unit_text


class BundledSystemdTests(unittest.TestCase):
    def test_units_are_bundled_and_remain_user_scoped(self):
        python = Path("/opt/bk venv/bin/python")
        environment = {"BK_DATA_DIR": "/data2/shared/bk", "PYTHONUNBUFFERED": "1"}
        worker = unit_text("worker", python, environment=environment)
        monitor = unit_text("monitor", python, environment=environment)

        self.assertIn('ExecStart="/opt/bk venv/bin/python" -m bk worker', worker)
        self.assertIn('ExecStart="/opt/bk venv/bin/python" -m bk monitor', monitor)
        self.assertIn('Environment="BK_DATA_DIR=/data2/shared/bk"', worker)
        self.assertIn("RestartPreventExitStatus=75", monitor)
        self.assertNotIn("EnvironmentFile=", worker)
        self.assertNotIn("@PYTHON_EXECUTABLE@", worker)
        self.assertNotIn("@SERVICE_ENVIRONMENT@", worker)
        self.assertNotIn("User=root", worker)

    def test_unit_escapes_systemd_specifiers_and_environment_markers(self):
        worker = unit_text(
            "worker",
            Path("/opt/percent%/$name/python"),
            environment={"BK_DATA_DIR": '/data/percent%/$name/quote"/back\\slash'},
        )

        self.assertIn('ExecStart="/opt/percent%%/$$name/python" -m bk worker', worker)
        self.assertIn(
            'Environment="BK_DATA_DIR=/data/percent%%/$name/quote\\"/back\\\\slash"',
            worker,
        )

    def test_unit_rejects_relative_interpreter_path(self):
        with self.assertRaisesRegex(BookingError, "absolute path"):
            unit_text("worker", Path("python3"))

    def test_install_never_enables_service_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            environment = {"BK_DATA_DIR": "/data2/shared/bk"}

            path = install_user_unit("worker", target, environment=environment)

            self.assertEqual(path, target / "bk-worker.service")
            self.assertTrue(path.is_file())
            with self.assertRaisesRegex(BookingError, "already exists"):
                install_user_unit("worker", target, environment=environment)
            install_user_unit("worker", target, environment=environment, force=True)

    def test_service_environment_captures_absolute_runtime_paths(self):
        config = Config(
            data_dir=Path("relative-data"),
            job_log_dir=Path("relative-logs"),
        )

        worker = service_environment(config, "worker")
        monitor = service_environment(config, "monitor")

        self.assertEqual(worker["BK_DATA_DIR"], str(Path("relative-data").absolute()))
        self.assertEqual(worker["BK_JOB_LOG_DIR"], str(Path("relative-logs").absolute()))
        self.assertEqual(monitor["BK_DATA_DIR"], str(Path("relative-data").absolute()))
        self.assertNotIn("BK_JOB_LOG_DIR", monitor)

    def test_unit_rejects_control_characters_in_environment(self):
        with self.assertRaisesRegex(BookingError, "control character"):
            unit_text("worker", Path("/usr/bin/python3"), environment={"BK_DATA_DIR": "/tmp/bad\npath"})


if __name__ == "__main__":
    unittest.main()

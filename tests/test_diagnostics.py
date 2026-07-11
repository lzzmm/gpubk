import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bk.config import Config
from bk.diagnostics import probes_ready, run_deployment_probes
from bk.gpu import GpuSnapshot


class DeploymentDiagnosticsTests(unittest.TestCase):
    def test_preflight_verifies_storage_lock_and_nvml_without_leaving_probe_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "shared"
            config = Config(data_dir=data_dir, gpu_count=2)
            devices = [
                GpuSnapshot(0, "gpu0", memory_total_mb=24000, source="nvml"),
                GpuSnapshot(1, "gpu1", memory_total_mb=24000, source="nvml"),
            ]

            with mock.patch("bk.diagnostics.snapshot", return_value=devices):
                checks = run_deployment_probes(config)

            self.assertTrue(probes_ready(checks), checks)
            self.assertEqual(
                [item["name"] for item in checks],
                ["data-directory", "atomic-replace", "process-lock", "disk-space", "gpu-telemetry"],
            )
            self.assertEqual(list(data_dir.glob(".gpubk-probe-*")), [])

    def test_simulation_is_reported_as_warning_not_real_gpu_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(data_dir=Path(tmp) / "data", gpu_count=1)
            devices = [GpuSnapshot(0, "sim", source="simulation")]

            with mock.patch("bk.diagnostics.snapshot", return_value=devices):
                checks = run_deployment_probes(config)

            gpu = next(item for item in checks if item["name"] == "gpu-telemetry")
            self.assertEqual(gpu["status"], "warn")
            self.assertFalse(probes_ready(checks))

    def test_wrong_existing_directory_mode_blocks_storage_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "shared"
            data_dir.mkdir(mode=0o755)
            data_dir.chmod(0o755)
            config = Config(data_dir=data_dir, dir_mode=0o700)

            with mock.patch(
                "bk.diagnostics.snapshot",
                return_value=[GpuSnapshot(0, "gpu0", source="nvml")],
            ):
                checks = run_deployment_probes(config)

            self.assertEqual(checks[0]["status"], "fail")
            self.assertEqual(checks[0]["actual_mode"], "0755")
            self.assertEqual(checks[1]["message"], "data directory is not ready")


if __name__ == "__main__":
    unittest.main()

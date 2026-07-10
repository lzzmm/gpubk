import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bk.config import load_config


class ConfigTests(unittest.TestCase):
    def test_generic_default_uses_xdg_data_home_not_lab_specific_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"XDG_DATA_HOME": tmp},
                clear=True,
            ):
                config = load_config()

            self.assertEqual(config.data_dir, Path(tmp) / "bk")
            self.assertEqual(config.file_mode, 0o600)
            self.assertEqual(config.dir_mode, 0o700)

    def test_shared_modes_and_memory_policy_parse_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "config.json").write_text(
                json.dumps(
                    {
                        "file_mode": "0660",
                        "dir_mode": "2770",
                        "require_shared_memory": True,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"BK_DATA_DIR": str(data_dir)}, clear=True):
                config = load_config()

            self.assertEqual(config.file_mode, 0o660)
            self.assertEqual(config.dir_mode, 0o2770)
            self.assertTrue(config.require_shared_memory)

    def test_file_mode_rejects_executable_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"BK_DATA_DIR": tmp, "BK_FILE_MODE": "0770"},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "executable bits"):
                    load_config()

    def test_allocator_command_is_opt_in_and_shell_split_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {
                    "BK_DATA_DIR": tmp,
                    "BK_ALLOCATOR_COMMAND": f"{sys.executable} -m example_allocator",
                    "BK_ALLOCATOR_WEIGHT": "7.5",
                },
                clear=True,
            ):
                config = load_config()

            self.assertEqual(config.allocator_command, (sys.executable, "-m", "example_allocator"))
            self.assertEqual(config.allocator_weight, 7.5)


if __name__ == "__main__":
    unittest.main()

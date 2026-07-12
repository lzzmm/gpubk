import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bk.fileio import (
    ensure_directory,
    fsync_directory,
    open_existing_regular,
    open_or_create_regular,
)


class SecureFileIoTests(unittest.TestCase):
    def test_existing_read_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            link = root / "link"
            target.write_text("secret", encoding="utf-8")
            link.symlink_to(target)

            with self.assertRaises(OSError):
                open_existing_regular(link)

    def test_existing_write_rejects_symbolic_link_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            link = root / "link"
            target.write_text("keep", encoding="utf-8")
            link.symlink_to(target)

            with self.assertRaises(OSError):
                open_or_create_regular(link, os.O_WRONLY | os.O_APPEND, 0o600)

            self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "fifo"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(OSError, "non-regular"):
                open_existing_regular(fifo)

    def test_existing_write_rejects_mode_drift_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "managed"
            path.write_text("keep", encoding="utf-8")
            path.chmod(0o644)

            with self.assertRaisesRegex(PermissionError, "expected 0600"):
                open_or_create_regular(path, os.O_WRONLY | os.O_APPEND, 0o600)

            self.assertEqual(path.read_text(encoding="utf-8"), "keep")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_existing_read_rejects_multiple_hard_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "managed"
            alias = root / "outside-alias"
            path.write_text("keep", encoding="utf-8")
            os.link(path, alias)

            with self.assertRaisesRegex(OSError, "2 hard links"):
                open_existing_regular(path)

            self.assertEqual(alias.read_text(encoding="utf-8"), "keep")

    def test_directory_helper_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            link = root / "link"
            target.mkdir()
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaises(NotADirectoryError):
                ensure_directory(link, 0o700)

    def test_directory_helper_rejects_mode_drift_when_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "managed"
            path.mkdir(mode=0o755)

            with self.assertRaisesRegex(PermissionError, "expected 0700"):
                ensure_directory(path, 0o700, require_mode=True)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_directory_helper_applies_mode_to_new_intermediate_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "usage" / "minute" / "2030" / "01"

            previous_umask = os.umask(0o077)
            try:
                ensure_directory(path, 0o2770, require_mode=True)
            finally:
                os.umask(previous_umask)

            for managed in (path, path.parent, path.parent.parent, path.parent.parent.parent):
                self.assertEqual(stat.S_IMODE(managed.stat().st_mode), 0o2770)

    def test_directory_fsync_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            link = root / "link"
            target.mkdir()
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaises(OSError):
                fsync_directory(link)

    def test_directory_fsync_propagates_failure_and_closes_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_close = os.close
            with (
                mock.patch("bk.fileio.os.fsync", side_effect=OSError("directory I/O failure")),
                mock.patch("bk.fileio.os.close", wraps=real_close) as close,
            ):
                with self.assertRaisesRegex(OSError, "directory I/O failure"):
                    fsync_directory(Path(tmp))

            close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

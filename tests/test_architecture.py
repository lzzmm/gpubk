import re
import tomllib
import unittest
from pathlib import Path

from bk.worker_guidance import (
    WORKER_ENABLE_COMMAND,
    WORKER_FOREGROUND_COMMAND,
    WORKER_INSTALL_COMMAND,
)


ROOT = Path(__file__).resolve().parents[1]


class SingleSourceOfTruthTests(unittest.TestCase):
    def test_worker_commands_have_one_python_source(self):
        literals = (
            WORKER_FOREGROUND_COMMAND,
            WORKER_INSTALL_COMMAND,
            WORKER_ENABLE_COMMAND,
        )
        offenders = []
        for path in (ROOT / "src" / "bk").glob("*.py"):
            if path.name == "worker_guidance.py":
                continue
            text = path.read_text(encoding="utf-8")
            for literal in literals:
                if literal in text:
                    offenders.append(f"{path.name}: {literal}")
        self.assertEqual(offenders, [])

    def test_public_schema_versions_are_not_redeclared(self):
        canonical = {
            "gpubk.usage.v1": "usage_schema.py",
            "gpubk.cluster.v1": "cluster.py",
        }
        offenders = []
        for path in (ROOT / "src" / "bk").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for literal, owner in canonical.items():
                if literal in text and path.name != owner:
                    offenders.append(f"{path.name}: {literal}")
        self.assertEqual(offenders, [])

    def test_repository_links_follow_pyproject_metadata(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        repository = project["project"]["urls"]["Repository"]
        expected_path = repository.removeprefix("https://github.com/")
        expected_owner, expected_name = expected_path.split("/", 1)

        files = [
            ROOT / "README.md",
            ROOT / "README.zh-CN.md",
            ROOT / "docs" / "GUIDE.md",
            ROOT / "docs" / "RELEASING.md",
            ROOT / "tools" / "remote_acceptance.py",
        ]
        mismatches = []
        pattern = re.compile(r"github\.com/([^/\s]+)/([^/\s)'\"`]+)")
        for path in files:
            for owner, name in pattern.findall(path.read_text(encoding="utf-8")):
                if owner != expected_owner or name.removesuffix(".git") != expected_name:
                    mismatches.append(f"{path.relative_to(ROOT)}: {owner}/{name}")
        self.assertEqual(mismatches, [])

    def test_guides_show_canonical_worker_commands(self):
        for relative in ("docs/GUIDE.md", "docs/GUIDE.zh-CN.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertIn(WORKER_FOREGROUND_COMMAND, text)
                self.assertIn(WORKER_INSTALL_COMMAND, text)
                self.assertIn(WORKER_ENABLE_COMMAND, text)

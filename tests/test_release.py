import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION_REF = re.compile(r"^\s*-\s+uses:\s+([^\s@]+)@([^\s#]+)")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


class ReleaseConfigurationTests(unittest.TestCase):
    def test_public_distribution_is_gpubk_and_cli_stays_bk(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertRegex(pyproject, r'(?m)^name = "gpubk"$')
        self.assertRegex(pyproject, r'(?m)^bk = "bk\.cli:main"$')

        public_files = [
            ROOT / "README.md",
            ROOT / "RELEASING.md",
            ROOT / "src" / "bk" / "mcp_server.py",
            ROOT / ".github" / "workflows" / "ci.yml",
        ]
        old_distribution = "bk-" + "gpu-booker"
        old_skill = "bk-" + "gpu-scheduler"
        for path in public_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(old_distribution, text, str(path.relative_to(ROOT)))
            self.assertNotIn(old_skill, text, str(path.relative_to(ROOT)))

    def test_external_github_actions_are_pinned_to_commit_shas(self):
        workflows = ROOT / ".github" / "workflows"
        if not workflows.is_dir():
            self.skipTest("GitHub workflows are not included in this source distribution")

        unpinned = []
        for path in sorted(workflows.glob("*.yml")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = ACTION_REF.match(line)
                if match and not match.group(1).startswith("./") and not COMMIT_SHA.fullmatch(match.group(2)):
                    unpinned.append(f"{path.relative_to(ROOT)}:{number}: {match.group(0).strip()}")

        self.assertEqual(unpinned, [], "mutable GitHub Action refs:\n" + "\n".join(unpinned))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy regression tests using only synthetic data in disposable repositories."""

import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SensitiveScanTests(unittest.TestCase):
    def setUp(self) -> None:
        # Git hooks export GIT_DIR and related variables. Never let a disposable
        # repository test reinitialize or commit into the invoking worktree.
        self.git_env = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic")
        self.git("config", "user.email", "test@example.invalid")
        shutil.copy(ROOT / ".gitleaks.toml", self.repo)
        self.env = {**self.git_env, "PRIVATE_IDENTIFIERS": "fixtureperson", "CI": "true"}
        tool = shutil.which("gitleaks") or str(ROOT / "data/tools/gitleaks")
        self.assertTrue(Path(tool).is_file(), "Install the pinned Gitleaks before testing")
        self.env["PATH"] = str(Path(tool).parent) + os.pathsep + os.environ["PATH"]

    def git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.git_env, check=True, capture_output=True
        )

    def scan(self, mode: str, blocked: bool = True) -> subprocess.CompletedProcess[str]:
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check-sensitive.py"), mode],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode != 0, blocked, run.stdout + run.stderr)
        self.assertNotIn("fixtureperson", run.stdout + run.stderr)
        return run

    def test_home_and_identifier_content(self) -> None:
        for value in (
            "/" + "home/syntheticperson/work",
            "/" + "Users/syntheticperson/work",
            "C:" + r"\Users\syntheticperson\work",
            "FIXTUREPERSON",
            "prefixfixturepersonSuffix",
        ):
            with self.subTest(value=value):
                (self.repo / "sample.txt").write_text(value)
                self.scan("working")

    def test_credentials(self) -> None:
        token = "ghp_" + "".join(random.Random(43).sample(string.ascii_letters + string.digits, 36))
        (self.repo / "sample.txt").write_text("github_token = " + token)
        result = self.scan("working")
        self.assertIn("github-pat", result.stdout)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_filename(self) -> None:
        (self.repo / "fixtureperson.txt").write_text("safe")
        self.scan("working")

    def test_private_identifier_in_lockfile(self) -> None:
        (self.repo / "package-lock.json").write_text('{"name": "fixtureperson"}')
        self.scan("working")
        self.git("add", "package-lock.json")
        self.git("commit", "-qm", "Synthetic lock fixture")
        self.scan("history")

    def test_staged_content_not_working_content(self) -> None:
        sample = self.repo / "sample.txt"
        sample.write_text("fixtureperson")
        self.git("add", "sample.txt")
        sample.write_text("safe")
        self.scan("staged")
        self.scan("working", blocked=False)

    def test_deleted_history_is_blocked(self) -> None:
        (self.repo / "sample.txt").write_text("fixtureperson")
        self.git("add", "sample.txt")
        self.git("commit", "-qm", "Synthetic fixture")
        self.git("rm", "-q", "sample.txt")
        self.git("commit", "-qm", "Delete fixture")
        self.scan("working", blocked=False)
        self.scan("history")

    def test_placeholders_and_example_file(self) -> None:
        (self.repo / ".env.example").write_text("/home/<USER>/work\n/Users/example/work")
        self.scan("working", blocked=False)
        self.git("add", ".env.example")
        self.scan("staged", blocked=False)

    def test_tracked_env_is_blocked(self) -> None:
        (self.repo / ".env").write_text("safe")
        self.git("add", ".env")
        self.scan("staged")

    def test_inline_suppression_does_not_bypass(self) -> None:
        (self.repo / "sample.txt").write_text("fixtureperson # gitleaks:allow")
        self.scan("working")

    def test_missing_ci_identifiers_fails(self) -> None:
        self.env["PRIVATE_IDENTIFIERS"] = ""
        run = self.scan("working")
        self.assertIn("CI requires", run.stderr)


if __name__ == "__main__":
    unittest.main()

"""Update-Schutzregeln mit einem lokalen, entbehrlichen Git-Repository prüfen."""

import runpy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


updater = runpy.run_path(str(Path(__file__).with_name("disk-image-update.py")))


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


@unittest.skipIf(os.name == "nt", "Git-Klone benötigen hier die Linux-Testumgebung")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "source"
        self.remote = root / "remote.git"
        self.client = root / "client"
        git("init", "-b", "main", str(self.source))
        git("config", "user.email", "test@example.invalid", cwd=self.source)
        git("config", "user.name", "Test", cwd=self.source)
        (self.source / "README.md").write_text("Version 1\n", encoding="utf-8")
        git("add", "README.md", cwd=self.source)
        git("commit", "-m", "initial", cwd=self.source)
        git("clone", "--bare", str(self.source), str(self.remote))
        git("clone", str(self.remote), str(self.client))
        git("config", "user.email", "test@example.invalid", cwd=self.client)
        git("config", "user.name", "Test", cwd=self.client)
        self.allowed = {git("remote", "get-url", "origin", cwd=self.client)}

    def publish_change(self):
        (self.source / "README.md").write_text("Version 2\n", encoding="utf-8")
        git("add", "README.md", cwd=self.source)
        git("commit", "-m", "second", cwd=self.source)
        git("push", str(self.remote), "main", cwd=self.source)

    def test_check_then_fast_forward(self):
        self.publish_change()
        result = updater["update"](self.client, allowed_origins=self.allowed)
        self.assertEqual(result["state"], "available")
        self.assertEqual((self.client / "README.md").read_text(), "Version 1\n")
        result = updater["update"](self.client, apply=True,
                                   allowed_origins=self.allowed)
        self.assertEqual(result["state"], "updated")
        self.assertEqual((self.client / "README.md").read_text(), "Version 2\n")

    def test_dirty_copy_is_preserved(self):
        self.publish_change()
        (self.client / "README.md").write_text("meine Änderung\n", encoding="utf-8")
        with self.assertRaisesRegex(updater["UpdateError"], "lokale Änderungen"):
            updater["update"](self.client, apply=True, allowed_origins=self.allowed)
        self.assertEqual((self.client / "README.md").read_text(), "meine Änderung\n")

    def test_other_origin_is_rejected(self):
        with self.assertRaisesRegex(updater["UpdateError"], "Git-Quelle"):
            updater["update"](self.client, allowed_origins={"https://example.invalid/repo.git"})


if __name__ == "__main__":
    unittest.main()

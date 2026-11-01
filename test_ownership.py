"""Dateirechte und Symlink-Schutz der Abschlussroutine ohne Datenträger prüfen."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix" and shutil.which("bash") and
                     shutil.which("getent"), "Linux-Werkzeuge erforderlich")
class OwnershipTests(unittest.TestCase):
    def test_outputs_become_readable_to_invoking_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_files = [root / name for name in ("image.img", "run.log", "image.sha256")]
            unrelated = root / "unrelated"
            for path in (*output_files, unrelated):
                path.write_text("Test", encoding="utf-8")
                path.chmod(0o644)
            link = root / "image.map"
            link.symlink_to(unrelated)
            helper = Path(__file__).with_name("disk-image-ownership.sh")
            env = dict(os.environ, SUDO_UID=str(os.getuid()), PKEXEC_UID="")
            command = ('source "$1"; IMG="$2"; LOG="$3"; HASHFILE="$4"; '
                       'MAPFILE="$5"; RESUME_IMAGE=""; release_outputs_to_caller')
            subprocess.run(["bash", "-c", command, "test", str(helper),
                            *map(str, output_files), str(link)],
                           env=env, capture_output=True, check=True)
            for path in output_files:
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(unrelated.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()

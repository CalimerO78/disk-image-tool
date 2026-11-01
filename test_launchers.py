"""Startmenü-Einträge ohne Änderungen am echten Benutzerprofil prüfen."""

import runpy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


installer = runpy.run_path(str(Path(__file__).with_name("install-launchers.py")))


class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0,
                         "Erstinstallation nur als Linux-Benutzer prüfbar")
    def test_first_install_sets_up_menu_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_pkexec = fake_bin / "pkexec"
            fake_pkexec.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            fake_pkexec.chmod(0o755)
            data = root / "share"
            env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                       XDG_DATA_HOME=str(data))
            result = subprocess.run(["bash", str(Path(__file__).with_name("install.sh"))],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            gui, cli, icon = installer["paths"](data)
            self.assertTrue(gui.is_file())
            self.assertTrue(cli.is_file())
            self.assertTrue(icon.is_file())

    def test_install_and_remove_only_managed_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installer["install"](base)
            gui, cli, icon = installer["paths"](base)
            self.assertTrue(icon.is_file())
            self.assertTrue(installer["large_icon_path"](base).is_file())
            self.assertIn("Terminal=false", gui.read_text(encoding="utf-8"))
            self.assertIn("Terminal=true", cli.read_text(encoding="utf-8"))
            self.assertIn("disk-image-gui.py", gui.read_text(encoding="utf-8"))
            self.assertIn("disk-image-cli-launcher.sh", cli.read_text(encoding="utf-8"))
            if shutil.which("desktop-file-validate"):
                subprocess.run(["desktop-file-validate", str(gui), str(cli)], check=True)
            installer["remove"](base)
            self.assertFalse(gui.exists())
            self.assertFalse(cli.exists())
            self.assertFalse(icon.exists())
            self.assertFalse(installer["large_icon_path"](base).exists())

    def test_unrelated_entry_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            gui, _, _ = installer["paths"](base)
            gui.parent.mkdir(parents=True)
            gui.write_text("[Desktop Entry]\nName=Eigener Starter\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "fremder Startmenü-Eintrag"):
                installer["install"](base)
            self.assertIn("Eigener Starter", gui.read_text(encoding="utf-8"))

    def test_exec_path_with_spaces_and_percent_is_quoted(self):
        rendered = installer["quote_exec"](PurePosixPath("/home/a b/100%/tool.py"))
        self.assertEqual(rendered, '"/home/a b/100%%/tool.py"')


if __name__ == "__main__":
    unittest.main()

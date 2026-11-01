"""Startmenü-Einträge ohne Änderungen am echten Benutzerprofil prüfen."""

import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath


installer = runpy.run_path(str(Path(__file__).with_name("install-launchers.py")))


class LauncherTests(unittest.TestCase):
    def test_install_and_remove_only_managed_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installer["install"](base)
            gui, cli, icon = installer["paths"](base)
            self.assertTrue(icon.is_file())
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

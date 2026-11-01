"""Wiederherstellungs-Schutzregeln ohne Schreibzugriff auf Blockgeräte prüfen."""

import hashlib
import gzip
import json
import os
import runpy
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


restore = runpy.run_path(str(Path(__file__).with_name("disk-image-restore.py")))
browse = runpy.run_path(str(Path(__file__).with_name("disk_image_browse.py")))


class RestoreTests(unittest.TestCase):
    def image(self, directory: str, content: bytes = b"Abbilddaten") -> Path:
        image = Path(directory) / "backup.img"
        image.write_bytes(content)
        (Path(directory) / "backup.sha256").write_text(
            hashlib.sha256(content).hexdigest() + f"  {image}\n", encoding="utf-8")
        return image

    def test_raw_preflight_and_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.image(directory)
            plan = restore["preflight"](image)
            self.assertEqual(plan["raw_size"], len(b"Abbilddaten"))
            output = Path(directory) / "copy.img"
            if os.name == "posix":
                restore["extract"](plan, None, output)
                self.assertEqual(output.read_bytes(), image.read_bytes())
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(restore["preflight"](output)["raw_sha256"], plan["raw_sha256"])

    def test_modified_image_is_rejected_before_target_access(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.image(directory)
            image.write_bytes(b"anders")
            with self.assertRaisesRegex(restore["RestoreError"], "SHA256-Prüfung"):
                restore["preflight"](image)

    @unittest.skipUnless(os.name == "posix", "Entpackungswerkzeug unter Linux prüfen")
    def test_gzip_is_fully_measured_before_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            data = b"Wiederherstellung" * 10000
            image = Path(directory) / "backup.img.gz"
            image.write_bytes(gzip.compress(data))
            (Path(directory) / "backup.sha256").write_text(
                hashlib.sha256(image.read_bytes()).hexdigest() + f"  {image}\n",
                encoding="utf-8")
            plan = restore["preflight"](image)
            self.assertEqual(plan["raw_size"], len(data))
            output = Path(directory) / "unpacked.img"
            restore["extract"](plan, None, output)
            self.assertEqual(output.read_bytes(), data)

    def test_existing_extract_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.image(directory)
            plan = restore["preflight"](image)
            output = Path(directory) / "existing.img"
            output.write_bytes(b"unveraendert")
            with self.assertRaisesRegex(restore["RestoreError"], "existiert bereits"):
                restore["extract"](plan, None, output)
            self.assertEqual(output.read_bytes(), b"unveraendert")

    @unittest.skipUnless(os.name == "posix", "Linux-Gerätepfade erforderlich")
    def test_device_plan_rejects_mounted_target_and_small_target(self):
        target = Path("/dev/sdz")
        image = Path("/backup/image.img")
        def fake_stat(path, **_kwargs):
            if str(path) == str(image):
                return SimpleNamespace(st_dev=999999)
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=2048)
        def fake_run(args, **_):
            if args[0] == "lsblk":
                mounted = self.mountpoint
                return SimpleNamespace(stdout=json.dumps({"blockdevices": [
                    {"path": str(target), "type": "disk", "size": 100,
                     "model": "Test", "ro": False, "mountpoints": [],
                     "children": [{"path": "/dev/sdz1", "type": "part", "size": 50,
                                   "ro": False, "mountpoints": [mounted]}]}
                ]}))
            return SimpleNamespace(stdout="100\n")
        self.mountpoint = "/mnt/used"
        with patch.dict(restore["device_plan"].__globals__, {"subprocess": SimpleNamespace(run=fake_run)}), \
             patch("os.stat", side_effect=fake_stat), \
             patch.object(Path, "read_text", return_value="Filename Type Size Used Priority\n"):
            with self.assertRaisesRegex(restore["RestoreError"], "eingehängt"):
                restore["device_plan"](target, image, "raw", 80)
            self.mountpoint = None
            with self.assertRaisesRegex(restore["RestoreError"], "zu klein"):
                restore["device_plan"](target, image, "raw", 120)

    def test_guest_path_rejects_traversal(self):
        with self.assertRaisesRegex(browse["BrowseError"], "kein \\.\\."):
            browse["guest_path"]("/home/../etc/passwd")

    def test_guestfish_is_forced_read_only_and_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.image(directory)
            with patch.object(browse["image_args"].__globals__["shutil"],
                              "which", return_value="/usr/bin/guestfish"):
                args = browse["image_args"](image)
            self.assertIn("--ro", args)
            self.assertIn("--format=raw", args)

    @unittest.skipUnless(os.name == "posix", "Linux-Gerätepfade erforderlich")
    def test_wrong_confirmation_never_opens_target(self):
        plan = {"image": "/tmp/backup.img", "mode": "raw", "raw_size": 10,
                "suffix": ".img", "raw_sha256": "0" * 64}
        fake = {"target": "/dev/sdz", "target_size": 100,
                "target_model": "Test", "target_rdev": 2048}
        with patch.dict(restore["restore"].__globals__, {"device_plan": lambda *_: fake}), \
             patch("os.geteuid", return_value=0), patch("os.open") as opened:
            with self.assertRaisesRegex(restore["RestoreError"], "Bestätigung"):
                restore["restore"](plan, None, Path("/dev/sdz"), "/dev/sdx")
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()

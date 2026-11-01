"""Fortschrittsdaten aus Map-Datei und Protokoll ohne Datenträger prüfen."""

import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path

from disk_image_progress import archive_percent, rescued_percent


class ProgressTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pv"), "pv ist lokal nicht installiert")
    def test_pv_emits_machine_readable_bytes_and_rate(self):
        result = subprocess.run(
            ["pv", "-n", "-b", "-r", "-s", "4"],
            input=b"abcd", capture_output=True, check=True,
        )
        lines = result.stderr.decode().splitlines()
        self.assertTrue(lines, result.stderr)
        bytes_done, rate = lines[-1].split()
        self.assertEqual(int(bytes_done), 4)
        self.assertGreaterEqual(float(rate), 0)

    def test_ddrescue_map_counts_only_rescued_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            mapfile = Path(directory) / "image.map"
            mapfile.write_text(
                "# current_pos  current_status\n0x0 0x0 +\n"
                "0x0 0x1000 +\n0x1000 0x1000 -\n0x2000 0x1000 +\n",
                encoding="ascii",
            )
            self.assertEqual(rescued_percent(mapfile, 0x3000), 66.67)

    def test_archive_requires_explicit_percentage(self):
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / "image.log"
            logfile.write_text("Beginn\n[42%] gesichert\n", encoding="utf-8")
            self.assertEqual(archive_percent(logfile), 42)
            logfile.write_text("keine Angabe\n", encoding="utf-8")
            self.assertIsNone(archive_percent(logfile))


if __name__ == "__main__":
    unittest.main()

"""Fortschrittsdaten aus Map-Datei und Protokoll ohne Datenträger prüfen."""

import tempfile
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from disk_image_progress import archive_percent, monitor_dd, rescued_percent


class ProgressTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and shutil.which("pv"),
                         "Linux und pv sind nötig")
    def test_dd_monitor_reports_both_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.img"
            process = subprocess.Popen(
                ["bash", "-c", 'dd if=/dev/zero bs=1M count=8 status=none | pv -q -L 1M > "$1"',
                 "test", str(image)],
                start_new_session=True,
            )
            stop = threading.Event()
            events: list[str] = []
            thread = threading.Thread(target=monitor_dd,
                                      args=(process.pid, {"image": str(image),
                                                          "size": 8 * 1048576}, stop,
                                            events.append))
            thread.start()
            try:
                time.sleep(4)
            finally:
                stop.set()
                thread.join(timeout=3)
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            payloads = [json.loads(item.removeprefix("@@DISK_IMAGE_PROGRESS "))
                        for item in events]
            self.assertTrue(any(isinstance(item.get("read_bps"), (int, float)) and
                                item["read_bps"] > 0 for item in payloads), payloads)
            self.assertTrue(any(isinstance(item.get("write_bps"), (int, float)) and
                                item["write_bps"] > 0 for item in payloads), payloads)

    @unittest.skipUnless(shutil.which("pv") and shutil.which("bash"), "pv und Bash sind nötig")
    def test_gui_pipe_emits_labeled_progress(self):
        helper = Path(__file__).with_name("disk-image-pv.sh")
        process = subprocess.Popen(
            ["bash", "-c", 'source "$1"; gui_pv read -s 4', "test", str(helper)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        process.stdin.write(b"ab")
        process.stdin.flush()
        time.sleep(0.35)
        output, errors = process.communicate(b"cd", timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(output, b"abcd")
        payloads = [json.loads(line.removeprefix("@@DISK_IMAGE_PROGRESS "))
                    for line in errors.decode().splitlines()
                    if line.startswith("@@DISK_IMAGE_PROGRESS ")]
        self.assertTrue(payloads, errors)
        self.assertEqual(payloads[-1], {"kind": "read", "bytes": 4})

    @unittest.skipUnless(shutil.which("pv"), "pv ist lokal nicht installiert")
    def test_pv_emits_machine_readable_bytes_and_rate(self):
        process = subprocess.Popen(
            ["pv", "-n", "-b", "-i", "0.2", "-s", "4"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        process.stdin.write(b"ab")
        process.stdin.flush()
        time.sleep(0.35)
        output, errors = process.communicate(b"cd", timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(output, b"abcd")
        lines = errors.decode().splitlines()
        self.assertTrue(lines, errors)
        self.assertEqual(int(lines[-1].split()[0]), 4)
        self.assertTrue(all(line.isdecimal() for line in lines), lines)

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

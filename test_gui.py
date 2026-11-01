"""Prüft die Übergabe an das CLI ohne Zugriff auf echte Datenträger."""

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("disk-image-gui.py")
spec = importlib.util.spec_from_file_location("disk_image_gui", SCRIPT)
gui = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gui
spec.loader.exec_module(gui)
mail = runpy.run_path(str(Path(__file__).with_name("disk-image-mail.py")))


class CommandTests(unittest.TestCase):
    def test_dd_options_reach_cli(self):
        with tempfile.TemporaryDirectory() as destination:
            settings = gui.Settings(
                engine="dd", source="/dev/sdb", dest=destination,
                mounted="unmount", compression="zstd", read_error="abort",
                smart="skip", verify=False, auto_install=True,
                ssh_host="backup@example.org", ssh_path="/srv/disk.img",
                notify="mail", mail_to="user@example.org",
            )
            script = Path("disk-image.sh")
            args = gui.build_command(settings, script)
            self.assertEqual(args[:3], ["bash", str(script), "--non-interactive"])
            for option, value in (("--engine", "dd"), ("--source", "/dev/sdb"),
                                  ("--mounted", "unmount"), ("--compression", "zstd"),
                                  ("--on-read-error", "abort"), ("--smart", "skip"),
                                  ("--ssh-host", "backup@example.org"),
                                  ("--ssh-path", "/srv/disk.img"),
                                  ("--notify", "mail"), ("--mail-to", "user@example.org")):
                self.assertEqual(args[args.index(option) + 1], value)
            self.assertIn("--no-verify", args)
            self.assertIn("--auto-install", args)
            self.assertIn("--gui-progress", args)

    def test_fsarchiver_rejects_mounted_source(self):
        with tempfile.TemporaryDirectory() as destination:
            settings = gui.Settings(engine="fsarchiver", source="/dev/sdb1",
                                    dest=destination, mounted="allow")
            with self.assertRaisesRegex(ValueError, "ausgehängte Partition"):
                gui.build_command(settings)

    def test_missing_destination_is_rejected_before_start(self):
        settings = gui.Settings(engine="dd", source="/dev/sda", dest="", mounted="unmount")
        with self.assertRaisesRegex(ValueError, "Zielordner auswählen"):
            gui.build_command(settings)

    def test_encrypted_verification_needs_identity(self):
        with tempfile.TemporaryDirectory() as destination:
            settings = gui.Settings(engine="dd", source="/dev/sdb", dest=destination,
                                    age_recipient="age1example", encrypt=True, verify=True)
            with self.assertRaisesRegex(ValueError, "Identitätsdatei"):
                gui.build_command(settings)

    def test_private_key_is_not_accepted_as_recipient(self):
        with tempfile.TemporaryDirectory() as destination:
            settings = gui.Settings(engine="dd", source="/dev/sdb", dest=destination,
                                    age_recipient="AGE-SECRET-KEY-1EXAMPLE", encrypt=True,
                                    verify=False)
            with self.assertRaisesRegex(ValueError, "geheimer Schlüssel"):
                gui.build_command(settings)

    def test_ddrescue_resume_keeps_map_pair(self):
        with tempfile.TemporaryDirectory() as destination:
            image = Path(destination) / "rescue.img"
            image.touch()
            Path(f"{image}.map").touch()
            settings = gui.Settings(engine="ddrescue", source="/dev/sdb", dest=destination,
                                    resume_image=str(image))
            args = gui.build_command(settings)
            self.assertEqual(args[args.index("--resume-image") + 1], str(image))
            self.assertNotIn("--compression", args)

    def test_smtp_config_is_passed_without_password(self):
        with tempfile.TemporaryDirectory() as destination:
            config = Path(destination) / "mail.json"
            config.touch()
            settings = gui.Settings(engine="dd", source="/dev/sdb", dest=destination,
                                    notify="mail", mail_to="user@example.org",
                                    mail_config=str(config))
            args = gui.build_command(settings)
            self.assertEqual(args[args.index("--mail-config") + 1], str(config))
            self.assertNotIn("password", " ".join(args))

    def test_smtp_requires_encrypted_transport(self):
        data = {"host": "smtp.example.org", "port": 587, "security": "none",
                "sender": "user@example.org", "username": "user", "password": "secret"}
        with self.assertRaisesRegex(ValueError, "STARTTLS"):
            mail["validate_config"](data)

    def test_smtp_starttls_auth_and_message(self):
        config = mail["validate_config"]({"host": "smtp.example.org", "port": 587,
                                          "security": "starttls", "sender": "user@example.org",
                                          "username": "user", "password": "secret"})
        with patch.dict(mail["send"].__globals__, {"smtplib": __import__("smtplib")}):
            with patch("smtplib.SMTP") as smtp_type:
                connection = smtp_type.return_value.__enter__.return_value
                mail["send"](config, "target@example.org", "Test", "Nachricht")
                connection.starttls.assert_called_once()
                connection.login.assert_called_once_with("user", "secret")
                message = connection.send_message.call_args.args[0]
                self.assertEqual(message["To"], "target@example.org")

    def test_gui_saves_smtp_config_without_putting_password_in_status(self):
        with tempfile.TemporaryDirectory() as destination:
            config_path = Path(destination) / "config" / "mail.json"
            with patch.object(gui, "MAIL_CONFIG", config_path), \
                 patch.object(gui.messagebox, "askyesno", return_value=True):
                app = gui.App()
                try:
                    app.smtp_host.set("smtp.example.org")
                    app.smtp_sender.set("user@example.org")
                    app.smtp_username.set("user")
                    app.smtp_password.set("private-test-password")
                    app._save_mail_config()
                    self.assertTrue(config_path.is_file())
                    self.assertEqual(json.loads(config_path.read_text())["security"], "starttls")
                    self.assertNotIn("private-test-password", app.mail_status.cget("text"))
                    if os.name == "posix":
                        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
                finally:
                    app.destroy()

    def test_visible_log_can_be_copied_and_saved(self):
        with tempfile.TemporaryDirectory() as destination:
            output = Path(destination) / "meldungen.txt"
            app = gui.App()
            try:
                app._append("FEHLER: Beispiel-Einhängepunkt\n")
                app._copy_output()
                self.assertEqual(app.clipboard_get(), "FEHLER: Beispiel-Einhängepunkt\n")
                with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(output)):
                    app._save_output()
                self.assertIn("FEHLER: Beispiel-Einhängepunkt", output.read_text(encoding="utf-8"))
            finally:
                app.destroy()

    def test_progress_uses_measured_bytes_and_rates(self):
        app = gui.App()
        try:
            app.source_size = 1000
            with patch.object(gui.time, "monotonic", side_effect=[1.0, 2.0, 3.0, 4.0]):
                app._show_progress({"kind": "read", "bytes": 250})
                app._show_progress({"kind": "read", "bytes": 1048826})
                app._show_progress({"kind": "write", "bytes": 100})
                app._show_progress({"kind": "write", "bytes": 2097252})
            self.assertEqual(app.progress.cget("value"), 100)
            self.assertIn("1.0 MiB/s", app.speed_label.get())
            self.assertIn("2.0 MiB/s", app.speed_label.get())
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()

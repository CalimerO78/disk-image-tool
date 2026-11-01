#!/usr/bin/env python3
"""Grafische Linux-Oberfläche für disk-image.sh.

Die GUI sammelt Einstellungen, prüft sie und startet das vorhandene Bash-Skript
im nicht interaktiven Modus. Die eigentliche Sicherungslogik bleibt im Skript.
"""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import runpy
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, scrolledtext, ttk


SCRIPT = Path(__file__).resolve().with_name("disk-image.sh")
RUNNER = Path(__file__).resolve().with_name("disk-image-runner.py")
MAIL_HELPER = Path(__file__).resolve().with_name("disk-image-mail.py")
MAIL_CONFIG = Path.home() / ".config" / "disk-image-tool" / "mail.json"
COMPRESSION = {"Keine (unverändertes Abbild)": "none", "gzip (kleinere Datei)": "gzip",
               "zstd (schnell und platzsparend)": "zstd"}
READ_ERROR = {"Fortfahren, Fehler mit Nullen füllen": "continue",
              "Beim ersten Lesefehler abbrechen": "abort"}
NOTIFICATION = {"Keine": "none", "Desktop-Meldung": "desktop", "E-Mail": "mail"}
MAIL_TRANSPORT = {"Lokales Mailprogramm": "local", "SMTP direkt": "smtp"}


@dataclass(frozen=True)
class Settings:
    """Alle Angaben eines Sicherungslaufs, unabhängig von den GUI-Widgets."""

    engine: str
    source: str
    dest: str
    mounted: str = "abort"
    compression: str = "none"
    read_error: str = "continue"
    resume_image: str = ""
    age_recipient: str = ""
    age_identity: str = ""
    encrypt: bool = False
    ssh_host: str = ""
    ssh_path: str = ""
    notify: str = "none"
    mail_to: str = ""
    mail_config: str = ""
    smart: str = "show"
    verify: bool = True
    auto_install: bool = True


def validate(settings: Settings) -> None:
    """Fehler vor dem Öffnen der Root-Abfrage verständlich anzeigen."""

    if settings.engine not in {"dd", "ddrescue", "fsarchiver"}:
        raise ValueError("Bitte eine Sicherungsart wählen.")
    if not re.fullmatch(r"/dev/[A-Za-z0-9._/+:-]+", settings.source):
        raise ValueError("Bitte ein Quellgerät aus der Liste wählen.")
    if not Path(settings.dest).is_dir():
        raise ValueError("Der Zielordner muss bereits existieren.")
    if settings.mounted not in {"abort", "unmount", "allow"}:
        raise ValueError("Bitte den Umgang mit eingehängten Datenträgern wählen.")
    if settings.smart not in {"show", "skip"} or settings.notify not in {"none", "desktop", "mail"}:
        raise ValueError("Eine Auswahl für SMART oder Benachrichtigung ist ungültig.")
    if settings.engine == "fsarchiver" and settings.mounted == "allow":
        raise ValueError("fsarchiver benötigt eine ausgehängte Partition.")
    if settings.engine != "dd":
        if settings.encrypt or settings.age_recipient or settings.age_identity:
            raise ValueError("Verschlüsselung ist nur für vollständige dd-Abbilder verfügbar.")
    elif settings.encrypt and not settings.age_recipient:
        raise ValueError("Für age fehlt der öffentliche Empfängerschlüssel.")
    elif not settings.encrypt and (settings.age_recipient or settings.age_identity):
        raise ValueError("Bitte age aktivieren, bevor Schlüssel angegeben werden.")
    elif settings.age_recipient and settings.verify and not settings.age_identity:
        raise ValueError("Für die Prüfung eines verschlüsselten Abbilds ist die private Identitätsdatei nötig.")
    elif settings.compression not in {"none", "gzip", "zstd"} or settings.read_error not in {"continue", "abort"}:
        raise ValueError("Kompression oder Lesefehlerverhalten ist ungültig.")
    if settings.age_identity and not settings.age_recipient:
        raise ValueError("Die private Identitätsdatei benötigt einen öffentlichen Empfängerschlüssel.")
    if settings.age_recipient.upper().startswith("AGE-SECRET-KEY"):
        raise ValueError("Hier wurde ein geheimer Schlüssel eingetragen. Bitte nur den öffentlichen age1-Schlüssel verwenden.")
    # Eine Datei unter /root kann für die normale GUI unsichtbar sein. Das
    # Sicherungsskript prüft sie nach der Administrator-Anmeldung erneut.
    if settings.age_identity and not PurePosixPath(settings.age_identity).is_absolute():
        raise ValueError("Bitte einen absoluten Pfad zur privaten Identitätsdatei angeben.")
    if settings.resume_image:
        if settings.engine != "ddrescue":
            raise ValueError("Eine unterbrochene Sicherung kann nur mit ddrescue fortgesetzt werden.")
        image = Path(settings.resume_image)
        if not image.is_file() or not Path(f"{image}.map").is_file():
            raise ValueError("Zum Fortsetzen müssen Abbild und zugehörige .map-Datei existieren.")
        if image.parent.resolve() != Path(settings.dest).resolve():
            raise ValueError("Das fortzusetzende Abbild muss im gewählten Zielordner liegen.")
    if bool(settings.ssh_host) != bool(settings.ssh_path):
        raise ValueError("Für SSH bitte Rechner und absoluten Zielpfad angeben.")
    if settings.ssh_host and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9_.-]+", settings.ssh_host):
        raise ValueError("Der SSH-Rechner muss als Benutzer@Host angegeben werden.")
    if settings.ssh_path and not re.fullmatch(r"/[A-Za-z0-9_./-]+", settings.ssh_path):
        raise ValueError("Der SSH-Zielpfad muss absolut sein und einfache Zeichen verwenden.")
    if settings.notify == "mail" and not settings.mail_to:
        raise ValueError("Für eine Mail-Benachrichtigung fehlt die Empfängeradresse.")
    if settings.notify == "mail" and not re.fullmatch(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", settings.mail_to):
        raise ValueError("Bitte eine gültige Mailadresse angeben.")
    if settings.mail_config:
        if settings.notify != "mail" or not Path(settings.mail_config).is_file():
            raise ValueError("Bitte die SMTP-Konfiguration zuerst speichern.")


def build_command(settings: Settings, script: Path = SCRIPT) -> list[str]:
    """Argumentliste ohne Shell-Auswertung erstellen; das CLI prüft erneut."""

    validate(settings)
    args = ["bash", str(script), "--non-interactive", "--engine", settings.engine,
            "--source", settings.source, "--dest", settings.dest,
            "--mounted", settings.mounted, "--smart", settings.smart,
            "--notify", settings.notify,
            "--verify" if settings.verify else "--no-verify"]
    if settings.auto_install:
        args.append("--auto-install")
    if settings.engine == "dd":
        args.extend(["--compression", settings.compression,
                     "--on-read-error", settings.read_error])
        if settings.age_recipient:
            args.extend(["--age-recipient", settings.age_recipient])
        if settings.age_identity:
            args.extend(["--age-identity", settings.age_identity])
    if settings.resume_image:
        args.extend(["--resume-image", settings.resume_image])
    if settings.ssh_host:
        args.extend(["--ssh-host", settings.ssh_host,
                     "--ssh-path", settings.ssh_path])
    if settings.notify == "mail":
        args.extend(["--mail-to", settings.mail_to])
        if settings.mail_config:
            args.extend(["--mail-config", settings.mail_config])
    return args


def list_devices() -> list[dict]:
    """lsblk liefert Geräte samt Unterpartitionen als strukturierte Daten."""

    result = subprocess.run(
        ["lsblk", "--json", "--bytes", "--paths", "--output",
         "PATH,SIZE,MODEL,TYPE,FSTYPE,MOUNTPOINTS"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    devices: list[dict] = []

    def add(items: list[dict]) -> None:
        for item in items:
            if item.get("type") in {"disk", "part"} and item.get("path"):
                devices.append(item)
            add(item.get("children") or [])

    add(json.loads(result.stdout).get("blockdevices") or [])
    return devices


class App(tk.Tk):
    """Einfache Auswahl, Kontrolle und Fortschrittsanzeige für einen Lauf."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Datenträger sichern")
        self.geometry("920x750")
        self.minsize(760, 620)
        style = ttk.Style(self)
        style.configure(".", font=("Sans", 10))
        style.configure("TNotebook.Tab", padding=(12, 8))
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.devices: dict[str, dict] = {}

        self.engine = tk.StringVar(value="dd")
        self.source = tk.StringVar()
        self.dest = tk.StringVar()
        self.mounted = tk.StringVar(value="abort")
        self.compression = tk.StringVar(value=next(iter(COMPRESSION)))
        self.read_error = tk.StringVar(value=next(iter(READ_ERROR)))
        self.resume_image = tk.StringVar()
        self.age_recipient = tk.StringVar()
        self.age_identity = tk.StringVar()
        self.encrypt = tk.BooleanVar(value=False)
        self.ssh_host = tk.StringVar()
        self.ssh_path = tk.StringVar()
        self.notify = tk.StringVar(value=next(iter(NOTIFICATION)))
        self.mail_to = tk.StringVar()
        self.mail_transport = tk.StringVar(value=next(iter(MAIL_TRANSPORT)))
        self.smtp_host = tk.StringVar()
        self.smtp_port = tk.StringVar(value="587")
        self.smtp_security = tk.StringVar(value="STARTTLS")
        self.smtp_sender = tk.StringVar()
        self.smtp_username = tk.StringVar()
        self.smtp_password = tk.StringVar()
        self.smart = tk.BooleanVar(value=True)
        self.verify = tk.BooleanVar(value=True)
        self.auto_install = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Bereit")

        self._build()
        self._load_mail_config()
        self._refresh_devices()
        self._engine_changed()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Datenträger sichern", font=("Sans", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Quelle wählen, Optionen prüfen und Sicherung starten. Die Kommandozeile bleibt weiterhin nutzbar.").pack(anchor="w", pady=(2, 12))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.basic = ttk.Frame(self.tabs, padding=18)
        self.options = ttk.Frame(self.tabs, padding=18)
        self.mail_tab = ttk.Frame(self.tabs, padding=18)
        self.run_tab = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.basic, text="1  Quelle und Ziel")
        self.tabs.add(self.options, text="2  Weitere Optionen")
        self.tabs.add(self.mail_tab, text="3  E-Mail")
        self.tabs.add(self.run_tab, text="4  Prüfen und starten")
        for frame in (self.basic, self.options, self.mail_tab):
            frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(self.basic, text="Sicherungsart", font=("Sans", 11, "bold")).grid(row=row, column=0, sticky="w", pady=5)
        self.engine_box = ttk.Combobox(self.basic, textvariable=self.engine,
                                       values=("dd", "ddrescue", "fsarchiver"), state="readonly")
        self.engine_box.grid(row=row, column=1, sticky="ew", pady=5)
        self.engine_box.bind("<<ComboboxSelected>>", lambda _e: self._engine_changed())
        row += 1
        ttk.Label(self.basic, text="dd: vollständiges Abbild · ddrescue: defekte Platten · fsarchiver: belegte Daten einer Partition",
                  wraplength=650).grid(row=row, column=1, sticky="w", pady=(0, 12))
        row += 1
        ttk.Label(self.basic, text="Quelle", font=("Sans", 11, "bold")).grid(row=row, column=0, sticky="w", pady=5)
        self.source_box = ttk.Combobox(self.basic, textvariable=self.source, state="readonly")
        self.source_box.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(self.basic, text="Aktualisieren", command=self._refresh_devices).grid(row=row, column=2, padx=8)
        row += 1
        self.device_detail = ttk.Label(self.basic, text="", wraplength=520)
        self.device_detail.grid(row=row, column=1, sticky="w", pady=(0, 12))
        self.source_box.bind("<<ComboboxSelected>>", lambda _e: self._show_device())
        row += 1
        ttk.Label(self.basic, text="Zielordner", font=("Sans", 11, "bold")).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(self.basic, textvariable=self.dest).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(self.basic, text="Auswählen", command=self._choose_dest).grid(row=row, column=2, padx=8)
        row += 1
        ttk.Label(self.basic, text="Das Abbild wird zuerst lokal gespeichert. Der Ordner darf nicht auf der Quelle liegen.",
                  wraplength=650).grid(row=row, column=1, sticky="w", pady=(0, 12))
        row += 1
        ttk.Label(self.basic, text="Wenn Quelle eingehängt ist", font=("Sans", 11, "bold")).grid(row=row, column=0, sticky="nw", pady=5)
        mount_frame = ttk.Frame(self.basic)
        mount_frame.grid(row=row, column=1, sticky="w")
        for value, label in (("abort", "Abbrechen (sicherer Standard)"),
                             ("unmount", "Vor der Sicherung aushängen"),
                             ("allow", "Eingehängt lassen (Abbild kann inkonsistent sein)")):
            ttk.Radiobutton(mount_frame, text=label, variable=self.mounted,
                            value=value).pack(anchor="w", pady=2)

        r = 0
        self.dd_controls = []
        self.dd_controls.append(self._field(self.options, r, "dd-Kompression", self.compression,
                                            tuple(COMPRESSION))); r += 1
        self.dd_controls.append(self._field(self.options, r, "Bei dd-Lesefehlern", self.read_error,
                                            tuple(READ_ERROR))); r += 1
        self.rescue_controls = self._entry(self.options, r, "ddrescue-Abbild fortsetzen", self.resume_image,
                                           browse=lambda: self._choose_file(self.resume_image)); r += 1
        self.encrypt_toggle = ttk.Checkbutton(self.options, text="Abbild mit age verschlüsseln",
                                               variable=self.encrypt, command=self._encryption_changed)
        self.encrypt_toggle.grid(row=r, column=1, sticky="w", pady=4)
        self.dd_controls.append(self.encrypt_toggle)
        ttk.Button(self.options, text="age erklären", command=self._show_age_help).grid(row=r, column=2, padx=8)
        r += 1
        self.age_controls = self._entry(self.options, r, "Öffentlicher Empfängerschlüssel", self.age_recipient); r += 1
        self.age_controls += self._entry(self.options, r, "Private Identitätsdatei zur Prüfung", self.age_identity,
                                         browse=lambda: self._choose_file(self.age_identity)); r += 1
        ttk.Label(self.options, text="Ohne den passenden privaten Schlüssel lässt sich das verschlüsselte Abbild später nicht öffnen.",
                  wraplength=530).grid(row=r, column=1, sticky="w", pady=(0, 8)); r += 1
        self._entry(self.options, r, "SSH-Rechner (Benutzer@Host)", self.ssh_host); r += 1
        self._entry(self.options, r, "SSH-Zielpfad", self.ssh_path); r += 1
        for label, variable in (("SMART-Kurzstatus anzeigen", self.smart),
                                ("Gespeichertes Abbild prüfen", self.verify),
                                ("Fehlende Pakete automatisch installieren (Debian/Ubuntu)", self.auto_install)):
            ttk.Checkbutton(self.options, text=label, variable=variable).grid(
                row=r, column=1, sticky="w", pady=3)
            r += 1
        ttk.Label(self.options, text="Weitere Optionen werden nur bei der passenden Sicherungsart verwendet.\n"
                  "Die Installation erfordert Netzwerk und Root-Rechte. Für Desktop-Benachrichtigungen kann die Sitzungserkennung unter Root fehlen.",
                  wraplength=530).grid(row=r, column=1, sticky="w", pady=12)

        ttk.Label(self.mail_tab, text="Benachrichtigungen",
                  font=("Sans", 15, "bold")).grid(row=0, column=0, columnspan=3,
                                                     sticky="w", pady=(0, 15))
        notify_box = self._field(self.mail_tab, 1, "Nach der Sicherung", self.notify,
                                 tuple(NOTIFICATION))
        notify_box.bind("<<ComboboxSelected>>", lambda _e: self._notify_changed())
        self.mail_controls = self._entry(self.mail_tab, 2, "Empfängeradresse", self.mail_to)
        self.transport_box = self._field(self.mail_tab, 3, "Versandweg", self.mail_transport,
                                         tuple(MAIL_TRANSPORT))
        self.transport_box.bind("<<ComboboxSelected>>", lambda _e: self._notify_changed())
        self.smtp_controls = []
        self.smtp_controls += self._entry(self.mail_tab, 4, "SMTP-Server", self.smtp_host)
        self.smtp_controls += self._entry(self.mail_tab, 5, "Port", self.smtp_port)
        self.smtp_security_box = self._field(self.mail_tab, 6, "Verbindung", self.smtp_security,
                                             ("STARTTLS", "SSL/TLS"))
        self.smtp_controls.append(self.smtp_security_box)
        self.smtp_controls += self._entry(self.mail_tab, 7, "Absenderadresse", self.smtp_sender)
        self.smtp_controls += self._entry(self.mail_tab, 8, "Benutzername", self.smtp_username)
        ttk.Label(self.mail_tab, text="Passwort").grid(row=9, column=0, sticky="w", padx=(0, 15), pady=4)
        password_entry = ttk.Entry(self.mail_tab, textvariable=self.smtp_password, show="•")
        password_entry.grid(row=9, column=1, sticky="ew", pady=4)
        self.smtp_controls.append(password_entry)
        self.mail_info = ttk.Label(self.mail_tab, text="", wraplength=570,
                                   justify="left")
        self.mail_info.grid(row=10, column=1, sticky="w", pady=(12, 14))
        self.smtp_save_button = ttk.Button(self.mail_tab, text="SMTP-Einstellungen speichern",
                                            command=self._save_mail_config)
        self.smtp_save_button.grid(row=11, column=1, sticky="w", pady=4)
        self.mail_check_button = ttk.Button(self.mail_tab, text="Mailprogramm prüfen",
                                            command=self._check_mail)
        self.mail_check_button.grid(row=12, column=1, sticky="w", pady=4)
        self.mail_test_button = ttk.Button(self.mail_tab, text="Testmail senden",
                                           command=self._send_test_mail)
        self.mail_test_button.grid(row=13, column=1, sticky="w", pady=4)
        self.mail_status = ttk.Label(self.mail_tab, text="", wraplength=570)
        self.mail_status.grid(row=14, column=1, sticky="w")

        self.summary = scrolledtext.ScrolledText(self.run_tab, height=11, wrap="word",
                                                 font=("TkFixedFont", 10), state="disabled")
        self.summary.pack(fill="x")
        ttk.Button(self.run_tab, text="Zusammenfassung aktualisieren", command=self._show_summary).pack(anchor="w", pady=8)
        buttons = ttk.Frame(self.run_tab)
        buttons.pack(fill="x", pady=8)
        self.start_button = ttk.Button(buttons, text="Sicherung starten", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Unterbrechen", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(buttons, textvariable=self.status).pack(side="right")
        self.progress = ttk.Progressbar(self.run_tab, mode="indeterminate", value=0)
        self.progress.pack(fill="x", pady=5)
        self.output = scrolledtext.ScrolledText(self.run_tab, height=12, wrap="word",
                                                font=("TkFixedFont", 10), state="disabled")
        self.output.pack(fill="both", expand=True, pady=(8, 0))
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self._show_summary()
                       if self.tabs.index("current") == 3 else None)
        self._notify_changed()

    @staticmethod
    def _field(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar,
               choices: tuple[str, ...]) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=4)
        widget = ttk.Combobox(parent, textvariable=variable, values=choices, state="readonly")
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        return widget

    @staticmethod
    def _entry(parent: ttk.Frame, row: int, label: str, variable: tk.StringVar,
               browse=None) -> list[ttk.Widget]:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        widgets: list[ttk.Widget] = [entry]
        if browse:
            button = ttk.Button(parent, text="Auswählen", command=browse)
            button.grid(row=row, column=2, padx=8)
            widgets.append(button)
        return widgets

    def _choose_dest(self) -> None:
        chosen = filedialog.askdirectory(title="Zielordner auswählen")
        if chosen:
            self.dest.set(chosen)

    @staticmethod
    def _choose_file(variable: tk.StringVar) -> None:
        chosen = filedialog.askopenfilename(title="Datei auswählen")
        if chosen:
            variable.set(chosen)

    def _refresh_devices(self) -> None:
        try:
            all_devices = list_devices()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                json.JSONDecodeError) as exc:
            self.devices = {}
            self.source_box.configure(values=())
            self.device_detail.configure(text=f"Geräteliste konnte nicht gelesen werden: {exc}")
            return
        self.devices = {item["path"]: item for item in all_devices
                        if item["type"] == ("part" if self.engine.get() == "fsarchiver" else "disk")}
        paths = list(self.devices)
        self.source_box.configure(values=paths)
        if self.source.get() not in self.devices:
            self.source.set("")
        self._show_device()

    def _engine_changed(self) -> None:
        if self.engine.get() == "fsarchiver" and self.mounted.get() == "allow":
            self.mounted.set("abort")
        for widget in self.dd_controls:
            widget.configure(state="readonly" if isinstance(widget, ttk.Combobox) and
                             self.engine.get() == "dd" else
                             "normal" if self.engine.get() == "dd" else "disabled")
        self._encryption_changed()
        for widget in self.rescue_controls:
            widget.configure(state="normal" if self.engine.get() == "ddrescue" else "disabled")
        self._refresh_devices()

    def _notify_changed(self) -> None:
        mail_enabled = self.notify.get() == "E-Mail"
        smtp_enabled = mail_enabled and self.mail_transport.get() == "SMTP direkt"
        for widget in self.mail_controls:
            widget.configure(state="normal" if mail_enabled else "disabled")
        self.transport_box.configure(state="readonly" if mail_enabled else "disabled")
        for widget in self.smtp_controls:
            widget.configure(state=("readonly" if smtp_enabled else "disabled")
                             if isinstance(widget, ttk.Combobox) else
                             ("normal" if smtp_enabled else "disabled"))
        self.smtp_save_button.configure(state="normal" if smtp_enabled else "disabled")
        self.mail_check_button.configure(state="normal" if mail_enabled and not smtp_enabled else "disabled")
        self.mail_test_button.configure(state="normal" if mail_enabled else "disabled")
        self.mail_info.configure(text=(
            "SMTP: Server, Port, TLS, Absender und ggf. Anmeldung eintragen. "
            "Beim Speichern liegt das Passwort lokal in einer Datei mit Rechten 600. "
            "Eine Testmail prüft die Verbindung; den tatsächlichen Eingang bitte selbst kontrollieren."
            if smtp_enabled else
            "Lokal: Das Sicherungsskript nutzt „mail“. Dazu muss auf Linux ein "
            "Mail-Transport eingerichtet sein. „Mailprogramm prüfen“ erkennt nur das Werkzeug; "
            "eine Testmail und ihr Eingang prüfen den Versandweg."
            if mail_enabled else
            "Wählen Sie „E-Mail“, um den Empfänger und den Versand zu prüfen. "
            "Bei einer Desktop-Meldung erscheint die Nachricht nur lokal."))

    def _mail_config_data(self) -> dict:
        try:
            port = int(self.smtp_port.get())
        except ValueError as exc:
            raise ValueError("Bitte einen gültigen SMTP-Port eintragen.") from exc
        return {"host": self.smtp_host.get().strip(), "port": port,
                "security": {"STARTTLS": "starttls", "SSL/TLS": "ssl"}[self.smtp_security.get()],
                "sender": self.smtp_sender.get().strip(),
                "username": self.smtp_username.get().strip(),
                "password": self.smtp_password.get()}

    def _smtp_settings_saved(self) -> bool:
        """Verhindert, dass sichtbare Änderungen mit einer alten Datei starten."""

        try:
            saved = runpy.run_path(str(MAIL_HELPER))["load_config"](MAIL_CONFIG)
            current = runpy.run_path(str(MAIL_HELPER))["validate_config"](self._mail_config_data())
            return saved == current
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return False

    def _load_mail_config(self) -> None:
        if not MAIL_CONFIG.exists():
            return
        try:
            data = runpy.run_path(str(MAIL_HELPER))["load_config"](MAIL_CONFIG)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.mail_status.configure(text=f"Gespeicherte SMTP-Einstellungen konnten nicht geladen werden: {exc}")
            return
        self.smtp_host.set(data["host"])
        self.smtp_port.set(str(data["port"]))
        self.smtp_security.set("STARTTLS" if data["security"] == "starttls" else "SSL/TLS")
        self.smtp_sender.set(data["sender"])
        self.smtp_username.set(data["username"])
        self.smtp_password.set(data["password"])
        self.mail_status.configure(text="Geschützte SMTP-Einstellungen geladen.")

    def _save_mail_config(self) -> None:
        try:
            data = runpy.run_path(str(MAIL_HELPER))["validate_config"](self._mail_config_data())
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("SMTP-Einstellungen prüfen", str(exc))
            return
        if not messagebox.askyesno(
                "SMTP-Einstellungen speichern",
                "Das SMTP-Passwort wird lokal in einer Datei mit Rechten 600 gespeichert. "
                "Es wird für Benachrichtigungen am Ende der Sicherung benötigt. Fortfahren?",
                default="no"):
            return
        temporary = None
        try:
            folder = MAIL_CONFIG.parent
            if folder.is_symlink() or MAIL_CONFIG.is_symlink():
                raise ValueError("Der Konfigurationspfad darf kein symbolischer Link sein.")
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                folder.chmod(0o700)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder,
                                             prefix=".mail-", delete=False) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o600)
                json.dump(data, output, ensure_ascii=False)
                output.write("\n")
            os.replace(temporary, MAIL_CONFIG)
            self.mail_status.configure(text=f"SMTP-Einstellungen gespeichert: {MAIL_CONFIG}")
        except (OSError, ValueError) as exc:
            self.mail_status.configure(text=f"Speichern fehlgeschlagen: {exc}")
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _check_mail(self) -> None:
        command = shutil.which("mail")
        if command:
            self.mail_status.configure(text=f"Mailprogramm gefunden: {command}. Versandkonfiguration bitte mit Testmail prüfen.")
        else:
            self.mail_status.configure(text="Kein Mailprogramm gefunden. Debian/Ubuntu: sudo apt install bsd-mailx.\n"
                                            "Zusätzlich muss ein Mail-Transport eingerichtet sein.")

    def _send_test_mail(self) -> None:
        address = self.mail_to.get().strip()
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", address):
            messagebox.showerror("Adresse prüfen", "Bitte eine gültige Empfängeradresse eintragen.")
            return
        smtp = self.mail_transport.get() == "SMTP direkt"
        if smtp and not self._smtp_settings_saved():
            messagebox.showerror("SMTP-Einstellungen speichern", "Bitte die aktuellen SMTP-Einstellungen zuerst speichern.")
            return
        if not smtp and not shutil.which("mail"):
            self._check_mail()
            return
        if not messagebox.askyesno("Testmail senden", f"Eine Testnachricht an {address} senden?",
                                   default="no"):
            return
        self.mail_status.configure(text="Testmail wird versendet …")
        self.mail_test_button.configure(state="disabled")
        threading.Thread(target=self._mail_test_worker, args=(address, smtp), daemon=True).start()

    def _mail_test_worker(self, address: str, smtp: bool) -> None:
        try:
            if smtp:
                command = ["python3", str(MAIL_HELPER), "--config", str(MAIL_CONFIG),
                           "--test", "--to", address]
                result = subprocess.run(command, text=True, capture_output=True,
                                        timeout=30, check=False)
            else:
                result = subprocess.run(["mail", "-s", "Datenträgersicherung: Testmail", address],
                                        input="Diese Testmail wurde über das lokale Mailprogramm versendet.\n",
                                        text=True, capture_output=True, timeout=20, check=False)
            status = ("Testmail übergeben. Bitte den Posteingang prüfen." if result.returncode == 0
                      else f"Versand fehlgeschlagen (Status {result.returncode}): {result.stderr.strip()}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            status = f"Testmail konnte nicht übergeben werden: {exc}"
        self.events.put(("mail_test_done", status))

    def _encryption_changed(self) -> None:
        enabled = self.engine.get() == "dd" and self.encrypt.get()
        for widget in self.age_controls:
            widget.configure(state="normal" if enabled else "disabled")

    def _show_age_help(self) -> None:
        help_window = tk.Toplevel(self)
        help_window.title("age-Verschlüsselung verstehen")
        help_window.geometry("620x410")
        help_window.transient(self)
        frame = ttk.Frame(help_window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Was bedeuten die beiden Schlüssel?",
                  font=("Sans", 15, "bold")).pack(anchor="w", pady=(0, 12))
        explanation = (
            "Öffentlicher Empfängerschlüssel: Er beginnt meist mit age1. "
            "Mit ihm wird das Abbild verschlüsselt. Er darf weitergegeben werden.\n\n"
            "Private Identitätsdatei: Sie enthält den geheimen Schlüssel zum "
            "Entschlüsseln. Bewahren Sie sie getrennt vom Abbild sicher auf. "
            "Für die Inhaltsprüfung direkt nach der Sicherung ist sie nötig.\n\n"
            "Schlüsselpaar auf Linux erzeugen:\n"
            "  age-keygen -o mein-age-schluessel.txt\n"
            "Der ausgegebene Public key gehört ins obere Feld; die erzeugte "
            "Datei ins untere Feld. Geben Sie die private Datei niemals an andere weiter.\n\n"
            "age ist nur für dd verfügbar. Ohne private Datei können Sie die "
            "Inhaltsprüfung abwählen; die gespeicherte Datei erhält dennoch eine "
            "SHA256-Prüfsumme. Eine spätere Entschlüsselung braucht immer den privaten Schlüssel."
        )
        ttk.Label(frame, text=explanation, justify="left", wraplength=565).pack(anchor="w")
        ttk.Button(frame, text="Schließen", command=help_window.destroy).pack(anchor="e", pady=15)

    def _show_device(self) -> None:
        item = self.devices.get(self.source.get())
        if not item:
            self.device_detail.configure(text="Bitte ein Gerät auswählen. Die Geräteliste kann sich nach dem Anstecken ändern.")
            return
        size = int(item.get("size") or 0) / 1_000_000_000
        mounts = ", ".join(m for m in (item.get("mountpoints") or []) if m) or "nicht eingehängt"
        self.device_detail.configure(text=f"{size:.1f} GB · {item.get('model') or 'Modell unbekannt'} · "
                                     f"{item.get('fstype') or 'Dateisystem unbekannt'} · {mounts}")

    def _settings(self) -> Settings:
        return Settings(
            engine=self.engine.get(), source=self.source.get(), dest=self.dest.get().strip(),
            mounted=self.mounted.get(), compression=COMPRESSION[self.compression.get()],
            read_error=READ_ERROR[self.read_error.get()],
            resume_image=self.resume_image.get().strip() if self.engine.get() == "ddrescue" else "",
            age_recipient=self.age_recipient.get().strip() if self.engine.get() == "dd" and self.encrypt.get() else "",
            age_identity=self.age_identity.get().strip() if self.engine.get() == "dd" and self.encrypt.get() else "",
            encrypt=self.engine.get() == "dd" and self.encrypt.get(),
            ssh_host=self.ssh_host.get().strip(), ssh_path=self.ssh_path.get().strip(),
            notify=NOTIFICATION[self.notify.get()], mail_to=self.mail_to.get().strip(),
            mail_config=str(MAIL_CONFIG) if self.notify.get() == "E-Mail" and
                        self.mail_transport.get() == "SMTP direkt" else "",
            smart="show" if self.smart.get() else "skip", verify=self.verify.get(),
            auto_install=self.auto_install.get())

    def _show_summary(self) -> None:
        settings = self._settings()
        mounted_label = {"abort": "Abbrechen", "unmount": "Vorher aushängen",
                         "allow": "Eingehängt lassen"}[settings.mounted]
        notify_label = next(label for label, value in NOTIFICATION.items()
                            if value == settings.notify)
        lines = [f"Quelle: {settings.source or 'noch nicht gewählt'}",
                 f"Zielordner: {settings.dest or 'noch nicht gewählt'}",
                 f"Sicherungsart: {settings.engine}",
                 f"Eingehängte Quelle: {mounted_label}",
                 f"SMART: {'anzeigen' if settings.smart == 'show' else 'überspringen'}",
                 f"Prüfung: {'ja' if settings.verify else 'nein'}",
                 f"Pakete automatisch installieren: {'ja' if settings.auto_install else 'nein'}"]
        if settings.engine == "dd":
            lines += [f"Kompression: {self.compression.get()}",
                      f"Lesefehler: {self.read_error.get()}",
                      f"Verschlüsselung: {'ja' if settings.age_recipient else 'nein'}"]
        if settings.resume_image and settings.engine == "ddrescue":
            lines.append(f"Fortsetzen: {settings.resume_image}")
        if settings.ssh_host:
            lines.append(f"SSH-Kopie: {settings.ssh_host}:{settings.ssh_path}")
        lines.append(f"Benachrichtigung: {notify_label}")
        if settings.notify == "mail":
            lines.append(f"Mail an: {settings.mail_to}")
            lines.append(f"Versandweg: {self.mail_transport.get()}")
        self._replace(self.summary, "\n".join(lines) + "\n")

    @staticmethod
    def _replace(widget: scrolledtext.ScrolledText, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _append(self, value: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", value)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _start(self) -> None:
        if platform.system() != "Linux":
            messagebox.showerror("Linux erforderlich", "Die Sicherung kann nur unter Linux gestartet werden.")
            return
        if not SCRIPT.is_file() or not RUNNER.is_file():
            messagebox.showerror("Datei fehlt", "Sicherungsskript oder GUI-Vermittler wurde nicht gefunden.")
            return
        settings = self._settings()
        if settings.mail_config and not self._smtp_settings_saved():
            messagebox.showerror("SMTP-Einstellungen speichern",
                                 "Bitte die aktuellen SMTP-Einstellungen zuerst speichern.")
            return
        try:
            command = build_command(settings)
        except ValueError as exc:
            messagebox.showerror("Angaben prüfen", str(exc))
            return
        if settings.source not in self.devices:
            messagebox.showerror("Quelle prüfen", "Bitte die Geräteliste aktualisieren und die Quelle erneut auswählen.")
            return
        command = ["python3", str(RUNNER), "--", *command]
        if os.geteuid() != 0:
            if not shutil.which("pkexec"):
                messagebox.showerror("Administratorrechte nötig",
                                     "pkexec fehlt. Bitte polkit installieren oder die GUI als root starten.")
                return
            command.insert(0, "pkexec")
        warning = ("Die ausgewählte Quelle wird gelesen. Falls Aushängen gewählt ist, "
                   "werden ihre Dateisysteme vor dem Start ausgehängt.\n\n"
                   f"Quelle: {settings.source}\nZiel: {settings.dest}\n\n"
                   "Jetzt starten?")
        if not messagebox.askyesno("Sicherung bestätigen", warning, default="no"):
            return
        self._replace(self.output, "")
        self.status.set("Sicherung läuft …")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(command,), daemon=True).start()

    def _worker(self, command: list[str]) -> None:
        try:
            # Kein shell=True: Gerätepfade und Benutzereingaben bleiben Argumente.
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            errors="replace", bufsize=1,
                                            start_new_session=True)
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.events.put(("output", line))
            self.events.put(("done", self.process.wait()))
        except OSError as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.process = None

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "output":
                    self._append(str(value))
                elif kind == "mail_test_done":
                    self.mail_status.configure(text=str(value))
                    self.mail_test_button.configure(state="normal" if self.notify.get() == "E-Mail" else "disabled")
                elif kind in {"done", "error"}:
                    self.progress.stop()
                    self.progress.configure(value=0)
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if kind == "error":
                        self.status.set("Start fehlgeschlagen")
                        self._append(f"\nStart fehlgeschlagen: {value}\n")
                    else:
                        code = int(value)
                        self.status.set({0: "Abgeschlossen", 2: "Prüfung fehlgeschlagen",
                                         3: "Unvollständige Sicherung"}.get(code,
                                            f"Beendet (Status {code})"))
                        self._append(f"\n{self.status.get()}.\n")
                        if code != 0:
                            messagebox.showwarning("Sicherung beendet", self.status.get() +
                                                   " – bitte Protokoll und Ausgabe prüfen.")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _stop(self) -> None:
        if self.process and messagebox.askyesno(
                "Sicherung unterbrechen", "Das angefangene Abbild bleibt erhalten. Wirklich unterbrechen?",
                default="no"):
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("cancel\n")
                self.process.stdin.flush()
                self.status.set("Unterbrechung angefordert …")
                self.stop_button.configure(state="disabled")
            except (BrokenPipeError, OSError):
                messagebox.showwarning("Unterbrechung", "Der Lauf antwortet nicht mehr. Bitte die Ausgabe prüfen.")

    def _close(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Sicherung läuft", "Bitte den laufenden Vorgang zuerst unterbrechen oder abschließen lassen.")
        else:
            self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()

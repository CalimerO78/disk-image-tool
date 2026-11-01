"""Eigener GUI-Dialog zum Prüfen, Zurückspielen und Dateizugriff."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk


ROOT = Path(__file__).resolve().parent
RESTORE = ROOT / "disk-image-restore.py"
BROWSE = ROOT / "disk_image_browse.py"


class RestoreWindow(tk.Toplevel):
    """Zuerst nur lesen und planen; das Überschreiben braucht eine zweite Freigabe."""

    def __init__(self, parent: tk.Misc, on_close) -> None:
        super().__init__(parent)
        self.title("Abbild wiederherstellen oder Dateien herauskopieren")
        self.geometry("900x750")
        self.minsize(760, 600)
        self.on_close = on_close
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.after_install: str | None = None
        self.plan: dict | None = None
        self.planned_inputs: tuple[str, ...] | None = None
        self.running_inputs: tuple[str, ...] | None = None
        self.browse_image: str | None = None
        self.browse_filesystems: list[dict] = []
        self.current_directory = "/"
        self.pending_directory = "/"
        self.image = tk.StringVar()
        self.identity = tk.StringVar()
        self.target = tk.StringVar()
        self.output = tk.StringVar()
        self.operation = tk.StringVar(value="restore")
        self.filesystem = tk.StringVar()
        self.status = tk.StringVar(value="Bitte zuerst ein Abbild auswählen.")
        self.browse_status = tk.StringVar(value="Nur unkomprimierte .img-Dateien werden schreibgeschützt geöffnet.")
        self._build()
        self.image.trace_add("write", self._image_changed)
        self._mode_changed()
        self._load_devices()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Wiederherstellen", font=("Sans", 19, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Das Zurückspielen überschreibt das Zielgerät vollständig. "
                  "Prüfen und Ausführen sind getrennte Schritte.", wraplength=820).pack(anchor="w", pady=(4, 12))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        restore_tab = ttk.Frame(tabs, padding=12)
        browse_tab = ttk.Frame(tabs, padding=12)
        tabs.add(restore_tab, text="Zurückspielen oder entpacken")
        tabs.add(browse_tab, text="Einzelne Dateien")

        self._field(restore_tab, 0, "Sicherungsdatei", self.image, self._choose_image)
        self._field(restore_tab, 1, "age-Identität (falls nötig)", self.identity,
                    lambda: self._choose_file(self.identity))
        modes = ttk.Frame(restore_tab)
        modes.grid(row=2, column=1, sticky="w", pady=10)
        ttk.Radiobutton(modes, text="Auf Datenträger zurückspielen", variable=self.operation,
                        value="restore", command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(modes, text="Als unkomprimierte .img entpacken",
                        value="extract", command=self._mode_changed).pack(side="left", padx=15)
        ttk.Label(restore_tab, text="Zielgerät").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=3)
        self.device_box = ttk.Combobox(restore_tab, textvariable=self.target, state="readonly")
        self.device_box.grid(row=3, column=1, sticky="ew", pady=3)
        self.target_refresh = ttk.Button(restore_tab, text="Aktualisieren", command=self._load_devices)
        self.target_refresh.grid(row=3, column=2, padx=6)
        self.output_entry, self.output_button = self._field(
            restore_tab, 4, "Neue .img-Datei", self.output, self._choose_output)
        ttk.Label(restore_tab, text="Für .fsa bitte eine Partition wählen. Für .img eine ganze Platte. "
                  "Eingehängte oder anderweitig verwendete Datenträger werden abgewiesen.",
                  wraplength=700).grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        actions = ttk.Frame(restore_tab)
        actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Button(actions, text="Abbild und Ziel prüfen", command=self._plan).pack(side="left")
        self.execute_button = ttk.Button(actions, text="Ausführen …", command=self._execute,
                                         state="disabled")
        self.execute_button.pack(side="left", padx=10)
        ttk.Label(restore_tab, textvariable=self.status, wraplength=720).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=6)
        self.progress = ttk.Progressbar(restore_tab, mode="indeterminate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=5)
        log_buttons = ttk.Frame(restore_tab)
        log_buttons.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        ttk.Label(log_buttons, text="Meldungen").pack(side="left")
        ttk.Button(log_buttons, text="Kopieren", command=self._copy_log).pack(side="right")
        ttk.Button(log_buttons, text="Als Datei speichern …", command=self._save_log).pack(side="right", padx=8)
        self.log = scrolledtext.ScrolledText(restore_tab, height=13, wrap="word", state="disabled")
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew")
        restore_tab.columnconfigure(1, weight=1)
        restore_tab.rowconfigure(10, weight=1)

        ttk.Label(browse_tab, text="Dateien aus einem .img schreibgeschützt ansehen",
                  font=("Sans", 14, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(browse_tab,
                  text="Bei .gz, .zst oder .age zuerst im ersten Reiter eine unkomprimierte .img-Datei erzeugen. "
                       "Dafür wird freier Speicherplatz in Größe des Originals benötigt.",
                  wraplength=730).pack(anchor="w", pady=(0, 8))
        browse_actions = ttk.Frame(browse_tab)
        browse_actions.pack(fill="x", pady=4)
        ttk.Button(browse_actions, text="Dateisysteme laden", command=self._browse_filesystems).pack(side="left")
        self.fs_box = ttk.Combobox(browse_actions, textvariable=self.filesystem,
                                  state="readonly", width=36)
        self.fs_box.pack(side="left", padx=9)
        ttk.Button(browse_actions, text="Ordner anzeigen", command=lambda: self._browse_directory("/")).pack(side="left")
        ttk.Label(browse_tab, textvariable=self.browse_status, wraplength=740).pack(anchor="w", pady=5)
        self.file_list = tk.Listbox(browse_tab, height=14)
        self.file_list.pack(fill="both", expand=True, pady=5)
        self.file_list.bind("<Double-Button-1>", lambda _event: self._open_selected())
        file_actions = ttk.Frame(browse_tab)
        file_actions.pack(anchor="w", pady=5)
        ttk.Button(file_actions, text="Ordner öffnen", command=self._open_selected).pack(side="left")
        ttk.Button(file_actions, text="Eine Ebene höher", command=self._browse_parent).pack(side="left", padx=8)
        ttk.Button(file_actions, text="Ausgewählte Datei herauskopieren …",
                   command=self._copy_selected).pack(side="left")

    @staticmethod
    def _field(parent, row: int, caption: str, variable: tk.StringVar, action):
        ttk.Label(parent, text=caption).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        button = ttk.Button(parent, text="Auswählen", command=action)
        button.grid(row=row, column=2, padx=6)
        return entry, button

    def _mode_changed(self) -> None:
        restore = self.operation.get() == "restore"
        self.device_box.configure(state="readonly" if restore else "disabled")
        self.target_refresh.configure(state="normal" if restore else "disabled")
        self.output_entry.configure(state="disabled" if restore else "normal")
        self.output_button.configure(state="disabled" if restore else "normal")
        self.execute_button.configure(state="disabled")

    def _image_changed(self, *_args) -> None:
        """Veraltete Dateisystemlisten nie für ein neues Abbild weiterverwenden."""
        self.browse_image = None
        self.browse_filesystems = []
        self.filesystem.set("")
        self.fs_box.configure(values=())
        self.file_list.delete(0, "end")
        self.current_directory = "/"
        self.plan = None
        self.planned_inputs = None
        self.execute_button.configure(state="disabled")

    def _choose_file(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(parent=self)
        if path:
            variable.set(path)

    def _choose_image(self) -> None:
        self._choose_file(self.image)

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".img",
                                            filetypes=(("Rohabbild", "*.img"),))
        if path:
            self.output.set(path)

    def _load_devices(self) -> None:
        try:
            result = subprocess.run(["lsblk", "--json", "--bytes", "--paths", "--output",
                                     "PATH,TYPE,SIZE,MODEL,MOUNTPOINTS"],
                                    capture_output=True, text=True, check=True, timeout=15)
            devices = []
            def walk(items):
                for item in items:
                    if item.get("type") in {"disk", "part"} and item.get("path"):
                        devices.append(item["path"])
                    walk(item.get("children") or [])
            walk(json.loads(result.stdout)["blockdevices"])
            self.device_box.configure(values=devices)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.device_box.configure(values=())

    def _inputs(self) -> tuple[str, ...]:
        return (self.image.get().strip(), self.identity.get().strip(),
                self.operation.get(), self.target.get().strip(), self.output.get().strip())

    def _base_command(self, script: Path) -> list[str]:
        command = [sys.executable, str(script), "--image", self.image.get().strip()]
        if self.identity.get().strip():
            command += ["--identity", self.identity.get().strip()]
        return command

    def _plan(self) -> None:
        if self.busy:
            return
        if not self.image.get().strip():
            messagebox.showerror("Abbild fehlt", "Bitte zuerst eine Sicherungsdatei auswählen.", parent=self)
            return
        if self.operation.get() == "restore" and not self.target.get().strip():
            messagebox.showerror("Ziel fehlt", "Bitte einen Datenträger auswählen.", parent=self)
            return
        if self.operation.get() == "extract" and self.image.get().endswith(".fsa"):
            messagebox.showerror("Archivformat", "Ein .fsa-Archiv kann nur auf eine Partition zurückgespielt werden.", parent=self)
            return
        if self.operation.get() == "extract" and not self.output.get().strip():
            messagebox.showerror("Ausgabe fehlt", "Bitte zuerst den Pfad für die neue .img-Datei wählen.", parent=self)
            return
        suffix = self.image.get().strip()
        required = []
        if suffix.endswith(".fsa"):
            required.append(("fsarchiver", "fsarchiver"))
        if suffix.endswith(".age"):
            required.append(("age", "age"))
        if ".zst" in suffix:
            required.append(("zstd", "zstd"))
        if ".gz" in suffix:
            required.append(("gzip", "gzip"))
        if suffix.endswith(".img") and Path(suffix + ".map").is_file():
            required.append(("ddrescuelog", "gddrescue"))
        if not self._ensure_tools(required, "plan"):
            return
        command = self._base_command(RESTORE) + ["--plan", "--json"]
        if self.operation.get() == "restore":
            command += ["--target", self.target.get().strip()]
        else:
            command += ["--output", self.output.get().strip()]
        self.plan = None
        self.planned_inputs = None
        self.execute_button.configure(state="disabled")
        self._start("plan", command, "Prüfe Prüfsumme und entpackten Inhalt. Das kann längere Zeit dauern …")

    def _execute(self) -> None:
        if self.busy or self.plan is None or self._inputs() != self.planned_inputs:
            messagebox.showerror("Erneut prüfen", "Die Angaben wurden geändert. Bitte erneut prüfen.", parent=self)
            return
        command = self._base_command(RESTORE)
        if self.operation.get() == "restore":
            target = self.target.get().strip()
            answer = simpledialog.askstring("Zielgerät bestätigen",
                f"ALLE Daten auf {target} werden überschrieben.\n"
                f"Modell: {self.plan.get('target_model', 'unbekannt')}\n"
                f"Größe: {self.plan.get('target_size', 0)} Bytes\n\n"
                "Bitte den vollständigen Gerätepfad exakt eintippen:", parent=self)
            if answer != target:
                self.status.set("Abgebrochen: Zielgerät wurde nicht exakt bestätigt.")
                return
            if not messagebox.askyesno("Letzte Bestätigung",
                    f"Jetzt {target} vollständig überschreiben und anschließend prüfen?",
                    default="no", parent=self):
                return
            command += ["--restore", "--target", target, "--confirm-target", answer]
            if os.geteuid() != 0:
                if not shutil.which("pkexec"):
                    messagebox.showerror("Administratorrechte fehlen", "pkexec ist nicht installiert.", parent=self)
                    return
                command.insert(0, "pkexec")
            label = "Stelle wieder her und prüfe das Ziel danach erneut …"
        else:
            output = self.output.get().strip()
            if not output:
                messagebox.showerror("Ausgabe fehlt", "Bitte einen neuen .img-Dateipfad wählen.", parent=self)
                return
            command += ["--extract", "--output", output]
            label = "Entpacke das geprüfte Abbild …"
        self._start("execute", command, label)

    def _start(self, kind: str, command: list[str], label: str) -> None:
        self.busy = True
        self.running_inputs = self._inputs()
        self.status.set(label)
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(kind, command), daemon=True).start()

    def _ensure_tools(self, requirements: list[tuple[str, str]], after: str) -> bool:
        """Fehlende Linux-Pakete nur nach einer Rückfrage installieren."""

        packages = sorted({package for tool, package in requirements if not shutil.which(tool)})
        if not packages:
            return True
        names = ", ".join(packages)
        if not messagebox.askyesno("Fehlende Programme", f"Für diese Funktion fehlen: {names}.\n"
                                   "Jetzt automatisch installieren?", default="yes", parent=self):
            self.status.set(f"Bitte zuerst installieren: {names}")
            return False
        if not Path("/etc/debian_version").exists() or not shutil.which("apt-get"):
            messagebox.showerror("Paketmanager", "Automatische Installation ist nur auf Debian/Ubuntu möglich.", parent=self)
            return False
        command = ["apt-get", "install", "-y", *packages]
        if os.geteuid() != 0:
            if not shutil.which("pkexec"):
                messagebox.showerror("Administratorrechte", "pkexec fehlt; bitte Pakete im Terminal installieren.", parent=self)
                return False
            command.insert(0, "pkexec")
        self.after_install = after
        self._start("install_tools", command, f"Installiere {names} …")
        return False

    def _worker(self, kind: str, command: list[str]) -> None:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", bufsize=1)
            lines = []
            assert process.stdout is not None
            for line in process.stdout:
                if line.startswith("@@DISK_IMAGE_RESTORE_PROGRESS "):
                    try:
                        self.events.put(("progress", json.loads(line.split(" ", 1)[1])))
                    except json.JSONDecodeError:
                        pass
                else:
                    lines.append(line)
                    if kind in {"execute", "install_tools"}:
                        self.events.put(("log", line))
            self.events.put((kind, (process.wait(), "".join(lines))))
        except (OSError, ValueError) as exc:
            self.events.put((kind, (1, str(exc))))

    def _drain(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append(str(value))
                elif kind == "progress" and isinstance(value, dict):
                    total = value.get("total") or 0
                    if total > 0:
                        percent = min(100, 100 * value.get("bytes", 0) / total)
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=percent)
                        phase = "Schreiben" if value.get("phase") == "write" else "Nachprüfen"
                        self.status.set(f"{phase}: {percent:.1f} %")
                elif kind in {"plan", "execute", "browse_fs", "browse_dir", "browse_copy", "install_tools"}:
                    self.busy = False
                    self.progress.stop()
                    code, output = value
                    if code != 0:
                        self.status.set("Fehler – Meldungen prüfen.")
                        self.browse_status.set("Fehler: " + output.strip()[-500:])
                        self._append(output)
                    elif kind == "plan":
                        try:
                            parsed = json.loads(output)
                            if self._inputs() != self.running_inputs:
                                self.status.set("Angaben wurden während der Prüfung geändert. Bitte erneut prüfen.")
                                continue
                            self.plan = parsed
                            self.planned_inputs = self.running_inputs
                            self.status.set(f"Geprüft: {self.plan['raw_size']} Bytes; " +
                                            (f"Ziel {self.plan['target']} geeignet." if self.plan.get("target")
                                             else "Entpacken möglich."))
                            self.execute_button.configure(state="normal")
                        except (ValueError, KeyError):
                            self.status.set("Ungültige Prüfausgabe.")
                    elif kind == "execute":
                        self.status.set("Vorgang erfolgreich abgeschlossen.")
                        if self.operation.get() == "extract":
                            self.image.set(self.output.get().strip())
                            self.browse_status.set("Entpacktes .img bereit. Im Reiter „Einzelne Dateien“ Dateisysteme laden.")
                    elif kind == "install_tools":
                        self.status.set("Benötigte Programme installiert.")
                        following, self.after_install = self.after_install, None
                        if following == "plan":
                            self.after(100, self._plan)
                        elif following == "browse_fs":
                            self.after(100, self._browse_filesystems)
                    elif kind == "browse_fs":
                        try:
                            if self._inputs() != self.running_inputs:
                                self.browse_status.set("Abbild während der Prüfung geändert. Bitte erneut laden.")
                                continue
                            self.browse_filesystems = json.loads(output)
                            self.browse_image = self.image.get().strip()
                            devices = [item["device"] for item in self.browse_filesystems]
                            self.fs_box.configure(values=devices)
                            if devices:
                                self.filesystem.set(devices[0])
                            self.browse_status.set("Dateisystem wählen und „Ordner anzeigen“ klicken.")
                        except (ValueError, KeyError):
                            self.browse_status.set("Ungültige Dateisystem-Liste.")
                    elif kind == "browse_dir":
                        try:
                            if self._inputs() != self.running_inputs:
                                self.browse_status.set("Angaben geändert. Bitte Ordner erneut laden.")
                                continue
                            names = json.loads(output)
                            self.current_directory = self.pending_directory
                            self.file_list.delete(0, "end")
                            for name in names:
                                self.file_list.insert("end", name)
                            self.browse_status.set(f"Ordner: {self.current_directory} · {len(names)} Einträge")
                        except ValueError:
                            self.browse_status.set("Ungültige Dateiliste.")
                    else:
                        if self._inputs() == self.running_inputs:
                            self.browse_status.set(output.strip() or "Datei kopiert.")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain)

    def _browse_command(self, *args: str) -> list[str]:
        return [sys.executable, str(BROWSE), "--image", self.image.get().strip(), *args]

    def _browse_filesystems(self) -> None:
        if self.busy:
            return
        if not self.image.get().endswith(".img"):
            self.browse_status.set("Bitte zuerst eine unkomprimierte .img-Datei wählen oder entpacken.")
            return
        if not self._ensure_tools([("guestfish", "libguestfs-tools")], "browse_fs"):
            return
        self._start("browse_fs", self._browse_command("--filesystems"),
                    "Prüfe die Abbild-Prüfsumme und suche Dateisysteme …")

    def _browse_directory(self, directory: str) -> None:
        if self.busy or not self.filesystem.get() or self.browse_image != self.image.get().strip():
            return
        self.pending_directory = directory
        self._start("browse_dir", self._browse_command("--list", "--filesystem",
                    self.filesystem.get(), "--path", directory), "Lese Ordner im Abbild …")

    def _selected_remote(self) -> str | None:
        selection = self.file_list.curselection()
        if not selection:
            self.browse_status.set("Bitte zuerst eine Datei oder einen Ordner auswählen.")
            return None
        name = self.file_list.get(selection[0])
        return str(PurePosixPath(self.current_directory) / name)

    def _open_selected(self) -> None:
        remote = self._selected_remote()
        if remote:
            self._browse_directory(remote)

    def _browse_parent(self) -> None:
        parent = str(PurePosixPath(self.current_directory).parent)
        self._browse_directory(parent if parent.startswith("/") else "/")

    def _copy_selected(self) -> None:
        remote = self._selected_remote()
        if not remote or self.busy or self.browse_image != self.image.get().strip():
            return
        output = filedialog.asksaveasfilename(parent=self, initialfile=PurePosixPath(remote).name)
        if output:
            self._start("browse_copy", self._browse_command("--copy-file", "--filesystem",
                        self.filesystem.get(), "--path", remote, "--output", output),
                        "Kopiere eine Datei aus dem Abbild …")

    def _append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end-1c"))

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt")
        if path:
            try:
                Path(path).write_text(self.log.get("1.0", "end-1c") + "\n", encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Speichern fehlgeschlagen", str(exc), parent=self)

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo("Vorgang läuft", "Bitte den Vorgang erst abschließen lassen.", parent=self)
            return
        self.on_close()
        self.destroy()

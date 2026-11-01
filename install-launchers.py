#!/usr/bin/env python3
"""Installiert zwei Linux-Startmenü-Einträge für den angemeldeten Benutzer.

Es werden nur eigene, eindeutig markierte Einträge ersetzt oder entfernt.
Administratorrechte sind weder nötig noch erwünscht. Der Quellordner muss
an seinem Platz bleiben, da die Starter absolute Pfade dorthin enthalten.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MARKER = "X-DiskImageTool-Managed=true"
ENTRY_NAMES = ("disk-image-tool.desktop", "disk-image-tool-cli.desktop")
ICON_NAME = "disk-image-tool.png"


def data_home() -> Path:
    """Das standardisierte Datenverzeichnis des aktuellen Benutzers ermitteln."""

    configured = os.environ.get("XDG_DATA_HOME", "")
    return Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".local/share"


def quote_exec(path: Path) -> str:
    """Ein Desktop-Exec-Argument gemäß Freedesktop-Syntax schützen."""

    value = str(path).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def entry(name: str, comment: str, command: str, terminal: bool) -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            f"Name={name}\n"
            f"Comment={comment}\n"
            f"Exec={command}\n"
            f"Terminal={'true' if terminal else 'false'}\n"
            "Icon=disk-image-tool\n"
            "Categories=Utility;System;\n"
            "Keywords=Backup;Abbild;Datenträger;Sicherung;\n"
            f"{MARKER}\n")


def paths(base: Path) -> tuple[Path, Path, Path]:
    applications = base / "applications"
    return (applications / ENTRY_NAMES[0], applications / ENTRY_NAMES[1],
            base / "icons/hicolor/48x48/apps" / ICON_NAME)


def atomic_write(path: Path, content: bytes) -> None:
    """Fertigen Inhalt im selben Ordner anlegen und ohne Teil-Datei ersetzen."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".disk-image-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(base: Path) -> None:
    """Zwei Starter und das Icon installieren; fremde Dateien bewahren."""

    gui = ROOT / "disk-image-gui.py"
    cli = ROOT / "disk-image-cli-launcher.sh"
    icon_source = ROOT / "assets" / ICON_NAME
    for source in (gui, cli, icon_source):
        if not source.is_file():
            raise RuntimeError(f"Benötigte Datei fehlt: {source}")
    gui_entry, cli_entry, icon_target = paths(base)
    for target in (gui_entry, cli_entry):
        if target.is_symlink() or (target.exists() and MARKER not in target.read_text(encoding="utf-8")):
            raise RuntimeError(f"Vorhandener fremder Startmenü-Eintrag bleibt unverändert: {target}")
    if icon_target.is_symlink() or (icon_target.exists() and
                                   icon_target.read_bytes() != icon_source.read_bytes() and
                                   not gui_entry.exists() and not cli_entry.exists()):
        raise RuntimeError(f"Vorhandenes fremdes Icon bleibt unverändert: {icon_target}")
    commands = (
        "python3 " + quote_exec(gui),
        "bash " + quote_exec(cli),
    )
    atomic_write(icon_target, icon_source.read_bytes())
    atomic_write(gui_entry, entry("Datenträger sichern", "Grafische Datenträgersicherung",
                                  commands[0], False).encode("utf-8"))
    atomic_write(cli_entry, entry("Datenträger sichern (Terminal)",
                                  "Interaktive Kommandozeile und Parameterhilfe",
                                  commands[1], True).encode("utf-8"))


def remove(base: Path) -> None:
    """Nur die eigenen Startmenü-Einträge entfernen."""

    gui_entry, cli_entry, icon_target = paths(base)
    for target in (gui_entry, cli_entry):
        if target.is_symlink():
            raise RuntimeError(f"Symbolischer Link wird nicht verändert: {target}")
        if target.exists() and MARKER not in target.read_text(encoding="utf-8"):
            raise RuntimeError(f"Fremder Startmenü-Eintrag bleibt unverändert: {target}")
    for target in (gui_entry, cli_entry):
        target.unlink(missing_ok=True)
    source = ROOT / "assets" / ICON_NAME
    if icon_target.is_file() and not icon_target.is_symlink() and source.is_file() and icon_target.read_bytes() == source.read_bytes():
        icon_target.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Startmenü-Einträge für disk-image-tool verwalten")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="GUI und CLI im Anwendungsmenü eintragen")
    action.add_argument("--remove", action="store_true", help="eigene Einträge entfernen")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise RuntimeError("Die Startmenü-Einträge sind für Linux vorgesehen.")
        if os.geteuid() == 0:
            raise RuntimeError("Bitte als normaler Benutzer ausführen, nicht mit sudo.")
        (install if args.install else remove)(data_home())
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    print("Startmenü-Einträge eingerichtet." if args.install else "Startmenü-Einträge entfernt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

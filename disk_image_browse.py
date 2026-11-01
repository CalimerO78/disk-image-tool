#!/usr/bin/env python3
"""Einzelne Dateien aus einem Rohabbild mit libguestfs nur lesend kopieren.

guestfish bekommt ausdrücklich --ro und --format=raw. Es wird kein Host-Mount
angelegt und weder Abbild noch Zielgerät werden beschrieben.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


class BrowseError(RuntimeError):
    """Abbild, Dateipfad oder libguestfs-Aufruf ist ungeeignet."""


def image_args(image: Path) -> list[str]:
    if not image.is_absolute() or image.is_symlink() or not image.is_file() or image.suffix != ".img":
        raise BrowseError("Zum Durchsuchen ist eine vorhandene, unkomprimierte .img-Datei nötig.")
    if not shutil.which("guestfish"):
        raise BrowseError("guestfish fehlt. Debian/Ubuntu: sudo apt install libguestfs-tools")
    return ["guestfish", "--ro", "--format=raw", "--no-progress-bars", "-a", str(image)]


def guest_path(value: str) -> str:
    if "\x00" in value or not value.startswith("/") or ".." in PurePosixPath(value).parts:
        raise BrowseError("Der Pfad im Abbild muss absolut sein und darf kein .. enthalten.")
    return value


def filesystem(value: str) -> str:
    if not re.fullmatch(r"/dev/[A-Za-z0-9_./-]+", value) or ".." in value:
        raise BrowseError("Ungültiges Dateisystem im Abbild.")
    return value


def call(args: list[str], *, binary: bool = False):
    result = subprocess.run(args, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise BrowseError(result.stderr.decode("utf-8", errors="replace")[-800:].strip() or
                          "guestfish konnte das Abbild nicht lesen.")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")


def filesystems(image: Path) -> list[dict]:
    args = image_args(image)
    try:
        verify = runpy.run_path(str(Path(__file__).with_name("disk-image-restore.py")))
        verify["checksum"](image, ".img")
    except RuntimeError as exc:
        raise BrowseError(str(exc)) from exc
    output = call(args + ["run", ":", "list-filesystems"])
    found = []
    for line in output.splitlines():
        if ": " in line:
            device, fs = line.split(": ", 1)
            try:
                found.append({"device": filesystem(device), "format": fs.strip()})
            except BrowseError:
                continue
    if not found:
        raise BrowseError("Im Abbild wurde kein lesbares Dateisystem gefunden.")
    return found


def list_dir(image: Path, device: str, directory: str) -> list[str]:
    raw = call(image_args(image) + ["-m", filesystem(device), "--", "ls0",
                                    guest_path(directory), "-"], binary=True)
    names = [part.decode("utf-8", errors="surrogateescape")
             for part in raw.split(b"\x00") if part]
    if any(name in {".", ".."} or "/" in name for name in names):
        raise BrowseError("Das Dateisystem lieferte ungültige Dateinamen.")
    return sorted(names, key=str.casefold)


def copy_file(image: Path, device: str, remote: str, output: Path) -> None:
    """Nur eine Datei in einen neuen lokalen Pfad schreiben, nie überschreiben."""

    args = image_args(image) + ["-m", filesystem(device), "--", "download",
                                guest_path(remote), "-"]
    if not output.is_absolute() or not output.parent.is_dir() or output.exists() or output.is_symlink():
        raise BrowseError("Bitte eine neue Ausgabedatei in einem vorhandenen Ordner wählen.")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    process = None
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
            assert process.stdout is not None
            for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
                view = memoryview(block)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise BrowseError("Die Ausgabedatei konnte nicht weiter geschrieben werden.")
                    view = view[count:]
            if process.wait(timeout=120) != 0:
                errors.seek(0)
                raise BrowseError(errors.read(800).decode("utf-8", errors="replace").strip() or
                                  "Die Datei konnte nicht kopiert werden.")
            os.fsync(fd)
    except BaseException:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(fd)
        output.unlink(missing_ok=True)
        raise
    os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dateien aus einem .img nur lesend ansehen und kopieren")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--filesystems", action="store_true")
    action.add_argument("--list", action="store_true")
    action.add_argument("--copy-file", action="store_true")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--filesystem")
    parser.add_argument("--path", default="/")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise BrowseError("Der Dateibrowser ist nur unter Linux verfügbar.")
        if args.filesystems:
            print(json.dumps(filesystems(args.image), ensure_ascii=True))
        elif args.list:
            if not args.filesystem:
                raise BrowseError("Bitte ein Dateisystem wählen.")
            print(json.dumps(list_dir(args.image, args.filesystem, args.path), ensure_ascii=True))
        else:
            if not args.filesystem or args.output is None:
                raise BrowseError("Bitte Dateisystem und lokalen Zielpfad angeben.")
            copy_file(args.image, args.filesystem, args.path, args.output)
            print(f"Datei kopiert: {args.output}")
        return 0
    except (BrowseError, OSError, subprocess.SubprocessError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

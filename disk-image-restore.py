#!/usr/bin/env python3
"""Geprüfte Wiederherstellung und Entpackung von disk-image-tool-Abbildern.

Vor jedem Schreibzugriff werden gespeicherte Prüfsumme, dekodierter Inhalt,
Zieltyp, Zielgröße, Mounts und aktive Geräte-Abhängigkeiten geprüft. Das
Zurückschreiben überschreibt unwiderruflich das bestätigte Zielgerät.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


CHUNK = 4 * 1024 * 1024
FORMATS = (".img.zst.age", ".img.gz.age", ".img.age",
           ".img.zst", ".img.gz", ".img", ".fsa")


class RestoreError(RuntimeError):
    """Ein Sicherheitscheck oder eine Wiederherstellung ist fehlgeschlagen."""


def kind_of(image: Path) -> str:
    """Nur die vom Sicherungsskript erzeugten Endungen zulassen."""

    for suffix in FORMATS:
        if image.name.endswith(suffix):
            return suffix
    raise RestoreError("Unbekanntes Abbildformat. Erwartet wird .img, .img.gz, .img.zst, optional .age, oder .fsa.")


def companion(image: Path, suffix: str) -> Path:
    return image.with_name(image.name[:-len(suffix)] + ".sha256")


def regular_image(image: Path) -> None:
    if not image.is_absolute() or image.is_symlink() or not image.is_file():
        raise RestoreError("Bitte eine vorhandene, reguläre Abbilddatei mit absolutem Pfad wählen.")


def checksum(image: Path, suffix: str) -> str:
    """Die daneben liegende Prüfsumme gegen exakt diese Datei abgleichen."""

    checkfile = companion(image, suffix)
    if checkfile.is_symlink() or not checkfile.is_file():
        raise RestoreError(f"Zugehörige Prüfsummendatei fehlt: {checkfile}")
    lines = checkfile.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) != 1:
        raise RestoreError("Die Prüfsummendatei muss genau eine SHA256-Zeile enthalten.")
    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", lines[0])
    if not match or Path(match[2]).name != image.name:
        raise RestoreError("Die Prüfsummendatei gehört nicht zu diesem Abbild.")
    expected = match[1].lower()
    actual = hashlib.sha256()
    with image.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            actual.update(block)
    if actual.hexdigest() != expected:
        raise RestoreError("SHA256-Prüfung fehlgeschlagen. Abbild nicht verwenden.")
    return expected


class Decoded:
    """age, gzip und zstd ohne Shell zu einem Rohdatenstrom verbinden."""

    def __init__(self, image: Path, suffix: str, identity: Path | None):
        self.image = image
        self.suffix = suffix
        self.identity = identity
        self.source = None
        self.stream = None
        self.processes: list[tuple[subprocess.Popen, object]] = []

    def __enter__(self):
        if self.suffix.endswith(".age") and (self.identity is None or not self.identity.is_file()):
            raise RestoreError("Für dieses age-Abbild ist die private Identitätsdatei nötig.")
        self.source = self.image.open("rb")
        self.stream = self.source
        commands = []
        if self.suffix.endswith(".age"):
            commands.append(["age", "-d", "-i", str(self.identity)])
        if ".gz" in self.suffix:
            commands.append(["gzip", "-dc"])
        elif ".zst" in self.suffix:
            commands.append(["zstd", "-dc"])
        try:
            for command in commands:
                if not shutil.which(command[0]):
                    raise RestoreError(f"Programm fehlt: {command[0]}")
                errors = tempfile.TemporaryFile()
                process = subprocess.Popen(command, stdin=self.stream,
                                           stdout=subprocess.PIPE, stderr=errors)
                if self.stream is not self.source:
                    self.stream.close()
                self.processes.append((process, errors))
                self.stream = process.stdout
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def read(self, size: int = CHUNK) -> bytes:
        assert self.stream is not None
        return self.stream.read(size)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            for process, _ in self.processes:
                if process.poll() is None:
                    process.terminate()
        if self.stream is not None:
            self.stream.close()
        if self.source is not None and self.source is not self.stream:
            self.source.close()
        failure = None
        for process, errors in self.processes:
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                code = -1
            errors.seek(0)
            detail = errors.read(2048).decode("utf-8", errors="replace").strip()
            errors.close()
            if code != 0 and exc_type is None:
                failure = detail or "Entschlüsselung oder Entpackung fehlgeschlagen."
        if failure:
            raise RestoreError(failure)


def raw_measure(image: Path, suffix: str, identity: Path | None) -> tuple[int, str]:
    """Dekodierten Inhalt vor jedem Überschreiben vollständig lesen."""

    digest = hashlib.sha256()
    size = 0
    with Decoded(image, suffix, identity) as stream:
        for block in iter(stream.read, b""):
            size += len(block)
            digest.update(block)
    if size <= 0:
        raise RestoreError("Das entpackte Abbild ist leer.")
    return size, digest.hexdigest()


def fsarchiver_size(image: Path) -> int:
    """Nur ein Archiv mit genau einem Dateisystem und bekannter Größe zulassen."""

    if not shutil.which("fsarchiver"):
        raise RestoreError("fsarchiver ist nicht installiert.")
    result = subprocess.run(["fsarchiver", "archinfo", str(image)],
                            capture_output=True, text=True, timeout=120,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode != 0:
        raise RestoreError("fsarchiver kann das Archiv nicht lesen: " + result.stderr.strip()[-500:])
    count = re.search(r"Filesystems count:\s*(\d+)", result.stdout)
    sizes = re.findall(r"Original filesystem size:.*?\((\d+) bytes\)", result.stdout)
    ids = re.findall(r"Filesystem id in archive:\s*(\d+)", result.stdout)
    if not count or count[1] != "1" or ids != ["0"] or len(sizes) != 1:
        raise RestoreError("Nur FSArchiver-Archive mit genau einem Dateisystem (ID 0) sind unterstützt.")
    return int(sizes[0])


def preflight(image: Path, identity: Path | None = None) -> dict:
    regular_image(image)
    suffix = kind_of(image)
    stored_sha = checksum(image, suffix)
    if suffix == ".fsa":
        size = fsarchiver_size(image)
        raw_sha = None
        mode = "fsarchiver"
    else:
        size, raw_sha = raw_measure(image, suffix, identity)
        mode = "raw"
        mapfile = Path(str(image) + ".map")
        if mapfile.exists():
            if not shutil.which("ddrescuelog"):
                raise RestoreError("Die ddrescue-Map-Datei benötigt ddrescuelog zur Prüfung.")
            if subprocess.run(["ddrescuelog", "-D", str(mapfile)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                raise RestoreError("Die ddrescue-Map-Datei meldet unvollständige Bereiche. Wiederherstellung gesperrt.")
    return {"image": str(image), "mode": mode, "suffix": suffix,
            "raw_size": size, "stored_sha256": stored_sha,
            "raw_sha256": raw_sha}


def _tree_nodes(items: list, parent=None):
    for item in items:
        yield item, parent
        yield from _tree_nodes(item.get("children") or [], item)


def device_plan(target: Path, image: Path, mode: str, raw_size: int) -> dict:
    """Zielgerät gegen Mounts, Systemnutzung, Quelle und Mindestgröße prüfen."""

    if (not target.is_absolute() or target.is_symlink() or
            not re.fullmatch(r"/dev/[A-Za-z0-9_./-]+", str(target)) or
            ".." in target.parts):
        raise RestoreError("Bitte ein direktes Gerät unter /dev mit absolutem Pfad wählen.")
    info = os.stat(target)
    if not stat.S_ISBLK(info.st_mode):
        raise RestoreError("Das Ziel ist kein Blockgerät.")
    result = subprocess.run(
        ["lsblk", "--json", "--bytes", "--paths", "--output",
         "PATH,TYPE,SIZE,MODEL,MOUNTPOINTS,RO"],
        capture_output=True, text=True, check=True, timeout=20,
    )
    roots = json.loads(result.stdout)["blockdevices"]
    selected = None
    related_roots = []
    for root in roots:
        for item, _ in _tree_nodes([root]):
            if item.get("path") == str(target):
                selected = item
                related_roots.append(root)
                break
    if selected is None or not related_roots:
        raise RestoreError("Das Zielgerät ist in lsblk nicht eindeutig sichtbar.")
    expected_type = "part" if mode == "fsarchiver" else "disk"
    if selected.get("type") != expected_type:
        raise RestoreError("Für Rohabbilder ist eine ganze Platte nötig; FSArchiver benötigt eine Partition.")
    related = [item for root in related_roots for item, _ in _tree_nodes([root])]
    if any(item.get("ro") in (1, True, "1") for item in related):
        raise RestoreError("Der Datenträger oder eine Partition ist schreibgeschützt.")
    mounts = [m for item in related
              for m in (item.get("mountpoints") or []) if m]
    if mounts:
        raise RestoreError("Der Zieldatenträger wird noch verwendet/eingehängt: " + ", ".join(mounts))
    try:
        active_swaps = {line.split()[0] for line in Path("/proc/swaps").read_text().splitlines()[1:]
                        if line.split()}
    except OSError as exc:
        raise RestoreError("Aktive Swap-Geräte konnten nicht geprüft werden.") from exc
    swap_devices = set()
    for swap_path in active_swaps:
        try:
            swap_info = os.stat(swap_path)
            swap_devices.add(swap_info.st_rdev if stat.S_ISBLK(swap_info.st_mode)
                             else swap_info.st_dev)
        except OSError as exc:
            raise RestoreError("Ein aktiver Swap-Bereich konnte nicht zugeordnet werden.") from exc
    for item in related:
        path = item.get("path")
        if not path:
            continue
        try:
            node = os.stat(path)
            if node.st_rdev in swap_devices:
                raise RestoreError("Auf dem Zieldatenträger liegt ein aktiver Swap-Bereich.")
            if node.st_rdev == os.stat(image).st_dev:
                raise RestoreError("Das Abbild liegt auf dem Zieldatenträger. Zurückspielen wäre selbstzerstörend.")
            holders = Path(f"/sys/dev/block/{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}/holders")
            if holders.is_dir() and any(holders.iterdir()):
                raise RestoreError("Das Ziel hat aktive Geräte-Abhängigkeiten (z. B. LUKS/LVM).")
        except OSError:
            continue
    capacity = int(selected.get("size") or 0)
    if os.geteuid() == 0:
        blockdev = subprocess.run(["blockdev", "--getsize64", str(target)],
                                  capture_output=True, text=True, check=True, timeout=10)
        if int(blockdev.stdout.strip()) != capacity:
            raise RestoreError("lsblk und blockdev melden unterschiedliche Zielgrößen.")
    if capacity < raw_size:
        raise RestoreError(f"Ziel zu klein oder Größe widersprüchlich: benötigt {raw_size} Bytes, Ziel {capacity} Bytes.")
    return {"target": str(target), "target_size": capacity,
            "target_type": expected_type,
            "target_model": selected.get("model") or related_roots[0].get("model") or "unbekannt",
            "target_rdev": info.st_rdev}


def progress(done: int, total: int, phase: str) -> None:
    print("@@DISK_IMAGE_RESTORE_PROGRESS " +
          json.dumps({"phase": phase, "bytes": done, "total": total}), flush=True)


def write_stream(stream, output_fd: int, size: int, expected_hash: str) -> None:
    digest = hashlib.sha256()
    done = 0
    next_report = time.monotonic() + 1
    for block in iter(stream.read, b""):
        done += len(block)
        if done > size:
            raise RestoreError("Der Datenstrom ist größer als bei der Vorprüfung. Schreibvorgang gestoppt.")
        digest.update(block)
        view = memoryview(block)
        while view:
            written = os.write(output_fd, view)
            if written <= 0:
                raise RestoreError("Das Ziel nimmt keine weiteren Daten an.")
            view = view[written:]
        if time.monotonic() >= next_report:
            progress(done, size, "write")
            next_report = time.monotonic() + 1
    if done != size or digest.hexdigest() != expected_hash:
        raise RestoreError("Die entpackten Daten stimmen nicht mit der Vorprüfung überein.")
    os.fsync(output_fd)
    progress(done, size, "write")


def readback(target: Path, size: int, expected_hash: str, expected_rdev: int) -> None:
    digest = hashlib.sha256()
    done = 0
    next_report = time.monotonic() + 1
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    with os.fdopen(os.open(target, flags), "rb", buffering=0) as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISBLK(opened.st_mode) or opened.st_rdev != expected_rdev:
            raise RestoreError("Das Gerät wurde vor der Nachprüfung ausgetauscht.")
        while done < size:
            block = source.read(min(CHUNK, size - done))
            if not block:
                raise RestoreError("Das Ziel konnte nicht vollständig zurückgelesen werden.")
            digest.update(block)
            done += len(block)
            if time.monotonic() >= next_report:
                progress(done, size, "readback")
                next_report = time.monotonic() + 1
    if digest.hexdigest() != expected_hash:
        raise RestoreError("NACHPRÜFUNG FEHLGESCHLAGEN: Ziel stimmt nicht mit dem Abbild überein.")
    progress(done, size, "readback")


def check_extract_target(plan: dict, output: Path) -> Path:
    """Ziel und freie Kapazität ohne Änderung am Dateisystem vorprüfen."""

    if plan["mode"] != "raw":
        raise RestoreError("FSArchiver ist ein Dateisystemarchiv und kann nicht als .img entpackt werden.")
    if not output.is_absolute() or output.suffix != ".img" or not output.parent.is_dir():
        raise RestoreError("Bitte einen absoluten Pfad für eine neue .img-Datei wählen.")
    if output.exists() or output.is_symlink():
        raise RestoreError("Die Ausgabedatei existiert bereits und wird nicht überschrieben.")
    sidecar = companion(output, ".img")
    if sidecar.exists() or sidecar.is_symlink():
        raise RestoreError("Die zugehörige .sha256-Datei existiert bereits und wird nicht überschrieben.")
    if shutil.disk_usage(output.parent).free < plan["raw_size"]:
        raise RestoreError("Für das entpackte Abbild ist nicht genug freier Platz vorhanden.")
    return sidecar


def extract(plan: dict, identity: Path | None, output: Path) -> None:
    sidecar = check_extract_target(plan, output)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    check_fd = None
    try:
        with Decoded(Path(plan["image"]), plan["suffix"], identity) as stream:
            write_stream(stream, fd, plan["raw_size"], plan["raw_sha256"])
        check_fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        remaining = memoryview(f"{plan['raw_sha256']}  {output}\n".encode("utf-8"))
        while remaining:
            written = os.write(check_fd, remaining)
            if written <= 0:
                raise RestoreError("Die Prüfsummendatei konnte nicht geschrieben werden.")
            remaining = remaining[written:]
        os.fsync(check_fd)
    except BaseException:
        if check_fd is not None:
            os.close(check_fd)
            sidecar.unlink(missing_ok=True)
        os.close(fd)
        output.unlink(missing_ok=True)
        raise
    if check_fd is not None:
        os.close(check_fd)
    os.close(fd)
    print(f"Entpacktes Abbild: {output}\nPrüfsumme: {sidecar}", flush=True)


def restore(plan: dict, identity: Path | None, target: Path, confirm: str | None) -> None:
    if os.geteuid() != 0:
        raise RestoreError("Das Zurückspielen benötigt Administratorrechte (sudo oder pkexec).")
    first = device_plan(target, Path(plan["image"]), plan["mode"], plan["raw_size"])
    print(f"ACHTUNG: {target} ({first['target_model']}, {first['target_size']} Bytes) wird überschrieben.", flush=True)
    if confirm is None:
        if not sys.stdin.isatty():
            raise RestoreError("Ohne Terminal ist --confirm-target mit dem exakten Gerätepfad erforderlich.")
        confirm = input("Zur Bestätigung den exakten Gerätepfad eintippen: ")
    if confirm != str(target):
        raise RestoreError("Bestätigung stimmt nicht mit dem Zielgerät überein.")
    second = device_plan(target, Path(plan["image"]), plan["mode"], plan["raw_size"])
    if second["target_rdev"] != first["target_rdev"] or second["target_size"] != first["target_size"]:
        raise RestoreError("Das Zielgerät hat sich während der Prüfung geändert.")
    if plan["mode"] == "fsarchiver":
        result = subprocess.run(["fsarchiver", "restfs", plan["image"],
                                 f"id=0,dest={target}"], stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RestoreError("FSArchiver-Wiederherstellung fehlgeschlagen; Ziel kann unvollständig sein.")
        print("FSArchiver meldet Erfolg. Dateisystem und Dateien auf dem Ziel bitte zusätzlich prüfen.", flush=True)
        return
    flags = os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_EXCL
    fd = os.open(target, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISBLK(opened.st_mode) or opened.st_rdev != first["target_rdev"]:
            raise RestoreError("Das geöffnete Ziel stimmt nicht mit dem bestätigten Datenträger überein.")
        with Decoded(Path(plan["image"]), plan["suffix"], identity) as stream:
            write_stream(stream, fd, plan["raw_size"], plan["raw_sha256"])
        if hasattr(os, "posix_fadvise"):
            try:
                os.posix_fadvise(fd, 0, plan["raw_size"], os.POSIX_FADV_DONTNEED)
            except OSError:
                pass
    finally:
        os.close(fd)
    readback(target, plan["raw_size"], plan["raw_sha256"], first["target_rdev"])
    print("WIEDERHERSTELLUNG UND NACHPRÜFUNG ERFOLGREICH.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Abbild prüfen, entpacken oder sicher zurückspielen")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="nur Prüfplan erstellen")
    action.add_argument("--extract", action="store_true", help="Rohabbild als .img entpacken")
    action.add_argument("--restore", action="store_true", help="Zielgerät überschreiben")
    parser.add_argument("--image", type=Path, required=True, help="Sicherungsdatei")
    parser.add_argument("--identity", type=Path, help="private age-Identitätsdatei")
    parser.add_argument("--target", type=Path, help="Zielgerät für den Prüfplan oder das Zurückspielen")
    parser.add_argument("--output", type=Path, help="neue .img-Datei für --extract")
    parser.add_argument("--confirm-target", help="exakt eingetippter Zielgerätepfad")
    parser.add_argument("--json", action="store_true", help="Prüfplan als JSON ausgeben")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise RestoreError("Wiederherstellung ist nur unter Linux verfügbar.")
        if args.restore and args.target is None or args.extract and args.output is None:
            raise RestoreError("Bitte Zielgerät beziehungsweise Ausgabedatei angeben.")
        plan = preflight(args.image, args.identity)
        if args.target is not None:
            plan.update(device_plan(args.target, args.image, plan["mode"], plan["raw_size"]))
        if args.plan and args.output is not None:
            check_extract_target(plan, args.output)
            plan["output"] = str(args.output)
        if args.plan:
            print(json.dumps(plan, ensure_ascii=False) if args.json else
                  f"Abbild geprüft: {plan['mode']}, {plan['raw_size']} Bytes" +
                  (f"; Ziel {plan['target']} ({plan['target_size']} Bytes) geeignet" if args.target else ""))
        elif args.extract:
            extract(plan, args.identity, args.output)
        else:
            restore(plan, args.identity, args.target, args.confirm_target)
        return 0
    except (RestoreError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

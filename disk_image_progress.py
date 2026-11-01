"""Messwerte für ddrescue und fsarchiver aus laufenden Linux-Prozessen.

Die Anzeige trennt echte Werte von unbekannten Werten. Der dd-Datenstrom wird
im Bash-Skript direkt mit pv gemessen und hier nicht doppelt gezählt.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path


def rescued_percent(mapfile: Path, size: int) -> float | None:
    """Die als erfolgreich markierten Bereiche der ddrescue-Map summieren."""

    if size <= 0:
        return None
    try:
        lines = mapfile.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return None
    rescued = 0
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 3 and parts[2] == "+":
            try:
                rescued += int(parts[1], 0)
            except ValueError:
                continue
    return round(min(100.0, 100.0 * rescued / size), 2)


def archive_percent(logfile: Path) -> float | None:
    """Nur ausdrücklich gemeldete fsarchiver-Prozentwerte übernehmen."""

    try:
        with logfile.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = re.findall(r"\[(\d{1,3}(?:\.\d+)?)%\]", tail)
    if not matches:
        return None
    value = float(matches[-1])
    return value if 0 <= value <= 100 else None


def child_pids(parent: int) -> list[int]:
    """Direkte und indirekte Kinder des Sicherungs-Shellprozesses finden."""

    found: list[int] = []
    pending = [parent]
    while pending:
        pid = pending.pop()
        try:
            raw = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="ascii")
        except OSError:
            continue
        for token in raw.split():
            child = int(token)
            found.append(child)
            pending.append(child)
    return found


def worker_io(shell_pid: int, name: str) -> tuple[int, int, int] | None:
    """Logische gelesene und geschriebene Bytes des Werkzeugprozesses."""

    for pid in child_pids(shell_pid):
        try:
            command = Path(f"/proc/{pid}/comm").read_text(encoding="ascii").strip()
            if command != name:
                continue
            rows = Path(f"/proc/{pid}/io").read_text(encoding="ascii").splitlines()
            values = dict(row.split(":", 1) for row in rows if ":" in row)
            return pid, int(values["rchar"]), int(values["wchar"])
        except (OSError, KeyError, ValueError):
            continue
    return None


def monitor(shell_pid: int, meta: dict, stop: threading.Event,
            emit) -> None:
    """Einmal pro Sekunde Prozesserfassung und Prozentstand ausgeben."""

    engine = meta.get("engine")
    if engine not in {"ddrescue", "fsarchiver"}:
        return
    previous: tuple[int, int, int, float] | None = None
    name = "ddrescue" if engine == "ddrescue" else "fsarchiver"
    while not stop.wait(1):
        current = worker_io(shell_pid, name)
        now = time.monotonic()
        read_rate = write_rate = None
        if current and previous and current[0] == previous[0]:
            seconds = max(0.001, now - previous[3])
            read_rate = max(0, (current[1] - previous[1]) / seconds)
            write_rate = max(0, (current[2] - previous[2]) / seconds)
        previous = (*current, now) if current else None
        percent = (rescued_percent(Path(meta["mapfile"]), int(meta["size"]))
                   if engine == "ddrescue" else archive_percent(Path(meta["log"])))
        payload = {"kind": engine, "percent": percent,
                   "read_bps": read_rate, "write_bps": write_rate}
        emit("@@DISK_IMAGE_PROGRESS " + json.dumps(payload) + "\n")

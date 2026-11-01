#!/usr/bin/env python3
"""Privilegierter Vermittler zwischen GUI und unverändertem Bash-Skript.

Die GUI sendet bei einem Abbruch "cancel" über stdin. Nur dieser bereits
als root gestartete Prozess signalisiert dann die Bash-Prozessgruppe. Er führt
keine Shell-Zeichenfolge aus; alle Argumente bleiben einzelne argv-Elemente.
"""

from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import threading

from disk_image_progress import monitor


def main() -> int:
    if os.geteuid() != 0:
        print("FEHLER: Der GUI-Vermittler benötigt Administratorrechte.", flush=True)
        return 1
    args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    if len(args) < 2 or args[0] != "bash":
        print("FEHLER: Ungültiger Start des GUI-Vermittlers.", flush=True)
        return 1

    try:
        child = subprocess.Popen(args, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 errors="replace", bufsize=1,
                                 start_new_session=True)
    except OSError as exc:
        print(f"FEHLER: Sicherung konnte nicht gestartet werden: {exc}", flush=True)
        return 1

    output_lock = threading.Lock()
    monitor_stop = threading.Event()
    monitor_thread = None

    def emit(value: str) -> None:
        with output_lock:
            print(value, end="", flush=True)

    def listen_for_cancel() -> None:
        for line in sys.stdin:
            if line.strip() == "cancel" and child.poll() is None:
                emit("Abbruch angefordert …\n")
                try:
                    os.killpg(child.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                return

    threading.Thread(target=listen_for_cancel, daemon=True).start()
    assert child.stdout is not None
    for line in child.stdout:
        if line.startswith("@@DISK_IMAGE_META ") and monitor_thread is None:
            try:
                meta = json.loads(line.removeprefix("@@DISK_IMAGE_META "))
                monitor_thread = threading.Thread(target=monitor,
                    args=(child.pid, meta, monitor_stop, emit), daemon=True)
                monitor_thread.start()
            except (ValueError, KeyError, TypeError):
                pass
        emit(line)
    result = child.wait()
    monitor_stop.set()
    if monitor_thread is not None:
        monitor_thread.join(timeout=2)
    return result


if __name__ == "__main__":
    sys.exit(main())

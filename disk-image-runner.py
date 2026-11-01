#!/usr/bin/env python3
"""Privilegierter Vermittler zwischen GUI und unverändertem Bash-Skript.

Die GUI sendet bei einer Unterbrechung "cancel" über stdin. Nur dieser bereits
als root gestartete Prozess signalisiert dann die Bash-Prozessgruppe. Er führt
keine Shell-Zeichenfolge aus; alle Argumente bleiben einzelne argv-Elemente.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading


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

    def listen_for_cancel() -> None:
        for line in sys.stdin:
            if line.strip() == "cancel" and child.poll() is None:
                print("Unterbrechung angefordert …", flush=True)
                try:
                    os.killpg(child.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                return

    threading.Thread(target=listen_for_cancel, daemon=True).start()
    assert child.stdout is not None
    for line in child.stdout:
        print(line, end="", flush=True)
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())

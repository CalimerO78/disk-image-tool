#!/usr/bin/env python3
"""Aktualisiert eine saubere Git-Installation ausschließlich per Fast-Forward.

Dieses Programm läuft ohne Administratorrechte. Es verändert nie eine Kopie mit
lokalen Änderungen, einen anderen Branch oder eine abweichende Git-Quelle.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent
TRUSTED_ORIGINS = {
    "https://github.com/CalimerO78/disk-image-tool.git",
    "https://github.com/CalimerO78/disk-image-tool",
    "git@github.com:CalimerO78/disk-image-tool.git",
}


class UpdateError(RuntimeError):
    """Eine notwendige Sicherheits- oder Git-Prüfung ist fehlgeschlagen."""


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Git ohne Shell starten und seine Fehlermeldungen knapp zurückgeben."""

    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Git konnte nicht ausgeführt werden oder antwortet nicht.") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise UpdateError(detail[-1] if detail else "Git meldet einen Fehler.")
    return result


def inspect_repo(repo: Path, allowed_origins: set[str] = TRUSTED_ORIGINS) -> str:
    """Nur die erwartete, unveränderte Hauptbranch-Installation zulassen."""

    if not (repo / ".git").exists():
        raise UpdateError("Diese Installation stammt nicht aus git clone. Bitte die GitHub-Anleitung verwenden.")
    top = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != repo.resolve():
        raise UpdateError("Das Update muss im Hauptordner dieser Installation laufen.")
    if git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() != "main":
        raise UpdateError("Automatische Updates sind nur auf dem Branch main möglich.")
    origin = git(repo, "remote", "get-url", "origin").stdout.strip()
    if origin not in allowed_origins:
        raise UpdateError("Die Git-Quelle weicht vom offiziellen Repository ab; Update abgebrochen.")
    if git(repo, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise UpdateError("Diese Kopie enthält lokale Änderungen oder zusätzliche Dateien. Bitte zuerst sichern oder prüfen.")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def update(repo: Path = REPO, *, apply: bool = False,
           allowed_origins: set[str] = TRUSTED_ORIGINS) -> dict:
    """Remote prüfen; bei apply nur einen vorwärts gerichteten Merge erlauben."""

    before = inspect_repo(repo, allowed_origins)
    git(repo, "fetch", "--no-tags", "origin", "main")
    remote = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    if before == remote:
        return {"state": "current", "before": before, "after": before}
    if git(repo, "merge-base", "--is-ancestor", before, remote,
           check=False).returncode != 0:
        raise UpdateError("Die lokale und die GitHub-Version sind auseinander gelaufen. Kein automatisches Update.")
    if not apply:
        return {"state": "available", "before": before, "after": remote}
    if inspect_repo(repo, allowed_origins) != before:
        raise UpdateError("Die lokale Kopie hat sich während der Prüfung geändert. Bitte erneut versuchen.")
    git(repo, "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", remote)
    after = git(repo, "rev-parse", "HEAD").stdout.strip()
    if after != remote:
        raise UpdateError("Git hat nicht die erwartete Version übernommen.")
    return {"state": "updated", "before": before, "after": after}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Updates für disk-image-tool prüfen oder installieren")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="GitHub-Version prüfen")
    action.add_argument("--apply", action="store_true", help="Update übernehmen, wenn sicher möglich")
    parser.add_argument("--json", action="store_true", help="Maschinenlesbare Ausgabe für die GUI")
    args = parser.parse_args(argv)
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise UpdateError("Updates bitte als normaler Benutzer starten, nicht mit sudo.")
        result = update(apply=args.apply)
        code = 0
    except UpdateError as exc:
        result = {"state": "error", "message": str(exc)}
        code = 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print({"current": "Bereits aktuell.", "available": "Ein Update ist verfügbar.",
               "updated": "Update installiert. Programm bitte neu starten.",
               "error": "Update nicht möglich: " + result.get("message", "")}[result["state"]])
    return code


if __name__ == "__main__":
    sys.exit(main())

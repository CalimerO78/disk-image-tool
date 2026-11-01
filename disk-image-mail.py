#!/usr/bin/env python3
"""SMTP-Benachrichtigungen für CLI und GUI, ohne Kennwort in Prozessargumenten.

Die Konfiguration liegt in einer nur für ihren Besitzer lesbaren JSON-Datei.
Der lokale mail-Versand des Bash-Skripts bleibt unabhängig davon verfügbar.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import stat
import sys
from email.message import EmailMessage
from pathlib import Path

ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\Z")


def validate_config(data: dict) -> dict:
    """Nur TLS-geschützte SMTP-Verbindungen und eindeutige Felder zulassen."""

    host = data.get("host", "")
    port = data.get("port")
    security = data.get("security")
    sender = data.get("sender", "")
    username = data.get("username", "")
    password = data.get("password", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("Ungültiger SMTP-Server.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Ungültiger SMTP-Port.")
    if security not in {"starttls", "ssl"}:
        raise ValueError("Bitte STARTTLS oder SSL/TLS wählen.")
    if not isinstance(sender, str) or not ADDRESS.fullmatch(sender):
        raise ValueError("Ungültige Absenderadresse.")
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("SMTP-Zugangsdaten sind ungültig.")
    if bool(username) != bool(password):
        raise ValueError("Für die Anmeldung werden Benutzername und Passwort benötigt.")
    return {"host": host, "port": port, "security": security,
            "sender": sender, "username": username, "password": password}


def load_config(path: Path) -> dict:
    """Dateirechte vor dem Lesen des geheimen SMTP-Passworts prüfen."""

    if not path.is_absolute() or path.is_symlink():
        raise ValueError("SMTP-Konfiguration muss eine absolute, reguläre Datei sein.")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("SMTP-Konfiguration muss nur für ihren Besitzer lesbar sein (chmod 600).")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("SMTP-Konfiguration hat ein ungültiges Format.")
    return validate_config(data)


def send(config: dict, recipient: str, subject: str, body: str) -> None:
    if not ADDRESS.fullmatch(recipient):
        raise ValueError("Ungültige Empfängeradresse.")
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if config["security"] == "ssl":
        connection = smtplib.SMTP_SSL(config["host"], config["port"],
                                       timeout=20, context=context)
    else:
        connection = smtplib.SMTP(config["host"], config["port"], timeout=20)
    with connection as smtp:
        if config["security"] == "starttls":
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        if config["username"]:
            smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMTP-Benachrichtigung für Datenträgersicherung")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--to")
    parser.add_argument("--status")
    parser.add_argument("--source")
    parser.add_argument("--image")
    parser.add_argument("--log")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.check:
            print("SMTP-Konfiguration und Dateirechte sind gültig.")
            return 0
        if not args.to:
            parser.error("--to fehlt")
        if args.test:
            send(config, args.to, "Datenträgersicherung: Testmail",
                 "Dies ist eine Testmail der Datenträgersicherung.\n")
        else:
            send(config, args.to, f"Datenträgersicherung: Status {args.status}",
                 f"Sicherung von {args.source} beendet. Status: {args.status}\n"
                 f"Abbild: {args.image}\nProtokoll: {args.log}\n")
        print("Nachricht an SMTP-Server übergeben; Zustellung bitte im Posteingang prüfen.")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, smtplib.SMTPException) as exc:
        # Keine Konfigurationsdaten oder Kennwörter in der Fehlermeldung ausgeben.
        print(f"SMTP-Versand fehlgeschlagen: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

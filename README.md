# Datenträger sichern mit `disk-image.sh`

**Öffentliche Vorabversion:** Die Bash-Syntax und die Hilfeausgabe wurden geprüft. Ein vollständiger Sicherungs- und Wiederherstellungslauf auf Linux-Hardware ist noch nicht verifiziert. Vor produktiver Nutzung mit einem entbehrlichen Testdatenträger erproben.

Ausführliche Erklärungen, Parameter und Fehlerbehebung stehen im [deutschen Handbuch](disk-image-Handbuch.md).

Das Skript läuft unter Linux mit Root-Rechten. Es bietet drei Sicherungsarten:

| Modus | Quelle | Ergebnis | Geeignet für |
|---|---|---|---|
| `dd` | ganzer Datenträger | `.img`, optional komprimiert und mit `age` verschlüsselt | vollständiges 1:1-Abbild |
| `ddrescue` | ganzer Datenträger | `.img` plus `.img.map` | beschädigte Datenträger und fortsetzbare Rettung |
| `fsarchiver` | einzelne Partition | `.fsa` | platzsparende Sicherung der Dateisysteminhalte |

`fsarchiver` ist kein Ersatz für ein vollständiges Abbild: Partitionstabelle und nicht belegte Blöcke gehören nicht zum Archiv. Welche Dateisysteme unterstützt werden, hängt von der installierten Version und den Dateisystemwerkzeugen ab.

## Grafische Oberfläche (Linux)

Die grafische Oberfläche ist ein zusätzlicher Startweg. Die bisherige interaktive
Kommandozeile und alle Parameter bleiben verfügbar. Die GUI übergibt die gewählten
Einstellungen an `disk-image.sh`; beide Wege erzeugen dasselbe Sicherungsformat.

### Von GitHub installieren und starten (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install git python3 python3-tk pkexec
git clone https://github.com/CalimerO78/disk-image-tool.git
cd disk-image-tool
python3 disk-image-gui.py
```

Die grafische Administrator-Abfrage benötigt einen laufenden Polkit-Agenten in
der Desktop-Sitzung. Die Sicherung selbst fragt nach der Bestätigung nach
Administratorrechten. Für die CLI im selben Ordner:

```bash
sudo bash disk-image.sh
```

Die benötigten Sicherungsprogramme bietet das Skript beim Start zur Installation
an; in der GUI ist die automatische Installation standardmäßig aktiviert.

Benötigt werden Python 3 mit Tkinter, `lsblk` und für eine grafische
Administrator-Anmeldung `pkexec` mit einem laufenden Polkit-Agenten. Unter
Debian/Ubuntu lässt sich Tkinter bei Bedarf mit `sudo apt install python3-tk`
installieren. Danach im Ordner mit `disk-image.sh`, `disk-image-gui.py`,
`disk-image-runner.py` und `disk-image-mail.py` starten:

```bash
python3 disk-image-gui.py
```

In der Oberfläche zuerst Sicherungsart, Quelle und vorhandenen Zielordner wählen.
Unter **Weitere Optionen** sind Kompression, Fortsetzung mit ddrescue,
age-Verschlüsselung mit einer Erklärung der Schlüssel, SSH-Kopie, SMART,
Verifikation und die automatische Paketinstallation erreichbar. Der eigene
Reiter **E-Mail** bietet das lokale Mailprogramm oder eine direkte SMTP-Verbindung
mit STARTTLS beziehungsweise SSL/TLS, Empfängeradresse und Testmail. Im vierten Reiter die Angaben prüfen
und den Start bestätigen. Die Administrator-Abfrage erscheint erst beim Start.
Die Quelle wird kurz vor dem eigentlichen Lesen im Skript erneut geprüft.

Die GUI zeigt den laufenden Status und die Skriptausgabe. Einige Werkzeuge
schreiben ausführliche Fortschrittsdaten nur in die Protokolldatei; deshalb
läuft währenddessen eine Aktivitätsanzeige. **Unterbrechen** erhält ein bereits
angefangenes Abbild zur Diagnose. Im letzten Reiter können die sichtbaren
Meldungen mit **Kopieren** in die Zwischenablage oder mit **Als Datei speichern …**
als Textdatei gesichert werden. Ein vollständiger Linux-Hardwaretest der GUI
steht noch aus.

## Interaktiver Start

```bash
sudo bash disk-image.sh
```

Fehlende Pakete werden angezeigt. Auf Debian/Ubuntu fragt das Skript, ob es sie installieren soll; **Enter bedeutet Ja**. Falls `pv` manuell installiert werden soll:

```bash
sudo apt install pv
```

Danach folgen die Auswahl der Sicherungsart und Quelle, eine optionale SMART-Anzeige, die Wahl des Zielordners und bei eingehängter Quelle die Auswahl **Aushängen / Eingehängt lassen / Abbrechen**. Ein Aushängen erfolgt ohne `--force` und ohne `--lazy`. Das Skript bricht ab, wenn der Einhängepunkt das Ziel, das aktuelle Arbeitsverzeichnis oder das Systemwurzelverzeichnis enthält.

## Beschädigtes Laufwerk mit `ddrescue`

```bash
sudo bash disk-image.sh --non-interactive --engine ddrescue \
  --source /dev/sdb --dest /backup --mounted abort --verify
```

`ddrescue` liest zunächst gut erreichbare Bereiche und versucht problematische Bereiche danach bis zu dreimal. Die Map-Datei bleibt beim Abbruch erhalten. Für einen weiteren Lauf dieselbe Quelle, denselben Zielordner und das vorhandene Abbild angeben:

```bash
sudo bash disk-image.sh --non-interactive --engine ddrescue \
  --source /dev/sdb --dest /backup --mounted abort \
  --resume-image /backup/mein-abbild.img --verify
```

Den Pfad im Beispiel durch den tatsächlich ausgegebenen Abbildpfad ersetzen. **Abbild und `.map` gehören zusammen.** Der Exit-Code `3` bedeutet, dass die Map-Datei noch nicht vollständig gerettete Bereiche enthält.

## Nur die belegten Daten einer Partition

```bash
sudo bash disk-image.sh --non-interactive --engine fsarchiver \
  --source /dev/sdb1 --dest /backup --mounted abort --verify
```

Das Skript prüft mit `fsarchiver archinfo`, ob die Archiv-Metadaten lesbar sind. Eine vollständige Wiederherstellung wird damit nicht nachgewiesen.

## Verschlüsselung mit `age`

Verschlüsselung ist im `dd`-Modus verfügbar. `--age-recipient` nimmt den öffentlichen Empfängerschlüssel an. Die private Identitätsdatei wird nur zur Inhaltsprüfung gebraucht und darf nicht im Zielordner des Backups liegen.

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort \
  --compression zstd --age-recipient 'age1...' \
  --age-identity /root/age-key.txt --verify
```

Ohne Identitätsdatei ist `--no-verify` erforderlich. Die `.sha256`-Datei prüft dann die gespeicherte verschlüsselte Datei, aber nicht den entschlüsselten Inhalt. `ddrescue` nutzt eine direkt beschreibbare Datei für seine Map-gesteuerte Fortsetzung und kombiniert diesen Modus deshalb nicht mit Verschlüsselung.

## Zusätzliche Kopie über SSH

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort \
  --ssh-host backup@example.org --ssh-path /srv/backups/disk.img --verify
```

Zuerst entsteht ein **lokales** Abbild; danach wird es über SSH übertragen. Auf dem Ziel muss der Ordner bereits existieren. Eine vorhandene Datei wird nicht überschrieben. Nach dem Transfer vergleicht das Skript die lokalen und entfernten SHA256-Werte. Bei `ddrescue` wird die Map-Datei unter demselben Zielpfad mit `.map` übertragen.

## Benachrichtigung und Cron

`--notify desktop` nutzt `notify-send`; bei Ausführung mit `sudo` kann die Desktop-Sitzung fehlen. `--notify mail --mail-to name@example.org` nutzt das lokal eingerichtete Mailprogramm. Mit `--mail-config /absoluter/pfad/mail.json` nutzt derselbe CLI-Aufruf stattdessen die in der GUI gespeicherte SMTP-Verbindung. Benachrichtigungen enthalten den Status und lokale Pfade, keine Schlüssel. Die [Schritt-für-Schritt-Anleitung](disk-image-Handbuch.md#15-e-mail-einrichten-schritt-f%C3%BCr-schritt) erklärt beide Versandwege.

Für Cron `--non-interactive` verwenden und `--engine`, `--source`, `--dest` und `--mounted` ausdrücklich angeben. Ohne `--auto-install` installiert ein Cron-Lauf keine Pakete. Für SSH muss die Anmeldung ohne Rückfrage funktionieren. Ein Beispiel:

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort --smart skip \
  --verify --notify mail --mail-to name@example.org
```

`--mounted abort` verhindert einen unbeaufsichtigten Lauf mit eingehängter Quelle. `--mounted unmount` hängt die erkannten Dateisysteme ausdrücklich aus; `--mounted allow` gestattet bei `dd` und `ddrescue` ein möglicherweise inkonsistentes Abbild.

## Ergebnisse und Grenzen

Zu jeder Sicherung gehören ein Protokoll und eine `.sha256`-Datei. Für `ddrescue` kommt die Map-Datei hinzu. Unvollständige Dateien bleiben zur Diagnose erhalten. Die `.sha256`-Datei lässt sich mit `sha256sum -c DATEI.sha256` prüfen.

Exit-Codes: `0` abgeschlossen; `1` Fehler; `2` Verifikation fehlgeschlagen; `3` abgeschlossen mit Lesefehlern oder nicht vollständig geretteten Bereichen; `130`/`143` unterbrochen. Ein erfolgreich lesbares Archiv ersetzt keinen Test der Wiederherstellung auf einem geeigneten Zielsystem.

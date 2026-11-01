# Handbuch für disk-image.sh

**Stand: 1. November 2026**

`disk-image.sh` sichert Linux-Datenträger oder einzelne Partitionen in Dateien. Es führt im Terminal durch die nötigen Entscheidungen und kann mit festen Parametern ohne Rückfragen laufen. Dieses Handbuch erklärt, welcher Modus für welchen Zweck passt, wie die erzeugten Dateien geprüft werden und was bei einer Unterbrechung zu tun ist.

## 1. Die passende Sicherungsart wählen

| Modus | Quelle | Ergebnis | Wofür er gedacht ist |
|---|---|---|---|
| `dd` | ganzer Datenträger | Rohabbild `.img`, optional `.gz`, `.zst` und `.age` | vollständige Kopie aller Sektoren einschließlich Partitionstabelle und freiem Platz |
| `ddrescue` | ganzer Datenträger | Rohabbild `.img` und Map-Datei `.img.map` | Rettung beschädigter Datenträger mit fortsetzbaren Leseversuchen |
| `fsarchiver` | einzelne Partition | Dateisystemarchiv `.fsa` | platzsparende Sicherung belegter Daten eines unterstützten Dateisystems |

`dd` und `ddrescue` lesen den ganzen ausgewählten Datenträger. `fsarchiver` archiviert ein Dateisystem einer Partition; die Partitionstabelle und andere Partitionen sind darin nicht enthalten. Für ein vollständiges Laufwerksabbild daher `dd` oder bei Lesefehlern `ddrescue` wählen. [GNU ddrescue](https://www.gnu.org/software/ddrescue/manual/ddrescue_manual.html) und [FSArchiver](https://www.fsarchiver.org/quickstart/) beschreiben die jeweiligen Formate und Verfahren.

## 2. Voraussetzungen

- Linux mit Bash und Root-Rechten. Starten mit `sudo bash disk-image.sh`.
- Ein vorhandener Zielordner auf einem **anderen** Datenträger. Der freie Platz muss zum gewählten Modus passen. Für ein unkomprimiertes Rohabbild mindestens die Größe der Quelle vorsehen.
- Bei Debian/Ubuntu kann das Skript fehlende Pakete mit `apt-get install` nach Rückfrage installieren. **Enter bedeutet Ja.** Es installiert nur die für die gewählten Funktionen benötigten Pakete und optional `smartmontools` für die SMART-Anzeige. Es führt kein `apt-get update` aus.
- Auf anderen Distributionen die gemeldeten Programme mit dem dortigen Paketmanager installieren.

Fehlt `pv`, lautet der manuelle Installationsbefehl unter Debian/Ubuntu:

```bash
sudo apt install pv
```

Zusätzliche Werkzeuge hängen vom gewählten Modus ab: `gddrescue` für `ddrescue`, `fsarchiver` für belegte Dateisystemdaten, `age` für Verschlüsselung, `openssh-client` für eine SSH-Kopie, `libnotify-bin` für Desktop-Meldungen und `bsd-mailx` für Mail. Ein installiertes Mailprogramm allein garantiert noch keinen Mailversand; dafür muss die lokale Mailzustellung eingerichtet sein.

## 3. Sicherung mit Dialogen starten

```bash
sudo bash disk-image.sh
```

Das Skript fragt in dieser Reihenfolge nach:

1. **Sicherungsart:** `dd`, `ddrescue` oder `fsarchiver`.
2. **Zusatzfunktionen:** bei `dd` optional `age`, außerdem SSH-Kopie und Benachrichtigung.
3. **Fehlenden Programmen:** automatische Installation auf Debian/Ubuntu, Standard **Ja**.
4. **Quelle:** bei `dd` und `ddrescue` ein ganzer Datenträger, bei `fsarchiver` eine Partition. Die Größe und die erkannten Dateisysteme werden angezeigt.
5. **SMART-Kurzstatus:** anzeigen oder überspringen. Die Anzeige fragt den Gesundheitsstatus mit `smartctl -H` ab.
6. **Zielordner:** ein vorhandener, beschreibbarer Ordner auf einem anderen Datenträger.
7. **Eingehängter Quelle:** aushängen, eingehängt fortfahren oder abbrechen. Standard ist **Abbrechen**.
8. **Weitere Einstellungen:** bei `dd` Kompression und Verhalten bei Lesefehlern.
9. **Zusammenfassung:** erst nach Bestätigung beginnt das Schreiben.
10. **Verifikation:** die erzeugte Datei wird auf Wunsch erneut gelesen; Standard ist **Ja**.

Während des Schreibens zeigt `pv` den Fortschritt. Ein Druck auf **Strg+C** beendet den Lauf. Eine begonnene Abbilddatei bleibt erhalten und darf nicht als vollständige Sicherung behandelt werden.

### Quelle sicher aushängen

Bei der Auswahl **Aushängen** listet das Skript die betroffenen Einhängepunkte auf. Untergeordnete Einhängepunkte werden zuerst mit normalem `umount` ausgehängt. Es nutzt weder erzwungenes noch verzögertes Aushängen. Das Systemwurzelverzeichnis `/`, der Zielordner und das aktuelle Arbeitsverzeichnis sind geschützt. Vor jedem `umount` wird geprüft, ob der Einhängepunkt noch zur gewählten Quelle gehört.

Falls `umount` wegen einer offenen Datei oder eines laufenden Programms fehlschlägt, beendet sich das Skript. Bereits erfolgreich ausgehängte Dateisysteme werden dabei **nicht automatisch wieder eingehängt**. Das betroffene Programm schließen, den Zustand mit `findmnt` oder `lsblk` prüfen und den Lauf erneut starten.

**Eingehängt fortfahren** ist nur für `dd` und `ddrescue` vorgesehen. Wenn sich Daten während des Lesens ändern, kann das Abbild inkonsistent sein. `fsarchiver` verlangt in diesem Skript eine ausgehängte Partition.

### SMART-Kurzstatus verstehen

SMART kann einen gemeldeten Gerätefehler sichtbar machen, belegt aber keine Fehlerfreiheit. Manche USB-Gehäuse reichen die SMART-Daten nicht durch. Ein nicht verfügbarer Status ist deshalb kein positives Prüfergebnis. Meldet SMART einen aktuellen oder früheren Fehler, fragt der interaktive Lauf vor dem Fortfahren erneut; ein unbeaufsichtigter Lauf mit `--smart show` bricht ab. [smartctl dokumentiert `-H` als Anzeige des Gesundheitsstatus.](https://www.smartmontools.org/static/doxygen/smartctl_8cpp_source.html)

## 4. Ergebnisse und Prüfsummen

Im Zielordner entstehen:

| Datei | Inhalt |
|---|---|
| `NAME.img`, `NAME.img.gz`, `NAME.img.zst` oder `NAME.img[.gz|.zst].age` | Rohabbild des ganzen Datenträgers |
| `NAME.fsa` | Archiv einer Partition mit `fsarchiver` |
| `NAME.img.map` | nur bei `ddrescue`: Fortschritt und problematische Bereiche |
| `NAME.log` | Quelle, Ablauf, SMART-Ausgabe bei gewählter Anzeige und Fehlermeldungen |
| `NAME.sha256` | SHA256-Wert der **gespeicherten Datei** |

Die Dateiprüfsumme lässt sich später prüfen:

```bash
sha256sum -c /backup/NAME.sha256
```

Die `dd`-Inhaltsprüfung vergleicht den Hash des während der Sicherung gelesenen Rohdatenstroms mit dem erneut aus der gespeicherten Datei gelesenen Inhalt. Bei Kompression wird vorher dekomprimiert, bei `age` vorher entschlüsselt. Das bestätigt die Übereinstimmung mit den damals gelesenen Daten; bei einer gleichzeitig veränderten, eingehängten Quelle beweist es keine konsistente Momentaufnahme.

Bei `ddrescue` wertet das Skript die Map-Datei aus. Es liest eine defekte Quelle zur Prüfung nicht erneut. Exit-Code `3` bedeutet, dass die Map noch nicht vollständig gerettete Bereiche ausweist. Bei `fsarchiver` prüft `archinfo` die Lesbarkeit der Archiv-Metadaten; ein echter Wiederherstellungstest bleibt zusätzlich nötig. Bei einer SSH-Kopie vergleicht das Skript die lokale mit der entfernten SHA256-Prüfsumme.

## 5. Beschädigten Datenträger retten

`ddrescue` versucht zuerst, gut lesbare Bereiche ohne wiederholte Fehlversuche zu kopieren. Danach folgen bis zu drei Wiederholungen für problematische Bereiche. Das Abbild und die Map-Datei müssen gemeinsam aufbewahrt werden. Die Map ermöglicht einen späteren Lauf ohne vollständigen Neustart.

```bash
sudo bash disk-image.sh --non-interactive --engine ddrescue \
  --source /dev/sdb --dest /backup --mounted abort --verify
```

Zum Fortsetzen den **tatsächlich erzeugten** Abbildpfad verwenden. Die Datei mit demselben Pfad und angehängtem `.map` muss vorhanden sein:

```bash
sudo bash disk-image.sh --non-interactive --engine ddrescue \
  --source /dev/sdb --dest /backup --mounted abort \
  --resume-image /backup/MEIN_ABBILD.img --verify
```

Das Skript überschreibt beim Fortsetzen keine anderen Dateien. `ddrescue` ergänzt anhand seiner Map die fehlenden Bereiche des angegebenen Abbilds. Vor dem Fortsetzen prüfen, ob Quelle, Abbild und Map tatsächlich zum selben Rettungsvorgang gehören. Für `ddrescue` sind Kompression und `age` im Skript deaktiviert, weil eine direkt beschreibbare Datei für die Fortsetzung erforderlich ist. [Die GNU-Anleitung empfiehlt ausdrücklich eine Map-Datei für fortsetzbare Rettung.](https://www.gnu.org/software/ddrescue/manual/ddrescue_manual.html)

## 6. Belegte Dateisystemdaten sichern

`fsarchiver` eignet sich, wenn nur der Inhalt einer einzelnen Partition gebraucht wird und das Dateisystem unterstützt wird:

```bash
sudo bash disk-image.sh --non-interactive --engine fsarchiver \
  --source /dev/sdb1 --dest /backup --mounted abort --verify
```

Das `.fsa`-Archiv hat ein eigenes Format und ist kein Rohabbild. `fsarchiver` komprimiert die Daten selbst. Das Skript bietet in diesem Modus keine zusätzliche `age`-Verschlüsselung an. Die unterstützten Dateisysteme und für deren Sicherung benötigten Werkzeuge hängen von der Installation ab; insbesondere nennt die [FSArchiver-Anleitung](https://www.fsarchiver.org/quickstart/) zusätzliche Dateisystemwerkzeuge als mögliche Voraussetzung.

## 7. Ein dd-Abbild verschlüsseln

Für `age` wird ein **öffentlicher Empfängerschlüssel** benötigt. Nur mit der passenden privaten Identitätsdatei lässt sich das Abbild entschlüsseln. Den privaten Schlüssel getrennt vom Abbild sicher aufbewahren; ein verlorener Schlüssel kann die Wiederherstellung unmöglich machen. [Die age-Dokumentation](https://github.com/FiloSottile/age/blob/main/README.md?plain=1) beschreibt Empfänger und Identitätsdateien.

Schlüssel für die GUI oder CLI vorbereiten:

1. Falls `age-keygen` fehlt, auf Debian/Ubuntu `sudo apt install age` ausführen.
2. `age-keygen -o mein-age-schluessel.txt` starten. Die Datei enthält den **privaten** Schlüssel; die Ausgabe `Public key: age1...` ist der **öffentliche** Schlüssel.
3. Die private Datei so aufbewahren, dass nur Berechtigte sie lesen können, und getrennt vom Abbild sichern. Den öffentlichen Schlüssel in der GUI ins obere age-Feld kopieren. Bei aktivierter Inhaltsprüfung die private Datei im unteren Feld auswählen.
4. Eine Testverschlüsselung durchführen und später mit `age -d -i mein-age-schluessel.txt DATEI.age > entschluesselte-datei` prüfen, ob sich eine Datei wieder öffnen lässt. Dafür genügend freien Platz einplanen.

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort \
  --compression zstd --age-recipient 'HIER_OEFFENTLICHEN_AGE_SCHLUESSEL' \
  --age-identity /root/age-key.txt --verify
```

`--age-recipient` erhält **keinen privaten Schlüssel**. `--age-identity` zeigt auf eine lesbare Datei mit dem passenden privaten Schlüssel. Ohne diese Datei ist nur `--no-verify` möglich: Die `.sha256`-Datei prüft dann die gespeicherte verschlüsselte Datei, nicht den entschlüsselten Inhalt. `age` wird nur im `dd`-Modus angeboten.

## 8. Eine Kopie über SSH schreiben

Mit den beiden SSH-Parametern kopiert das Skript **nach der lokalen Sicherung** das fertige Abbild auf einen zweiten Rechner. Es ist kein reiner Netzwerk-Stream; lokaler Speicherplatz bleibt erforderlich.

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort --verify \
  --ssh-host backup@example.org --ssh-path /srv/backups/disk.img
```

Der Zielordner auf dem anderen Rechner muss bereits existieren. Der angegebene Dateipfad muss absolut sein und darf nur Buchstaben, Ziffern, `_`, `-`, `.`, und `/` enthalten. Eine dort vorhandene Datei wird nicht ersetzt. Bei `ddrescue` wird zusätzlich die Map-Datei unter `SSH-PFAD.map` übertragen. Ein fehlgeschlagener Transfer lässt die lokale Sicherung stehen; eine teilweise entfernte Datei muss vor einem neuen Versuch separat geprüft werden.

Ein unverschlüsseltes Rohabbild liegt auf dem SSH-Ziel unverschlüsselt vor. Soll es auch dort verschlüsselt gespeichert werden, im `dd`-Modus `--age-recipient` verwenden. Bei `ddrescue` ist diese Kombination im Skript nicht verfügbar.

## 9. Benachrichtigungen

Mit `--notify desktop` versucht das Skript am Ende `notify-send` aufzurufen. Da das Skript mit `sudo` läuft, kann die grafische Sitzung für Root unerreichbar sein. Eine Meldung im Terminal weist dann auf den fehlgeschlagenen Benachrichtigungsversuch hin.

Mit `--notify mail --mail-to name@example.org` sendet das lokale Mailprogramm den Exit-Code sowie Quell-, Abbild- und Protokollpfad. Dafür ist eine funktionierende Mailzustellung nötig; `bsd-mailx` allein richtet keinen externen SMTP-Dienst ein. Die Benachrichtigung wird auch bei einem Fehler während des Sicherungslaufs versucht. Der Erfolg der Benachrichtigung ändert den Exit-Code der Sicherung nicht.

Mit `--mail-config /absoluter/pfad/mail.json` wird stattdessen direkt über SMTP mit STARTTLS oder SSL/TLS versendet. Die Konfiguration kann im GUI-Reiter **E-Mail** gespeichert werden. Sie enthält das SMTP-Passwort in einer lokalen Datei mit Zugriffsrechten `600`; diese Datei gehört weder in das Sicherungsziel noch in ein öffentliches Repository. Das Passwort erscheint nicht in den Kommandozeilenparametern.

## 10. Ohne Rückfragen und mit Cron arbeiten

`--non-interactive` verlangt `--engine`, `--source`, `--dest` und `--mounted`. Die Quelle wird dabei als Gerätedatei angegeben; das Skript wählt niemals automatisch einen Datenträger aus. Standardwerte sind `dd`-Kompression `none`, Lesefehlerverhalten `continue`, SMART `skip` und Verifikation `yes`.

```bash
sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort \
  --compression zstd --verify --notify mail --mail-to name@example.org
```

`--mounted abort` beendet den Lauf, falls die Quelle eingehängt ist. `--mounted unmount` autorisiert das Aushängen ohne Rückfrage; dies nur wählen, wenn der geplante Lauf andere Programme nicht unterbricht. `--mounted allow` ist für `dd` und `ddrescue` möglich, erhöht aber das Risiko eines inkonsistenten Abbilds.

Im unbeaufsichtigten Modus werden fehlende Pakete **nicht automatisch** installiert. `--auto-install` erlaubt die Installation per `apt-get` ohne weitere Rückfrage. Für Cron sind vorinstallierte Pakete meist einfacher zu kontrollieren. Bei SSH verwendet das Skript im unbeaufsichtigten Modus eine Anmeldung ohne Passwortfrage; Schlüssel und Zugriffsrechte müssen vorher eingerichtet sein.

Eine beispielhafte Zeile für die Root-Crontab, täglich um 02:00 Uhr, lautet:

```cron
0 2 * * * /usr/bin/bash /opt/tools/disk-image.sh --non-interactive --engine dd --source /dev/sdb --dest /backup --mounted abort --compression zstd --verify >> /var/log/disk-image-cron.log 2>&1
```

Die Pfade und das Gerät sind Beispiele und müssen vor dem Eintragen zum eigenen System passen. Cron liefert keine interaktive Bestätigung; die Wahl des Quellgeräts und des Zielordners deshalb besonders sorgfältig prüfen.

## 11. Parameterübersicht

| Parameter | Bedeutung |
|---|---|
| `--engine dd\|ddrescue\|fsarchiver` | Sicherungsart |
| `--source /dev/...` | Quellgerät oder Partition |
| `--dest ORDNER` | vorhandener lokaler Zielordner |
| `--compression none\|gzip\|zstd` | nur `dd`; bei `gzip` kann `pigz` parallel komprimieren |
| `--on-read-error continue\|abort` | nur `dd`; Standard `continue` mit Nullauffüllung fehlerhafter Bereiche |
| `--mounted unmount\|allow\|abort` | Umgang mit einer eingehängten Quelle |
| `--resume-image DATEI` | vorhandenes `ddrescue`-Abbild mit `DATEI.map` fortsetzen |
| `--age-recipient SCHLÜSSEL` | öffentlichen `age`-Empfänger für `dd` angeben |
| `--age-identity DATEI` | privaten Schlüssel zur Inhaltsprüfung angeben |
| `--ssh-host BENUTZER@HOST` | SSH-Zielrechner |
| `--ssh-path /pfad/datei` | exakte Zieldatei auf dem SSH-Rechner |
| `--notify none\|desktop\|mail` | Benachrichtigungsart |
| `--mail-to ADRESSE` | Empfänger bei `--notify mail` |
| `--smart show\|skip` | SMART-Kurzstatus anzeigen oder auslassen |
| `--verify`, `--no-verify` | zusätzliche Prüfung durchführen oder überspringen |
| `--non-interactive` | ohne Eingabedialoge arbeiten |
| `--auto-install` | fehlende Pakete auf Debian/Ubuntu ohne Rückfrage installieren |
| `--help` | Hilfe anzeigen, ohne eine Sicherung zu starten |

Bei interaktiver Nutzung werden nicht gesetzte Optionen abgefragt. Eine nicht unterstützte Kombination, etwa `ddrescue` mit `age`, führt vor dem Schreibbeginn zu einem Fehler.

## 12. Exit-Codes und typische Probleme

| Code | Bedeutung | Nächster Schritt |
|---|---|---|
| `0` | Lauf abgeschlossen | Prüfsumme und bei Bedarf Wiederherstellbarkeit kontrollieren |
| `1` | Auswahl-, Paket-, Lese-, Schreib- oder Übertragungsfehler | Meldung und `.log` prüfen; unvollständige Datei nicht als fertige Sicherung nutzen |
| `2` | zusätzliche Verifikation fehlgeschlagen | gespeicherte Datei und Zielmedium prüfen |
| `3` | `dd` meldete Lesefehler oder `ddrescue` hat noch offene Bereiche | `.log` beziehungsweise `.map` auswerten; `ddrescue` gegebenenfalls fortsetzen |
| `130` / `143` | Strg+C / TERM | erhaltene Teil-Datei und gegebenenfalls Map-Datei prüfen |

**`pv` fehlt:** Automatische Installation mit **Enter** bestätigen oder `sudo apt install pv` ausführen.

**Aushängen schlägt fehl:** Ein Programm verwendet das Dateisystem. Das Skript setzt die Sicherung nicht stillschweigend fort. Offene Programme schließen und die Mounts erneut prüfen.

**Zielordner liegt auf der Quelle:** Einen anderen Datenträger als Ziel wählen. Eine Sicherung auf dieselbe Platte wäre bei einem Defekt nicht belastbar und könnte den Lesevorgang stören.

**SMART liefert keinen Status:** Verbindung oder USB-Gehäuse können die Abfrage verhindern. Die Sicherung kann im interaktiven Modus dennoch fortgesetzt werden; der fehlende Status sagt nichts über die Gesundheit aus.

**SMART meldet „Unknown USB bridge“:** Der USB-Adapter leitet die Standardabfrage nicht durch. Das Skript rät keinen Gerätetyp und setzt die Sicherung ohne SMART-Bewertung fort. Für eine gesonderte SMART-Prüfung den vom Hersteller unterstützten Adapter- beziehungsweise Gerätetyp ermitteln.

**SSH-Kopie schlägt fehl:** Anmeldung, Zielordner, Rechte und vorhandene Zieldateien prüfen. Die lokale Datei bleibt erhalten. Die entfernte Teil-Datei wird nicht automatisch gelöscht.

**Mail kommt nicht an:** Das Skript nutzt das lokale Programm `mail`. Die tatsächliche Zustellung hängt von dessen Konfiguration und einem Mail-Transport ab.

## 13. Wiederherstellung vorbereiten

Eine Wiederherstellung überschreibt das gewählte Zielgerät. Vorher das Backup mit `sha256sum -c` prüfen, den Inhaltstyp feststellen und das Ziel mit `lsblk` eindeutig identifizieren. Die Zielpartitionen müssen ausgehängt sein. Für `dd`- und `ddrescue`-Abbilder muss das Ziel ausreichend groß sein. `fsarchiver` stellt eine gespeicherte Partition mit `restfs` wieder her; die genaue Zielpartition ist dabei ausdrücklich anzugeben. Die [FSArchiver-Anleitung](https://www.fsarchiver.org/quickstart/) enthält die Syntax für `restfs`.

Der konkrete Wiederherstellungsbefehl hängt von Format, Verschlüsselung und Zielgerät ab. Das Skript führt selbst **keine** Wiederherstellung aus. Ein Test auf einem geeigneten Ersatzdatenträger ist die aussagekräftigste Prüfung, ob sich die gesicherten Daten wirklich nutzen lassen.

## 14. Grafische Bedienung unter Linux

Zusätzlich zum Terminal steht `disk-image-gui.py` bereit. Starten Sie es mit
`python3 disk-image-gui.py` im Ordner der Skripte einschließlich
`disk-image-runner.py`, `disk_image_progress.py`, `disk-image-update.py` und
`disk-image-mail.py`. Python 3 und Tkinter sind
erforderlich; auf Debian/Ubuntu kann Tkinter mit `sudo apt install python3-tk`
nachinstalliert werden. Für die Administrator-Abfrage benötigt die Sitzung
`pkexec` und einen Polkit-Agenten. Alternativ kann die Oberfläche als root
gestartet werden; dabei funktionieren Desktop-Benachrichtigungen je nach Sitzung
gegebenenfalls nicht.

1. **Quelle und Ziel:** Sicherungsart wählen. `dd` und `ddrescue` zeigen ganze
   Datenträger; `fsarchiver` zeigt Partitionen. Den vorhandenen lokalen
   Zielordner wählen. Bei eingehängter Quelle ist Abbrechen der Standard;
   optional kann vor dem Sichern ausgehängt werden.
2. **Weitere Optionen:** Bei `dd` Kompression und Lesefehlerverhalten einstellen.
   Mit `ddrescue` kann ein Abbild fortgesetzt werden, wenn die zugehörige
   `.map`-Datei vorhanden ist. Für `dd` kann age-Verschlüsselung aktiviert werden:
   Ins obere Feld kommt der öffentliche Empfängerschlüssel, ins untere bei
   aktivierter Inhaltsprüfung die private Identitätsdatei. **age erklären**
   beschreibt die Schlüssel und ihre Erzeugung. Weitere Felder steuern SSH-Kopie,
   SMART-Anzeige, Verifikation und die automatische Installation fehlender
   Pakete. Diese ist standardmäßig eingeschaltet.
3. **E-Mail:** Benachrichtigungsart und Empfängeradresse wählen. Der Versandweg
   kann das lokale `mail`-Programm oder eine direkte SMTP-Verbindung sein.
   Die Anleitung im nächsten Abschnitt führt durch beide Einstellungen.
4. **Prüfen und starten:** Zusammenfassung lesen und den Start bestätigen.
   Erst dann wird nach Administratorrechten gefragt. Die Oberfläche zeigt
   Meldungen, den messbaren Fortschritt in Prozent und die Lese- und
   Schreibrate in MiB/s. Die ausführliche Protokolldatei liegt
   im Zielordner. Die Meldungen im Fenster lassen sich mit **Kopieren** in die
   Zwischenablage übernehmen oder mit **Als Datei speichern …** als Textdatei
   sichern, auch wenn vor dem Anlegen der Protokolldatei ein Fehler auftritt.
   **Abbrechen** beendet den Lauf ohne Pause. Ein begonnenes Abbild bleibt
   erhalten; nur `ddrescue` kann mit passender Map-Datei fortgesetzt werden.
5. **Updates:** Mit **Nach Updates suchen** die offizielle GitHub-Version
   prüfen, mit **Update installieren** übernehmen und anschließend die GUI
   neu starten. **Beim Programmstart automatisch aktualisieren** schaltet
   die Aktualisierung beim nächsten Start ein. Die Einstellung ist zunächst
   ausgeschaltet. Während einer Sicherung ist ein Update gesperrt.
6. **Startsymbole:** Im selben Reiter mit **Startsymbole einrichten** zwei
   Einträge im Anwendungsmenü des aktuellen Benutzers anlegen. Der grafische
   Eintrag öffnet die GUI. Der Terminal-Eintrag zeigt ein kleines Menü:
   **1** startet die interaktive CLI, **2** zeigt die Parameterhilfe, **3**
   schließt das Terminal. Erst beim Start einer Sicherung fragt `sudo` nach
   dem Passwort. Die Startsymbole benötigen den Installationsordner weiterhin
   an seinem ursprünglichen Ort.

Alternativ die Startsymbole ohne Administratorrechte direkt installieren
oder entfernen:

```bash
python3 install-launchers.py --install
python3 install-launchers.py --remove
```

Ein leerer Zielordner wird bereits vor der Administrator-Abfrage abgewiesen.
Deshalb erscheint bei diesem Eingabefehler auch noch keine Lese- oder
Schreibrate. Sobald Daten fließen, berechnet die GUI die Rate aus den
gemessenen Bytezählern und der verstrichenen Zeit.

Nach einem Lauf über `sudo` oder die GUI mit `pkexec` gehören alle neu
erzeugten Ausgabedateien (Abbild, `.sha256`, `.log`, bei `ddrescue` auch
`.map`) dem aufrufenden Benutzer. Die Rechte werden auf `600` gesetzt.
Bei einem direkten Root-Start ohne bekannten aufrufenden Benutzer bleibt
root Besitzer. Manche Ziel-Dateisysteme unterstützen Unix-Eigentümer und
Rechte nicht; das Skript zeigt dann eine Warnung. Bereits vorhandene
Dateien aus älteren Sicherungen ändert ein Update nicht rückwirkend.

Bei `dd` misst `pv` den gelesenen Datenstrom und die geschriebene Datei.
Bei `ddrescue` wird der Anteil erfolgreich geretteter Bereiche aus der
Map-Datei berechnet. Bei `fsarchiver` erscheint ein Prozentwert nur, wenn
das Werkzeug ihn meldet. Die Raten bei `ddrescue` und `fsarchiver` stammen
aus den logischen Ein-/Ausgaben des Prozesses und können von der tatsächlichen
Laufwerksgeschwindigkeit abweichen. Nicht verfügbare Werte bleiben erkennbar
unbekannt. Eine Verifikation ist ein eigener Arbeitsschritt nach der Sicherung.

### Updates für Git-Installationen

Die eingebaute Update-Funktion benötigt eine unveränderte Installation per
`git clone` auf Branch `main` mit dem offiziellen Repository als `origin`.
Eigene Änderungen und zusätzliche Dateien verhindern ein automatisches
Update, damit sie nicht überschrieben werden. Ein installiertes Update wird
erst nach dem Neustart der GUI wirksam. Der Update-Vorgang läuft ohne
Administratorrechte. Die erste Aktualisierung einer älteren GUI ohne
Update-Reiter erfolgt einmalig im Installationsordner:

```bash
git pull --ff-only
python3 disk-image-gui.py
```

Für einen geplanten Lauf außerhalb der GUI kann als normaler Benutzer
`python3 disk-image-update.py --apply` verwendet werden, etwa mit Cron.
Ein Aufruf mit `--check` prüft nur. Bei einem Archivdownload ohne `.git`
ist die eingebaute Aktualisierung nicht möglich; dafür eine Git-Kopie
gemäß README installieren.

Die GUI nutzt denselben nicht interaktiven CLI-Modus, der auch für Cron geeignet
ist. Der Terminal-Aufruf `sudo bash disk-image.sh` und alle CLI-Parameter stehen
unabhängig von der GUI weiter zur Verfügung. Die GUI ist für Linux vorgesehen;
ein vollständiger Test mit realen Datenträgern steht noch aus.

## 15. E-Mail einrichten: Schritt für Schritt

### Möglichkeit A: Lokales Mailprogramm

1. Im GUI-Reiter **E-Mail** bei **Nach der Sicherung** „E-Mail“ wählen und die Empfängeradresse eintragen.
2. Als **Versandweg** „Lokales Mailprogramm“ wählen und **Mailprogramm prüfen** anklicken. Fehlt das Programm, auf Debian/Ubuntu `sudo apt install bsd-mailx` ausführen. Die GUI kann es für einen Sicherungslauf auch über die aktivierte Paketinstallation nachinstallieren.
3. Einen Mail-Transport auf dem Linux-Rechner einrichten. Das konkrete Vorgehen hängt von Distribution und Mail-Anbieter ab; `mail` allein stellt keine SMTP-Verbindung bereit. Für einen bereits eingerichteten System-Mail-Transport sind in der GUI keine Zugangsdaten nötig.
4. **Testmail senden** anklicken und anschließend den Posteingang sowie gegebenenfalls den Spam-Ordner kontrollieren. Erst der tatsächliche Eingang bestätigt die Zustellung.
5. Im vierten Reiter den Versandweg prüfen und die Sicherung starten. Auf der CLI `--notify mail --mail-to ADRESSE` verwenden.

### Möglichkeit B: SMTP direkt

1. Vom Mail-Anbieter Servername, Port, Verbindungstyp (**STARTTLS** oder **SSL/TLS**), Absenderadresse, Benutzername und gegebenenfalls ein App-Passwort beschaffen. Nur die vom Anbieter genannten Werte verwenden; Port 587 mit STARTTLS ist eine Voreinstellung der GUI, keine allgemeine Zusage für jeden Anbieter.
2. Im GUI-Reiter **E-Mail** „E-Mail“ und „SMTP direkt“ wählen. Empfängeradresse und SMTP-Felder ausfüllen. Benutzername und Passwort entweder beide eintragen oder beide leer lassen, falls der Server ausdrücklich keine Anmeldung benötigt.
3. **SMTP-Einstellungen speichern** anklicken und die Speicherung bestätigen. Die Datei liegt unter `~/.config/disk-image-tool/mail.json`, ist nur für ihren Besitzer lesbar (`chmod 600`) und enthält das Passwort im Klartext. Die Datei nicht weitergeben, nicht in Git einchecken und aus Backups mit breitem Zugriff ausschließen.
4. **Testmail senden** anklicken. Bei Fehlern die Angaben und die Erreichbarkeit des Servers prüfen. Danach im Posteingang kontrollieren, ob die Nachricht tatsächlich zugestellt wurde.
5. Für einen CLI- oder Cron-Lauf zusätzlich zu `--notify mail --mail-to ADRESSE` den absoluten Pfad mit `--mail-config /home/BENUTZER/.config/disk-image-tool/mail.json` angeben. Root muss diese Datei lesen können. Der CLI-Modus prüft Format und Dateirechte vor der Sicherung und sendet erst nach dem Lauf.

Die Testmail aus der GUI läuft mit dem angemeldeten Benutzer. Die Sicherungsbenachrichtigung läuft mit Administratorrechten; deshalb kann sich der lokale Mailversand zwischen Test und Sicherung unterscheiden. SMTP nutzt in beiden Fällen dieselbe Konfigurationsdatei. Weder eine erfolgreiche SMTP-Übergabe noch ein Exit-Code 0 des lokalen Mailprogramms beweist die spätere Zustellung.


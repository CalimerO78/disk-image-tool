#!/usr/bin/env bash
# Erstellt ein Abbild eines ganzen Linux-Datenträgers.
#
# Aufruf: sudo bash disk-image.sh
# Benötigt: Linux, Bash, Root-Rechte und je nach Sicherungsart dd/pv,
# ddrescue oder fsarchiver. Weitere Pflichtprogramme prüft das Skript selbst.
# Fehlende Programme können auf Debian/Ubuntu nach Rückfrage per apt-get
# installiert werden (Standardantwort: Ja). Manuell: sudo apt install pv
# Optional: gzip/pigz oder zstd, age, smartctl, SSH und Benachrichtigung.
#
# Das Skript liest die Quelle nur. Es schreibt Abbild, Prüfsumme und Protokoll
# in den gewählten Zielordner. Ein unvollständiges Abbild bleibt zur Diagnose
# erhalten und darf nicht als vollständige Sicherung verwendet werden.
# Rückgabewerte: 0 = abgeschlossen; 1 = Auswahl-/Lese-/Schreibfehler;
# 2 = Verifikation fehlgeschlagen; 3 = Lesefehler/unvollständige Rettung;
# 130/143 = durch Strg+C bzw. TERM unterbrochen.
# Die .sha256-Datei lässt sich mit "sha256sum -c DATEI.sha256" prüfen.

set -o pipefail
umask 077 # Nur root darf neu erzeugte Abbild- und Protokolldateien lesen.

readonly BS_NORMAL=4M
readonly BS_CONTINUE=128K
readonly DEFAULT_DEST="$PWD"
LOG=""
LOG_READY=0
TEMP_DIR=""
HASH_PID=""
ENGINE=""
CLI_SOURCE=""
CLI_DEST=""
CLI_COMPRESSION=""
CLI_ERROR_MODE=""
MOUNT_ACTION=""
AGE_RECIPIENT=""
AGE_IDENTITY=""
SSH_HOST=""
SSH_PATH=""
NOTIFY_MODE=none
NOTIFY_SPECIFIED=0
MAIL_TO=""
MAIL_CONFIG=""
NON_INTERACTIVE=0
AUTO_INSTALL=0
VERIFY_MODE=""
SMART_MODE=""
RESUME_IMAGE=""
RUN_STARTED=0

usage() {
    cat <<'HELP'
Aufruf: sudo bash disk-image.sh [Optionen]

Ohne Optionen führt das Skript durch die Auswahl. Für Cron sind
--non-interactive, --source, --dest, --engine und --mounted erforderlich.

--engine dd|ddrescue|fsarchiver   Ganzes Laufwerk mit dd/ddrescue oder
                                 einzelne Partition mit fsarchiver sichern
--source /dev/...                 Quellgerät
--dest VERZEICHNIS               Lokaler Zielordner
--compression none|gzip|zstd      Nur für dd (Standard: none)
--on-read-error continue|abort    Nur für dd (Standard: continue)
--mounted unmount|allow|abort     Umgang mit eingehängter Quelle
--resume-image DATEI              Vorhandenes ddrescue-Abbild fortsetzen;
                                 die zugehörige Map-Datei heißt DATEI.map
--age-recipient SCHLÜSSEL         dd-Abbild mit age verschlüsseln
--age-identity DATEI              Privater Schlüssel für die Verifikation
--ssh-host BENUTZER@HOST          Fertiges Abbild über SSH kopieren
--ssh-path /ziel/datei            Exakter Dateipfad auf dem SSH-Ziel
--notify none|desktop|mail        Benachrichtigung nach Abschluss
--mail-to ADRESSE                 Empfänger für --notify mail
--mail-config DATEI               SMTP-Konfiguration statt lokalem mail-Programm;
                                 nur für --notify mail (Dateirechte 600)
--smart show|skip                 SMART-Kurzstatus anzeigen oder auslassen
--verify | --no-verify            Gespeichertes Abbild prüfen oder überspringen
--non-interactive                 Keine Rückfragen; fehlende Angaben sind Fehler
--auto-install                    Fehlende Pakete ohne Rückfrage installieren
--help                            Diese Hilfe anzeigen

Beispiel: sudo bash disk-image.sh --non-interactive --engine dd \
  --source /dev/sdb --dest /backup --mounted abort --verify
HELP
}

parse_args() {
while (( $# > 0 )); do
    case "$1" in
        --help) usage; exit 0 ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --auto-install) AUTO_INSTALL=1; shift ;;
        --verify) VERIFY_MODE=yes; shift ;;
        --no-verify) VERIFY_MODE=no; shift ;;
        --engine|--source|--dest|--compression|--on-read-error|--mounted|--resume-image|--age-recipient|--age-identity|--ssh-host|--ssh-path|--notify|--mail-to|--mail-config|--smart)
            (( $# >= 2 )) || die "Wert fehlt für $1"
            case "$1" in
                --engine) ENGINE="$2" ;; --source) CLI_SOURCE="$2" ;;
                --dest) CLI_DEST="$2" ;; --compression) CLI_COMPRESSION="$2" ;;
                --on-read-error) CLI_ERROR_MODE="$2" ;; --mounted) MOUNT_ACTION="$2" ;;
                --resume-image) RESUME_IMAGE="$2" ;; --age-recipient) AGE_RECIPIENT="$2" ;;
                --age-identity) AGE_IDENTITY="$2" ;; --ssh-host) SSH_HOST="$2" ;;
                --ssh-path) SSH_PATH="$2" ;; --notify) NOTIFY_MODE="$2" ;;
                --mail-to) MAIL_TO="$2" ;; --mail-config) MAIL_CONFIG="$2" ;;
                --smart) SMART_MODE="$2" ;;
            esac
            [[ "$1" != --notify ]] || NOTIFY_SPECIFIED=1
            shift 2 ;;
        *) die "Unbekannte Option: $1 (siehe --help)" ;;
    esac
done
}

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') $*"
    printf '%s\n' "$line"
    if [[ "$LOG_READY" == 1 ]]; then
        printf '%s\n' "$line" >> "$LOG"
    fi
}

die() {
    printf 'FEHLER: %s\n' "$*" >&2
    if [[ "$LOG_READY" == 1 ]]; then
        printf 'FEHLER: %s\n' "$*" >> "$LOG"
    fi
    exit 1
}

# Eine geschlossene Eingabe gilt auch bei einer Standardantwort als Nein.
ask_yn() {
    local question="$1" default="$2" answer hint
    if [[ "$default" == J ]]; then hint='[J/n]'; else hint='[j/N]'; fi
    read -r -p "$question $hint: " answer || return 1
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[JjYy]$ ]]
}

human() {
    numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%s B\n' "$1"
}

cleanup() {
    local exit_status=$?
    if [[ -n "$HASH_PID" ]]; then
        kill "$HASH_PID" 2>/dev/null || true
        wait "$HASH_PID" 2>/dev/null || true
    fi
    [[ -z "$TEMP_DIR" ]] || rm -rf -- "$TEMP_DIR"
    if (( RUN_STARTED )); then
        if [[ "$NOTIFY_MODE" == desktop ]]; then
            notify-send 'Datenträgersicherung' "Beendet mit Status $exit_status: $SRC" \
                || printf 'WARNUNG: Desktop-Benachrichtigung fehlgeschlagen.\n' >&2
        elif [[ "$NOTIFY_MODE" == mail && -n "$MAIL_CONFIG" ]]; then
            python3 "$(dirname -- "${BASH_SOURCE[0]}")/disk-image-mail.py" \
                --config "$MAIL_CONFIG" --to "$MAIL_TO" \
                --status "$exit_status" --source "$SRC" --image "$IMG" --log "$LOG" \
                || printf 'WARNUNG: SMTP-Benachrichtigung fehlgeschlagen.\n' >&2
        elif [[ "$NOTIFY_MODE" == mail ]]; then
            printf 'Sicherung von %s beendet. Status: %s\nAbbild: %s\nProtokoll: %s\n' \
                "$SRC" "$exit_status" "$IMG" "$LOG" \
                | mail -s "Datenträgersicherung: Status $exit_status" "$MAIL_TO" \
                || printf 'WARNUNG: Mail-Benachrichtigung fehlgeschlagen.\n' >&2
        fi
    fi
    return "$exit_status"
}
trap cleanup EXIT
trap 'log "ABBRUCH durch Benutzer; ein eventuell begonnenes Abbild bleibt erhalten."; exit 130' INT
trap 'log "ABBRUCH durch Signal TERM; ein eventuell begonnenes Abbild bleibt erhalten."; exit 143' TERM

parse_args "$@"
[[ $EUID -eq 0 ]] || die "Bitte mit sudo als root ausführen."
if (( NON_INTERACTIVE )); then
    [[ -n "$ENGINE" && -n "$CLI_SOURCE" && -n "$CLI_DEST" && -n "$MOUNT_ACTION" ]] \
        || die "Für --non-interactive sind --engine, --source, --dest und --mounted nötig."
    SMART_MODE="${SMART_MODE:-skip}"
    VERIFY_MODE="${VERIFY_MODE:-yes}"
else
    if [[ -z "$ENGINE" ]]; then
        printf 'Sicherungsart:\n  [1] dd: vollständiges Laufwerksabbild\n  [2] ddrescue: beschädigtes Laufwerk mit Map-Datei\n  [3] fsarchiver: belegte Daten einer Partition\n'
        read -r -p 'Auswahl [1]: ' engine_choice || die "Keine Sicherungsart erhalten."
        case "${engine_choice:-1}" in 1) ENGINE=dd ;; 2) ENGINE=ddrescue ;; 3) ENGINE=fsarchiver ;; *) die "Ungültige Sicherungsart." ;; esac
    fi
fi
case "$ENGINE" in dd|ddrescue|fsarchiver) ;; *) die "Ungültige Sicherungsart: $ENGINE" ;; esac
if (( ! NON_INTERACTIVE )); then
    if [[ "$ENGINE" == dd && -z "$AGE_RECIPIENT" ]] && ask_yn 'Abbild mit age verschlüsseln?' N; then
        read -r -p 'Öffentlicher age-Empfängerschlüssel: ' AGE_RECIPIENT || die "Keinen Schlüssel erhalten."
        [[ -n "$AGE_RECIPIENT" ]] || die "Leerer age-Empfängerschlüssel."
        read -r -p 'Private Identitätsdatei für Verifikation (optional): ' AGE_IDENTITY || die "Keine Eingabe erhalten."
    fi
    if [[ -z "$SSH_HOST" ]] && ask_yn 'Fertiges Abbild zusätzlich über SSH kopieren?' N; then
        read -r -p 'SSH-Ziel BENUTZER@HOST: ' SSH_HOST || die "Kein SSH-Ziel erhalten."
        read -r -p 'Absoluter Dateipfad auf dem Ziel: ' SSH_PATH || die "Keinen SSH-Pfad erhalten."
    fi
    if (( ! NOTIFY_SPECIFIED )) && ask_yn 'Am Ende benachrichtigen?' N; then
        printf '  [1] Desktop\n  [2] Mail\n'
        read -r -p 'Auswahl: ' notify_choice || die "Keine Auswahl erhalten."
        case "$notify_choice" in
            1) NOTIFY_MODE=desktop ;;
            2) NOTIFY_MODE=mail
               read -r -p 'Mailadresse: ' MAIL_TO || die "Keine Mailadresse erhalten." ;;
            *) die "Ungültige Benachrichtigung." ;;
        esac
    fi
fi
case "${SMART_MODE:-show}" in show|skip) ;; *) die "--smart muss show oder skip sein." ;; esac
case "${VERIFY_MODE:-yes}" in yes|no) ;; *) die "Ungültige Verifikationsauswahl." ;; esac
case "$NOTIFY_MODE" in none|desktop|mail) ;; *) die "Ungültige Benachrichtigung." ;; esac
case "$MOUNT_ACTION" in ''|unmount|allow|abort) ;; *) die "--mounted muss unmount, allow oder abort sein." ;; esac
if [[ "$NOTIFY_MODE" == mail ]]; then
    [[ "$MAIL_TO" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "Für Mail ist --mail-to mit gültiger Adresse nötig."
fi
if [[ -n "$MAIL_CONFIG" ]]; then
    [[ "$NOTIFY_MODE" == mail && "$MAIL_CONFIG" == /* ]] \
        || die "--mail-config benötigt --notify mail und einen absoluten Dateipfad."
    command -v python3 >/dev/null 2>&1 || die "Für SMTP wird Python 3 benötigt."
    python3 "$(dirname -- "${BASH_SOURCE[0]}")/disk-image-mail.py" \
        --config "$MAIL_CONFIG" --check >/dev/null \
        || die "SMTP-Konfiguration fehlt, ist ungültig oder nicht geschützt."
fi
if [[ -n "$SSH_HOST" || -n "$SSH_PATH" ]]; then
    [[ "$SSH_HOST" =~ ^[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9_.-]+$ && "$SSH_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]] \
        || die "Für SSH sind --ssh-host BENUTZER@HOST und --ssh-path /absoluter/pfad nötig."
fi
if [[ "$ENGINE" != dd && ( -n "$AGE_RECIPIENT" || -n "$AGE_IDENTITY" || -n "$CLI_COMPRESSION" || -n "$CLI_ERROR_MODE" ) ]]; then
    die "Kompression, Lesefehler-Modus und age gelten nur für --engine dd."
fi
if [[ -n "$RESUME_IMAGE" && "$ENGINE" != ddrescue ]]; then die "--resume-image gilt nur für ddrescue."; fi
if [[ -n "$AGE_IDENTITY" && -z "$AGE_RECIPIENT" ]]; then die "--age-identity benötigt --age-recipient."; fi
if [[ -n "$AGE_IDENTITY" ]]; then
    [[ -f "$AGE_IDENTITY" && -r "$AGE_IDENTITY" ]] || die "age-Identitätsdatei ist nicht lesbar."
fi

# Nur fehlende Pakete anbieten. smartctl ist für die gewünschte SMART-Anzeige
# optional; die übrigen Programme werden für die Sicherung benötigt.
check_prerequisites() {
    local tool package missing_smart=0
    local -a required=(lsblk blockdev findmnt umount df sha256sum sync mktemp stat numfmt cat awk grep tail tr basename dirname date)
    local -a missing=() packages=()
    local -A needed_packages=()

    case "$ENGINE" in
        dd) required+=(dd pv mkfifo tee) ;;
        ddrescue) required+=(ddrescue ddrescuelog) ;;
        fsarchiver) required+=(fsarchiver) ;;
    esac
    [[ -z "$AGE_RECIPIENT" ]] || required+=(age)
    [[ -z "$SSH_HOST" ]] || required+=(ssh)
    case "$NOTIFY_MODE" in desktop) required+=(notify-send) ;;
        mail) [[ -n "$MAIL_CONFIG" ]] || required+=(mail) ;; esac

    for tool in "${required[@]}"; do
        command -v "$tool" >/dev/null 2>&1 && continue
        missing+=("$tool")
        case "$tool" in
            lsblk|blockdev|findmnt) package=util-linux ;;
            umount) package=mount ;;
            pv) package=pv ;;
            ddrescue|ddrescuelog) package=gddrescue ;;
            fsarchiver) package=fsarchiver ;;
            age) package=age ;;
            ssh) package=openssh-client ;;
            notify-send) package=libnotify-bin ;;
            mail) package=bsd-mailx ;;
            awk) package=mawk ;;
            grep) package=grep ;;
            *) package=coreutils ;;
        esac
        needed_packages["$package"]=1
    done
    if [[ "$SMART_MODE" != skip ]] && ! command -v smartctl >/dev/null 2>&1; then
        missing_smart=1
        needed_packages[smartmontools]=1
    fi
    (( ${#missing[@]} > 0 || missing_smart )) || return 0

    if (( ${#missing[@]} > 0 )); then
        printf 'Fehlende Pflichtprogramme: %s\n' "${missing[*]}"
    fi
    (( ! missing_smart )) || printf 'Für die SMART-Anzeige fehlt: smartctl (optional).\n'
    for package in "${!needed_packages[@]}"; do packages+=("$package"); done
    printf 'Benötigte Debian/Ubuntu-Pakete: %s\n' "${packages[*]}"

    if command -v apt-get >/dev/null 2>&1 && [[ -f /etc/debian_version ]]; then
        if (( AUTO_INSTALL )) || { (( ! NON_INTERACTIVE )) && ask_yn 'Fehlende Pakete jetzt automatisch installieren?' J; }; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y -- "${packages[@]}" \
                || die "Paketinstallation fehlgeschlagen. Paketlisten und Netzwerk prüfen."
        elif (( ${#missing[@]} > 0 )); then
            die "Pflichtprogramme fehlen. Beispiel für pv: sudo apt install pv"
        fi
    elif (( ${#missing[@]} > 0 )); then
        die "Pflichtprogramme fehlen. Automatische Installation wird nur auf Debian/Ubuntu mit apt-get unterstützt."
    else
        printf 'HINWEIS: SMART-Anzeige nicht verfügbar; smartctl bitte mit dem Paketmanager installieren.\n'
    fi

    for tool in "${required[@]}"; do
        command -v "$tool" >/dev/null 2>&1 || die "Pflichtprogramm weiterhin nicht verfügbar: $tool"
    done
}
check_prerequisites

ensure_program() {
    local tool="$1" package="$2"
    command -v "$tool" >/dev/null 2>&1 && return 0
    if command -v apt-get >/dev/null 2>&1 && [[ -f /etc/debian_version ]]; then
        if (( AUTO_INSTALL )) || { (( ! NON_INTERACTIVE )) && ask_yn "$tool fehlt. Paket $package jetzt installieren?" J; }; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y -- "$package" \
                || die "Paketinstallation fehlgeschlagen: $package"
        fi
    fi
    command -v "$tool" >/dev/null 2>&1 || die "$tool fehlt (Debian/Ubuntu: sudo apt install $package)."
}

if [[ -n "$CLI_SOURCE" ]]; then
    SRC="$CLI_SOURCE"
else
    # fsarchiver sichert genau ein Dateisystem, dd und ddrescue ganze Platten.
    if [[ "$ENGINE" == fsarchiver ]]; then
        device_list="$(lsblk -lnpo NAME,SIZE,FSTYPE,TYPE -e 1,7,11 | awk '$NF == "part"')" \
            || die "Partitionen konnten nicht gelesen werden."
        printf '=== Verfügbare Partitionen ===\n'
    else
        device_list="$(lsblk -dnp -o NAME,SIZE,MODEL,TRAN -e 1,7,11)" \
            || die "Datenträger konnten nicht gelesen werden."
        printf '=== Verfügbare Datenträger ===\n'
    fi
    [[ -n "$device_list" ]] || die "Keine passenden Geräte gefunden."
    mapfile -t devices <<< "$device_list"
    for i in "${!devices[@]}"; do printf '  [%d] %s\n' "$((i + 1))" "${devices[$i]}"; done
    read -r -p 'Nummer der Quelle: ' selection || die "Keine Auswahl erhalten."
    [[ "$selection" =~ ^[0-9]+$ ]] || die "Ungültige Auswahl."
    (( 10#$selection >= 1 && 10#$selection <= ${#devices[@]} )) || die "Ungültige Auswahl."
    read -r SRC _ <<< "${devices[$((10#$selection - 1))]}"
fi
[[ -b "$SRC" ]] || die "$SRC ist kein Blockgerät."
source_type="$(lsblk -dn -o TYPE "$SRC")" || die "Gerätetyp konnte nicht bestimmt werden."
if [[ "$ENGINE" == fsarchiver ]]; then
    [[ "$source_type" == part ]] || die "fsarchiver benötigt eine Partition als Quelle."
else
    [[ "$source_type" == disk ]] || die "$ENGINE benötigt einen ganzen Datenträger als Quelle."
fi
SIZE="$(blockdev --getsize64 "$SRC")" || die "Größe von $SRC konnte nicht ermittelt werden."
[[ "$SIZE" =~ ^[0-9]+$ ]] && (( SIZE > 0 )) || die "Ungültige Datenträgergröße."
MODEL="$(lsblk -dn -o MODEL "$SRC" | tr -s ' ' '_' | tr -cd '[:alnum:]_.-')"
MODEL="${MODEL:-$(basename "$SRC")}"

printf '\n'
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS "$SRC" || die "Gerätedetails konnten nicht gelesen werden."
printf '\n'

# -H zeigt den kurzen Gesundheitsstatus. Einige USB-Adapter leiten SMART nicht
# durch; eine fehlende Antwort beweist deshalb keinen guten oder schlechten Zustand.
SMART_TEXT=""
SMART_CHECKED=0
if command -v smartctl >/dev/null 2>&1; then
    if [[ "$SMART_MODE" == show ]] || { [[ -z "$SMART_MODE" ]] && ask_yn 'SMART-Kurzstatus des Datenträgers anzeigen?' J; }; then
        SMART_CHECKED=1
        SMART_TEXT="$(smartctl -H "$SRC" 2>&1)"
        smart_rc=$?
        printf '\n=== SMART-Kurzstatus ===\n%s\n' "$SMART_TEXT"
        if (( smart_rc & 8 || smart_rc & 16 || smart_rc & 32 )); then
            printf 'WARNUNG: SMART meldet einen aktuellen oder früheren Fehler.\n'
            if (( NON_INTERACTIVE )); then die "SMART meldet Fehler; unbeaufsichtigter Lauf abgebrochen."; fi
            ask_yn 'Trotzdem fortfahren?' N || exit 1
        elif (( smart_rc != 0 )); then
            if [[ "$SMART_TEXT" == *'Unknown USB bridge'* ]]; then
                printf 'HINWEIS: Der USB-Adapter reicht SMART ohne passenden Gerätetyp nicht durch. Die Sicherung kann fortfahren; ein SMART-Status liegt nicht vor.\n'
            else
                printf 'HINWEIS: SMART konnte nicht vollständig ermittelt werden (Status %d).\n' "$smart_rc"
            fi
        fi
    fi
elif [[ "$SMART_MODE" != skip ]]; then
    printf 'HINWEIS: SMART-Anzeige nicht verfügbar (smartctl fehlt; Debian/Ubuntu: sudo apt install smartmontools).\n'
fi

if [[ -n "$CLI_DEST" ]]; then
    DEST="$CLI_DEST"
else
    read -r -p "Zielordner [$DEFAULT_DEST]: " DEST || die "Keinen Zielordner erhalten."
    DEST="${DEST:-$DEFAULT_DEST}"
fi
[[ -d "$DEST" && -w "$DEST" ]] || die "Zielordner existiert nicht oder ist nicht beschreibbar: $DEST"
DEST="$(cd -- "$DEST" && pwd -P)" || die "Zielordner konnte nicht aufgelöst werden."

# Die Gerätekennung MAJ:MIN erkennt auch Partitionen und eingebundene
# Dateisysteme auf der Quellplatte. So landet das Abbild nicht auf der Quelle.
dest_device="$(findmnt -n -o MAJ:MIN -T "$DEST")" || die "Zielgerät konnte nicht bestimmt werden."
source_devices="$(lsblk -nr -o MAJ:MIN "$SRC")" || die "Quellgeräte konnten nicht bestimmt werden."
source_serial="$(lsblk -dn -o SERIAL "$SRC" 2>/dev/null)"
if grep -Fxq -- "$dest_device" <<< "$source_devices"; then
    die "Zielordner liegt auf dem zu sichernden Datenträger."
fi

# Alle Einhängepunkte von Quelle und Partitionen über die Gerätekennung
# sammeln. findmnt --raw kodiert Leerzeichen und andere Sonderzeichen als
# Hex-Escapes; printf %b stellt den tatsächlichen Pfad wieder her.
source_mounts() {
    local table row device escaped target existing duplicate
    SOURCE_MOUNTS=()
    table="$(findmnt -rn -o MAJ:MIN,TARGET)" || die "Einhängepunkte konnten nicht ermittelt werden."
    while IFS= read -r row; do
        read -r device escaped <<< "$row"
        if grep -Fxq -- "$device" <<< "$source_devices"; then
            [[ -n "$escaped" ]] || die "Ein Einhängepunkt hat keinen lesbaren Pfad."
            printf -v target '%b' "$escaped"
            duplicate=0
            for existing in "${SOURCE_MOUNTS[@]}"; do
                [[ "$existing" != "$target" ]] || duplicate=1
            done
            (( duplicate )) || SOURCE_MOUNTS+=("$target")
        fi
    done <<< "$table"
}

source_mounts
if (( ${#SOURCE_MOUNTS[@]} > 0 )); then
    printf '\nDie Quelle ist an folgenden Stellen eingehängt:\n'
    printf '  %s\n' "${SOURCE_MOUNTS[@]}"
    printf '  [1] Angezeigte Dateisysteme jetzt aushängen\n'
    printf '  [2] Eingehängt lassen (Abbild kann inkonsistent sein)\n'
    printf '  [3] Abbrechen (Standard)\n'
    if [[ -n "$MOUNT_ACTION" ]]; then
        mount_choice="$MOUNT_ACTION"
    else
        read -r -p 'Auswahl [3]: ' mount_choice || die "Keine Auswahl erhalten."
        mount_choice="${mount_choice:-3}"
    fi
    case "$mount_choice" in
        1|unmount)
            MOUNT_ACTION=unmount
            command -v umount >/dev/null 2>&1 || die "Zum Aushängen fehlt 'umount'."
            # Weder das Ziel noch das aktuelle Arbeitsverzeichnis darf durch
            # diese Aktion verschwinden. Das Systemwurzelverzeichnis bleibt tabu.
            current_dir="$(pwd -P)" || die "Arbeitsverzeichnis konnte nicht aufgelöst werden."
            for mount_path in "${SOURCE_MOUNTS[@]}"; do
                [[ "$mount_path" != / ]] || die "Das Systemwurzelverzeichnis wird nicht ausgehängt."
                [[ "$DEST" != "$mount_path" && "$DEST" != "$mount_path/"* ]] || die "Ein Einhängepunkt enthält den Zielordner: $mount_path"
                [[ "$current_dir" != "$mount_path" && "$current_dir" != "$mount_path/"* ]] || die "Ein Einhängepunkt enthält das aktuelle Arbeitsverzeichnis: $mount_path"
            done
            # Untergeordnete Einhängepunkte müssen zuerst verschwinden.
            while (( ${#SOURCE_MOUNTS[@]} > 0 )); do
                longest=0
                for i in "${!SOURCE_MOUNTS[@]}"; do
                    if (( ${#SOURCE_MOUNTS[i]} > ${#SOURCE_MOUNTS[longest]} )); then longest=$i; fi
                done
                mount_path="${SOURCE_MOUNTS[longest]}"
                # -M kann bei übereinanderliegenden Mounts mehrere Einträge
                # liefern. -T ermittelt genau den jetzt sichtbaren Eintrag.
                current_device="$(findmnt -nro MAJ:MIN -T "$mount_path")" || die "Einhängepunkt hat sich geändert: $mount_path"
                grep -Fxq -- "$current_device" <<< "$source_devices" || die "Einhängepunkt gehört nicht mehr zur Quelle: $mount_path"
                printf 'Hänge aus: %s\n' "$mount_path"
                umount -- "$mount_path" || die "Aushängen fehlgeschlagen: $mount_path. Bereits ausgehängte Dateisysteme bleiben ausgehängt."
                unset "SOURCE_MOUNTS[$longest]"
                SOURCE_MOUNTS=("${SOURCE_MOUNTS[@]}")
            done
            source_mounts
            (( ${#SOURCE_MOUNTS[@]} == 0 )) || die "Die Quelle ist weiterhin eingehängt; Abbruch."
            printf 'Die Dateisysteme der Quelle sind ausgehängt.\n'
            ;;
        2|allow)
            [[ "$ENGINE" != fsarchiver ]] || die "fsarchiver benötigt eine ausgehängte Partition."
            MOUNT_ACTION=allow
            printf 'WARNUNG: Das Abbild einer eingehängten Quelle kann inkonsistent sein.\n' ;;
        3|abort) if (( NON_INTERACTIVE )); then die "Quelle ist eingehängt."; fi
            printf 'Abgebrochen.\n'; exit 0 ;;
        *) die "Ungültige Auswahl." ;;
    esac
fi

if [[ "$ENGINE" == dd ]]; then
    if [[ -n "$CLI_COMPRESSION" ]]; then
        compression="$CLI_COMPRESSION"
    elif (( NON_INTERACTIVE )); then
        compression=none
    else
        printf '\nKompression:\n  [1] keine (.img)\n  [2] gzip (.img.gz)\n  [3] zstd (.img.zst)\n'
        read -r -p 'Auswahl [1]: ' compression || die "Keine Kompressionsauswahl erhalten."
    fi
    case "${compression:-1}" in
        1|none) COMP=(cat); DECOMP=(cat); EXT=.img ;;
        2|gzip) ensure_program gzip gzip
            if command -v pigz >/dev/null 2>&1; then COMP=(pigz -c); else COMP=(gzip -c); fi
            DECOMP=(gzip -dc); EXT=.img.gz ;;
        3|zstd) ensure_program zstd zstd
            COMP=(zstd -T0 -c); DECOMP=(zstd -dc); EXT=.img.zst ;;
        *) die "Ungültige Kompressionsauswahl." ;;
    esac
    [[ -z "$AGE_RECIPIENT" ]] || EXT="$EXT.age"
elif [[ "$ENGINE" == ddrescue ]]; then
    EXT=.img
else
    EXT=.fsa
fi

base="${MODEL}_${ENGINE}_$(date +%Y%m%d_%H%M%S)_$$"
IMG="$DEST/$base$EXT"
if [[ -n "$RESUME_IMAGE" ]]; then
    [[ "$RESUME_IMAGE" == /* && -f "$RESUME_IMAGE" && -f "$RESUME_IMAGE.map" ]] \
        || die "Zum Fortsetzen müssen Abbild und DATEI.map existieren."
    IMG="$RESUME_IMAGE"
    [[ "$(cd -- "$(dirname -- "$IMG")" && pwd -P)" == "$DEST" ]] \
        || die "Fortsetzungs-Abbild muss im gewählten Zielordner liegen."
fi
LOG="$DEST/$base.log"
HASHFILE="$DEST/$base.sha256"
MAPFILE="$IMG.map"
for path in "$LOG" "$HASHFILE"; do
    [[ ! -e "$path" && ! -L "$path" ]] || die "Zieldatei existiert bereits: $path"
done
[[ ! -L "$IMG" && ! -L "$MAPFILE" ]] || die "Symbolische Links sind als Abbild oder Map-Datei nicht erlaubt."
[[ -n "$RESUME_IMAGE" || ! -e "$IMG" ]] || die "Zieldatei existiert bereits: $IMG"
if [[ "$ENGINE" == ddrescue && -z "$RESUME_IMAGE" ]]; then
    [[ ! -e "$MAPFILE" ]] || die "Map-Datei existiert bereits: $MAPFILE"
fi

available="$(df --output=avail -B1 "$DEST" | tail -n 1 | tr -d '[:space:]')" || die "Freier Platz konnte nicht ermittelt werden."
[[ "$available" =~ ^[0-9]+$ ]] || die "Ungültige Angabe zum freien Platz."
if [[ "$ENGINE" == ddrescue || ( "$ENGINE" == dd && "$EXT" == .img ) ]] && (( available < SIZE )) && [[ -z "$RESUME_IMAGE" ]]; then
    die "Zu wenig Platz: benötigt $(human "$SIZE"), frei $(human "$available")."
elif (( available < SIZE )); then
    printf 'WARNUNG: Komprimierte Daten können bis zu %s Platz benötigen; frei sind %s.\n' "$(human "$SIZE")" "$(human "$available")"
fi

if [[ "$ENGINE" == dd ]]; then
    if [[ -n "$CLI_ERROR_MODE" ]]; then error_mode="$CLI_ERROR_MODE"
    elif (( NON_INTERACTIVE )); then error_mode=continue
    else
        printf '\nVerhalten bei Lesefehlern:\n  [F] Fortfahren und fehlerhafte Blöcke mit Nullen füllen\n  [A] Beim ersten Fehler abbrechen\n'
        read -r -p 'Auswahl [F/a]: ' error_mode || die "Keine Fehlerauswahl erhalten."
    fi
    case "${error_mode:-F}" in
        [Ff]|continue) DD_CONV=(conv=noerror,sync); BS="$BS_CONTINUE"; MODE='Fortfahren bei Lesefehlern' ;;
        [Aa]|abort) DD_CONV=(); BS="$BS_NORMAL"; MODE='Abbrechen bei Lesefehlern' ;;
        *) die "Ungültige Fehlerauswahl." ;;
    esac
else
    BS=intern
    MODE="$ENGINE"
fi
if [[ -n "$AGE_RECIPIENT" && -z "$AGE_IDENTITY" && "$VERIFY_MODE" != no ]]; then
    if (( NON_INTERACTIVE )); then die "Für verschlüsselte Abbilder sind --age-identity oder --no-verify nötig."; fi
    if ask_yn 'Ohne Identitätsdatei ist die Inhaltsprüfung unmöglich. Überspringen?' N; then
        VERIFY_MODE=no
    else
        die "Private Identitätsdatei für die Inhaltsprüfung fehlt."
    fi
fi

printf '\n=== Zusammenfassung ===\n  Quelle: %s (%s)\n  Abbild: %s\n  Modus:  %s\n' "$SRC" "$(human "$SIZE")" "$IMG" "$MODE"
if (( ! NON_INTERACTIVE )); then
    ask_yn 'Sicherung starten?' N || { printf 'Abgebrochen.\n'; exit 0; }
fi

# Direkt vor dem Lauf erneut prüfen, ob sich Quelle oder Ziel verändert haben.
current_dest_device="$(findmnt -n -o MAJ:MIN -T "$DEST")" || die "Zielgerät ist nicht mehr verfügbar."
[[ "$current_dest_device" == "$dest_device" ]] || die "Das Zielgerät hat sich seit der Auswahl geändert."
current_source_devices="$(lsblk -nr -o MAJ:MIN "$SRC")" || die "Quelle ist nicht mehr verfügbar."
current_source_size="$(blockdev --getsize64 "$SRC")" || die "Quellgröße ist nicht mehr verfügbar."
current_source_serial="$(lsblk -dn -o SERIAL "$SRC" 2>/dev/null)"
[[ "$current_source_devices" == "$source_devices" && "$current_source_size" == "$SIZE" && "$current_source_serial" == "$source_serial" ]] \
    || die "Die Quelle hat sich seit der Auswahl geändert."
source_mounts
if [[ "$ENGINE" == fsarchiver ]] && (( ${#SOURCE_MOUNTS[@]} > 0 )); then
    die "Die fsarchiver-Quelle wurde zwischenzeitlich eingehängt."
fi
if [[ "$MOUNT_ACTION" != allow ]] && (( ${#SOURCE_MOUNTS[@]} > 0 )); then
    die "Die Quelle wurde zwischenzeitlich eingehängt; Abbruch."
fi

# Temporäre Prüfdateien bleiben vom Abbild getrennt. Bei dd liefert eine
# benannte Pipe den Hash der gelesenen Rohdaten; der Prozess wird abgewartet.
TEMP_DIR="$(mktemp -d)" || die "Temporärer Ordner konnte nicht erstellt werden."
set -C # Vorhandene Zieldateien niemals durch eine parallele Ausführung ersetzen.
: > "$LOG" || die "Protokoll konnte nicht erstellt werden."
LOG_READY=1
RUN_STARTED=1
log "Start: Quelle=$SRC, Größe=$SIZE Bytes, Ziel=$IMG, Modus=$MODE, Blockgröße=$BS"
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "$SRC" >> "$LOG" 2>&1
if command -v sfdisk >/dev/null 2>&1; then
    sfdisk -d "$SRC" >> "$LOG" 2>&1 || log "WARNUNG: Partitionstabelle konnte nicht protokolliert werden."
fi
if (( SMART_CHECKED )); then
    printf '\n--- SMART-Kurzstatus ---\n%s\n' "$SMART_TEXT" >> "$LOG"
fi

# Der entfernte Pfad ist zuvor auf einfache Zeichen beschränkt. Die entfernte
# Shell überschreibt dank set -C keine vorhandene Datei. Anschließend werden
# die lokalen und entfernten SHA256-Werte verglichen.
send_remote_file() {
    local local_file="$1" remote_file="$2" expected actual
    local -a ssh_opts=()
    if (( NON_INTERACTIVE )); then ssh_opts=(-o BatchMode=yes); fi
    expected="$(sha256sum -- "$local_file" | awk '{print $1}')"
    ssh "${ssh_opts[@]}" "$SSH_HOST" "set -C; cat > '$remote_file'" < "$local_file" \
        || die "SSH-Kopie fehlgeschlagen; lokale Datei bleibt erhalten: $local_file"
    actual="$(ssh "${ssh_opts[@]}" "$SSH_HOST" "sha256sum '$remote_file'" | awk '{print $1}')" \
        || die "Entfernte Prüfsumme konnte nicht gelesen werden."
    [[ "$actual" == "$expected" ]] || die "SSH-Kopie hat eine abweichende Prüfsumme: $remote_file"
    log "SSH-Kopie und Prüfsumme OK: $SSH_HOST:$remote_file"
}

# ddrescue arbeitet auf einer direkt beschreibbaren Datei. fsarchiver erzeugt
# ein Dateisystemarchiv einer Partition. Beide haben daher eigene Prüfwege.
if [[ "$ENGINE" != dd ]]; then
    start="$(date +%s)"
    result=0
    if [[ "$ENGINE" == ddrescue ]]; then
        log "ddrescue-Map-Datei: $MAPFILE"
        ddrescue -n "$SRC" "$IMG" "$MAPFILE" >> "$LOG" 2>&1 \
            || log "WARNUNG: Der erste ddrescue-Durchlauf meldete Probleme."
        ddrescue -r3 "$SRC" "$IMG" "$MAPFILE" >> "$LOG" 2>&1 \
            || log "WARNUNG: ddrescue konnte nicht alle Bereiche lesen."
        [[ -f "$IMG" && -f "$MAPFILE" ]] || die "ddrescue hat kein vollständiges Abbild/keine Map-Datei erstellt."
        if ! ddrescuelog -D "$MAPFILE" >/dev/null 2>&1; then
            log "WARNUNG: Die Map-Datei enthält noch nicht vollständig gerettete Bereiche."
            result=3
        fi
    else
        fsarchiver savefs "$IMG" "$SRC" >> "$LOG" 2>&1 \
            || die "fsarchiver fehlgeschlagen; unvollständiges Archiv bleibt erhalten."
    fi
    sync -f "$IMG" || die "Abbild konnte nicht zuverlässig geschrieben werden."
    sha256sum -- "$IMG" > "$HASHFILE" || die "Datei-Prüfsumme konnte nicht erstellt werden."
    log "Dauer: $(( $(date +%s) - start )) Sekunden; Größe: $(human "$(stat -c %s "$IMG")")"
    if [[ -z "$VERIFY_MODE" ]]; then
        if ask_yn 'Archiv/Map-Datei prüfen?' J; then VERIFY_MODE=yes; else VERIFY_MODE=no; fi
    fi
    if [[ "$VERIFY_MODE" == yes ]]; then
        if [[ "$ENGINE" == fsarchiver ]]; then
            fsarchiver archinfo "$IMG" >> "$LOG" 2>&1 \
                || { log "Archiv-Metadatenprüfung fehlgeschlagen."; exit 2; }
            log "Archiv-Metadaten lesbar; eine Wiederherstellung wurde nicht getestet."
        else
            log "Map-Datei ausgewertet; die defekte Quelle wurde zur Prüfung nicht erneut gelesen."
        fi
    fi
    if [[ -n "$SSH_HOST" ]]; then
        send_remote_file "$IMG" "$SSH_PATH"
        if [[ "$ENGINE" == ddrescue ]]; then send_remote_file "$MAPFILE" "$SSH_PATH.map"; fi
    fi
    printf '\nAbbild: %s\nLog:    %s\nSHA256: %s\n' "$IMG" "$LOG" "$HASHFILE"
    if [[ "$ENGINE" == ddrescue ]]; then printf 'Map:    %s\n' "$MAPFILE"; fi
    exit "$result"
fi

mkfifo -- "$TEMP_DIR/raw.fifo" || die "Hash-Pipe konnte nicht erstellt werden."

sha256sum < "$TEMP_DIR/raw.fifo" > "$TEMP_DIR/raw.sha256" &
HASH_PID=$!
start="$(date +%s)"

# dd meldet Lesefehler auf stderr. Bei conv=noerror,sync kann es trotzdem 0
# zurückgeben; deshalb wird auch die Fehlermeldung geprüft.
if [[ -n "$AGE_RECIPIENT" ]]; then
    LC_ALL=C dd if="$SRC" bs="$BS" iflag=fullblock "${DD_CONV[@]}" status=none 2> "$TEMP_DIR/dd.stderr" \
        | pv -petrab -s "$SIZE" -N "$(basename "$SRC")" \
        | tee "$TEMP_DIR/raw.fifo" \
        | "${COMP[@]}" 2> "$TEMP_DIR/comp.stderr" \
        | age -r "$AGE_RECIPIENT" > "$IMG" 2> "$TEMP_DIR/age.stderr"
    pipeline_rc=("${PIPESTATUS[@]}")
else
    LC_ALL=C dd if="$SRC" bs="$BS" iflag=fullblock "${DD_CONV[@]}" status=none 2> "$TEMP_DIR/dd.stderr" \
        | pv -petrab -s "$SIZE" -N "$(basename "$SRC")" \
        | tee "$TEMP_DIR/raw.fifo" \
        | "${COMP[@]}" > "$IMG" 2> "$TEMP_DIR/comp.stderr"
    pipeline_rc=("${PIPESTATUS[@]}")
fi
wait "$HASH_PID"
hash_rc=$?
HASH_PID=""
cat -- "$TEMP_DIR/dd.stderr" "$TEMP_DIR/comp.stderr" >> "$LOG"
[[ ! -f "$TEMP_DIR/age.stderr" ]] || cat -- "$TEMP_DIR/age.stderr" >> "$LOG"
log "Dauer: $(( $(date +%s) - start )) Sekunden; Status: Pipeline=${pipeline_rc[*]}, Hash=$hash_rc"

if (( hash_rc != 0 )); then
    die "Hash-Berechnung fehlgeschlagen; unvollständige Datei bleibt erhalten: $IMG"
fi
for status in "${pipeline_rc[@]}"; do
    if (( status != 0 )); then
    die "Abbild-Erstellung fehlgeschlagen. Unvollständige Datei zur Diagnose erhalten: $IMG"
    fi
done
sync -f "$IMG" || die "Abbild konnte nicht zuverlässig auf das Zielmedium geschrieben werden."
source_hash="$(awk '{print $1}' "$TEMP_DIR/raw.sha256")"
[[ "$source_hash" =~ ^[0-9a-f]{64}$ ]] || die "Quell-Hash fehlt oder ist ungültig."

# Eine standardmäßige .sha256-Datei prüft die gespeicherte Datei selbst,
# einschließlich Kompressionsformat. Der Rohdaten-Hash steht im Protokoll.
sha256sum -- "$IMG" > "$HASHFILE" || die "Datei-Prüfsumme konnte nicht erstellt werden."
log "SHA256 der gelesenen Rohdaten: $source_hash"
log "Abbildgröße: $(human "$(stat -c %s "$IMG")")"

if [[ -z "$VERIFY_MODE" ]]; then
    if ask_yn 'Abbild jetzt durch erneutes Lesen verifizieren?' J; then VERIFY_MODE=yes; else VERIFY_MODE=no; fi
fi
if [[ "$VERIFY_MODE" == yes ]]; then
    if [[ -n "$AGE_RECIPIENT" ]]; then
        age -d -i "$AGE_IDENTITY" < "$IMG" 2> "$TEMP_DIR/verify-age.stderr" \
            | "${DECOMP[@]}" 2> "$TEMP_DIR/verify.stderr" \
            | pv -petrab -s "$SIZE" -N verify \
            | sha256sum > "$TEMP_DIR/verify.sha256"
        verify_rc=("${PIPESTATUS[@]}")
        cat -- "$TEMP_DIR/verify-age.stderr" >> "$LOG"
    else
        "${DECOMP[@]}" < "$IMG" 2> "$TEMP_DIR/verify.stderr" \
            | pv -petrab -s "$SIZE" -N verify \
            | sha256sum > "$TEMP_DIR/verify.sha256"
        verify_rc=("${PIPESTATUS[@]}")
    fi
    cat -- "$TEMP_DIR/verify.stderr" >> "$LOG"
    verified_hash="$(awk '{print $1}' "$TEMP_DIR/verify.sha256")"
    verify_failed=0
    for status in "${verify_rc[@]}"; do (( status == 0 )) || verify_failed=1; done
    if (( verify_failed )) || [[ "$verified_hash" != "$source_hash" ]]; then
        log "VERIFIKATION FEHLGESCHLAGEN; Abbild zur Diagnose erhalten."
        exit 2
    fi
    log "VERIFIKATION OK: Gespeicherter Inhalt stimmt mit dem gelesenen Datenstrom überein."
else
    log "Verifikation auf Wunsch übersprungen."
fi

if [[ -s "$TEMP_DIR/dd.stderr" ]]; then
    log "WARNUNG: dd meldete Lesefehler oder andere Hinweise; Details stehen im Protokoll."
    result=3
else
    log "Fertig: keine Lesefehler von dd gemeldet."
    result=0
fi
if [[ -n "$SSH_HOST" ]]; then send_remote_file "$IMG" "$SSH_PATH"; fi
printf '\nAbbild: %s\nLog:    %s\nSHA256: %s\n' "$IMG" "$LOG" "$HASHFILE"
exit "$result"

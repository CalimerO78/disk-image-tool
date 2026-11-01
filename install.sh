#!/usr/bin/env bash
# Einmalige Einrichtung nach git clone: GUI-Voraussetzungen und Startmenü.
# Als normaler Desktop-Benutzer ausführen, niemals mit sudo.

set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    printf 'FEHLER: Diese Installation ist für Linux vorgesehen.\n' >&2
    exit 1
fi
if [[ "$EUID" -eq 0 ]]; then
    printf 'FEHLER: Bitte ohne sudo als normaler Benutzer ausführen.\n' >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
missing=()
if ! command -v python3 >/dev/null 2>&1; then
    missing+=(python3 python3-tk)
elif ! python3 -c 'import tkinter' >/dev/null 2>&1; then
    missing+=(python3-tk)
fi
command -v pkexec >/dev/null 2>&1 || missing+=(pkexec)

if (( ${#missing[@]} > 0 )); then
    printf 'Für die GUI fehlen: %s\n' "${missing[*]}"
    if [[ ! -f /etc/debian_version ]] || ! command -v apt-get >/dev/null 2>&1; then
        printf 'Bitte die Pakete mit dem Paketmanager deiner Distribution installieren und danach install.sh erneut starten.\n' >&2
        exit 1
    fi
    read -r -p 'Fehlende Pakete jetzt automatisch installieren? [J/n]: ' answer || answer='J'
    case "${answer:-J}" in
        J|j|Y|y)
            sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -- "${missing[@]}"
            ;;
        *) printf 'Installation abgebrochen; Startsymbole wurden nicht angelegt.\n'; exit 1 ;;
    esac
fi

python3 "$script_dir/install-launchers.py" --install
printf '\nFertig. Im Anwendungsmenü nach „Datenträger sichern“ suchen.\n'
printf 'Der zweite Eintrag „Datenträger sichern (Terminal)“ öffnet die interaktive CLI und ihre Hilfe.\n'

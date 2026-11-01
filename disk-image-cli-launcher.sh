#!/usr/bin/env bash
# Öffnet die interaktive CLI aus dem Anwendungsmenü in einem Terminal.
# Die Administratorrechte werden erst für den wirklichen Sicherungslauf geholt.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
script="$script_dir/disk-image.sh"
if [[ ! -f "$script" ]]; then
    printf 'Das Sicherungsskript fehlt: %s\n' "$script" >&2
    read -r -p 'Enter zum Schließen … ' _
    exit 1
fi

while true; do
    printf '\nDatenträger sichern – Kommandozeile\n'
    printf '  [1] Interaktive Sicherung starten\n'
    printf '  [2] Hilfe und Parameter anzeigen\n'
    printf '  [3] Terminal schließen\n'
    read -r -p 'Auswahl [1]: ' choice || exit 0
    case "${choice:-1}" in
        1)
            if command -v sudo >/dev/null 2>&1; then
                sudo bash "$script"
                status=$?
                printf '\nSicherung beendet (Status %s).\n' "$status"
            else
                printf 'sudo fehlt. Bitte Administratorrechte im Terminal einrichten.\n' >&2
            fi
            read -r -p 'Enter zurück zum Menü … ' _ || exit 0
            ;;
        2)
            bash "$script" --help
            read -r -p 'Enter zurück zum Menü … ' _ || exit 0
            ;;
        3) exit 0 ;;
        *) printf 'Bitte 1, 2 oder 3 eingeben.\n' ;;
    esac
done

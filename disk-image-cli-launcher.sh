#!/usr/bin/env bash
# Öffnet die interaktive CLI aus dem Anwendungsmenü in einem Terminal.
# Die Administratorrechte werden erst für den wirklichen Sicherungslauf geholt.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
script="$script_dir/disk-image.sh"
restore_tool="$script_dir/disk-image-restore.py"
if [[ ! -f "$script" ]]; then
    printf 'Das Sicherungsskript fehlt: %s\n' "$script" >&2
    read -r -p 'Enter zum Schließen … ' _
    exit 1
fi

while true; do
    printf '\nDatenträger sichern – Kommandozeile\n'
    printf '  [1] Interaktive Sicherung starten\n'
    printf '  [2] Hilfe und Parameter anzeigen\n'
    printf '  [3] Abbild prüfen, entpacken oder zurückspielen\n'
    printf '  [4] Terminal schließen\n'
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
        3)
            printf '  [1] Abbild prüfen\n  [2] Als .img entpacken\n  [3] Auf Datenträger zurückspielen\n'
            read -r -p 'Auswahl: ' restore_choice || exit 0
            read -r -p 'Abbilddatei (vollständiger Pfad): ' restore_image || exit 0
            read -r -p 'age-Identitätsdatei, falls verschlüsselt (sonst leer): ' restore_identity || exit 0
            identity_args=()
            [[ -z "$restore_identity" ]] || identity_args=(--identity "$restore_identity")
            case "$restore_choice" in
                1) python3 "$restore_tool" --plan --image "$restore_image" "${identity_args[@]}" ;;
                2)
                    read -r -p 'Neue .img-Datei (vollständiger Pfad): ' restore_output || exit 0
                    python3 "$restore_tool" --extract --image "$restore_image" \
                        --output "$restore_output" "${identity_args[@]}" ;;
                3)
                    read -r -p 'Zielgerät (z. B. /dev/sdb): ' restore_target || exit 0
                    sudo python3 "$restore_tool" --restore --image "$restore_image" \
                        --target "$restore_target" "${identity_args[@]}" ;;
                *) printf 'Ungültige Auswahl.\n' ;;
            esac
            read -r -p 'Enter zurück zum Menü … ' _ || exit 0
            ;;
        4) exit 0 ;;
        *) printf 'Bitte 1, 2, 3 oder 4 eingeben.\n' ;;
    esac
done

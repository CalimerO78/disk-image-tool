#!/usr/bin/env bash
# Eigentum der von diesem Lauf erzeugten Dateien an sudo-/pkexec-Aufrufer geben.
# Erwartet die Variablen IMG, LOG, HASHFILE, MAPFILE und RESUME_IMAGE.

release_outputs_to_caller() {
    local uid="${PKEXEC_UID:-${SUDO_UID:-}}" account gid path current_uid
    local -a outputs=()
    [[ "$uid" =~ ^[1-9][0-9]*$ ]] || return 0
    account="$(getent passwd "$uid")" || {
        printf 'WARNUNG: Benutzer-ID %s ist unbekannt; Ausgaben bleiben bei root.\n' "$uid" >&2
        return 0
    }
    IFS=: read -r _ _ _ gid _ <<< "$account"
    [[ "$gid" =~ ^[0-9]+$ ]] || return 0
    [[ -z "$IMG" ]] || outputs+=("$IMG")
    [[ -z "$LOG" ]] || outputs+=("$LOG")
    [[ -z "$HASHFILE" ]] || outputs+=("$HASHFILE")
    [[ -z "$MAPFILE" ]] || outputs+=("$MAPFILE")
    for path in "${outputs[@]}"; do
        [[ -f "$path" && ! -L "$path" ]] || continue
        current_uid="$(stat -c %u -- "$path" 2>/dev/null)" || continue
        if [[ -n "$RESUME_IMAGE" && ( "$path" == "$IMG" || "$path" == "$MAPFILE" ) &&
              "$current_uid" != 0 && "$current_uid" != "$uid" ]]; then
            printf 'WARNUNG: Eigentümer der vorhandenen Datei bleibt unverändert: %s\n' "$path" >&2
            continue
        fi
        if ! chown -- "$uid:$gid" "$path"; then
            printf 'WARNUNG: Besitzerwechsel nicht möglich: %s\n' "$path" >&2
        elif ! chmod 600 -- "$path"; then
            printf 'WARNUNG: Dateirechte konnten nicht gesetzt werden: %s\n' "$path" >&2
        fi
    done
}

#!/usr/bin/env bash
# pv-Ausgabe für die GUI: numerische Bytezähler mit Kennzeichnung und Flush.
# Die GUI berechnet die Rate aus der Zeit zwischen zwei Messpunkten.

gui_pv() {
    local kind="$1"
    shift
    pv -n -b -i 0.2 "$@" 2> >(
        awk -v kind="$kind" '
            $1 ~ /^[0-9]+$/ && NF == 1 {
                printf "@@DISK_IMAGE_PROGRESS {\"kind\":\"%s\",\"bytes\":%s}\n", kind, $1
                fflush()
                next
            }
            { print > "/dev/stderr"; fflush("/dev/stderr") }
        ' >&2
    )
}

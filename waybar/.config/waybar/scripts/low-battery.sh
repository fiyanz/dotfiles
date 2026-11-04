#!/bin/sh
# Notify once per discharge when the battery falls to THRESHOLD or below.
THRESHOLD=20
SYS="${LOW_BAT_SYS:-/sys/class/power_supply}"
STATE="${XDG_RUNTIME_DIR:-/tmp}/low-battery-notified"

for bat in "$SYS"/BAT*; do
    [ -r "$bat/capacity" ] || continue
    capacity=$(cat "$bat/capacity")
    status=$(cat "$bat/status")
    if [ "$status" = "Discharging" ] && [ "$capacity" -le "$THRESHOLD" ]; then
        [ -e "$STATE" ] && exit 0
        notify-send -u critical -i battery-caution -a "Battery" \
            "Low battery" "${capacity}% remaining. Plug in the charger." && touch "$STATE"
    else
        rm -f "$STATE"
    fi
    exit 0
done

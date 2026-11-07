#!/bin/sh
# Show one low battery notification while discharging, updated at 20%, 10% and 5%.
# A dismissed notification returns at the next level. It closes once the battery
# stops discharging.
SYS="${LOW_BAT_SYS:-/sys/class/power_supply}"
STATE="${XDG_RUNTIME_DIR:-/tmp}/low-battery-notified"

id=""
last=""
[ -r "$STATE" ] && read -r id last < "$STATE"

for bat in "$SYS"/BAT*; do
    [ -r "$bat/capacity" ] || continue
    capacity=$(cat "$bat/capacity")
    status=$(cat "$bat/status")

    level=""
    for candidate in 20 10 5; do
        [ "$capacity" -le "$candidate" ] && level=$candidate
    done

    if [ "$status" = "Discharging" ] && [ -n "$level" ]; then
        [ "$level" = "$last" ] && exit 0
        new=$(notify-send -p ${id:+-r "$id"} -u critical -i battery-caution -a "Battery" \
            "Low battery" "Battery is at ${level}% or below. Plug in the charger.") || exit 0
        echo "$new $level" > "$STATE"
    else
        [ -n "$id" ] && makoctl dismiss -n "$id" 2>/dev/null
        rm -f "$STATE"
    fi
    exit 0
done

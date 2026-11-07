#!/bin/sh
# Take a screenshot and confirm it with a notification: screenshot.sh area|window|screen
dir="$HOME/Pictures/Screenshots"
latest() { ls -t "$dir"/*.png 2>/dev/null | head -n 1; }

case "${1:-}" in
    area) exec niri msg action screenshot ;;
    window) action=screenshot-window ;;
    screen) action=screenshot-screen ;;
    *) echo "usage: $0 area|window|screen" >&2; exit 2 ;;
esac

before=$(latest)
sleep 0.3  # let a closing popup hand focus back to the window
niri msg action "$action" || exit 1

tries=0
while [ "$tries" -lt 20 ]; do
    file=$(latest)
    if [ -n "$file" ] && [ "$file" != "$before" ]; then
        notify-send -a Screenshot -i "$file" "Screenshot saved" "${file##*/}"
        exit 0
    fi
    tries=$((tries + 1))
    sleep 0.1
done
notify-send -u critical -a Screenshot "Screenshot failed" "No file was saved."
exit 1

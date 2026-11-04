#!/bin/sh
# Open the newest niri screenshot in satty for annotation.
latest=$(ls -t "$HOME"/Pictures/Screenshots/*.png 2>/dev/null | head -n 1)
if [ -z "$latest" ]; then
    notify-send "Screenshot" "No screenshot to edit yet."
    exit 1
fi
exec satty --filename "$latest" --output-filename "${latest%.png}-edit.png"

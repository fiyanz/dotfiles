#!/bin/sh
# Turn the laptop panel back on if no output is active, e.g. the only
# external monitor was unplugged while the panel was off.
while sleep 3; do
    outputs=$(niri msg -j outputs) || exit 0
    case "$outputs" in
        *'"logical":{'*) ;;
        *)
            internal=$(printf '%s' "$outputs" | grep -oE '"(eDP|LVDS|DSI)-[0-9]+":' | head -n 1 | tr -d '":')
            [ -n "$internal" ] && niri msg output "$internal" on
            ;;
    esac
done

#!/bin/bash
# Run an idle action unless media is playing in the focused window.
#   idle-guard.sh check             exit 0 if media is being watched
#   idle-guard.sh <grace> <cmd...>  run cmd once nothing has been watched for grace seconds
# swayidle kills this script on resume, so any input cancels a pending action.

watching() {
    pidof hyprlock >/dev/null && return 1
    local focused player owner
    focused=$(niri msg -j focused-window 2>/dev/null | jq -r '.pid // empty')
    [ -n "$focused" ] || return 1
    while read -r player; do
        [ "$(playerctl -p "$player" status 2>/dev/null)" = Playing ] || continue
        owner=$(busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus \
            GetConnectionUnixProcessID s "org.mpris.MediaPlayer2.$player" 2>/dev/null | cut -d' ' -f2)
        [ "$owner" = "$focused" ] && return 0
    done < <(playerctl -l 2>/dev/null)
    return 1
}

if [ "$1" = check ]; then
    watching
    exit
fi

grace=$1
shift

# Nothing playing when the idle timeout fired: the usual timeout already passed.
watching || exec "$@"

while :; do
    while watching; do sleep 5; done
    waited=0
    while [ "$waited" -lt "$grace" ] && ! watching; do
        sleep 5
        waited=$((waited + 5))
    done
    [ "$waited" -ge "$grace" ] && exec "$@"
done

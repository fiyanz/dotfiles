#!/bin/sh
# Install packages and link these configs into $HOME. Safe to run repeatedly.
# SKIP_PACKAGES=1 skips pacman.
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
packages="niri waybar mako fuzzel kitty hypr gtk systemd"
backup="$HOME/.config-backup-$(date +%Y%m%d-%H%M%S)"

if [ "${SKIP_PACKAGES:-0}" != 1 ]; then
    command -v pacman >/dev/null || { echo "Requires Arch Linux (pacman)." >&2; exit 1; }
    grep -v '^#' "$repo/packages.txt" | xargs sudo pacman -S --needed
fi
command -v stow >/dev/null || { echo "stow is not installed." >&2; exit 1; }

# Move conflicting real files aside; existing links into this repo are left alone.
for package in $packages; do
    (cd "$repo/$package" && find . -type f) | while IFS= read -r file; do
        target="$HOME/${file#./}"
        if [ -e "$target" ] || [ -L "$target" ]; then
            if [ "$(realpath -- "$target")" != "$repo/$package/${file#./}" ]; then
                mkdir -p "$backup/$(dirname -- "${file#./}")"
                mv -- "$target" "$backup/${file#./}"
            fi
        fi
    done
done

mkdir -p "$HOME/.config/systemd/user"
stow --dir="$repo" --target="$HOME" --no-folding --restow $packages

# Machine-specific files stay out of the repo and are only created when missing.
[ -e "$HOME/.config/niri/outputs.kdl" ] || printf '// Written by the Waybar display panel.\n' > "$HOME/.config/niri/outputs.kdl"
if [ ! -e "$HOME/.config/waypaper/config.ini" ]; then
    mkdir -p "$HOME/.config/waypaper"
    cp "$repo/templates/waypaper-config.ini" "$HOME/.config/waypaper/config.ini"
fi

if command -v gsettings >/dev/null; then
    gsettings set org.gnome.desktop.interface color-scheme prefer-dark || true
    gsettings set org.gnome.desktop.interface gtk-theme adw-gtk3-dark || true
fi

# OnlyOffice draws its own frame with a wide transparent margin; use the system frame
# so its gaps match other windows under niri. OnlyOffice rewrites this file on exit.
onlyoffice="$HOME/.config/onlyoffice/DesktopEditors.conf"
if pgrep -x DesktopEditors >/dev/null; then
    echo "Close OnlyOffice and run install.sh again to set its title bar." >&2
elif [ -f "$onlyoffice" ]; then
    if grep -q '^titlebar=' "$onlyoffice"; then
        sed -i 's/^titlebar=.*/titlebar=system/' "$onlyoffice"
    elif grep -q '^\[General\]' "$onlyoffice"; then
        sed -i '/^\[General\]/a titlebar=system' "$onlyoffice"
    else
        printf '[General]\ntitlebar=system\n' >> "$onlyoffice"
    fi
else
    mkdir -p "$HOME/.config/onlyoffice"
    printf '[General]\ntitlebar=system\n' > "$onlyoffice"
fi

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now low-battery.timer
fi

[ -d "$backup" ] && echo "Old files saved to $backup"
echo "Done. Log out and choose the niri session."

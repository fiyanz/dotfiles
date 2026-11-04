#!/bin/sh
# Install the custom login screen. Takes effect on next boot, or with:
#   sudo systemctl restart greetd   (this ends the running session)
#
# Rollback, from a TTY (Ctrl+Alt+F3):
#   sudo systemctl disable greetd && sudo systemctl enable cosmic-greeter && sudo reboot
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
username="${1:-$(id -un)}"

sudo pacman -S --needed greetd cage python-gobject gtk3 noto-fonts hyprlock
getent passwd greeter >/dev/null || { echo "User 'greeter' does not exist." >&2; exit 1; }

if [ -e /etc/greetd/config.toml ]; then
    sudo cp -a /etc/greetd/config.toml "/etc/greetd/config.toml.bak-$(date +%Y%m%d-%H%M%S)"
fi
sudo install -m 644 "$here/greeter.py" /etc/greetd/greeter.py
printf '%s\n' "$username" | sudo tee /etc/greetd/greeter-user >/dev/null
sudo chmod 644 /etc/greetd/greeter-user
sudo install -m 644 "$here/greetd-config.toml" /etc/greetd/config.toml

if systemctl is-enabled cosmic-greeter.service >/dev/null 2>&1; then
    sudo systemctl disable cosmic-greeter.service
fi
sudo install -m 644 "$here/quiet-console.conf" /etc/sysctl.d/20-quiet-console.conf
sudo install -d /etc/systemd/system/greetd.service.d
sudo install -m 644 "$here/greetd-clear-console.conf" /etc/systemd/system/greetd.service.d/clear-console.conf
sudo systemctl daemon-reload
sudo systemctl enable greetd.service

echo "greetd: $(systemctl is-enabled greetd.service)"
echo "Done. Reboot to see the login screen."

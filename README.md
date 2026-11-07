# niri dotfiles

A [niri](https://github.com/YaLTeR/niri) and Waybar desktop for Arch Linux.

## Contents

- **niri**: layout and keybindings. `outputs.kdl` is per-machine and not stored in this repo.
- **waybar**: bar with a calendar and media popup, plus Notifications, Microphone, Volume, Bluetooth, WiFi, Battery, and Power panels.
- **mako**, **fuzzel**, **kitty**: notifications, launcher, terminal.
- **systemd**: low-battery warning at 20%.

## Install

```sh
git clone <repo-url> ~/Projects/my-projects/dotfiles
cd ~/Projects/my-projects/dotfiles
./install.sh
```

Conflicting files are moved to `~/.config-backup-<timestamp>`. The login screen is not changed. Wallpapers are not included: put images in `~/Pictures/wp` and choose one with `waypaper`. It also sets OnlyOffice to the system title bar, so its gaps match other windows under niri.

## Login screen (optional)

`system/login/apply.sh` installs a minimal custom greetd login screen on the next boot. The rollback command is at the top of that file.

## Extra keybindings

| Key | Action |
|---|---|
| `Mod+P` | Display panel |
| `Mod+Shift+S` | Screenshot menu (area, window, screen) |
| `Super+Alt+L` | Lock screen |
| `Mod+Alt+V` | Clipboard history |
| `Mod+Shift+Print` | Edit latest screenshot |

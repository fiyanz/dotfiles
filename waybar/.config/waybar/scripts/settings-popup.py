#!/usr/bin/env python3
"""Per-icon settings popups for Waybar: settings-popup.py <panel>.

Same panel again closes it; another panel replaces it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from glob import glob
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell

REFRESH_SECONDS = 3
SLIDER_HOLD_US = 1_500_000
SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
MAC_RE = re.compile(r"Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.+)")

CSS = b"""
window { background: transparent; }
* { font-family: "JetBrainsMono Nerd Font", monospace; color: #d0d0d0; font-size: 13px; }
#panel {
    background: rgba(17, 17, 17, 0.97);
    border: 1px solid #333333;
    border-radius: 0;
    padding: 14px;
}
#heading { font-size: 15px; font-weight: bold; color: #ffffff; }
#big { font-size: 26px; font-weight: bold; color: #ffffff; }
#muted { color: #909090; font-size: 12px; }
#notif { background: #1e1e1e; padding: 8px 10px; }
#notif-title { color: #ffffff; font-weight: bold; }
#error { color: #e06060; font-size: 12px; }
button {
    background: #1e1e1e;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 0;
    padding: 7px 10px;
    text-shadow: none;
}
button:hover { background: #333333; }
button.on { background: #ffffff; }
button.on label { color: #111111; font-weight: bold; }
button.warn { background: #7a2e2e; }
entry { background: #1e1e1e; border: 1px solid #333333; border-radius: 0; padding: 6px; box-shadow: none; }
switch { background: #3a3a3a; background-image: none; border: none; border-radius: 0; }
switch:checked { background: #ffffff; }
switch slider { background: #909090; background-image: none; border: none; border-radius: 0; box-shadow: none; }
switch:checked slider { background: #111111; }
scale trough { background: #3a3a3a; min-height: 4px; border-radius: 2px; border: none; }
scale highlight { background: #ffffff; border-radius: 2px; border: none; }
scale slider {
    background: #ffffff; min-width: 12px; min-height: 12px; margin: -5px;
    border-radius: 6px; border: none; box-shadow: none;
}
"""


def label(text="", name=None, **kwargs):
    kwargs.setdefault("xalign", 0)
    return Gtk.Label(label=text, name=name, **kwargs)


def row(*children):
    box = Gtk.Box(spacing=8)
    for child in children:
        box.add(child)
    return box


def clear(box):
    for child in box.get_children():
        box.remove(child)


def run_cmd(cmd):
    """Return (ok, stdout, stderr) without raising."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return done.returncode == 0, done.stdout, done.stderr.strip()
    except FileNotFoundError:
        return False, "", f"{cmd[0]} is not installed"
    except subprocess.TimeoutExpired:
        return False, "", "Timed out"


def nmcli_fields(line):
    parts = re.split(r"(?<!\\):", line)
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in parts]


def add_wifi_with_password(iface, ssid, password):
    """Create and activate a WPA-PSK profile over D-Bus, keeping the password out of argv."""
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
    nm = ("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
          "org.freedesktop.NetworkManager")
    device = bus.call_sync(*nm, "GetDeviceByIpIface", GLib.Variant("(s)", (iface,)),
                           None, Gio.DBusCallFlags.NONE, 10000, None).unpack()[0]
    bus.call_sync(*nm, "AddAndActivateConnection", wifi_settings(ssid, password, device),
                  None, Gio.DBusCallFlags.NONE, 30000, None)


def wifi_settings(ssid, password, device):
    settings = {
        "connection": {"id": GLib.Variant("s", ssid), "type": GLib.Variant("s", "802-11-wireless")},
        "802-11-wireless": {"ssid": GLib.Variant("ay", ssid.encode())},
        "802-11-wireless-security": {"key-mgmt": GLib.Variant("s", "wpa-psk"),
                                     "psk": GLib.Variant("s", password)},
    }
    return GLib.Variant("(a{sa{sv}}oo)", (settings, device, "/"))


class Popup(Gtk.ApplicationWindow):
    # Distance from the right screen edge, roughly under each Waybar icon.
    OFFSETS = {"power": 8, "battery": 8, "wifi": 60, "bluetooth": 150,
               "volume": 170, "mic": 240, "dnd": 310, "display": 350, "screenshot": 0}

    def __init__(self, app):
        super().__init__(application=app, title="settings-popup")
        self.generation = 0
        self.refresh = None
        self.panel_name = None
        self.slider_used = 0

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "settings-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        self.connect("key-press-event", self.on_key)
        self.connect("button-press-event", lambda *_: self.close())

        self.events = Gtk.EventBox(halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=8)
        self.events.connect("button-press-event", lambda *_: True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, name="panel")
        self.box.set_size_request(320, -1)
        self.events.add(self.box)
        self.add(self.events)
        GLib.timeout_add_seconds(REFRESH_SECONDS, self.tick)

    def on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
        return False

    def tick(self):
        if self.refresh:
            self.refresh()
        return True

    def show_panel(self, name):
        self.generation += 1
        self.panel_name = name
        self.refresh = None
        clear(self.box)
        centered = name in ("display", "screenshot")  # opened from the keyboard, not under a bar icon
        self.events.set_halign(Gtk.Align.CENTER if centered else Gtk.Align.END)
        self.events.set_margin_end(0 if centered else self.OFFSETS[name])
        getattr(self, f"build_{name}")()
        self.show_all()
        if self.refresh:
            self.refresh()

    def run(self, cmd, done=None):
        generation = self.generation

        def finish(result):
            if done and generation == self.generation:
                done(*result)
            return False

        def work():
            GLib.idle_add(finish, run_cmd(cmd))

        threading.Thread(target=work, daemon=True).start()

    def run_then_refresh(self, cmd, error_label=None):
        def done(ok, _out, err):
            if error_label is not None:
                error_label.set_text("" if ok else (err or "Failed"))
            if self.refresh:
                self.refresh()
        self.run(cmd, done)

    def header(self, title):
        self.box.add(label(title, "heading"))

    def make_switch(self, on_change):
        switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER, hexpand=True)
        handler = switch.connect("notify::active", lambda s, _: on_change(s.get_active()))

        def set_quiet(value):
            with switch.handler_block(handler):
                switch.set_active(value)

        return switch, set_quiet

    def make_slider(self, on_change):
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=True)
        scale.set_range(0, 100)

        def changed(_, __, value):
            self.slider_used = GLib.get_monotonic_time()
            on_change(max(0, min(100, value)))
            return False

        scale.connect("change-value", changed)

        def set_quiet(value):
            if GLib.get_monotonic_time() - self.slider_used > SLIDER_HOLD_US:
                scale.set_value(value)

        return scale, set_quiet

    # ---- volume / mic ----
    def build_volume(self):
        self.build_audio("Volume", SINK, "sink")

    def build_mic(self):
        self.build_audio("Microphone", SOURCE, "source")

    def build_audio(self, title, target, kind):
        self.header(title)
        slider, set_level = self.make_slider(
            lambda v: self.run(["wpctl", "set-volume", target, f"{v / 100:.2f}"]))
        percent = label(width_chars=4, xalign=1)
        mute = Gtk.Button(label="Mute")
        mute.connect("clicked", lambda _: self.run_then_refresh(
            ["wpctl", "set-mute", target, "toggle"]))
        self.box.add(row(slider, percent, mute))
        self.box.add(label("Devices", "muted"))
        devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.add(devices)
        last = {"sig": None}

        def show_level(ok, out, _err):
            match = re.search(r"([\d.]+)", out) if ok else None
            if match:
                value = float(match[1]) * 100
                set_level(value)
                percent.set_text(f"{value:.0f}%")
            ctx = mute.get_style_context()
            (ctx.add_class if "MUTED" in out else ctx.remove_class)("on")

        def show_devices(default, listing):
            try:
                items = [d for d in json.loads(listing)
                         if not d["name"].endswith(".monitor")]
            except (ValueError, KeyError, TypeError):
                return
            sig = (default, [d["name"] for d in items])
            if sig == last["sig"]:
                return
            last["sig"] = sig
            clear(devices)
            for item in items:
                button = Gtk.Button(label=item.get("description") or item["name"])
                button.get_child().set_ellipsize(3)
                button.get_child().set_xalign(0)
                button.get_child().set_max_width_chars(1)
                if item["name"] == default:
                    button.get_style_context().add_class("on")
                button.connect("clicked", lambda _, n=item["name"]: self.run_then_refresh(
                    ["pactl", f"set-default-{kind}", n]))
                devices.add(button)
            devices.show_all()

        def refresh():
            self.run(["wpctl", "get-volume", target], show_level)
            self.run(["pactl", f"get-default-{kind}"], lambda ok, out, _: self.run(
                ["pactl", "--format=json", "list", f"{kind}s"],
                lambda ok2, listing, _e: ok and ok2 and show_devices(out.strip(), listing)))

        self.refresh = refresh

    # ---- dnd ----
    def build_dnd(self):
        switch, set_on = self.make_switch(lambda _: self.run_then_refresh(
            ["makoctl", "mode", "-t", "do-not-disturb"]))
        self.box.add(row(label("Do not disturb"), switch))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_size_request(-1, 300)
        entries = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.add(entries)
        self.box.add(scroller)
        clear_btn = Gtk.Button(label="Clear all")
        self.box.add(clear_btn)
        state = {"sig": None, "ids": []}

        def hide(ids):
            hide_notifications(ids)
            state["sig"] = None
            refresh()

        def clear_all(_):
            self.run(["makoctl", "dismiss", "--all", "--no-history"],
                     lambda *_r: hide(state["ids"]))

        clear_btn.connect("clicked", clear_all)

        def add_entry(item):
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            texts.add(label(item.get("app_name") or "Notification", "muted"))
            summary = label(item.get("summary") or "", "notif-title", ellipsize=3, max_width_chars=1)
            texts.add(summary)
            if item.get("body"):
                texts.add(label(item["body"], "muted", wrap=True, max_width_chars=1, lines=3, ellipsize=3))
            close = Gtk.Button(label="\uf00d", valign=Gtk.Align.START)
            close.connect("clicked", lambda _, n=item["id"]: self.run(
                ["makoctl", "dismiss", "-n", str(n), "--no-history"], lambda *_r: hide([n])))
            card = Gtk.Box(spacing=8, name="notif")
            card.add(texts)
            card.add(close)
            entries.add(card)

        def show(items):
            hidden = hidden_notifications()
            visible = [i for i in items if i["id"] not in hidden]
            state["ids"] = [i["id"] for i in items]
            clear_btn.set_sensitive(bool(visible))
            sig = [i["id"] for i in visible]
            if sig == state["sig"]:
                return
            state["sig"] = sig
            clear(entries)
            for item in visible:
                add_entry(item)
            if not visible:
                entries.add(label("No notifications", "muted", xalign=0.5, margin_top=20))
            entries.show_all()

        def refresh():
            self.run(["makoctl", "mode"], lambda ok, out, _: ok and set_on("do-not-disturb" in out))
            self.run(["makoctl", "list", "-j"], lambda ok, active, _: self.run(
                ["makoctl", "history", "-j"],
                lambda ok2, past, _e: ok and ok2 and show(merge_notifications(active, past))))

        self.refresh = refresh

    # ---- battery ----
    def build_battery(self):
        self.header("Battery")
        big = label(name="big")
        detail = label(name="muted")
        self.box.add(big)
        self.box.add(detail)
        self.box.add(label("Power mode", "muted"))
        modes = Gtk.Box(spacing=4, homogeneous=True)
        buttons = {}
        for mode, text in (("power-saver", "Saver"), ("balanced", "Balanced"),
                           ("performance", "Performance")):
            button = Gtk.Button(label=text)
            button.connect("clicked", lambda _, m=mode: self.run_then_refresh(
                ["powerprofilesctl", "set", m]))
            buttons[mode] = button
            modes.add(button)
        self.box.add(modes)
        self.box.add(label("Brightness", "muted"))
        has_tool = shutil.which("brightnessctl") is not None
        slider, set_level = self.make_slider(lambda v: self.run(
            ["brightnessctl", "--class=backlight", "set", f"{max(v, 1):.0f}%"]))
        slider.set_sensitive(has_tool)
        self.box.add(slider)
        if not has_tool:
            self.box.add(label("Requires brightnessctl", "muted"))

        def show_mode(ok, out, _):
            for mode, button in buttons.items():
                ctx = button.get_style_context()
                (ctx.add_class if ok and out.strip() == mode else ctx.remove_class)("on")

        def refresh():
            info = read_battery()
            if info:
                big.set_text(f"{info['capacity']}%")
                detail.set_text(info["detail"])
            self.run(["powerprofilesctl", "get"], show_mode)
            level = read_brightness()
            if level is not None:
                set_level(level)

        self.refresh = refresh

    # ---- wifi ----
    def build_wifi(self):
        switch, set_on = self.make_switch(lambda v: self.run_then_refresh(
            ["nmcli", "radio", "wifi", "on" if v else "off"]))
        self.box.add(row(label("WiFi", "heading"), switch))
        status = label(name="muted")
        self.box.add(status)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_size_request(-1, 280)
        networks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.add(networks)
        self.box.add(scroller)
        error = label(name="error", wrap=True)
        self.box.add(error)
        state = {"sig": None, "editing": False, "iface": None}

        def connect(ssid):
            status.set_text(f"Connecting to {ssid}...")
            self.run_then_refresh(["nmcli", "device", "wifi", "connect", ssid], error)

        def connect_password(ssid, password):
            status.set_text(f"Connecting to {ssid}...")
            state["editing"] = False
            state["sig"] = None
            generation = self.generation

            def finish(message):
                if generation == self.generation:
                    error.set_text(message)
                    refresh()
                return False

            def work():
                try:
                    add_wifi_with_password(state["iface"], ssid, password)
                    GLib.idle_add(finish, "")
                except GLib.Error as exc:
                    GLib.idle_add(finish, exc.message)

            threading.Thread(target=work, daemon=True).start()

        def ask_password(holder, ssid):
            state["editing"] = True
            if len(holder.get_children()) > 1:
                return
            entry = Gtk.Entry(visibility=False, placeholder_text="Password")
            entry.connect("activate", lambda e: connect_password(ssid, e.get_text()))
            holder.add(entry)
            holder.show_all()
            entry.grab_focus()

        def add_network(active, ssid, signal, secure, known):
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lock = " " if secure else ""
            button = Gtk.Button(label=f"{lock}{ssid}  {signal}%")
            button.get_child().set_xalign(0)
            button.get_child().set_ellipsize(3)
            button.get_child().set_max_width_chars(1)
            if active:
                button.get_style_context().add_class("on")

            def clicked(_):
                error.set_text("")
                if active:
                    self.run_then_refresh(["nmcli", "connection", "down", "id", ssid], error)
                elif secure and not known:
                    ask_password(holder, ssid)
                else:
                    connect(ssid)

            button.connect("clicked", clicked)
            holder.add(button)
            networks.add(holder)

        def show(ok, out, _, known):
            if not ok or state["editing"]:
                return
            best = {}
            for line in out.splitlines():
                fields = nmcli_fields(line)
                if len(fields) < 4 or not fields[1]:
                    continue
                active, ssid, signal, security = fields[:4]
                entry = (active == "*", int(signal or 0), security not in ("", "--"))
                if ssid not in best or entry[:2] > best[ssid][:2]:
                    best[ssid] = entry
            ordered = sorted(best.items(), key=lambda kv: (not kv[1][0], -kv[1][1]))
            current = next((s for s, v in ordered if v[0]), None)
            status.set_text(f"Connected to {current}" if current else "Not connected")
            sig = [(s, v[0], v[1] // 10) for s, v in ordered]
            if sig == state["sig"]:
                return
            state["sig"] = sig
            clear(networks)
            for ssid, (active, signal, secure) in ordered:
                add_network(active, ssid, signal, secure, ssid in state.get("known", ()))
            networks.show_all()

        def refresh():
            def radio(ok, out, _):
                if ok:
                    on = out.strip() == "enabled"
                    set_on(on)
                    if not on:
                        state["sig"] = None
                        clear(networks)
                        status.set_text("WiFi is off")

            def devices(ok, out, _):
                for line in out.splitlines() if ok else ():
                    name, _sep, kind = line.partition(":")
                    if kind == "wifi":
                        state["iface"] = name
                        break

            def saved(ok, out, _):
                if ok:
                    state["known"] = {nmcli_fields(l)[0] for l in out.splitlines()
                                      if l.endswith(":802-11-wireless")}
                self.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                          "device", "wifi", "list", "--rescan", "no"],
                         lambda *r: show(*r, None))

            self.run(["nmcli", "radio", "wifi"], radio)
            self.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], devices)
            self.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], saved)

        self.refresh = refresh
        self.run(["nmcli", "device", "wifi", "rescan"])

    # ---- bluetooth ----
    def build_bluetooth(self):
        switch, set_on = self.make_switch(lambda v: self.run_then_refresh(
            ["bluetoothctl", "power", "on" if v else "off"]))
        self.box.add(row(label("Bluetooth", "heading"), switch))
        status = label(name="muted")
        self.box.add(status)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_size_request(-1, 220)
        devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.add(devices)
        self.box.add(scroller)
        scan = Gtk.Button(label="Scan for devices")
        self.box.add(scan)
        error = label(name="error", wrap=True)
        self.box.add(error)
        state = {"sig": None}

        def start_scan(_):
            scan.set_sensitive(False)
            scan.set_label("Scanning...")

            def done(*_r):
                scan.set_sensitive(True)
                scan.set_label("Scan for devices")
                refresh()

            self.run(["bluetoothctl", "--timeout", "10", "scan", "on"], done)

        scan.connect("clicked", start_scan)

        def pair(mac):
            status.set_text("Pairing...")

            def trusted(ok, _o, err):
                if ok:
                    self.run_then_refresh(["bluetoothctl", "connect", mac], error)
                else:
                    error.set_text(err or "Failed")

            def paired(ok, _o, err):
                if ok:
                    self.run(["bluetoothctl", "trust", mac], trusted)
                else:
                    error.set_text(err or "Pairing failed")
                    refresh()

            self.run(["bluetoothctl", "pair", mac], paired)

        def add_device(mac, name, connected, is_paired):
            prefix = "" if is_paired else "+ "
            button = Gtk.Button(label=f"{prefix}{name}")
            button.get_child().set_xalign(0)
            button.get_child().set_ellipsize(3)
            button.get_child().set_max_width_chars(1)
            if connected:
                button.get_style_context().add_class("on")

            def clicked(_):
                error.set_text("")
                if connected:
                    self.run_then_refresh(["bluetoothctl", "disconnect", mac], error)
                elif is_paired:
                    status.set_text(f"Connecting to {name}...")
                    self.run_then_refresh(["bluetoothctl", "connect", mac], error)
                else:
                    pair(mac)

            button.connect("clicked", clicked)
            devices.add(button)

        def show(all_out, paired_out, conn_out):
            parse = lambda text: dict(m.groups() for m in map(MAC_RE.search, text.splitlines()) if m)
            everything, paired, connected = parse(all_out), parse(paired_out), parse(conn_out)
            status.set_text(f"{len(connected)} connected")
            nameless = re.compile(r"^(?:[0-9A-F]{2}-){5}[0-9A-F]{2}$")
            items = [(mac, name) for mac, name in everything.items()
                     if mac in paired or not nameless.match(name)]
            items.sort(key=lambda d: (d[0] not in connected, d[0] not in paired, d[1]))
            sig = [(m, n, m in connected, m in paired) for m, n in items]
            if sig == state["sig"]:
                return
            state["sig"] = sig
            clear(devices)
            for args in sig:
                add_device(*args)
            devices.show_all()

        def refresh():
            def powered(ok, out, err):
                if not ok:
                    error.set_text(err or "Bluetooth unavailable")
                    return
                on = "Powered: yes" in out
                set_on(on)
                scan.set_sensitive(on)
                if not on:
                    state["sig"] = None
                    clear(devices)
                    status.set_text("Bluetooth is off")
                    return
                self.run(["bluetoothctl", "devices"], lambda ok1, a, _: self.run(
                    ["bluetoothctl", "devices", "Paired"], lambda ok2, p, _: self.run(
                        ["bluetoothctl", "devices", "Connected"],
                        lambda ok3, c, _: ok1 and ok2 and ok3 and show(a, p, c))))

            self.run(["bluetoothctl", "show"], powered)

        self.refresh = refresh

    # ---- display ----
    def build_display(self):
        self.header("Display")
        status = label(name="muted", wrap=True)
        self.box.add(status)
        layouts = Gtk.Grid(row_spacing=4, column_spacing=4, column_homogeneous=True)
        self.box.add(layouts)
        buttons = {}
        for index, (key, text) in enumerate((
                ("left", "Extend left"), ("right", "Extend right"), ("mirror", "Mirror"),
                ("external", "External only"), ("laptop", "Laptop only"))):
            button = Gtk.Button(label=text)
            button.connect("clicked", lambda _, k=key: self.apply_display(
                lambda: apply_layout(k, state["internal"], state["external"]), error))
            buttons[key] = button
            layouts.attach(button, index % 2, index // 2, 1, 1)
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.add(settings)
        save = Gtk.Button(label="Save for next login")
        save.connect("clicked", lambda _: self.apply_display(save_outputs, error, "Saved"))
        self.box.add(save)
        error = label(name="error", wrap=True)
        self.box.add(error)
        state = {"internal": None, "external": None, "sig": None}
        self.display_error = error

        def add_output_settings(name, output):
            settings.add(label(name, "muted"))
            modes = Gtk.ComboBoxText(hexpand=True)
            for mode in output["modes"]:
                modes.append(mode_string(mode), f"{mode['width']}x{mode['height']} "
                                                f"{mode['refresh_rate'] / 1000:.0f}Hz")
            if output["current_mode"] is not None:
                modes.set_active(output["current_mode"])
            modes.connect("changed", lambda c: c.get_active_id() and self.apply_display(
                lambda: output_cmd(name, "mode", c.get_active_id()), error))
            scales = Gtk.ComboBoxText()
            current = output["logical"]["scale"] if output["logical"] else 1.0
            values = sorted({1.0, 1.25, 1.5, 1.75, 2.0, current})
            for value in values:
                scales.append(str(value), f"{value * 100:.0f}%")
            scales.set_active_id(str(current))
            scales.connect("changed", lambda c: c.get_active_id() and self.apply_display(
                lambda: output_cmd(name, "scale", c.get_active_id()), error))
            settings.add(row(modes, scales))
            sensitive = output["logical"] is not None
            modes.set_sensitive(sensitive)
            scales.set_sensitive(sensitive)

        def show(result):
            ok, out, err = result
            if not ok:
                error.set_text(err or "Could not read monitors")
                return
            outputs = json.loads(out)
            internal = next((n for n in sorted(outputs) if is_internal(n)), None)
            external = next((n for n in sorted(outputs) if not is_internal(n)), None)
            state.update(internal=internal, external=external)
            has_pair = bool(internal and external)
            for button in buttons.values():
                button.set_sensitive(has_pair)
            status.set_text(f"{len(outputs)} monitor: {', '.join(sorted(outputs))}"
                            if has_pair else "Only one monitor connected. "
                                             "Plug in a monitor to choose a layout.")
            active = None
            if has_pair:
                a, b = outputs[internal]["logical"], outputs[external]["logical"]
                if a and not b:
                    active = "laptop"
                elif b and not a:
                    active = "external"
                elif a and b:
                    active = "right" if b["x"] >= a["x"] + a["width"] else "left"
                    if run_cmd(["pgrep", "-x", "wl-mirror"])[0]:
                        active = "mirror"
            for key, button in buttons.items():
                ctx = button.get_style_context()
                (ctx.add_class if key == active else ctx.remove_class)("on")
            sig = [(n, o["current_mode"], bool(o["logical"]),
                    o["logical"] and o["logical"]["scale"]) for n, o in sorted(outputs.items())]
            if sig != state["sig"]:
                state["sig"] = sig
                clear(settings)
                for name, output in sorted(outputs.items()):
                    add_output_settings(name, output)
                settings.show_all()

        def refresh():
            generation = self.generation

            def finish(result):
                if generation == self.generation:
                    show(result)
                return False

            threading.Thread(target=lambda: GLib.idle_add(
                finish, run_cmd(["niri", "msg", "-j", "outputs"])), daemon=True).start()

        self.refresh = refresh

    def apply_display(self, action, error, success=""):
        generation = self.generation

        def finish(message):
            if generation == self.generation:
                error.set_text(message)
                if self.refresh:
                    self.refresh()
            return False

        def work():
            try:
                action()
                GLib.idle_add(finish, success)
            except (RuntimeError, OSError, ValueError, KeyError) as exc:
                GLib.idle_add(finish, str(exc))

        threading.Thread(target=work, daemon=True).start()

    # ---- screenshot ----
    def build_screenshot(self):
        self.header("Screenshot")
        options = Gtk.Box(spacing=4, homogeneous=True)
        script = str(Path(__file__).resolve().with_name("screenshot.sh"))

        def take(_, mode):
            subprocess.Popen([script, mode], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.close()

        for text, mode in (("Area", "area"), ("Window", "window"), ("Screen", "screen")):
            button = Gtk.Button(label=text)
            button.connect("clicked", take, mode)
            options.add(button)
        self.box.add(options)

    # ---- power ----
    def build_power(self):
        self.header("Power")
        error = label(name="error", wrap=True)
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.add(grid)
        self.box.add(error)
        actions = (
            ("  Lock", ["hyprlock"], False),
            ("  Log out", ["niri", "msg", "action", "quit", "--skip-confirmation"], True),
            ("  Suspend", ["systemctl", "suspend"], True),
            ("  Restart", ["systemctl", "reboot"], True),
            ("  Shut down", ["systemctl", "poweroff"], True),
        )

        def launch(cmd):
            if shutil.which(cmd[0]) is None:
                error.set_text(f"{cmd[0]} is not installed")
                return
            subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.close()

        pending = {"button": None, "text": None}

        def reset_pending():
            if pending["button"]:
                pending["button"].set_label(pending["text"])
                pending["button"].get_child().set_xalign(0)
                pending["button"].get_style_context().remove_class("warn")
                pending["button"] = None

        def clicked(button, text, cmd, confirm):
            if not confirm or pending["button"] is button:
                launch(cmd)
                return
            reset_pending()
            pending.update(button=button, text=text)
            button.set_label("Click again to confirm")
            button.get_child().set_xalign(0)
            button.get_style_context().add_class("warn")

        for text, cmd, confirm in actions:
            button = Gtk.Button(label=text)
            button.get_child().set_xalign(0)
            button.connect("clicked", clicked, text, cmd, confirm)
            grid.add(button)


def niri_outputs():
    ok, out, err = run_cmd(["niri", "msg", "-j", "outputs"])
    if not ok:
        raise RuntimeError(err or "Could not read monitors")
    return json.loads(out)


def is_internal(name):
    return name.startswith(("eDP", "LVDS", "DSI"))


def output_cmd(name, *action):
    ok, _out, err = run_cmd(["niri", "msg", "output", name, *action])
    if not ok:
        raise RuntimeError(err or f"Could not configure {name}")


def logical_width(output):
    if output["logical"]:
        return output["logical"]["width"]
    index = output["current_mode"]
    mode = output["modes"][index if index is not None else 0]
    return mode["width"]


def apply_layout(layout, internal, external):
    """Apply a layout; the panel that stays on is enabled before the other turns off."""
    run_cmd(["pkill", "-x", "wl-mirror"])
    if layout == "laptop":
        output_cmd(internal, "on")
        output_cmd(external, "off")
    elif layout == "external":
        output_cmd(external, "on")
        if not niri_outputs()[external]["logical"]:
            raise RuntimeError("Could not turn on the external monitor")
        output_cmd(internal, "off")
    else:
        output_cmd(internal, "on")
        output_cmd(external, "on")
        outputs = niri_outputs()
        if layout == "right":
            output_cmd(internal, "position", "set", "0", "0")
            output_cmd(external, "position", "set", str(logical_width(outputs[internal])), "0")
        elif layout == "left":
            output_cmd(external, "position", "set", "0", "0")
            output_cmd(internal, "position", "set", str(logical_width(outputs[external])), "0")
        elif layout == "mirror":
            if shutil.which("wl-mirror") is None:
                raise RuntimeError("wl-mirror is not installed")
            subprocess.Popen(["wl-mirror", "--fullscreen-output", external, internal],
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def mode_string(mode):
    return f"{mode['width']}x{mode['height']}@{mode['refresh_rate'] / 1000:.3f}"


def save_outputs():
    """Persist the live layout to niri/outputs.kdl with an atomic replace."""
    blocks = []
    for name, output in sorted(niri_outputs().items()):
        lines = [f'output "{name}" {{']
        logical = output["logical"]
        if not logical:
            lines.append("    off")
        else:
            if output["current_mode"] is not None:
                lines.append(f'    mode "{mode_string(output["modes"][output["current_mode"]])}"')
            lines.append(f"    scale {logical['scale']}")
            lines.append(f"    position x={logical['x']} y={logical['y']}")
        lines.append("}")
        blocks.append("\n".join(lines))
    path = Path.home() / ".config/niri/outputs.kdl"
    temp = path.with_suffix(".kdl.tmp")
    temp.write_text("\n".join(blocks) + "\n")
    os.replace(temp, path)


def merge_notifications(active, past):
    """Active notifications and history as one newest-first list without duplicates."""
    items = {}
    for text in (past, active):
        for item in json.loads(text):
            items[item["id"]] = item
    return [items[key] for key in sorted(items, reverse=True)]


def hidden_path():
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "waybar-hidden-notifications.json"


def mako_pid():
    return run_cmd(["pidof", "mako"])[1].strip()


def hidden_notifications():
    """mako cannot clear its history, so cleared ids are remembered per mako process."""
    try:
        data = json.loads(hidden_path().read_text())
        return set(data["ids"]) if data["pid"] == mako_pid() else set()
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def hide_notifications(ids):
    data = {"pid": mako_pid(), "ids": sorted(hidden_notifications() | set(ids))}
    hidden_path().write_text(json.dumps(data))


def read_battery():
    paths = glob("/sys/class/power_supply/BAT*")
    if not paths:
        return None
    bat = Path(paths[0])

    def value(name):
        try:
            return (bat / name).read_text().strip()
        except OSError:
            return None

    status = value("status") or "Unknown"
    names = {"Charging": "Charging", "Discharging": "Discharging",
             "Full": "Full", "Not charging": "Not charging"}
    detail = names.get(status, status)
    now = value("energy_now") or value("charge_now")
    full = value("energy_full") or value("charge_full")
    rate = value("power_now") or value("current_now")
    if now and full and rate and int(rate) > 0:
        hours = None
        if status == "Discharging":
            hours = int(now) / int(rate)
        elif status == "Charging":
            hours = (int(full) - int(now)) / int(rate)
        if hours is not None:
            suffix = "remaining" if status == "Discharging" else "until full"
            detail += f" - {int(hours)}h {int(hours * 60) % 60}m {suffix}"
    return {"capacity": value("capacity") or "?", "detail": detail}


def read_brightness():
    paths = glob("/sys/class/backlight/*")
    if not paths:
        return None
    try:
        base = Path(paths[0])
        return int((base / "brightness").read_text()) * 100 / int((base / "max_brightness").read_text())
    except (OSError, ValueError, ZeroDivisionError):
        return None


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.waybar.SettingsPopup",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.popup = None

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        name = args[1] if len(args) > 1 else ""
        if name not in Popup.OFFSETS:
            command_line.printerr_literal(f"Panel: {', '.join(Popup.OFFSETS)}\n")
            return 2
        if self.popup and self.popup.panel_name == name:
            self.quit()
            return 0
        if not self.popup:
            self.popup = Popup(self)
        self.popup.show_panel(name)
        return 0


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

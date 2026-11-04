#!/usr/bin/env python3
"""Minimal greetd login screen: clock, user name, password, power actions."""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

USER_FILE = Path("/etc/greetd/greeter-user")
SESSION_CMD = ["niri-session"]
FADE_MS = 280

CSS = b"""
window { background: #0c0c0c; transition: background-color 280ms ease-out; }
window.leaving { background: #000000; }
* { font-family: "Noto Sans", sans-serif; color: #e8e8e8; }
#clock { font-size: 104px; font-weight: 200; letter-spacing: 2px; }
#date { font-size: 17px; font-weight: 300; color: #8a8a8a; letter-spacing: 1px; }
#user { font-size: 15px; font-weight: 400; color: #bdbdbd; letter-spacing: 1px; }
entry {
    background: #161616;
    background-image: none;
    border: 1px solid #2a2a2a;
    border-radius: 10px;
    box-shadow: none;
    outline: none;
    padding: 11px 14px;
    font-size: 15px;
    caret-color: #ffffff;
}
entry:focus { border-color: #6a6a6a; }
entry.failed { border-color: #a85a5a; }
#message { font-size: 13px; color: #c98a8a; }
button {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    text-shadow: none;
    border-radius: 8px;
    padding: 6px 12px;
}
button label { font-size: 13px; color: #7a7a7a; }
button:hover { background: #1a1a1a; }
button:hover label { color: #e8e8e8; }
"""


class LoginError(Exception):
    pass


def send(sock, request):
    payload = json.dumps(request).encode()
    sock.sendall(struct.pack("=I", len(payload)) + payload)


def receive_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise LoginError("Login service disconnected")
        data += chunk
    return data


def receive(sock):
    size = struct.unpack("=I", receive_exact(sock, 4))[0]
    return json.loads(receive_exact(sock, size))


def login(socket_path, username, password, command, on_authenticated=None):
    """Run the greetd handshake. Raises LoginError if login is refused.

    on_authenticated runs after the password is accepted, before the session starts.
    """
    with socket.socket(socket.AF_UNIX) as sock:
        sock.connect(socket_path)
        send(sock, {"type": "create_session", "username": username})
        while True:
            reply = receive(sock)
            kind = reply.get("type")
            if kind == "auth_message":
                request = {"type": "post_auth_message_response"}
                if reply.get("auth_message_type") in ("secret", "visible"):
                    request["response"] = password
                send(sock, request)
            elif kind == "success":
                if on_authenticated:
                    on_authenticated()
                send(sock, {"type": "start_session", "cmd": command, "env": []})
                reply = receive(sock)
                if reply.get("type") != "success":
                    raise LoginError(reply.get("description") or "Could not start session")
                return
            else:
                send(sock, {"type": "cancel_session"})
                if reply.get("error_type") == "auth_error":
                    raise LoginError("Wrong password")
                raise LoginError(reply.get("description") or "Login failed")


def read_username():
    try:
        return USER_FILE.read_text().strip()
    except OSError:
        return os.environ.get("USER", "")


class Greeter(Gtk.Window):
    def __init__(self):
        super().__init__(title="greeter")
        self.username = read_username()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        self.column = column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        column.set_size_request(320, -1)
        self.clock = Gtk.Label(name="clock")
        self.date = Gtk.Label(name="date", margin_bottom=56)
        name = Gtk.Label(label=self.username, name="user", margin_bottom=12)
        self.password = Gtk.Entry(visibility=False, placeholder_text="Password",
                                  input_purpose=Gtk.InputPurpose.PASSWORD)
        self.password.connect("activate", self.submit)
        self.message = Gtk.Label(name="message", margin_top=10, wrap=True, justify=Gtk.Justification.CENTER)
        power = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_top=18)
        for text, command in (("Suspend", "suspend"), ("Restart", "reboot"), ("Shut down", "poweroff")):
            button = Gtk.Button(label=text, can_focus=False)
            button.connect("clicked", lambda _, c=command: self.power(c))
            power.add(button)
        for widget in (self.clock, self.date, name, self.password, self.message, power):
            column.add(widget)
        self.add(column)

        self.tick()
        GLib.timeout_add_seconds(1, self.tick)
        self.fullscreen()
        self.show_all()
        self.password.grab_focus()

    def tick(self):
        now = datetime.now()
        self.clock.set_text(now.strftime("%H:%M"))
        self.date.set_text(now.strftime("%A, %-d %B %Y"))
        return True

    def power(self, command):
        subprocess.Popen(["systemctl", command], start_new_session=True)

    def submit(self, entry):
        password = entry.get_text()
        if not password:
            return
        socket_path = os.environ.get("GREETD_SOCK")
        if not socket_path:
            self.fail("Preview mode: login is disabled")
            return
        entry.set_sensitive(False)
        self.message.set_text("")

        def work():
            try:
                login(socket_path, self.username, password, SESSION_CMD, self.wait_for_fade)
                GLib.idle_add(Gtk.main_quit)
            except (LoginError, OSError, ValueError, struct.error) as exc:
                GLib.idle_add(self.fail, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def wait_for_fade(self):
        """Fade to black on the UI thread; blocks the login thread until finished."""
        done = threading.Event()
        GLib.idle_add(self.fade_out, done)
        done.wait(FADE_MS / 1000 + 1)

    def fade_out(self, done):
        self.get_style_context().add_class("leaving")
        start = GLib.get_monotonic_time()

        def step():
            progress = (GLib.get_monotonic_time() - start) / (FADE_MS * 1000)
            self.column.set_opacity(max(0.0, 1.0 - progress))
            if progress >= 1:
                done.set()
                return False
            return True

        GLib.timeout_add(16, step)
        return False

    def fail(self, message):
        self.message.set_text(message)
        self.password.get_style_context().add_class("failed")
        self.password.set_sensitive(True)
        self.password.set_text("")
        self.password.grab_focus()
        return False


def main():
    window = Greeter()
    window.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())

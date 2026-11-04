#!/usr/bin/env python3
"""GNOME-style clock popup for Waybar: calendar with media controls below it.

Running it again while open closes it (Gtk.Application forwards to the first instance).
"""
import math
import threading
import urllib.request
from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Playerctl", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Playerctl

ART_SIZE = 88
MAX_ART_BYTES = 10 * 1024 * 1024

CSS = b"""
window { background: transparent; }
* { font-family: "JetBrainsMono Nerd Font", monospace; color: #d0d0d0; }
#panel {
    background: rgba(17, 17, 17, 0.97);
    border: 1px solid #333333;
    border-radius: 14px;
    padding: 16px;
}
#weekday { font-size: 13px; color: #909090; }
#date { font-size: 20px; font-weight: bold; color: #ffffff; }
calendar {
    background: transparent;
    font-size: 14px;
    padding: 4px;
    border: none;
}
calendar:selected { background: #ffffff; color: #111111; border-radius: 6px; }
calendar.header, calendar.button { background: transparent; border: none; box-shadow: none; }
calendar:indeterminate { color: #555555; }
#media { background: #1e1e1e; border-radius: 12px; padding: 12px; }
#title { font-size: 14px; font-weight: bold; color: #ffffff; }
#artist { font-size: 12px; color: #a0a0a0; }
#time { font-size: 11px; color: #a0a0a0; }
#media button {
    background: transparent;
    border: none;
    box-shadow: none;
    border-radius: 18px;
    padding: 6px;
    min-width: 24px;
    min-height: 24px;
}
#media button:hover { background: #333333; }
#media button:disabled image { color: #555555; }
scale trough { background: #3a3a3a; min-height: 4px; border-radius: 2px; border: none; }
scale highlight { background: #ffffff; border-radius: 2px; border: none; }
scale slider {
    background: #ffffff;
    min-width: 10px;
    min-height: 10px;
    margin: -4px;
    border-radius: 5px;
    border: none;
    box-shadow: none;
}
"""


def fmt_time(us):
    total = max(int(us // 1_000_000), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Popup(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="calendar-media")
        self.player = None
        self.player_key = None
        self.art = None
        self.art_url = None

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "calendar-media")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        # Cover the screen below the bar so a click outside the panel closes it.
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

        panel_events = Gtk.EventBox(halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=8)
        panel_events.connect("button-press-event", lambda *_: True)  # keep inside clicks open
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, name="panel")
        panel.set_size_request(380, -1)
        panel_events.add(panel)
        self.add(panel_events)

        now = datetime.now()
        panel.add(Gtk.Label(label=now.strftime("%A"), name="weekday", xalign=0))
        panel.add(Gtk.Label(label=now.strftime("%-d %B %Y"), name="date", xalign=0))
        panel.add(Gtk.Calendar())
        panel.add(self.build_media())

        self.show_all()
        self.media.set_no_show_all(True)
        self.refresh()
        GLib.timeout_add_seconds(1, self.refresh)

    def build_media(self):
        self.media = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="media")

        top = Gtk.Box(spacing=12)
        self.art_area = Gtk.DrawingArea()
        self.art_area.set_size_request(ART_SIZE, ART_SIZE)
        self.art_area.connect("draw", self.draw_art)
        top.add(self.art_area)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       valign=Gtk.Align.CENTER, hexpand=True)
        self.title = Gtk.Label(name="title", xalign=0, ellipsize=3, max_width_chars=1, hexpand=True)
        self.artist = Gtk.Label(name="artist", xalign=0, ellipsize=3, max_width_chars=1, hexpand=True)
        info.add(self.title)
        info.add(self.artist)

        controls = Gtk.Box(spacing=4, halign=Gtk.Align.START)
        self.prev_btn = self.control("media-skip-backward-symbolic", lambda p: p.previous())
        self.play_btn = self.control("media-playback-start-symbolic", lambda p: p.play_pause())
        self.next_btn = self.control("media-skip-forward-symbolic", lambda p: p.next())
        for button in (self.prev_btn, self.play_btn, self.next_btn):
            controls.add(button)
        info.add(controls)
        top.add(info)
        self.media.add(top)

        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False)
        self.scale.set_range(0, 1)
        self.scale.connect("change-value", self.on_seek)  # fires only on user input
        self.media.add(self.scale)

        times = Gtk.Box()
        self.pos_label = Gtk.Label(name="time")
        self.len_label = Gtk.Label(name="time")
        times.pack_start(self.pos_label, False, False, 0)
        times.pack_end(self.len_label, False, False, 0)
        self.media.add(times)
        return self.media

    def control(self, icon, action):
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR))

        def clicked(_):
            if self.player:
                try:
                    action(self.player)
                except GLib.Error:
                    pass
                GLib.timeout_add(150, self.refresh_once)

        button.connect("clicked", clicked)
        return button

    def refresh_once(self):
        self.refresh()
        return False

    def on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
        return False

    def on_seek(self, _, __, value):
        if self.player:
            try:
                self.player.set_position(int(value))
            except GLib.Error:
                pass
        return False

    def pick_player(self):
        players = []
        for name in Playerctl.list_players():
            try:
                player = (self.player if name.instance == self.player_key
                          else Playerctl.Player.new_from_name(name))
                players.append((name.instance, player))
            except GLib.Error:
                continue
        if not players:
            return None, None
        for key, player in players:
            if player.props.playback_status == Playerctl.PlaybackStatus.PLAYING:
                return key, player
        for key, player in players:
            if key == self.player_key:
                return key, player
        return players[0]

    def refresh(self):
        try:
            self.player_key, self.player = self.pick_player()
            if not self.player:
                self.media.hide()
                return True
            self.update_media(self.player)
            self.media.show()
        except GLib.Error:
            self.media.hide()
        return True

    def update_media(self, player):
        self.title.set_text(player.get_title() or player.props.player_name or "Unknown")
        self.artist.set_text(player.get_artist() or "")
        playing = player.props.playback_status == Playerctl.PlaybackStatus.PLAYING
        self.play_btn.get_image().set_from_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic",
            Gtk.IconSize.LARGE_TOOLBAR)
        self.prev_btn.set_sensitive(player.props.can_go_previous)
        self.next_btn.set_sensitive(player.props.can_go_next)
        self.play_btn.set_sensitive(player.props.can_play or player.props.can_pause)

        length = 0
        try:
            length = int(player.print_metadata_prop("mpris:length") or 0)
        except (GLib.Error, ValueError):
            pass
        position = 0
        try:
            position = player.get_position()
        except GLib.Error:
            pass
        self.scale.set_range(0, max(length, 1))
        self.scale.set_value(position)
        self.scale.set_sensitive(length > 0 and player.props.can_seek)
        self.pos_label.set_text(fmt_time(position))
        self.len_label.set_text(fmt_time(length) if length else "--:--")

        try:
            url = player.print_metadata_prop("mpris:artUrl")
        except GLib.Error:
            url = None
        self.load_art(url)

    def load_art(self, url):
        if url == self.art_url:
            return
        self.art_url = url
        self.art = None
        self.art_area.queue_draw()
        if not url:
            return
        if url.startswith("file://"):
            try:
                path = GLib.filename_from_uri(url)[0]
                self.set_art(url, GdkPixbuf.Pixbuf.new_from_file(path))
            except GLib.Error:
                pass
        elif url.startswith(("http://", "https://")):
            threading.Thread(target=self.fetch_art, args=(url,), daemon=True).start()

    def fetch_art(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = response.read(MAX_ART_BYTES)
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            GLib.idle_add(self.set_art, url, loader.get_pixbuf())
        except (OSError, GLib.Error):
            pass

    def set_art(self, url, pixbuf):
        if url != self.art_url or pixbuf is None:
            return False
        # Center-crop to a square so non-square thumbnails aren't distorted.
        w, h = pixbuf.get_width(), pixbuf.get_height()
        side = min(w, h)
        square = pixbuf.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
        self.art = square.scale_simple(ART_SIZE, ART_SIZE, GdkPixbuf.InterpType.BILINEAR)
        self.art_area.queue_draw()
        return False

    def draw_art(self, _, cr):
        s, r = ART_SIZE, 10
        cr.new_sub_path()
        cr.arc(s - r, r, r, -math.pi / 2, 0)
        cr.arc(s - r, s - r, r, 0, math.pi / 2)
        cr.arc(r, s - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if self.art:
            Gdk.cairo_set_source_pixbuf(cr, self.art, 0, 0)
        else:
            cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.fill()
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.waybar.CalendarMedia",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.popup = None

    def do_activate(self):
        if self.popup:  # second launch: toggle closed
            self.quit()
            return
        self.popup = Popup(self)


if __name__ == "__main__":
    App().run(None)

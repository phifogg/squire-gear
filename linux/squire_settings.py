#!/usr/bin/env python3
"""Squire settings window for Linux (GTK 4 + libadwaita).

Devices on the left; the selected device drawn in the middle with clickable zones and callouts; the values for
the selected zone on the right. This version only shows the settings it reads from the mouse
(squire.read_mouse_settings, read-only). Changing them comes in a later step, with an explicit Apply.

Runs as its own process because the tray is GTK 3 (AppIndicator) and GTK 3 and 4 cannot share a process.
"""
import sys
import threading
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
try:
    import squire
except ImportError:  # running from the repository: squire.py lives one folder up
    sys.path.insert(0, str(HERE.parent))
    import squire
sys.path.insert(0, str(HERE))
from squire_tray import unsnap_environment  # noqa: E402  (pure function, does not load GTK)

APP_ID = "io.github.phifogg.Squire.Settings"
SCREENSHOT: list = []   # set by --screenshot PATH [ZONE]
DEMO = False            # --demo: show the captures from protocol.json instead of asking the devices

MODE_NAMES = {"static": "Static", "breathing": "Breathing", "color-cycle": "Color cycle", "rainbow": "Rainbow",
              "react": "React", "comet": "Comet", "battery": "Battery", "off": "Off"}
ZONE_TITLES = {"wheel": "Scroll wheel light", "logo": "Logo light", "side": "Side light",
               "dpi": "DPI stages (button on the underside)", "sensor": "Sensor", "power": "Power"}
ZONE_KINDS = {"wheel": "Lighting", "logo": "Lighting", "side": "Lighting", "dpi": "Performance",
              "sensor": "Performance", "power": "Battery"}

# Stage layout in px (same as the design proposal): drawing, hotspots, callouts, connector lines.
STAGE_W, STAGE_H = 660, 660
DRAWING = (230, 110, 200, 370)
# Only what is visible from above gets a hotspot. The DPI button is on the underside of the Keris II Origin,
# so DPI is a chip below the drawing, next to sensor and power.
HOTSPOTS = {"wheel": (314, 148, 32, 58), "logo": (296, 322, 68, 68), "side": (226, 278, 32, 140)}
CALLOUTS = {"wheel": (470, 152), "logo": (470, 332), "side": (48, 324)}
LINES = {"wheel": (346, 176, 124), "logo": (364, 356, 106), "side": (198, 348, 28)}
CALLOUT_TITLES = {"wheel": "Scroll wheel", "logo": "Logo", "side": "Side"}
CHIPS = (("dpi", "DPI"), ("sensor", "Sensor"), ("power", "Power"))


# --- pure helpers (tested without GTK) ---------------------------------------------------------------------

def mode_name(mode: str) -> str:
    return MODE_NAMES.get(mode, mode)


def shows_color(mode: str) -> bool:
    """Only one-colour effects have a colour to show; cycle, rainbow and battery pick their own colours."""
    return mode in ("static", "breathing", "react", "comet")


def zone_summary(s: squire.MouseSettings, zone: str) -> str:
    """Short value under a callout or chip."""
    if zone in squire.LED_ZONES:
        led = s.leds.get(zone)
        if not led:
            return "not read"
        return "Off" if led["mode"] == "off" else f"{mode_name(led['mode'])} · {led['brightness']} %"
    if zone == "dpi":
        return " / ".join(str(d) for d in s.dpi[:s.dpi_stage_count or 4]) + " DPI" if s.dpi else "not read"
    if zone == "sensor":
        return f"{s.polling_hz} Hz · lift-off {s.liftoff}" if s.polling_hz else "not read"
    if zone == "power":
        off = "never off" if s.power_off_minutes is None else f"off after {s.power_off_minutes} min"
        return f"{off} · warn at {s.low_warning} %"
    return ""


def _led_paint(led: Optional[dict], gradient_id: str) -> tuple[str, float]:
    """SVG fill and opacity for a zone, as the mouse would roughly look."""
    if not led or led["mode"] == "off" or led["brightness"] == 0:
        return "none", 0.0
    opacity = round(0.35 + led["brightness"] / 100 * 0.65, 2)
    if led["mode"] in ("color-cycle", "rainbow"):
        return f"url(#{gradient_id})", opacity
    if led["mode"] == "battery":
        return "#3ccf7a", opacity
    return led["color"], opacity


def mouse_svg(s: squire.MouseSettings) -> str:
    """Top view of the mouse (own neutral artwork, no ASUS marks) with the three light zones."""
    wheel, wheel_o = _led_paint(s.leds.get("wheel"), "cycle")
    logo, logo_o = _led_paint(s.leds.get("logo"), "cycle")
    side, side_o = _led_paint(s.leds.get("side"), "cycle-v")
    body = ("M100 12 C 150 12 182 44 188 114 C 194 184 196 254 186 304 C 176 344 140 358 100 358 "
            "C 60 358 24 344 14 304 C 4 254 6 184 12 114 C 18 44 50 12 100 12 Z")
    strip = "M13 178 C 7 220 7 262 15 300"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="200" height="370" viewBox="0 0 200 370">
<defs>
<filter id="glow" x="-60%" y="-20%" width="220%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
<linearGradient id="body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2f37"/><stop offset="1" stop-color="#1c2026"/></linearGradient>
<linearGradient id="cycle" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff5a5a"/><stop offset="0.5" stop-color="#3ccf7a"/><stop offset="1" stop-color="#3d8bff"/></linearGradient>
<linearGradient id="cycle-v" x1="0" y1="178" x2="0" y2="300" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#ff5a5a"/><stop offset="0.5" stop-color="#3ccf7a"/><stop offset="1" stop-color="#3d8bff"/></linearGradient>
</defs>
<path d="{body}" fill="url(#body)" stroke="#3a404a" stroke-width="1.5"/>
<path d="{strip}" fill="none" stroke="{side}" stroke-width="12" stroke-linecap="round" opacity="{side_o}" filter="url(#glow)"/>
<path d="{strip}" fill="none" stroke="{side}" stroke-width="4" stroke-linecap="round" opacity="{side_o}"/>
<path d="M100 14 V 134" stroke="#0f1115" stroke-width="2"/>
<path d="M14 146 C 60 158 140 158 186 146" fill="none" stroke="#0f1115" stroke-width="2"/>
<rect x="89" y="46" width="22" height="46" rx="11" fill="#14171b" stroke="#3a404a"/>
<rect x="93" y="52" width="14" height="34" rx="7" fill="{wheel}" opacity="{wheel_o}"/>
<path d="M100 222 L 122 246 L 100 270 L 78 246 Z" fill="{logo}" opacity="{logo_o}"/>
<path d="M100 232 L 112 246 L 100 260 L 88 246 Z" fill="#1c2026"/>
</svg>"""


def demo_data() -> tuple[dict, "squire.MouseSettings"]:
    """Batteries and mouse settings from the real captures in protocol.json (sends nothing)."""
    import json
    root = HERE.parent if (HERE.parent / "protocol.json").exists() else HERE
    data = json.loads((root / "protocol.json").read_text())
    s = squire.MouseSettings("ROG Keris II Origin", "omni")
    battery = next(v for v in data["vectors"] if v["device"] == "mouse" and v["report_id"] == 3)
    squire.parse_mouse_settings(s, "battery", bytes.fromhex(battery["reply"])[1:])
    for capture in data["mouse_settings"]["captures"]:
        squire.parse_mouse_settings(s, capture["command"], bytes.fromhex(capture["reply"])[1:])
    batteries = {"mouse": squire.Battery("mouse", s.name, "omni", 80, low_warning=20),
                 "keyboard": squire.Battery("keyboard", "ROG Falchion RX LP", "omni", 78, low_warning=20)}
    return batteries, s


def swatch_svg(color: str) -> str:
    if color in ("#000000", "none"):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"><circle cx="18" cy="18" r="15" '
                'fill="none" stroke="#7a828e" stroke-width="2" stroke-dasharray="4 3"/></svg>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"><circle cx="18" cy="18" r="15" '
            f'fill="{color}" stroke="#00000033" stroke-width="1"/></svg>')


CSS = """
.squire-stage { background: alpha(@view_fg_color, 0.03); }
.squire-hotspot { min-width: 0; min-height: 0; padding: 0; background: transparent; box-shadow: none;
  border: 2px dashed alpha(@view_fg_color, 0.45); border-radius: 12px; }
.squire-hotspot:hover { background: alpha(@accent_bg_color, 0.12); }
.squire-hotspot.selected { border: 2px solid @accent_bg_color; background: alpha(@accent_bg_color, 0.16); }
.squire-callout { padding: 8px 10px; border-radius: 8px; }
.squire-callout.selected { outline: 2px solid @accent_bg_color; outline-offset: -2px; }
.squire-line { min-height: 0; border-top: 1px dashed alpha(@view_fg_color, 0.45); }
.squire-line.selected { border-top: 1px solid @accent_bg_color; }
"""


# --- GTK app -------------------------------------------------------------------------------------------------

def run(argv: list[str]) -> int:
    unsnap_environment()
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    def texture(svg: str):
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(svg.encode()))

    def row(title: str, value: str, subtitle: str = "", suffix=None):
        r = Adw.ActionRow(title=title, subtitle=subtitle)
        r.set_subtitle_selectable(True)
        if suffix is not None:
            r.add_suffix(suffix)
        label = Gtk.Label(label=value)
        label.set_selectable(True)
        r.add_suffix(label)
        return r

    def swatch(color: str):
        image = Gtk.Image.new_from_paintable(texture(swatch_svg(color)))
        image.set_pixel_size(20)
        image.set_valign(Gtk.Align.CENTER)
        return image

    class Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Squire", default_width=1320, default_height=800)
            self.settings: Optional[squire.MouseSettings] = None
            self.batteries: dict = {}
            self.selected = "logo"
            self.device = "mouse"
            self.loading = False

            split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)
            split.set_sidebar(self.build_sidebar())
            self.content_view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read again")
            self.refresh_button.connect("clicked", lambda _: self.refresh())
            header.pack_start(self.refresh_button)
            self.content_view.add_top_bar(header)
            self.banner = Adw.Banner(title="Read-only preview: Squire shows the mouse settings but does not change "
                                           "them yet.", revealed=True)
            self.content_view.add_top_bar(self.banner)
            split.set_content(Adw.NavigationPage(title="Settings", child=self.content_view))
            self.set_content(split)
            self.show_status("Reading the mouse…", "Squire reads the current settings. This takes about a second.",
                             spinner=True)
            self.refresh()

        # sidebar -------------------------------------------------------------------------------------------
        def build_sidebar(self):
            self.device_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
            self.device_rows = {}
            for device, icon, name in (("mouse", "input-mouse-symbolic", "ROG Keris II Origin"),
                                       ("keyboard", "input-keyboard-symbolic", "ROG Falchion RX LP")):
                r = Adw.ActionRow(title=name, subtitle="…")
                r.add_prefix(Gtk.Image(icon_name=icon))
                r.device = device
                self.device_list.append(r)
                self.device_rows[device] = r
            self.device_list.connect("row-selected", self.on_device)
            self.device_list.select_row(self.device_rows["mouse"])
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar(show_title=True))
            toolbar.set_content(self.device_list)
            return Adw.NavigationPage(title="Devices", child=toolbar)

        def on_device(self, _box, r):
            if r is None:
                return
            self.device = r.device
            self.render()

        def update_sidebar(self):
            for device, r in self.device_rows.items():
                b = self.batteries.get(device)
                if b is None:
                    text = "not found"
                elif b.percent is None:
                    text = {"asleep": "asleep", "no-access": "no access"}.get(b.status, b.status)
                else:
                    text = f"{'Wired' if b.connection == 'wired' else 'Omni receiver'} · {b.percent} %" + \
                           (" · charging" if b.charging else "")
                if device == "keyboard":
                    text += " · settings later"
                r.set_subtitle(text)

        # reading -------------------------------------------------------------------------------------------
        def refresh(self):
            if self.loading:
                return
            self.loading = True
            self.refresh_button.set_sensitive(False)

            def worker():
                try:
                    if DEMO:
                        batteries, settings = demo_data()
                    else:
                        batteries = {b.device: b for b in squire.read_all()}
                        settings = squire.read_mouse_settings()
                except Exception as error:  # keep the window alive whatever the device does
                    print(f"squire-settings: read failed: {error}", file=sys.stderr)
                    batteries, settings = {}, squire.MouseSettings("ROG Keris II Origin", "", status="error")
                GLib.idle_add(self.apply, batteries, settings)

            threading.Thread(target=worker, name="squire-read", daemon=True).start()

        def apply(self, batteries, settings):
            self.loading = False
            self.refresh_button.set_sensitive(True)
            self.batteries, self.settings = batteries, settings
            self.update_sidebar()
            self.render()
            if SCREENSHOT:
                GLib.timeout_add(800, self.capture)
            return False

        def capture(self):
            """--screenshot PATH [ZONE]: save the window as PNG and quit (development aid)."""
            path, zone = SCREENSHOT[0], (SCREENSHOT[1:] or [None])[0]
            if zone and zone != self.selected:
                self.select(zone)
                GLib.timeout_add(400, self.capture)
                return False
            w, h = self.get_width(), self.get_height()
            snapshot = Gtk.Snapshot()
            Gtk.WidgetPaintable.new(self).snapshot(snapshot, w, h)
            self.get_renderer().render_texture(snapshot.to_node(), None).save_to_png(path)
            print(f"saved {path}", file=sys.stderr)
            self.get_application().quit()
            return False

        # content -------------------------------------------------------------------------------------------
        def show_status(self, title, description, icon="input-mouse-symbolic", spinner=False):
            page = Adw.StatusPage(title=title, description=description)
            if spinner:
                page.set_paintable(Adw.SpinnerPaintable.new(page))
            else:
                page.set_icon_name(icon)
            self.content_view.set_content(page)

        def render(self):
            if self.device == "keyboard":
                b = self.batteries.get("keyboard")
                battery = f"Battery {b.percent} %." if b and b.percent is not None else ""
                self.show_status("Keyboard settings come later",
                                 f"{battery} Squire starts with the mouse; the keyboard follows.",
                                 icon="input-keyboard-symbolic")
                return
            s = self.settings
            if s is None:
                return
            if s.status != "ok":
                texts = {
                    "asleep": ("The mouse is asleep", "Move the mouse, then read again."),
                    "no-response": ("No answer from the mouse", "Move the mouse, then read again."),
                    "no-access": ("No access to the receiver", "Install the udev rule (see the README), then "
                                                              "re-plug the receiver."),
                    "not-found": ("Mouse not found", "Plug in the Omni receiver or the USB cable."),
                }
                title, text = texts.get(s.status, ("Could not read the mouse", s.status))
                self.show_status(title, text)
                return
            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            stage_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                              vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, hexpand=True)
            stage = self.build_stage(s)
            stage.set_halign(Gtk.Align.CENTER)
            stage_scroll.set_child(stage)
            stage_scroll.add_css_class("squire-stage")
            body.append(stage_scroll)
            body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            inspector = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False)
            inspector.set_size_request(360, -1)
            inspector.set_child(self.build_inspector(s))
            body.append(inspector)
            self.content_view.set_content(body)

        def select(self, zone):
            self.selected = zone
            self.render()

        def build_stage(self, s):
            fixed = Gtk.Fixed()
            fixed.set_size_request(STAGE_W, STAGE_H)
            title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            title.append(Gtk.Label(label=s.name, xalign=0, css_classes=["title-2"]))
            b = self.batteries.get("mouse")
            battery = f" · battery {b.percent} %" if b and b.percent is not None else ""
            title.append(Gtk.Label(label=f"{'Omni receiver' if s.connection == 'omni' else 'Wired'}{battery} · "
                                         f"profile {s.profile} · firmware {s.firmware}",
                                   xalign=0, css_classes=["dim-label"]))
            fixed.put(title, 32, 24)

            x, y, w, h = DRAWING
            drawing = Gtk.Picture.new_for_paintable(texture(mouse_svg(s)))
            drawing.set_size_request(w, h)
            drawing.set_can_shrink(True)
            drawing.set_alternative_text("Top view of the mouse with its three light zones")
            fixed.put(drawing, x, y)

            for zone, (lx, ly, lw) in LINES.items():
                line = Gtk.Box(css_classes=["squire-line"] + (["selected"] if zone == self.selected else []))
                line.set_size_request(lw, 1)
                fixed.put(line, lx, ly)
            for zone, (hx, hy, hw, hh) in HOTSPOTS.items():
                spot = Gtk.Button(css_classes=["squire-hotspot"] + (["selected"] if zone == self.selected else []),
                                  tooltip_text=ZONE_TITLES[zone])
                spot.update_property([Gtk.AccessibleProperty.LABEL], [ZONE_TITLES[zone]])
                spot.set_size_request(hw, hh)
                spot.connect("clicked", lambda _b, z=zone: self.select(z))
                fixed.put(spot, hx, hy)
            for zone, (cx, cy) in CALLOUTS.items():
                fixed.put(self.card(zone, CALLOUT_TITLES[zone], zone_summary(s, zone), 150), cx, cy)

            hint = Gtk.Label(label="Click a highlighted part, or one of these:", css_classes=["dim-label"])
            chips = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
            for zone, label in CHIPS:
                chips.append(self.card(zone, label, zone_summary(s, zone), 190))
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            column.set_size_request(STAGE_W - 64, -1)
            column.append(hint)
            column.append(chips)
            fixed.put(column, 32, 520)
            return fixed

        def card(self, zone, title, value, width):
            button = Gtk.Button(css_classes=["card", "squire-callout"] + (["selected"] if zone == self.selected else []))
            button.set_size_request(width, -1)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
            box.append(Gtk.Label(label=value, xalign=0, css_classes=["dim-label", "caption"], wrap=True))
            button.set_child(box)
            button.connect("clicked", lambda _b: self.select(zone))
            return button

        def build_inspector(self, s):
            page = Adw.PreferencesPage(hexpand=False)
            zone = self.selected
            group = Adw.PreferencesGroup(title=ZONE_TITLES[zone], description=ZONE_KINDS[zone])
            if zone in squire.LED_ZONES:
                led = s.leds.get(zone)
                if led:
                    group.add(row("Effect", mode_name(led["mode"])))
                    if led["mode"] != "off":
                        group.add(row("Brightness", f"{led['brightness']} %"))
                    if shows_color(led["mode"]):
                        group.add(row("Color", led["color"], suffix=swatch(led["color"])))
                else:
                    group.add(row("Not read", ""))
            elif zone == "dpi":
                count = s.dpi_stage_count or len(s.dpi)
                for i, dpi in enumerate(s.dpi[:count]):
                    xy = s.dpi_xy[i] if i < len(s.dpi_xy) else None
                    sub = "active" if s.dpi_stage == i + 1 else ""
                    if xy and xy[0] != xy[1]:
                        sub = (sub + " · " if sub else "") + f"X {xy[0]} / Y {xy[1]}"
                    color = s.dpi_colors[i] if i < len(s.dpi_colors) else "none"
                    group.add(row(f"Stage {i + 1}", f"{dpi} DPI", sub, suffix=swatch(color)))
                group.add(row("Stages on the DPI button", str(count)))
            elif zone == "sensor":
                group.add(row("Polling rate", f"{s.polling_hz} Hz",
                              f"raw 0x{s.polling_raw or 0:02x}; 8000 Hz mode not confirmed yet"))
                group.add(row("Lift-off distance", (s.liftoff or "").capitalize()))
                group.add(row("Angle snapping", "On" if s.angle_snapping else "Off"))
                group.add(row("Angle tuning", f"{s.angle_tuning}°"))
                group.add(row("Button response", f"{s.debounce_ms} ms" if s.debounce_ms else f"index {s.debounce_index}"))
            elif zone == "power":
                b = self.batteries.get("mouse")
                if b and b.percent is not None:
                    group.add(row("Battery", f"{b.percent} %", "charging" if b.charging else ""))
                group.add(row("Turn off after", "Never" if s.power_off_minutes is None else f"{s.power_off_minutes} min"))
                group.add(row("Low-battery warning", f"{s.low_warning} %", "Squire's notification uses this level"))
            page.add(group)
            info = Adw.PreferencesGroup(title="Device")
            info.add(row("Connection", "Omni receiver" if s.connection == "omni" else "Wired"))
            info.add(row("Profile", str(s.profile), "Squire reads the active profile only"))
            info.add(row("Firmware", s.firmware or "–"))
            page.add(info)
            return page

    class App(Adw.Application):
        def __init__(self):
            # single instance, except for --screenshot runs, which must not just raise an open window
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.NON_UNIQUE if SCREENSHOT else Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window = None

        def do_startup(self):
            Adw.Application.do_startup(self)
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def do_activate(self):
            if self.window is None:
                self.window = Window(self)
            self.window.present()

    return App().run(argv)


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"squire-settings {squire.__version__}")
        return 0
    args = sys.argv[1:]
    if "--screenshot" in args:
        i = args.index("--screenshot")
        SCREENSHOT.extend(a for a in args[i + 1:i + 3] if not a.startswith("-"))
        del args[i:i + 1 + len(SCREENSHOT)]
    global DEMO
    DEMO = "--demo" in args
    return run([sys.argv[0]] + [a for a in args if a not in ("--debug", "--demo")])


if __name__ == "__main__":
    sys.exit(main())

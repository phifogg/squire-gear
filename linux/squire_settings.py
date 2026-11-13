#!/usr/bin/env python3
"""Squire settings window for Linux (GTK 4 + libadwaita).

Devices on the left; the selected device drawn in the middle with clickable zones and callouts; the values for
the selected zone on the right. Shows what squire.read_mouse_settings reads; the three lights, power-off and
low-battery warning, angle snapping and tuning, polling rate, surface, lift-off distance and the DPI stages (value,
colour, active stage, stage count) can be changed, and are written only on an explicit Apply (read back, then one
save).

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


# The Squire shield with a blade (same shape as linux/icons/squire-gear-symbolic.svg, 16x16): the logo zone.
SHIELD = ("M8 0.8 L14.2 3 V7.8 C14.2 11.4 11.6 13.9 8 15.2 C4.4 13.9 1.8 11.4 1.8 7.8 V3 Z "
          "M8 3 L7.1 4.4 V9.2 H5.4 V10.4 H7.1 V12.4 H8.9 V10.4 H10.6 V9.2 H8.9 V4.4 Z")


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
<filter id="logo-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6"/></filter>
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
<g transform="translate(76 222) scale(3)">
<path d="{SHIELD}" fill="{logo}" opacity="{round(logo_o * 0.7, 2)}" filter="url(#logo-glow)"/>
<path d="{SHIELD}" fill="#14171b" fill-rule="evenodd" stroke="#3a404a" stroke-width="0.35"/>
<path d="{SHIELD}" fill="{logo}" fill-rule="evenodd" opacity="{logo_o}"/>
</g>
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


# LED zones that can be changed (51 28 with zone 00 logo, 01 wheel, 02 side; logo confirmed on the device).
EDITABLE_ZONES = ("logo", "wheel", "side")
EDIT_MODES = list(squire.LED_WRITE_MODES)      # static, breathing, color-cycle, react, battery, off


# Besides the light zones, `pending` can hold these keys (power: {"power_off_minutes", "low_warning"}; the others a
# bool / degrees / "low" or "high"). Power and angle bytes approved and confirmed on the device 2026-11-12
# (protocol.json settings_writes); polling, surface and lift-off approved 2026-11-13 (armoury_crate captures).
# Surface comes before lift-off: the surface write keeps the lift-off the mouse has, then lift-off keeps the new surface.
# DPI (approved 2026-11-13): "dpi_1".."dpi_4" = {"dpi", "color"} per stage, "dpi_stage" (active), "dpi_stage_count".
DPI_KEYS = tuple(f"dpi_{i}" for i in range(1, squire.DPI_STAGES + 1))
OTHER_KEYS = DPI_KEYS + ("dpi_stage", "dpi_stage_count", "power", "angle_snapping", "angle_tuning", "polling_hz",
                         "surface_id", "liftoff")
OTHER_TITLES = {"power": "Power", "angle_snapping": "Angle snapping", "angle_tuning": "Angle tuning",
                "polling_hz": "Polling rate", "surface_id": "Surface", "liftoff": "Lift-off distance",
                "dpi_stage": "Active DPI stage", "dpi_stage_count": "DPI stages",
                **{key: f"DPI stage {key[4:]}" for key in DPI_KEYS}}
POWER_OFF_CHOICES = [1, 2, 3, 5, 10, None]      # minutes, None = never


def current_other(s: squire.MouseSettings, key: str):
    if key == "power":
        return {"power_off_minutes": s.power_off_minutes, "low_warning": s.low_warning}
    if key in DPI_KEYS:
        i = int(key[4:]) - 1
        if i >= len(s.dpi_xy) or i >= len(s.dpi_colors):
            return None
        return {"dpi": s.dpi_xy[i][0], "color": s.dpi_colors[i]}
    return getattr(s, key)


def can_edit_dpi(s: squire.MouseSettings, stage: int) -> bool:
    """A stage with separate X/Y values is shown only: the write sets both alike."""
    i = stage - 1
    return i < len(s.dpi_xy) and i < len(s.dpi_colors) and s.dpi_xy[i][0] == s.dpi_xy[i][1]


def edit_dpi_stages(s: squire.MouseSettings, pending: dict, active: Optional[int] = None,
                    count: Optional[int] = None) -> dict:
    """Active stage or stage count. They stay consistent: a stage beyond the count raises the count, a count below
    the active stage lowers the active stage."""
    p = preview_settings(s, pending)
    new_active = active if active is not None else p.dpi_stage
    new_count = count if count is not None else p.dpi_stage_count
    if active is not None and new_active > new_count:
        new_count = new_active
    if count is not None and new_active > new_count:
        new_active = new_count
    pending = edit_other(s, pending, "dpi_stage", new_active)
    return edit_other(s, pending, "dpi_stage_count", new_count)


def preview_settings(s: squire.MouseSettings, pending: dict) -> squire.MouseSettings:
    """The settings as they will be after Apply (for the drawing, the callouts and the chips)."""
    import dataclasses
    leds = {k: v for k, v in pending.items() if k in squire.LED_ZONES}
    other = dict(pending.get("power", {}))
    for key in ("angle_snapping", "angle_tuning", "liftoff", "polling_hz", "surface_id", "dpi_stage", "dpi_stage_count"):
        if key in pending:
            other[key] = pending[key]
    if any(key in pending for key in DPI_KEYS):
        dpi, xy, colors = list(s.dpi), [list(v) for v in s.dpi_xy], list(s.dpi_colors)
        for key in DPI_KEYS:
            if key in pending:
                i = int(key[4:]) - 1
                dpi[i], xy[i], colors[i] = pending[key]["dpi"], [pending[key]["dpi"]] * 2, pending[key]["color"]
        other.update(dpi=dpi, dpi_xy=xy, dpi_colors=colors)
    if "surface_id" in pending:
        other["surface_params"] = squire.SURFACE_PARAMS.get(pending["surface_id"])
    return dataclasses.replace(s, leds={**s.leds, **leds}, **other)


def changed_zones(s: squire.MouseSettings, pending: dict) -> list[str]:
    return [zone for zone, led in pending.items()
            if zone in squire.LED_ZONES and not squire.led_matches(led, s.leds.get(zone))]


def changes(s: squire.MouseSettings, pending: dict) -> list[str]:
    """Everything Apply will write, in a fixed order: changed light zones, then OTHER_KEYS. The active stage goes
    after the stage count only when it is beyond the count the mouse has (the mouse refuses neither, but Squire keeps
    the active stage on the button at every step)."""
    keys = list(OTHER_KEYS)
    if pending.get("dpi_stage", 0) > (s.dpi_stage_count or 0):
        keys.remove("dpi_stage")
        keys.insert(keys.index("dpi_stage_count") + 1, "dpi_stage")
    return changed_zones(s, pending) + [k for k in keys if k in pending and pending[k] != current_other(s, k)]


def change_title(key: str) -> str:
    return ZONE_TITLES.get(key) if key in squire.LED_ZONES else OTHER_TITLES[key]


def change_command(s: squire.MouseSettings, key: str, value, after: Optional[squire.MouseSettings] = None) -> bytes:
    """The bytes for one change. Surface keeps the lift-off as last read; lift-off keeps the surface of `after` (the
    preview: a new surface is written first). set_surface()/set_liftoff() read 12 06 again right before they write,
    so what is sent keeps whatever the mouse has at that moment."""
    if key in squire.LED_ZONES:
        return squire.led_command(key, value["mode"], value["brightness"], value["color"])
    if key == "power":
        return squire.power_command(value["power_off_minutes"], value["low_warning"])
    if key == "angle_snapping":
        return squire.angle_snapping_command(value)
    if key == "liftoff":
        after = after or s
        return squire.liftoff_command(value, after.surface_id, after.surface_params)
    if key == "surface_id":
        return squire.surface_command(value, s.liftoff)
    if key == "polling_hz":
        return squire.polling_command(value)
    if key in DPI_KEYS:
        return squire.dpi_command(int(key[4:]), value["dpi"], value["color"])
    if key == "dpi_stage":
        return squire.active_stage_command(value)
    if key == "dpi_stage_count":
        return squire.stage_count_command(value)
    return squire.angle_tuning_command(value)


def write_change(key: str, value, save: bool) -> "squire.WriteResult":
    if key in squire.LED_ZONES:
        return squire.set_led(key, value["mode"], value["brightness"], value["color"], save=save)
    if key == "power":
        return squire.set_power(value["power_off_minutes"], value["low_warning"], save=save)
    if key == "angle_snapping":
        return squire.set_angle_snapping(value, save=save)
    if key == "liftoff":
        return squire.set_liftoff(value, save=save)
    if key == "surface_id":
        return squire.set_surface(value, save=save)
    if key == "polling_hz":
        return squire.set_polling(value, save=save)
    if key in DPI_KEYS:
        return squire.set_dpi(int(key[4:]), value["dpi"], color=value["color"], save=save)
    if key == "dpi_stage":
        return squire.set_active_stage(value, save=save)
    if key == "dpi_stage_count":
        return squire.set_stage_count(value, save=save)
    return squire.set_angle_tuning(value, save=save)


def edit_other(s: squire.MouseSettings, pending: dict, key: str, value) -> dict:
    """A power or sensor edit: kept pending, or dropped when it is back to what the mouse has."""
    pending = dict(pending)
    if value == current_other(s, key):
        pending.pop(key, None)
    else:
        pending[key] = value
    return pending


def can_edit_liftoff(s: squire.MouseSettings) -> bool:
    """Only with a surface Squire knows (or none): an unknown one could be a calibration it must not touch."""
    try:
        squire.liftoff_command("low", s.surface_id, s.surface_params)
        return s.liftoff in squire.LIFTOFF_CODES
    except ValueError:
        return False


def wrapping_list_factory(row):
    """Popover rows for a ComboRow whose items are long: wrap instead of ellipsizing, with the check mark on the
    current choice that the default list has."""
    from gi.repository import Gtk     # GTK 4 is loaded in run(), never at import (tests run without it)
    factory, bound = Gtk.SignalListItemFactory(), set()

    def mark(item):
        item.get_child().get_last_child().set_opacity(1 if item.get_position() == row.get_selected() else 0)

    def setup(_f, item):
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Label(xalign=0, wrap=True, max_width_chars=32, width_chars=24, hexpand=True))
        box.append(Gtk.Image(icon_name="object-select-symbolic"))
        item.set_child(box)

    def bind(_f, item):
        item.get_child().get_first_child().set_label(item.get_item().get_string())
        bound.add(item)
        mark(item)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    factory.connect("unbind", lambda _f, item: bound.discard(item))
    row.connect("notify::selected", lambda *_: [mark(i) for i in bound])
    return factory


def can_edit_surface(s: squire.MouseSettings) -> bool:
    """Only replace a surface Squire knows (or none), and only with a lift-off it can write back."""
    return s.surface_id in squire.SURFACE_PARAMS and s.liftoff in squire.LIFTOFF_CODES


# Surface choices: none first, then the presets by name
SURFACE_CHOICES = [squire.NO_SURFACE] + sorted(squire.SURFACES, key=lambda i: squire.SURFACES[i])


def can_edit_power(s: squire.MouseSettings) -> bool:
    """Only when the warning level read from the mouse is one Squire can write back."""
    return s.low_warning in squire.LOW_WARNING_LEVELS and s.power_off_minutes in squire.POWER_OFF_CODES


def edit_pending(s: squire.MouseSettings, pending: dict, zone: str, linked: bool = False, **changes) -> dict:
    """An edit to one zone, or with `linked` to every light zone ("Same effect on all zones"): the zone's state
    plus the changes becomes pending for each target, or is dropped where it is back to what the mouse has.
    A linked edit without changes copies the zone to the others (the switch being turned on)."""
    led = dict(pending.get(zone) or s.leds[zone])
    led.update(changes)
    pending = dict(pending)
    for target in ([z for z in EDITABLE_ZONES if s.leds.get(z)] if linked else [zone]):
        current = s.leds[target]
        if squire.led_matches(led, current) and led["mode"] == current["mode"]:
            pending.pop(target, None)       # back to what the mouse has
        else:
            pending[target] = dict(led)
    return pending


def can_link(s: squire.MouseSettings, pending: dict, zone: str) -> bool:
    """Only an effect Squire can write can be copied to the other zones."""
    led = pending.get(zone) or s.leds.get(zone)
    return bool(led) and led["mode"] in EDIT_MODES


def apply_status(s: squire.MouseSettings, pending: dict) -> str:
    keys = changes(s, pending)
    if not keys:
        return "Showing the values read from the mouse. Nothing is sent until you click Apply."
    names = ", ".join(change_title(k) for k in keys)
    return f"Not sent yet: {names}. Apply writes it to the mouse, reads it back and saves it."


def planned_packets(s: squire.MouseSettings, pending: dict) -> list[str]:
    """Exactly what Apply will send, for "Show bytes"."""
    report_id = 0x03 if s.connection == "omni" else None
    length = 64 if s.connection == "omni" else 65
    lines = []
    after = preview_settings(s, pending)
    for key in changes(s, pending):
        command = change_command(s, key, pending[key], after)
        lines.append(squire.packet(report_id, command, length, write=True)[:len(command) + 1].hex(" ") + " …")
    if lines:
        lines.append(squire.packet(report_id, squire.SAVE_COMMAND, length, write=True)[:3].hex(" ") + " …  (save)")
    return lines


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
.squire-bytes { font-family: monospace; font-size: 0.9em; }
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
            self.applying = False
            self.pending: dict = {}        # zone -> led dict not sent yet
            self.link_zones = False        # "Same effect on all zones"
            self.value_labels: dict = {}   # zone -> callout/chip value label

            split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)
            split.set_sidebar(self.build_sidebar())
            self.content_view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read again")
            self.refresh_button.connect("clicked", lambda _: self.refresh())
            header.pack_start(self.refresh_button)
            self.content_view.add_top_bar(header)
            self.banner = Adw.Banner(title="Lights, DPI, sensor and power settings can be changed. "
                                           "Nothing is sent until you click Apply.", revealed=True)
            self.content_view.add_top_bar(self.banner)
            split.set_content(Adw.NavigationPage(title="Settings", child=self.content_view))
            self.toasts = Adw.ToastOverlay(child=split)
            self.set_content(self.toasts)
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
            if self.loading or self.applying:
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
            if "edit" in SCREENSHOT[2:] and zone in EDITABLE_ZONES and zone not in self.pending:
                self.on_edit(zone, mode="static", brightness=100, color="#3d8bff")   # sample edit, not sent
                self.render()
                self.bytes_toggle.set_active(True)
                GLib.timeout_add(600, self.capture)
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
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=False)
            column.set_size_request(360, -1)
            inspector = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            inspector.set_child(self.build_inspector(s))
            column.append(inspector)
            column.append(Gtk.Separator())
            column.append(self.build_apply_bar())
            body.append(column)
            self.content_view.set_content(body)
            self.update_preview()

        def select(self, zone):
            self.selected = zone
            self.render()

        def build_stage(self, s):
            fixed = Gtk.Fixed()
            fixed.set_size_request(STAGE_W, STAGE_H)
            title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            title.append(Gtk.Label(label=s.name, xalign=0, css_classes=["title-2"]))
            b = self.batteries.get("mouse")
            if b and b.percent is not None:   # connection, profile, firmware: in the Device group on the right
                title.append(Gtk.Label(label=f"Battery {b.percent} %" + (" · charging" if b.charging else ""),
                                       xalign=0, css_classes=["dim-label"]))
            fixed.put(title, 32, 24)

            x, y, w, h = DRAWING
            drawing = Gtk.Picture.new_for_paintable(texture(mouse_svg(preview_settings(s, self.pending))))
            self.drawing = drawing
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
            value_label = Gtk.Label(label=value, xalign=0, css_classes=["dim-label", "caption"], wrap=True)
            self.value_labels[zone] = value_label
            box.append(value_label)
            button.set_child(box)
            button.connect("clicked", lambda _b: self.select(zone))
            return button

        def build_inspector(self, s):
            page = Adw.PreferencesPage(hexpand=False)
            zone = self.selected
            group = Adw.PreferencesGroup(title=ZONE_TITLES[zone], description=ZONE_KINDS[zone])
            if zone in EDITABLE_ZONES and s.leds.get(zone):
                self.build_led_editor(group, zone, self.pending.get(zone) or s.leds[zone])
            elif zone in squire.LED_ZONES:
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
                self.build_dpi_editor(group, s)
            elif zone == "sensor":
                p = preview_settings(s, self.pending)
                if s.polling_hz in squire.POLLING_WRITE_HZ:
                    rates = sorted(squire.POLLING_WRITE_HZ)
                    poll = Adw.ComboRow(title="Polling rate", subtitle="Reports per second",
                                        model=Gtk.StringList.new([f"{hz} Hz" for hz in rates]))
                    poll.set_selected(rates.index(p.polling_hz))
                    poll.connect("notify::selected", lambda r, _p: self.on_other("polling_hz", rates[r.get_selected()]))
                    group.add(poll)
                else:
                    group.add(row("Polling rate", f"{s.polling_hz} Hz", f"raw 0x{s.polling_raw or 0:02x}"))
                if can_edit_liftoff(s):
                    choices = list(squire.LIFTOFF_CODES)
                    lift = Adw.ComboRow(title="Lift-off distance", subtitle="The surface setting stays as it is",
                                        model=Gtk.StringList.new([c.capitalize() for c in choices]))
                    lift.set_selected(choices.index(p.liftoff))
                    lift.connect("notify::selected", lambda r, _p: self.on_other("liftoff", choices[r.get_selected()]))
                    group.add(lift)
                else:
                    group.add(row("Lift-off distance", (s.liftoff or "").capitalize(),
                                  "Shown only: the surface is not one Squire knows, so it is left alone"))
                if can_edit_surface(s):
                    # the names are long (up to 47 characters): no subtitle, so the value on the right gets the
                    # room; the list wraps instead of cutting names off
                    surf = Adw.ComboRow(title="Surface",
                                        tooltip_text="Mouse pad preset; the lift-off stays as it is",
                                        model=Gtk.StringList.new([squire.surface_name(i) for i in SURFACE_CHOICES]))
                    surf.set_list_factory(wrapping_list_factory(surf))
                    surf.set_selected(SURFACE_CHOICES.index(p.surface_id))
                    surf.connect("notify::selected",
                                 lambda r, _p: self.on_other("surface_id", SURFACE_CHOICES[r.get_selected()]))
                    group.add(surf)
                else:
                    group.add(row("Surface", squire.surface_name(s.surface_id) or "–",
                                  "Not a preset Squire knows (a calibration?), so it is left alone"))
                if p.angle_snapping is not None:
                    snap = Adw.SwitchRow(title="Angle snapping", active=p.angle_snapping,
                                         subtitle="Straightens nearly horizontal and vertical movements")
                    snap.connect("notify::active", lambda r, _p: self.on_other("angle_snapping", r.get_active()))
                    group.add(snap)
                if p.angle_tuning is not None:
                    degrees = list(squire.ANGLE_TUNING_DEGREES)
                    if p.angle_tuning not in degrees:
                        degrees.append(p.angle_tuning)          # shown, not offered as a write
                    tune = Adw.ComboRow(title="Angle tuning", subtitle="Rotates the sensor's axes, in steps of 5°",
                                        model=Gtk.StringList.new([f"{d}°" for d in degrees]))
                    tune.set_selected(degrees.index(p.angle_tuning))
                    tune.connect("notify::selected", lambda r, _p: self.on_other("angle_tuning", degrees[r.get_selected()])
                                 if degrees[r.get_selected()] in squire.ANGLE_TUNING_DEGREES else None)
                    group.add(tune)
                group.add(row("Button response", f"{s.debounce_ms} ms" if s.debounce_ms else f"index {s.debounce_index}"))
            elif zone == "power":
                b = self.batteries.get("mouse")
                if b and b.percent is not None:
                    group.add(row("Battery", f"{b.percent} %", "charging" if b.charging else ""))
                if can_edit_power(s):
                    power = self.pending.get("power") or current_other(s, "power")
                    off = Adw.ComboRow(title="Turn off after", model=Gtk.StringList.new(
                        ["Never" if m is None else f"{m} min" for m in POWER_OFF_CHOICES]))
                    off.set_selected(POWER_OFF_CHOICES.index(power["power_off_minutes"]))
                    off.connect("notify::selected", lambda r, _p: self.on_power(power_off_minutes=POWER_OFF_CHOICES[r.get_selected()]))
                    group.add(off)
                    levels = list(squire.LOW_WARNING_LEVELS)
                    warn = Adw.ComboRow(title="Low-battery warning", subtitle="Squire's notification uses this level",
                                        model=Gtk.StringList.new([f"{w} %" for w in levels]))
                    warn.set_selected(levels.index(power["low_warning"]))
                    warn.connect("notify::selected", lambda r, _p: self.on_power(low_warning=levels[r.get_selected()]))
                    group.add(warn)
                else:
                    group.add(row("Turn off after", "Never" if s.power_off_minutes is None else f"{s.power_off_minutes} min"))
                    group.add(row("Low-battery warning", f"{s.low_warning} %",
                                  "Not one Squire can write back (10–50 %), so power is shown only"))
            page.add(group)
            info = Adw.PreferencesGroup(title="Device")
            info.add(row("Connection", "Omni receiver" if s.connection == "omni" else "Wired"))
            info.add(row("Profile", str(s.profile), "Squire reads the active profile only"))
            info.add(row("Firmware", s.firmware or "–"))
            if s.receiver_firmware:
                info.add(row("Receiver firmware", s.receiver_firmware))
            page.add(info)
            return page

        # editing -------------------------------------------------------------------------------------------
        def build_led_editor(self, group, zone, led):
            linkable = can_link(self.settings, self.pending, zone)
            link = Adw.SwitchRow(title="Same effect on all zones",
                                 subtitle="One edit sets logo, scroll wheel and side light" if linkable
                                 else "Not for an effect Squire cannot write",
                                 active=self.link_zones and linkable, sensitive=linkable)
            link.connect("notify::active", lambda r, _p: self.on_link(zone, r.get_active()))
            group.add(link)

            modes = Gtk.StringList.new([mode_name(m) for m in EDIT_MODES])
            effect = Adw.ComboRow(title="Effect", model=modes)
            effect.set_selected(EDIT_MODES.index(led["mode"]) if led["mode"] in EDIT_MODES
                                else Gtk.INVALID_LIST_POSITION)
            effect.connect("notify::selected",
                           lambda r, _p: self.on_edit(zone, mode=EDIT_MODES[r.get_selected()])
                           if r.get_selected() < len(EDIT_MODES) else None)
            group.add(effect)

            self.brightness_row = Adw.ActionRow(title="Brightness")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            scale.set_value(led["brightness"])
            scale.set_digits(0)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.set_size_request(180, -1)
            for mark in (0, 25, 50, 75, 100):
                scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", lambda sc: self.on_edit(zone, brightness=int(round(sc.get_value()))))
            self.brightness_row.add_suffix(scale)
            group.add(self.brightness_row)

            self.color_row = Adw.ActionRow(title="Color")
            button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
            rgba = Gdk.RGBA()
            rgba.parse(led["color"])
            button.set_rgba(rgba)

            def on_color(btn, _p):
                c = btn.get_rgba()
                self.on_edit(zone, color="#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255),
                                                             round(c.blue * 255)))
            button.connect("notify::rgba", on_color)
            self.color_row.add_suffix(button)
            group.add(self.color_row)
            self.editor_zone = zone

        def build_dpi_editor(self, group, s):
            p = preview_settings(s, self.pending)
            count = p.dpi_stage_count or len(p.dpi)
            stages = list(range(1, squire.DPI_STAGES + 1))
            self.dpi_rows = {}
            for stage in stages:
                i = stage - 1
                if i >= len(p.dpi):
                    break
                sub = "active" if p.dpi_stage == stage else "" if stage <= count else "not on the DPI button"
                if not can_edit_dpi(s, stage):
                    xy = s.dpi_xy[i] if i < len(s.dpi_xy) else None
                    if xy:
                        sub = (sub + " · " if sub else "") + f"X {xy[0]} / Y {xy[1]}, shown only"
                    color = s.dpi_colors[i] if i < len(s.dpi_colors) else "none"
                    group.add(row(f"Stage {stage}", f"{p.dpi[i]} DPI", sub, suffix=swatch(color)))
                    continue
                spin = Adw.SpinRow(title=f"Stage {stage}", subtitle=sub, digits=0, snap_to_ticks=True,
                                   adjustment=Gtk.Adjustment(value=p.dpi[i], lower=squire.DPI_MIN,
                                                             upper=squire.DPI_MAX, step_increment=squire.DPI_STEP,
                                                             page_increment=squire.DPI_STEP * 10))
                button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER,
                                               tooltip_text="Stage colour")
                rgba = Gdk.RGBA()
                rgba.parse(p.dpi_colors[i])
                button.set_rgba(rgba)

                def on_dpi(_w, _p, stage=stage, spin=spin, button=button):
                    c = button.get_rgba()
                    value = int(round(spin.get_value() / squire.DPI_STEP)) * squire.DPI_STEP
                    color = "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
                    self.on_other(f"dpi_{stage}", {"dpi": value, "color": color})
                spin.connect("notify::value", on_dpi)
                button.connect("notify::rgba", on_dpi)
                spin.add_suffix(button)
                group.add(spin)
            active = Adw.ComboRow(title="Active stage", subtitle="The stage in use now",
                                  model=Gtk.StringList.new([f"Stage {n}" for n in stages]))
            active.set_selected((p.dpi_stage or 1) - 1)
            active.connect("notify::selected", lambda r, _p: self.on_dpi_stages(active=r.get_selected() + 1))
            group.add(active)
            number = Adw.ComboRow(title="Stages on the DPI button", subtitle="The button cycles through these",
                                  model=Gtk.StringList.new([str(n) for n in stages]))
            number.set_selected(count - 1)
            number.connect("notify::selected", lambda r, _p: self.on_dpi_stages(count=r.get_selected() + 1))
            group.add(number)

        def on_dpi_stages(self, active=None, count=None):
            before = preview_settings(self.settings, self.pending)
            if (active or before.dpi_stage) == before.dpi_stage and (count or before.dpi_stage_count) == before.dpi_stage_count:
                return      # the combo was set by render(), not by the user
            self.pending = edit_dpi_stages(self.settings, self.pending, active=active, count=count)
            self.render()   # the other combo and the stage notes may follow

        def on_edit(self, zone, **changes):
            self.pending = edit_pending(self.settings, self.pending, zone, linked=self.link_zones, **changes)
            self.update_preview()

        def on_other(self, key, value):
            self.pending = edit_other(self.settings, self.pending, key, value)
            self.update_preview()

        def on_power(self, **change):
            power = dict(self.pending.get("power") or current_other(self.settings, "power"))
            power.update(change)
            self.on_other("power", power)

        def on_link(self, zone, on):
            self.link_zones = on
            if on:   # copy this zone to the others; from now on every edit sets all of them
                self.on_edit(zone)

        def build_apply_bar(self):
            bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
            self.apply_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, css_classes=["dim-label", "caption"])
            bar.append(self.apply_label)
            buttons = Gtk.Box(spacing=8)
            self.bytes_toggle = Gtk.ToggleButton(label="Show bytes")
            buttons.append(self.bytes_toggle)
            buttons.append(Gtk.Box(hexpand=True))
            self.revert_button = Gtk.Button(label="Revert")
            self.revert_button.connect("clicked", lambda _b: self.revert())
            self.apply_button = Gtk.Button(label="Apply", css_classes=["suggested-action"])
            self.apply_button.connect("clicked", lambda _b: self.apply_changes())
            buttons.append(self.revert_button)
            buttons.append(self.apply_button)
            bar.append(buttons)
            self.bytes_label = Gtk.Label(xalign=0, selectable=True, wrap=True, max_width_chars=40,
                                         css_classes=["squire-bytes"])
            revealer = Gtk.Revealer(child=self.bytes_label)
            self.bytes_toggle.bind_property("active", revealer, "reveal-child")
            bar.append(revealer)
            return bar

        def update_preview(self):
            s = self.settings
            if s is None or s.status != "ok":
                return
            preview = preview_settings(s, self.pending)
            if getattr(self, "drawing", None) is not None:
                self.drawing.set_paintable(texture(mouse_svg(preview)))
            for zone, label in self.value_labels.items():
                label.set_label(zone_summary(preview, zone))
            zone = getattr(self, "editor_zone", None)
            if zone == self.selected and zone in preview.leds:
                mode = preview.leds[zone]["mode"]
                self.brightness_row.set_visible(mode != "off")
                self.color_row.set_visible(shows_color(mode))
            if getattr(self, "apply_label", None) is not None:
                dirty = bool(changes(s, self.pending))
                self.apply_label.set_label("Applying…" if self.applying else apply_status(s, self.pending))
                self.apply_button.set_sensitive(dirty and not self.applying)
                self.revert_button.set_sensitive(dirty and not self.applying)
                lines = planned_packets(s, self.pending)
                self.bytes_label.set_label("\n".join(lines) if lines else "Nothing to send.")

        def revert(self):
            self.pending.clear()
            self.link_zones = False
            self.render()

        def apply_changes(self):
            keys = changes(self.settings, self.pending) if self.settings else []
            if self.applying or not keys:
                return
            self.applying = True
            self.refresh_button.set_sensitive(False)
            self.update_preview()
            work = {key: self.pending[key] for key in keys}

            def worker():
                # write every change, save once with the last one, and only if all earlier ones matched
                results = {}
                for i, key in enumerate(keys):
                    if DEMO:   # demo: nothing is sent
                        results[key] = squire.WriteResult("ok", saved=True, message="demo")
                        continue
                    try:
                        results[key] = write_change(key, work[key], save=i == len(keys) - 1)
                    except Exception as error:  # keep the window alive
                        results[key] = squire.WriteResult("error", message=str(error))
                    if results[key].status != "ok":
                        break   # stop: nothing more is written and nothing is saved
                saved = len(results) == len(keys) and all(r.status == "ok" for r in results.values()) \
                    and results[keys[-1]].saved
                for r in results.values():
                    r.saved = saved
                GLib.idle_add(self.apply_done, results, work)

            threading.Thread(target=worker, name="squire-write", daemon=True).start()

        def apply_done(self, results, work):
            self.applying = False
            self.refresh_button.set_sensitive(True)
            failed = []
            for key, result in results.items():
                if result.status == "ok" and result.saved:
                    value = work[key]
                    if key in squire.LED_ZONES:
                        self.settings.leds[key] = result.after or value
                    elif key == "power":
                        self.settings.power_off_minutes = value["power_off_minutes"]
                        self.settings.low_warning = value["low_warning"]
                    elif key in DPI_KEYS:
                        i = int(key[4:]) - 1
                        self.settings.dpi[i], self.settings.dpi_xy[i] = value["dpi"], [value["dpi"]] * 2
                        self.settings.dpi_colors[i] = value["color"]
                    else:
                        setattr(self.settings, key, value)
                    if key == "surface_id":
                        self.settings.surface_params = squire.SURFACE_PARAMS.get(value)
                    elif key == "polling_hz" and self.settings.polling_raw is not None:
                        self.settings.polling_raw = (self.settings.polling_raw & 0xF8) | squire.POLLING_WRITE_HZ[value]
                    self.pending.pop(key, None)
                else:
                    failed.append((key, result))
            if failed:
                texts = {"asleep": "The mouse is asleep. Move it and click Apply again.",
                         "ok": "Written to the mouse's memory but not saved (lost when it is switched off).",
                         "mismatch": "The mouse did not take the new value (read back differs). Nothing was saved.",
                         "no-response": "The mouse did not answer. Move it and click Apply again."}
                body = "\n".join(f"{change_title(k)}: {texts.get(r.status, r.status + ' ' + r.message)}"
                                  + (f" ({r.message})" if r.status == "ok" and r.message else "")
                                  for k, r in failed)
                dialog = Adw.AlertDialog(heading="Not applied", body=body)
                dialog.add_response("ok", "OK")
                dialog.present(self)
            else:
                self.toasts.add_toast(Adw.Toast(title="Saved to the mouse" + (" (demo, nothing sent)" if DEMO else "")))
            self.render()
            return False

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
        SCREENSHOT.extend(a for a in args[i + 1:i + 4] if not a.startswith("-"))
        del args[i:i + 1 + len(SCREENSHOT)]
    global DEMO
    DEMO = "--demo" in args
    return run([sys.argv[0]] + [a for a in args if a not in ("--debug", "--demo")])


if __name__ == "__main__":
    sys.exit(main())

"""The mouse page's state logic: what is pending, what Apply writes and in which order, the bytes it sends, the
summaries under the callouts and chips. No GTK."""
from typing import Optional

import squire

from .. import common
from ..common import ROOT


MODE_NAMES = {"static": "Static", "breathing": "Breathing", "color-cycle": "Color cycle", "rainbow": "Rainbow",
              "react": "React", "comet": "Comet", "battery": "Battery", "off": "Off"}
ZONE_TITLES = {"wheel": "Scroll wheel light", "logo": "Logo light", "side": "Side light",
               "dpi": "DPI stages (button on the underside)", "sensor": "Sensor", "power": "Power"}
ZONE_KINDS = {"wheel": "Lighting", "logo": "Lighting", "side": "Lighting", "dpi": "Performance",
              "sensor": "Performance", "power": "Battery"}
ZONE_CARD_TITLES = {"wheel": "Scroll wheel", "logo": "Logo", "side": "Side", "dpi": "DPI", "sensor": "Sensor",
                    "power": "Power"}


def zone_title(zone: str) -> str:
    """Inspector title; an LED zone of another model gets its name from devices.json."""
    return ZONE_TITLES.get(zone) or f"{zone.capitalize()} light"


def mouse_zones(model) -> list:
    """(zone, card title) per setting the model's capabilities give, in page order: its LED zones (devices.json
    order), DPI, sensor (polling, sensor, surface), power."""
    zones = []
    if model.has("led_zones"):
        zones += [(z["name"], ZONE_CARD_TITLES.get(z["name"], z["name"].capitalize()))
                  for z in model.capability("led_zones")["zones"]]
    if model.has("dpi"):
        zones.append(("dpi", "DPI"))
    if any(model.has(c) for c in ("polling", "sensor", "surface")):
        zones.append(("sensor", "Sensor"))
    if model.has("power"):
        zones.append(("power", "Power"))
    return zones


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


def demo_data() -> tuple[dict, "squire.MouseSettings"]:
    """Batteries and mouse settings from the real captures in protocol.json (sends nothing)."""
    import json
    data = json.loads((ROOT / "protocol.json").read_text())
    s = squire.MouseSettings("ROG Keris II Origin", "omni")
    battery = next(v for v in data["vectors"] if v["device"] == "mouse" and v["report_id"] == 3)
    squire.parse_mouse_settings(s, "battery", bytes.fromhex(battery["reply"])[1:])
    for capture in data["mouse_settings"]["captures"]:
        squire.parse_mouse_settings(s, capture["command"], bytes.fromhex(capture["reply"])[1:])
    batteries = {"mouse": squire.Battery("mouse", s.name, "omni", 80, low_warning=20),
                 "keyboard": squire.Battery("keyboard", "ROG Falchion RX LP", "omni", 78, low_warning=20)}
    return batteries, s


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


def current_other(s: squire.MouseSettings, key: str):
    if key == "power":
        return {"power_off_minutes": s.power_off_minutes, "low_warning": s.low_warning}
    if key in DPI_KEYS:
        i = int(key[4:]) - 1
        if i >= len(s.dpi_xy) or i >= len(s.dpi_colors):
            return None
        return {"dpi": s.dpi_xy[i][0], "color": s.dpi_colors[i]}
    return getattr(s, key)


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
        lines.append(squire.packet(report_id, command, length, write=True, device="mouse")[:len(command) + 1].hex(" ") + " …")
    if lines:
        lines.append(squire.packet(report_id, squire.SAVE_COMMAND, length, write=True, device="mouse")[:3].hex(" ") + " …  (save)")
    return lines


class MousePage:
    """The mouse page: the drawing with hotspots and callouts, the chips below it and, on the right, the editor of
    the selected zone (one editor per capability: led_zones, dpi, sensor, power). The window owns the apply bar
    and the Apply flow; this page says what Apply writes and how a result is taken over."""
    noun = "mouse"
    icon = "input-mouse-symbolic"
    banner = "Lights, DPI, sensor and power settings can be changed. Nothing is sent until you click Apply."
    status_texts = {
        "asleep": ("The mouse is asleep", "Move the mouse, then read again."),
        "no-response": ("No answer from the mouse", "Move the mouse, then read again."),
        "no-access": ("No access to the receiver", "Install the udev rule (see the README), then "
                                                  "re-plug the receiver."),
        "not-found": ("Mouse not found", "Plug in the Omni receiver or the USB cable."),
    }
    failure_texts = {"asleep": "The mouse is asleep. Move it and click Apply again.",
                     "ok": "Written to the mouse's memory but not saved (lost when it is switched off).",
                     "mismatch": "The mouse did not take the new value (read back differs). Nothing was saved.",
                     "no-response": "The mouse did not answer. Move it and click Apply again."}

    def __init__(self, window, model):
        self.window = window
        self.model = model             # the devices.json model (id, name, kind)
        self.zones = mouse_zones(model)
        self.settings: Optional[squire.MouseSettings] = None
        self.selected = "logo" if "logo" in dict(self.zones) else self.zones[0][0]
        self.pending: dict = {}        # zone -> led dict not sent yet, plus OTHER_KEYS
        self.link_zones = False        # "Same effect on all zones"
        self.value_labels: dict = {}   # zone -> callout/chip value label
        self.drawing = None
        self.editor_zone = None        # the LED zone whose editor rows exist (brightness_row, color_row)
        self.brightness_row = self.color_row = None

    @property
    def data(self):
        return self.settings

    @property
    def title(self) -> str:
        return self.model.name

    @property
    def battery(self):
        return self.window.batteries.get(self.model.kind)

    def read(self):
        """Read the settings (worker thread; --demo: the protocol.json captures)."""
        return demo_data()[1] if common.DEMO else squire.read_mouse_settings()

    def error_data(self):
        return squire.MouseSettings(self.title, "", status="error")

    def load(self, settings):
        self.settings = settings

    # stage ---------------------------------------------------------------------------------------------------
    def select(self, zone):
        self.selected = zone
        self.window.render()

    def card(self, zone, title, value, width):
        from ..widgets import card
        button, label = card(title, value, width, zone == self.selected, lambda: self.select(zone))
        self.value_labels[zone] = label
        return button

    def build_stage(self):
        from ..drawings import has_drawing
        from ..widgets import plain_stage
        if common.PLAIN or not has_drawing(self.model):
            self.drawing = None
            return plain_stage(self.settings.name, self.battery,
                               [self.card(zone, title, zone_summary(self.settings, zone), 190)
                                for zone, title in self.zones])
        from ..drawings.mouse import CALLOUTS, CALLOUT_TITLES, CHIPS, DRAWING, HOTSPOTS, LINES, STAGE_H, STAGE_W, \
            mouse_svg
        from ..widgets import Gtk, chip_row, stage_title, texture
        s = self.settings
        fixed = Gtk.Fixed()
        fixed.set_size_request(STAGE_W, STAGE_H)
        fixed.put(stage_title(s.name, self.battery), 32, 24)

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

        chips = [self.card(zone, label, zone_summary(s, zone), 190) for zone, label in CHIPS]
        fixed.put(chip_row("Click a highlighted part, or one of these:", chips, STAGE_W - 64), 32, 520)
        return fixed

    # inspector: one editor per capability --------------------------------------------------------------------
    def build_inspector(self):
        from ..editors import dpi, led_zones, power, sensor
        from ..widgets import Adw, row
        s = self.settings
        page = Adw.PreferencesPage(hexpand=False)
        zone = self.selected
        group = Adw.PreferencesGroup(title=zone_title(zone), description=ZONE_KINDS.get(zone, "Lighting"))
        if zone in squire.LED_ZONES:
            led_zones.build(self, group, zone)
        elif zone == "dpi":
            dpi.build(self, group)
        elif zone == "sensor":
            sensor.build(self, group)
        elif zone == "power":
            power.build(self, group)
        page.add(group)
        info = Adw.PreferencesGroup(title="Device")
        info.add(row("Connection", "Omni receiver" if s.connection == "omni" else "Wired"))
        info.add(row("Profile", str(s.profile), "Squire reads the active profile only"))
        info.add(row("Firmware", s.firmware or "–"))
        if s.receiver_firmware:
            info.add(row("Receiver firmware", s.receiver_firmware))
        page.add(info)
        return page

    # edits ---------------------------------------------------------------------------------------------------
    def on_edit(self, zone, **changes):
        from ..editors.led_zones import edit_pending
        self.pending = edit_pending(self.settings, self.pending, zone, linked=self.link_zones, **changes)
        self.window.update_preview()

    def on_other(self, key, value):
        self.pending = edit_other(self.settings, self.pending, key, value)
        self.window.update_preview()

    def update_preview(self):
        """The drawing, the callouts / chips and the LED editor rows follow the pending edits."""
        from ..drawings.mouse import mouse_svg
        from ..widgets import texture
        preview = preview_settings(self.settings, self.pending)
        if self.drawing is not None:
            self.drawing.set_paintable(texture(mouse_svg(preview)))
        for zone, label in self.value_labels.items():
            label.set_label(zone_summary(preview, zone))
        zone = self.editor_zone
        if zone == self.selected and zone in preview.leds:
            mode = preview.leds[zone]["mode"]
            self.brightness_row.set_visible(mode != "off")
            self.color_row.set_visible(shows_color(mode))

    def revert(self):
        self.pending.clear()
        self.link_zones = False

    # Apply (the window runs it: each change written and read back, one save with the last one) ---------------
    def changes(self) -> list:
        return changes(self.settings, self.pending)

    def saved_changes(self, work: dict) -> list:
        return list(work)                 # every mouse change is saved (50 03 once, with the last)

    def apply_status(self) -> str:
        return apply_status(self.settings, self.pending)

    def planned_packets(self) -> list:
        return planned_packets(self.settings, self.pending)

    def change_title(self, key) -> str:
        return change_title(key)

    def write(self, key, value, save) -> "squire.WriteResult":
        return write_change(key, value, save)

    def success_title(self, work) -> str:
        return "Saved to the mouse"

    def accept(self, key, value, result) -> bool:
        """Take over a written change into the settings read; False = it failed (shown in the dialog)."""
        if not (result.status == "ok" and result.saved):
            return False
        s = self.settings
        if key in squire.LED_ZONES:
            s.leds[key] = result.after or value
        elif key == "power":
            s.power_off_minutes = value["power_off_minutes"]
            s.low_warning = value["low_warning"]
        elif key in DPI_KEYS:
            i = int(key[4:]) - 1
            s.dpi[i], s.dpi_xy[i] = value["dpi"], [value["dpi"]] * 2
            s.dpi_colors[i] = value["color"]
        else:
            setattr(s, key, value)
        if key == "surface_id":
            s.surface_params = squire.SURFACE_PARAMS.get(value)
        elif key == "polling_hz" and s.polling_raw is not None:
            s.polling_raw = (s.polling_raw & 0xF8) | squire.POLLING_WRITE_HZ[value]
        self.pending.pop(key, None)
        return True

    def demo_edit(self, zone, flags) -> bool:
        """--screenshot ... ZONE edit: a sample edit (not sent); True when the page changed."""
        from ..editors.led_zones import EDITABLE_ZONES
        if "edit" in flags and zone in EDITABLE_ZONES and zone not in self.pending:
            self.on_edit(zone, mode="static", brightness=100, color="#3d8bff")
            return True
        return False

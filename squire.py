#!/usr/bin/env python3
"""Squire: battery monitor and mouse settings for ASUS ROG Falchion RX Low Profile + Keris II Origin (macOS, Linux).

Battery display and --settings only send *get* requests (keyboard 12 01, mouse 12 07 and the settings reads).
Writes only on an explicit --set-... (+ --save) or Apply in the settings window, only from the device's own
allowlist (is_allowed_write(command, device)), each read back before it is saved. It never touches firmware or switches profiles.

Protocol facts (byte positions relative to the echoed command, i.e. without report ID):
  keyboard  12 01 -> [10] battery %, [5] battery % (copy), [8] charging, [9] low-battery warning %
  mouse     12 07 -> [4]  battery %, [9] charging, [6] low-battery warning %
  error reply: ff aa (device asleep / command not supported)
Source: G-Helper (GPL-3.0) app/Peripherals/{Keyboard,Mouse}; facts only, no code copied.

Backends: hidapi on macOS; on Linux /dev/hidraw* directly (standard library only), because Ubuntu's hidapi
package uses libusb, which reports no usage pages and would have to detach kernel drivers.
"""
__version__ = "0.4.1"

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))   # squirelib/ sits next to squire.py (repo and installs)
from squirelib import capabilities, registry, routes, session, transport  # noqa: E402
from squirelib.session import Battery, WriteResult  # noqa: E402,F401
from squirelib.capabilities import kb_lighting  # noqa: E402
from squirelib.protocol import Exchange, find_echo, is_error, MAX_READS  # noqa: E402,F401
from squirelib.routes import CONNECTION_LABELS, Target, connection_label  # noqa: E402,F401
from squirelib.state import IS_LINUX, state_dir  # noqa: E402,F401
from squirelib.transport import (DRAIN_TIMEOUT_MS, READ_TIMEOUT_MS, HidrawBackend, HidrawDevice,  # noqa: E402,F401
                                 descriptor_collections, device_lock, load_backend)
from squirelib.transport.bluez import bluez_objects  # noqa: E402,F401

# Device models (ids, routes, commands, capability parameters) come from devices.json; load() refuses a file that
# fails the checker. The module constants below are derived from it, so the rest of Squire reads them as before.
DEVICES = registry.load()
KEYBOARD = DEVICES.by_kind("keyboard")   # Falchion RX Low Profile
MOUSE = DEVICES.by_kind("mouse")         # Keris II Origin
_hex = registry.byte


def _pid(device, connection: str) -> int:
    return int(device.route(connection)["pid"], 16)


VID = DEVICES.vendor_id
PID_OMNI = int(DEVICES.receivers["rog-omni"]["pid"], 16)   # ROG Omni receiver (keyboard + mouse share it)
PID_FALCHION_RX_LP = _pid(KEYBOARD, "wired")
PID_KERIS_II_ORIGIN = _pid(MOUSE, "wired")
# Paired directly over Bluetooth LE (macOS 2026-11-26, Linux 2026-11-27): only the normal input collections, no
# vendor page, so no 12 xx query. Squire reads only the standard battery level (GATT 180F / 2A19) the OS holds.
BLUETOOTH_PIDS = {_pid(d, "bluetooth"): d.kind for d in DEVICES.devices if d.route("bluetooth")}
# Interface of the vendor collection per HID pid (Omni 2, wired keyboard 1, wired mouse 0).
HID_INTERFACES = {int(r["pid"], 16): r["interface"] for d in DEVICES.devices for r in d.routes()
                  if r["connection"] != "bluetooth"}

KEYBOARD_QUERY = KEYBOARD.read(KEYBOARD.capability("battery")["read"])
MOUSE_QUERY = MOUSE.read(MOUSE.capability("battery")["read"])
DEFAULT_LOW_WARNING = 20   # used when a device does not report a plausible warning level


STATE_FILE = state_dir() / "notified.json"


# Every HID route (Omni first, then the cable) from devices.json; see squirelib/routes.py.
TARGETS = routes.build_targets(DEVICES)


# Read-only commands ("get" requests all start with 0x12; writes start with 0x50/0x51 and are not on any list
# here), in devices.json's order, without the battery query. Keris II Origin, facts only: libratbag src/asus.c +
# driver-asus.c (MIT, confirmed on this mouse in the owner's libratbag fork) and G-Helper AsusMouse.cs (GPL-3.0):
# profile (active profile, DPI stage, firmware), settings (DPI, polling, debounce, angle snapping/tuning), dpi_xy,
# dpi_colors, led_logo / led_wheel / led_side (zones 0 / 1 / 2; G-Helper/libratbag: "underglow"), liftoff (lift-off
# + surface). Falchion RX Low Profile (approved 2026-11-13), G-Helper AsusKeyboard.cs; rogdriver sends the same two:
# profile (firmware, lighting effect, active profile), layout (factory layout id, ISO / ANSI).
MOUSE_SETTINGS_QUERIES = {n: c for n, c in MOUSE.reads().items() if n != MOUSE.capability("battery")["read"]}
KEYBOARD_SETTINGS_QUERIES = {n: c for n, c in KEYBOARD.reads().items() if n != KEYBOARD.capability("battery")["read"]}
# The transport refuses anything not on the list of the device it talks to: the keyboard never gets a mouse
# command and the other way round (same bytes can mean something else on the other device). Only commands
# devices.json marks "confirmed" are on it.
ALLOWED_READS = {d.kind: frozenset(d.reads().values()) for d in (KEYBOARD, MOUSE)}
ALLOWED_COMMANDS = ALLOWED_READS["keyboard"] | ALLOWED_READS["mouse"]   # all reads, for checks over both
# Report ID of each device on the Omni receiver; wired, both use unnumbered reports (None).
REPORT_IDS = {d.kind: _hex(d.route("omni")["report_id"]) for d in (KEYBOARD, MOUSE)}


# --- Capabilities ---------------------------------------------------------------------------------------------
# One module per capability in squirelib/capabilities/ (parameters from devices.json): its write builders, the byte
# check of its writes, the parser of its part of the read replies and the read-back check of a write (a Change).
# The write allowlist of a device = the writes its capabilities accept, with a prefix devices.json marks confirmed.
# The names below are the ones Squire has always used (window, tray, tests); they point into the capabilities.
CAPABILITIES = {d.kind: capabilities.for_device(d) for d in (KEYBOARD, MOUSE)}
_M, _K = CAPABILITIES["mouse"], CAPABILITIES["keyboard"]
_rgb = capabilities.rgb
# Confirmed write prefixes per device (devices.json status): a write needs one of them. Changing this dict changes
# nothing; the check reads DeviceCapabilities.confirmed_prefixes.
WRITE_PREFIXES = {kind: caps.confirmed_prefixes for kind, caps in CAPABILITIES.items()}

# Mouse LED zones (51 28 zone 00 mode brightness r g b 00 00 00; 50 03 save): capabilities/led_zones.py
_led = _M["led_zones"]
LED_WRITE, SAVE_COMMAND = _led.write_prefix, _led.save
LED_ZONE_INDEX, LED_ZONES = _led.zone_index, _led.zones
LED_WRITE_MODES, LED_MODES = _led.write_modes, _led.modes
led_command, led_matches = _led.command, _led.matches

# Mouse power (51 37 00 00 <power-off code> 00 <warning %>): capabilities/power.py
_power = _M["power"]
POWER_WRITE = _power.write_prefix
POWER_OFF_CODES, POWER_OFF_MINUTES = _power.power_off_codes, _power.power_off_minutes   # minutes (None = never)
LOW_WARNING_LEVELS = _power.warning_levels
power_command = _power.command

# Mouse sensor (51 31 06 angle snapping, 51 31 0b angle tuning): capabilities/sensor.py
_sensor = _M["sensor"]
SENSOR_WRITE = _sensor.write_prefix                       # 51 31 <field> 00 <value lo> <value hi>
ANGLE_SNAPPING_FIELD, ANGLE_TUNING_FIELD = _sensor.snapping_field, _sensor.tuning_field
ANGLE_TUNING_DEGREES = _sensor.tuning_degrees
angle_snapping_command, angle_tuning_command = _sensor.snapping_command, _sensor.tuning_command

# Mouse polling rate (51 31 04 00 <index>): capabilities/polling.py
_polling = _M["polling"]
POLLING_FIELD, POLLING_WRITE_HZ, POLLING_RATES_HZ = _polling.field, _polling.write_hz, _polling.read_rates_hz
polling_command = _polling.command

# Mouse lift-off + surface (51 35): capabilities/surface.py
_surface = _M["surface"]
LIFTOFF_WRITE, NO_SURFACE, LIFTOFF_CODES = _surface.write_prefix, _surface.no_surface, _surface.liftoff_codes
SURFACES, SURFACE_PARAMS = _surface.surfaces, _surface.surface_params
surface_name, liftoff_command, surface_command = _surface.surface_name, _surface.liftoff_command, _surface.surface_command

# Mouse DPI (51 31 <stage> / 09 active stage / 0a stage count): capabilities/dpi.py
_dpi = _M["dpi"]
ACTIVE_STAGE_FIELD, STAGE_COUNT_FIELD = _dpi.active_stage_field, _dpi.stage_count_field
DPI_STAGES, DPI_MIN, DPI_MAX, DPI_STEP = _dpi.stages, _dpi.min, _dpi.max, _dpi.step
dpi_command, active_stage_command, stage_count_command = _dpi.command, _dpi.active_stage_command, _dpi.stage_count_command

# Keyboard lighting (51 2c, 50 55 save): capabilities/kb_lighting.py
_kbl = _K["kb_lighting"]
KEYBOARD_LIGHT_MODES = _kbl.modes                 # 12 00 [9] effect id -> name
KEYBOARD_SAVE_COMMAND, KEYBOARD_LIGHT_WRITE = _kbl.save, _kbl.write_prefix
KEYBOARD_LIGHT_SPEEDS, KEYBOARD_LIGHT_IDS = _kbl.speeds, _kbl.ids
KEYBOARD_OFF, KEYBOARD_OFF_BRIGHTNESS, KEYBOARD_EFFECTS = kb_lighting.OFF, _kbl.off_brightness, _kbl.effects
KEYBOARD_SINGLE_COLOUR, KEYBOARD_OPTION_EFFECTS = kb_lighting.SINGLE_COLOUR, kb_lighting.OPTION_EFFECTS
KEYBOARD_COLOUR_MODES, KEYBOARD_SECOND_COLOUR = _kbl.colour_modes, kb_lighting.SECOND_COLOUR
KEYBOARD_GRADIENT_EFFECTS, KEYBOARD_GRADIENTS = kb_lighting.GRADIENT_EFFECTS, _kbl.gradients
KEYBOARD_GRADIENT_STOPS = _kbl.gradient_stops
KEYBOARD_DIRECTIONS, KEYBOARD_DIRECTION_ALIASES = _kbl.directions, kb_lighting.DIRECTION_ALIASES
KEYBOARD_THICKNESS = _kbl.thickness
KEYBOARD_QUICKSAND_SCALE, KEYBOARD_QUICKSAND_MODES = _kbl.quicksand_scale, _kbl.quicksand_modes
KEYBOARD_COLOURLESS, KEYBOARD_LIGHT_FIELDS = kb_lighting.COLOURLESS, kb_lighting.FIELDS
keyboard_effect_read, keyboard_speed, keyboard_direction = _kbl.effect_read, _kbl.speed, _kbl.direction
keyboard_gradient_stops, keyboard_gradient_name = _kbl.gradient_stops_of, _kbl.gradient_name
keyboard_light_value, keyboard_light_from, keyboard_light_packets = _kbl.value, _kbl.value_from, _kbl.packets

# Keyboard sleep (51 38) and low-battery warning (51 3a): capabilities/kb_sleep.py, kb_warning.py
_kb_sleep, _kb_warning = _K["kb_sleep"], _K["kb_warning"]
KEYBOARD_SLEEP_WRITE, KEYBOARD_WARNING_WRITE = _kb_sleep.write_prefix, _kb_warning.write_prefix
KEYBOARD_SLEEP_CODES, KEYBOARD_SLEEP_MINUTES = _kb_sleep.codes, _kb_sleep.minutes   # minutes (None = never)
KEYBOARD_WARNING_LEVELS = _kb_warning.levels       # 0 = never
keyboard_sleep_command, keyboard_warning_command = _kb_sleep.command, _kb_warning.command

# Keyboard per-key colours (c0 81 -> c0 82, RAM only): capabilities/per_key.py
_per_key = _K["per_key"]
KEYBOARD_DIRECT_WRITE, KEYBOARD_DIRECT_ACK = _per_key.write_prefix, _per_key.ack
KEYBOARD_DIRECT_PER_PACKET, KEYBOARD_DIRECT_KEYS = _per_key.per_packet, _per_key.keys
KEYBOARD_KEY_IDS = _per_key.layouts               # drawing order per layout (iso / ansi)
keyboard_key_ids, keyboard_keys_value, keyboard_direct_packets = _per_key.key_ids, _per_key.keys_value, _per_key.packets

# Keyboard layout (12 12 [5]): capabilities/kb_layout.py
KEYBOARD_LAYOUTS, KEYBOARD_ISO_LAYOUTS = _K["kb_layout"].layouts, _K["kb_layout"].iso_layouts


def keyboard_light_commands(effect: str, brightness: int, color: Optional[str] = "#ffffff",
                            speed: Optional[str] = None, gradient: Optional[str] = None,
                            direction: Optional[str] = None, **options) -> list:
    """Every 51 2c chunk for one lighting setting (see keyboard_light_value for the options)."""
    return keyboard_light_packets(keyboard_light_value(effect, brightness, color, speed, gradient, direction, **options))


def keyboard_light_command(effect: str, brightness: int, color: str = "#ffffff", speed: Optional[str] = None,
                           **options) -> bytes:
    """51 2c for a single-colour effect. `speed` None = "normal" if the effect has it, else its only value."""
    if keyboard_effect_read(effect) not in KEYBOARD_SINGLE_COLOUR:
        raise ValueError(f"{effect} is not a single-colour effect")
    return keyboard_light_commands(effect, brightness, color, speed, **options)[0]


def warning_text(level: Optional[int]) -> str:
    """A low-battery warning level for people: "20 %", "never" (0) or "–" (unknown)."""
    return "–" if level is None else "never" if level == 0 else f"{level} %"


def reply_expected(command: bytes) -> Optional[bytes]:
    """The reply that acknowledges a command that is not echoed: per-key colours c0 81 <n> 00 -> c0 82 <n> 00."""
    return next((r for caps in CAPABILITIES.values() if (r := caps.reply_expected(command)) is not None), None)


def is_allowed_write(command: bytes, device: str) -> bool:
    """Writes are allowed per device: a mouse command is never sent to the keyboard and the other way round.
    The command must start with a prefix devices.json marks confirmed for the device, and pass the byte check of
    the capability that sends it."""
    caps = CAPABILITIES.get(device)
    return caps is not None and caps.is_allowed_write(command)


# Battery reply layout per device (devices.json capabilities.battery, capabilities/battery.py).
BATTERY_LAYOUT = {kind: caps["battery"].params for kind, caps in CAPABILITIES.items()}


def parse(device: str, reply: bytes, base: int) -> tuple[Optional[int], bool]:
    return CAPABILITIES[device]["battery"].reading(reply, base)


def parse_warning(device: str, reply: bytes, base: int) -> Optional[int]:
    """Low-battery warning level (%) configured on the device, or None if absent/implausible."""
    return CAPABILITIES[device]["battery"].warning(reply, base)


def read_all(debug: bool = False, hid=None, bluetooth: Optional[Callable[[bool], list]] = None) -> list[Battery]:
    """HID first (Omni, cable); Bluetooth only fills in a device without a HID value.

    bluetooth: reader for the Bluetooth levels; default read_bluetooth on Linux when no fake hid is given."""
    with device_lock():
        results = _read_all(debug, hid)
    if bluetooth is None and hid is None and IS_LINUX:
        bluetooth = read_bluetooth
    if bluetooth is None or (len(results) == 2 and all(b.percent is not None for b in results)):
        return results   # nothing missing: no D-Bus call
    return merge_bluetooth(results, bluetooth(debug))


def merge_bluetooth(hid: list[Battery], bluetooth: list[Battery]) -> list[Battery]:
    """HID wins when it has a value; otherwise the Bluetooth reading, if any; otherwise HID's status
    (same rule as Bluetooth.merge in SquireCore; squirelib.routes.best_reading)."""
    return routes.best_reading(hid, bluetooth)


def _modalias_pid(modalias: str) -> Optional[int]:
    """'usb:v0B05p1B08d0001' -> 0x1B08 for an ASUS device, else None."""
    return transport.bluez.modalias_pid(modalias, VID)


def parse_bluez_objects(objects: dict) -> list[Battery]:
    """Batteries from BlueZ's ObjectManager.GetManagedObjects (busctl --json: {"type": ..., "data": ...} per value).

    Only connected devices with a known ASUS Bluetooth PID. Percentage 0 = asleep (same rule as the HID replies),
    above 100 = error; no Battery1 interface yet (BlueZ reads it shortly after connecting) = no-response."""
    def value(props: dict, key: str):
        v = props.get(key)
        return v.get("data") if isinstance(v, dict) else v

    found: dict[str, Battery] = {}
    for path, interfaces in sorted(objects.items()):
        device_props = interfaces.get("org.bluez.Device1")
        if not device_props or not value(device_props, "Connected"):
            continue
        device = BLUETOOTH_PIDS.get(_modalias_pid(value(device_props, "Modalias") or "") or -1)
        if device is None:
            continue
        name = next(t.name for t in TARGETS if t.device == device)
        battery_props = interfaces.get("org.bluez.Battery1")
        level = value(battery_props, "Percentage") if battery_props else None
        b = Battery(device, name, "bluetooth", None, raw=f"{path} Percentage={level}")
        if level is None:
            b.status = "no-response"
        elif not isinstance(level, int) or not 0 <= level <= 100:
            b.status = "error"
        elif level == 0:
            b.status = "asleep"
        else:
            b.percent = level
        if device not in found or (found[device].percent is None and b.percent is not None):
            found[device] = b
    return [found[k] for k in ("keyboard", "mouse") if k in found]


def read_bluetooth(debug: bool = False) -> list[Battery]:
    """Battery levels of the devices paired over Bluetooth (Linux: BlueZ org.bluez.Battery1)."""
    if not IS_LINUX:
        return []   # macOS: CoreBluetooth in the Swift app (BluetoothReader)
    results = parse_bluez_objects(bluez_objects(debug))
    if debug:
        for b in results:
            print(f"[{b.device}] bluetooth: {b.raw} -> {b.percent} ({b.status})", file=sys.stderr)
    return results


# --- Mouse settings (read-only) ------------------------------------------------------------------------------
# Each capability parses its part of the replies (capabilities/*.py: parse).


@dataclass
class MouseSettings:
    name: str
    connection: str
    status: str = "ok"                          # ok | asleep | no-response | no-access | error | not-found
    profile: Optional[int] = None               # 1-based, as on the device
    dpi_stage: Optional[int] = None             # active DPI stage, 1-based
    dpi_stage_count: Optional[int] = None       # stages the DPI button cycles through
    dpi: list = field(default_factory=list)     # DPI per stage
    dpi_xy: list = field(default_factory=list)  # [x, y] per stage
    dpi_colors: list = field(default_factory=list)
    polling_hz: Optional[int] = None
    debounce_index: Optional[int] = None        # raw value
    debounce_ms: Optional[int] = None           # 1..7 = 8..32 ms (libratbag and G-Helper agree); 0 is unclear
    polling_raw: Optional[int] = None           # low 3 bits = rate; G-Helper reads the high nibble in "booster" mode
    angle_snapping: Optional[bool] = None
    angle_tuning: Optional[int] = None          # degrees
    liftoff: Optional[str] = None               # low | high
    surface_id: Optional[int] = None            # preset surface (SURFACES), 0xff = none
    surface_params: Optional[str] = None        # its 3 parameter bytes as hex, None without a surface
    leds: dict = field(default_factory=dict)    # zone -> {mode, brightness (0-100 %), color}
    led_speed_raw: dict = field(default_factory=dict)  # zone -> raw speed byte from the reply (no effect seen)
    power_off_minutes: Optional[int] = None     # None with status ok = never
    low_warning: Optional[int] = None
    firmware: Optional[str] = None              # the mouse's own firmware
    receiver_firmware: Optional[str] = None     # the Omni receiver's (Omni only)
    raw: dict = field(default_factory=dict)     # command name -> reply hex (for captures)


def parse_mouse_settings(s: MouseSettings, name: str, p: bytes) -> None:
    """Fill s from one reply payload p (starting at the echoed command); each capability parses its part."""
    if len(p) < 20:
        return
    _M.parse(s, name, p)


def read_mouse_settings(debug: bool = False, hid=None) -> MouseSettings:
    """Current settings of the Keris II Origin (active profile only; never switches profiles).

    Sends only the read commands in MOUSE_SETTINGS_QUERIES plus the battery query."""
    return SESSIONS.read_settings("mouse", debug, hid)


# --- Keyboard settings (Falchion RX Low Profile), read-only ---
# Profile, lighting effect and firmware: capabilities/kb_profile.py; layout: kb_layout.py; sleep, warning: kb_sleep.py,
# kb_warning.py (from the battery reply 12 01).


@dataclass
class KeyboardSettings:
    name: str
    connection: str
    status: str = "ok"                          # ok | asleep | no-response | no-access | error | not-found
    profile: Optional[int] = None               # 1-based (01/02 = profile 1/2, owner 2026-11-13; 5 profiles)
    light_mode: Optional[str] = None            # effect the keyboard runs now (colour is not readable)
    layout_id: Optional[int] = None
    layout: Optional[str] = None                # e.g. CH-DE; None for an id Squire does not know
    iso: Optional[bool] = None
    sleep_minutes: Optional[int] = None         # 12 01 [6], mouse power-off coding (AC, 2026-11-21)
    low_warning: Optional[int] = None
    firmware: Optional[str] = None              # the keyboard's: 12 00 [11..13] via the Omni, [4..6] wired
    receiver_firmware: Optional[str] = None     # the Omni receiver's (Omni only)
    raw: dict = field(default_factory=dict)     # command name -> reply hex (for captures)


def parse_keyboard_settings(s: KeyboardSettings, name: str, p: bytes) -> None:
    """Fill s from one reply payload p (starting at the echoed command)."""
    if len(p) < 14:
        return
    _K.parse(s, name, p)


def read_keyboard_settings(debug: bool = False, hid=None) -> KeyboardSettings:
    return SESSIONS.read_settings("keyboard", debug, hid)


# --- Sessions (squirelib/session.py): every packet is built by SESSIONS.packet, the allowlist gate ---
SESSIONS = session.Sessions(DEVICES, CAPABILITIES, TARGETS, {
    "mouse": session.SettingsReader(lambda name, connection: MouseSettings(name, connection), parse_mouse_settings),
    "keyboard": session.SettingsReader(lambda name, connection: KeyboardSettings(name, connection),
                                       parse_keyboard_settings),
})
packet, build_packet, exchange, query = SESSIONS.packet, SESSIONS.build_packet, SESSIONS.exchange, SESSIONS.query
enumerate_paths, write_verified = SESSIONS.enumerate_paths, SESSIONS.write_verified


def _read_all(debug: bool = False, hid=None) -> list[Battery]:
    return SESSIONS.read_batteries(debug, hid)


def print_keyboard_settings(s: KeyboardSettings) -> None:
    if s.status != "ok":
        print(f"{s.name}: {s.status}")
        return
    firmware = f", firmware {s.firmware}" if s.firmware else ""
    receiver = f", receiver {s.receiver_firmware}" if s.receiver_firmware else ""
    print(f"{s.name} ({s.connection}){firmware}{receiver}, profile {s.profile}")
    layout = s.layout or f"unknown id {s.layout_id}"
    print(f"  Layout:       {layout}{' (ISO)' if s.iso else ''}")
    print(f"  Lighting:     {s.light_mode} (the colour cannot be read)")
    sleep = "never sleeps" if s.sleep_minutes is None else f"sleep after {s.sleep_minutes} min"
    print(f"  Power:        {sleep}, low-battery warning {warning_text(s.low_warning)}")


def _apply(change, save: bool, dry_run: bool, debug: bool, hid, device: str = "mouse") -> WriteResult:
    """Run a capability's Change through write_verified (one device session)."""
    return write_verified(change.command, change.reads, change.extract, change.matches, save, dry_run, debug, hid,
                          device=device)


def set_led(zone: str, mode: str, brightness: int, color: str = "#000000", save: bool = False,
            dry_run: bool = False, debug: bool = False, hid=None) -> WriteResult:
    """Set one LED zone of the Keris II Origin, then read it back. Only on an explicit request (CLI or a click).

    Without save the change lives in the mouse's RAM (lost when it powers off); save=True also sends 50 03."""
    return _apply(_led.change(zone, mode, brightness, color), save, dry_run, debug, hid)


def set_power(power_off_minutes: Optional[int], low_warning: int, save: bool = False, dry_run: bool = False,
              debug: bool = False, hid=None) -> WriteResult:
    """Auto power-off (1/2/3/5/10 min, None = never) and low-battery warning (%), read back from 12 07."""
    return _apply(_power.change(power_off_minutes, low_warning), save, dry_run, debug, hid)


def set_angle_snapping(on: bool, save: bool = False, dry_run: bool = False, debug: bool = False,
                       hid=None) -> WriteResult:
    return _apply(_sensor.snapping_change(on), save, dry_run, debug, hid)


def set_angle_tuning(degrees: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                     hid=None) -> WriteResult:
    return _apply(_sensor.tuning_change(degrees), save, dry_run, debug, hid)


def set_liftoff(liftoff: str, save: bool = False, dry_run: bool = False, debug: bool = False,
                hid=None) -> WriteResult:
    """Lift-off low/high. Reads 12 06 first and writes the surface id + parameters it found back unchanged; the
    read-back must show the new lift-off and the same surface. --dry-run reads 12 06 (a get) to show the bytes."""
    return _apply(_surface.liftoff_change(liftoff), save, dry_run, debug, hid)


def set_surface(surface_id: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                hid=None) -> WriteResult:
    """Pick a preset surface (mouse pad) or NO_SURFACE. Keeps the lift-off read from 12 06 just before (Armoury Crate
    resets it to low). Refused while the mouse has a surface Squire does not know: it may be a calibration."""
    return _apply(_surface.surface_change(surface_id), save, dry_run, debug, hid)


def set_polling(hz: int, save: bool = False, dry_run: bool = False, debug: bool = False, hid=None) -> WriteResult:
    """Polling rate 125/250/500/1000 Hz, read back from 12 04 00 [12] (low 3 bits)."""
    return _apply(_polling.change(hz), save, dry_run, debug, hid)


def set_dpi(stage: int, dpi: int, color: Optional[str] = None, save: bool = False, dry_run: bool = False,
            debug: bool = False, hid=None) -> WriteResult:
    """One DPI stage (1-based), X and Y alike. The stage colour travels in the same command: without `color` the
    one read from 12 04 03 just before is written back. Refused for a stage with separate X/Y values."""
    return _apply(_dpi.change(stage, dpi, color), save, dry_run, debug, hid)


def set_active_stage(stage: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                     hid=None) -> WriteResult:
    """The DPI stage in use (1-based), read back from 12 00 [11]. Only a stage the DPI button cycles through."""
    return _apply(_dpi.active_stage_change(stage), save, dry_run, debug, hid)


def set_stage_count(count: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                    hid=None) -> WriteResult:
    """How many stages the DPI button cycles through, read back from 12 00 [18]. Never fewer than the active stage
    (what the mouse would do then is unknown)."""
    return _apply(_dpi.stage_count_change(count), save, dry_run, debug, hid)


def keyboard_light_file() -> Path:
    return state_dir() / "keyboard-lighting.json"


def remembered_keyboard_light(profile: Optional[int], effect: Optional[str], path: Optional[Path] = None) -> Optional[dict]:
    """The lighting Squire last saved to this profile, if the keyboard still runs that effect. The keyboard cannot
    report its colour or brightness, so this is the only way to show them; if the effect differs (changed on the
    keyboard or in Armoury Crate) the remembered values are stale and None is returned."""
    if profile is None or effect is None:
        return None
    try:
        data = _keyboard_light_data(path or keyboard_light_file())
        value = keyboard_light_from(data["profiles"][str(profile)])      # still valid
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return value if keyboard_effect_read(value["effect"]) == effect else None


KEYBOARD_LIGHT_FILE_VERSION = 2


def _keyboard_light_data(path: Path) -> dict:
    """The remembered lighting, migrated: before version 2 Squire sent reactive only at 0f and called it "fast";
    AC calls 0f "normal" (capture_4). The old direction names are mapped by keyboard_direction."""
    data = json.loads(path.read_text())
    profiles = data["profiles"]
    if data.get("version", 1) < 2:
        for stored in profiles.values():
            if isinstance(stored, dict) and stored.get("effect") == "reactive" and stored.get("speed") == "fast":
                stored["speed"] = "normal"
    return data


def remember_keyboard_light(profile: int, value: dict, path: Optional[Path] = None) -> None:
    path = path or keyboard_light_file()
    try:
        data = _keyboard_light_data(path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        data = {"profiles": {}}
    data["version"] = KEYBOARD_LIGHT_FILE_VERSION
    data["profiles"][str(profile)] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as error:
        print(f"squire: could not remember the keyboard lighting: {error}", file=sys.stderr)


def set_keyboard_light(effect: str, brightness: int, color: Optional[str] = "#ffffff", speed: Optional[str] = None,
                       gradient: Optional[str] = None, direction: Optional[str] = None,
                       save: bool = False, dry_run: bool = False, debug: bool = False, hid=None,
                       **options) -> WriteResult:
    """Lighting effect of the Falchion. Only the effect can be read back (12 00 [9]): colour and brightness are not
    readable on this keyboard (Armoury Crate keeps its own copy too), so the result says so. Once saved, the values
    are remembered for the active profile (remembered_keyboard_light) so the window can show them."""
    value = keyboard_light_value(effect, brightness, color, speed, gradient, direction, **options)
    result = _apply(_kbl.change(value), save, dry_run, debug, hid, device="keyboard")
    if result.status == "ok" and result.saved and result.after.get("profile"):
        remember_keyboard_light(result.after["profile"], value)
    if result.status == "ok" and not result.message:
        result.message = "effect read back; colour and brightness cannot be read from the keyboard"
    return result


def set_keyboard_keys(spec: str, save: bool = False, dry_run: bool = False, debug: bool = False,
                      hid=None) -> WriteResult:
    """--set-kb-keys: KEY=RRGGBB,..., all=RRGGBB or @PRESET (see set_keyboard_key_colours)."""
    if save:
        raise ValueError("per-key colours cannot be saved: the keyboard keeps them in RAM only (50 55 stores nothing)")
    return set_keyboard_key_colours(keyboard_keys_spec(spec), dry_run=dry_run, debug=debug, hid=hid)


def set_keyboard_key_colours(colours: dict, dry_run: bool = False, debug: bool = False, hid=None) -> WriteResult:
    """Per-key colours {key id: (r, g, b)} (direct mode, c0 81). Each packet must be acknowledged (c0 82). Nothing to
    read back except the effect in 12 00 [9], which is reported. Never saved: 50 55 after it was tested (2026-11-25,
    protocol.json keyboard_writes.direct) and stores nothing, the profile keeps its effect."""
    result = _apply(_per_key.change(colours), False, dry_run, debug, hid, device="keyboard")
    if result.status == "ok":
        result.message = (f"sent; effect now reads {result.after.get('light_mode')!r}; "
                          "key colours cannot be read back; lost at off/on")
    return result


# --- Per-key setups ("key presets"), kept by Squire: the keyboard cannot store per-key colours (50 55 tested) ---
KEY_PRESETS_VERSION = 1


# Built-in key presets: common per-key looks, generated per layout (ISO / ANSI) from the key ids, read-only.
# Key ids (KEYBOARD_KEY_IDS): column = id // 8 (0..14, left to right), row = id % 8 (1..5, top to bottom).
KEY_GROUPS = {
    "wasd": (0x12, 0x0B, 0x13, 0x1B),                          # W A S D
    "arrows": (0x6C, 0x65, 0x6D, 0x75),                        # up, left, down, right
    "nav": (0x71, 0x72, 0x73, 0x74),                           # Ins, Del, PgUp, PgDn
    "home": (0x23, 0x3B),                                      # F and J
    "game_mods": (0x35, 0x04, 0x05),                           # Space, left Shift, left Ctrl
    # Esc, Tab, Caps, Shift, Ctrl, Win, Alt, Space, Alt, Fn, Ctrl, Backspace, Enter, ANSI backslash, right Shift
    "modifiers": (0x01, 0x02, 0x03, 0x04, 0x05, 0x0D, 0x15, 0x35, 0x4D, 0x55, 0x5D, 0x69, 0x6B, 0x6A, 0x64),
}


def _hue(h: float) -> str:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return "#%02x%02x%02x" % tuple(int(c * 255 + 0.5) for c in (r, g, b))   # halves up, like the Swift port


def _blend(a: str, b: str, t: float) -> str:
    ca, cb = bytes.fromhex(a[1:]), bytes.fromhex(b[1:])
    return "#" + bytes(int(x + (y - x) * t + 0.5) for x, y in zip(ca, cb)).hex()   # halves up, like the Swift port


def builtin_key_presets(iso: Optional[bool] = None) -> dict:
    """{name: {key id: "#rrggbb"}} for the keys of this layout. Colours, not brightness: the keyboard has no
    brightness in per-key mode, so dimmed keys are dark colours."""
    ids = keyboard_key_ids(iso)
    on = lambda group, colour, base: {k: (colour if k in KEY_GROUPS[group] else base[k]) for k in ids}  # noqa: E731
    dim = {k: "#202020" for k in ids}
    gaming = on("game_mods", "#ff8000", on("arrows", "#ff0000", on("wasd", "#ff0000", dim)))
    white = {k: "#ffffff" for k in ids}
    modifiers = on("arrows", "#0000ff", on("nav", "#0000ff", on("modifiers", "#0000ff", white)))   # LEDs: strong hues
    return {
        "Fixed rainbow": {k: _hue(k // 8 / 15) for k in ids},       # "fixed": not the rainbow effect
        "Gaming": gaming,
        "Typing": on("home", "#0000ff", {k: "#ff8c00" for k in ids}),
        "Modifiers": modifiers,
        "Ocean": {k: _blend("#0030ff", "#00ffc8", (k % 8 - 1) / 4) for k in ids},
        "Fixed sunset": {k: _blend("#ff7a00", "#8a00ff", k // 8 / 14) for k in ids},
    }


def all_key_presets(iso: Optional[bool] = None, path: Optional[Path] = None) -> dict:
    """The user's presets, then the built-in ones (a built-in name cannot be saved over)."""
    return {**key_presets(path), **builtin_key_presets(iso)}


def key_presets_file() -> Path:
    return state_dir() / "keyboard-keys.json"


def key_presets(path: Optional[Path] = None) -> dict:
    """{name: {key id: "#rrggbb"}}; broken or missing entries are skipped, a broken file reads as empty."""
    try:
        data = json.loads((path or key_presets_file()).read_text())
        stored = data["presets"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    presets = {}
    for name, entry in (stored.items() if isinstance(stored, dict) else ()):
        try:
            keys = {int(k, 16): _rgb(v) for k, v in entry["keys"].items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if isinstance(name, str) and name.strip() and all(k in KEYBOARD_DIRECT_KEYS for k in keys):
            presets[name] = keys
    return presets


def _write_key_presets(presets: dict, path: Optional[Path]) -> None:
    path = path or key_presets_file()
    data = {"version": KEY_PRESETS_VERSION,
            "presets": {name: {"keys": {f"{k:02x}": v for k, v in sorted(keys.items())}}
                        for name, keys in sorted(presets.items(), key=lambda item: item[0].casefold())}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def save_key_preset(name: str, keys: dict, path: Optional[Path] = None) -> None:
    """Store (or replace) a named per-key setup: {key id: "#rrggbb"}. Only Squire's file changes, never the keyboard."""
    name = name.strip()
    if not name:
        raise ValueError("a preset needs a name")
    if name.casefold() in (n.casefold() for n in builtin_key_presets()):
        raise ValueError(f"{name!r} is a Squire default preset: choose another name")
    keys = {key: _rgb(color) for key, color in keys.items()}
    if not keys or any(key not in KEYBOARD_DIRECT_KEYS for key in keys):
        raise ValueError("a preset needs at least one key, all within 01..75")
    presets = key_presets(path)
    presets[name] = keys
    _write_key_presets(presets, path)


def delete_key_preset(name: str, path: Optional[Path] = None) -> bool:
    presets = key_presets(path)
    if presets.pop(name, None) is None:
        return False
    _write_key_presets(presets, path)
    return True


def key_preset_colours(keys: dict, iso: Optional[bool] = None) -> dict:
    """What a preset sends: every key of the layout, keys it does not name black, so the result does not depend on
    what was lit before. {key id: (r, g, b)}."""
    full = {key: "#000000" for key in keyboard_key_ids(iso)}
    full.update(keys)
    return {key: tuple(bytes.fromhex(color[1:])) for key, color in full.items()}


def keyboard_keys_spec(spec: str, iso: Optional[bool] = None, path: Optional[Path] = None) -> dict:
    """--set-kb-keys: "@NAME" is a stored preset (all keys, unnamed ones black), anything else keyboard_keys_value."""
    if spec.startswith("@"):
        presets = all_key_presets(iso, path)
        if spec[1:] not in presets:
            raise ValueError(f"no key preset {spec[1:]!r} (see --kb-presets)")
        return key_preset_colours(presets[spec[1:]], iso)
    return keyboard_keys_value(spec)


def set_keyboard_sleep(minutes: Optional[int], save: bool = False, dry_run: bool = False, debug: bool = False,
                       hid=None) -> WriteResult:
    """Sleep after 1/2/3/5/10 min (None = never), read back from 12 01 [6]."""
    return _apply(_kb_sleep.change(minutes), save, dry_run, debug, hid, device="keyboard")


def set_keyboard_warning(percent: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                         hid=None) -> WriteResult:
    """Low-battery warning level (10..50 %, 0 = never), read back from 12 01 [9]."""
    return _apply(_kb_warning.change(percent), save, dry_run, debug, hid, device="keyboard")


def print_mouse_settings(s: MouseSettings) -> None:
    if s.status != "ok":
        print(f"{s.name}: {s.status}")
        return
    stages = ", ".join(f"{'*' if s.dpi_stage == i + 1 else ''}{d}" for i, d in enumerate(s.dpi))
    receiver = f", receiver {s.receiver_firmware}" if s.receiver_firmware else ""
    print(f"{s.name} ({s.connection}), firmware {s.firmware}{receiver}, profile {s.profile}")
    print(f"  DPI stages:   {stages}   (* = active)")
    if s.dpi_xy:
        print(f"  DPI X/Y:      {', '.join(f'{x}/{y}' for x, y in s.dpi_xy)}")
    if s.dpi_colors:
        print(f"  DPI colors:   {', '.join(s.dpi_colors)}")
    debounce = f"{s.debounce_ms} ms" if s.debounce_ms else f"index {s.debounce_index}"
    print(f"  DPI button:   cycles through {s.dpi_stage_count} stages")
    print(f"  Polling rate: {s.polling_hz} Hz (raw 0x{s.polling_raw or 0:02x})   debounce {debounce}")
    print(f"  Angle:        snapping {'on' if s.angle_snapping else 'off'}, tuning {s.angle_tuning}°")
    params = f" ({s.surface_params})" if s.surface_params else ""
    print(f"  Lift-off:     {s.liftoff}, surface {surface_name(s.surface_id)}{params}")
    for zone in LED_ZONES:
        led = s.leds.get(zone)
        if led:
            print(f"  LED {zone:<6}    {led['mode']}, {led['brightness']} %, {led['color']}")
    power = "never" if s.power_off_minutes is None else f"{s.power_off_minutes} min"
    print(f"  Power:        off after {power}, low-battery warning {s.low_warning} %")


def list_devices(hid=None) -> None:
    for info in (hid or load_backend()).enumerate(VID, 0):
        pages = info.get("collections")
        usage = (" collections=" + ",".join(f"{p:04x}:{u:04x}" for p, u in pages) if pages is not None else
                 f" usage_page={info.get('usage_page', 0):04x} usage={info.get('usage', 0):04x}")
        print(f"pid={info['product_id']:04x} iface={info.get('interface_number')}{usage} "
              f"name={info.get('product_string')!r} path={info['path']!r}")
    if hid is None:   # Bluetooth (Linux: what BlueZ already holds; sends nothing to the device)
        for b in read_bluetooth():
            pid = next(p for p, d in BLUETOOTH_PIDS.items() if d == b.device)
            level = f"{b.percent}%" if b.percent is not None else b.status
            print(f"pid={pid:04x} bluetooth battery={level} name={b.name!r} ({b.raw.split()[0]})")


def send_notification(title: str, message: str) -> None:
    """Desktop notification: notify-send on Linux, osascript on macOS.
    Arguments are passed separately, never interpolated into a script."""
    if IS_LINUX:
        try:
            subprocess.run(["notify-send", "--app-name=Squire", "--icon=squire-gear", "--", title, message],
                           check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    try:
        subprocess.run(["osascript", "-e", script, title, message], check=False, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_low_battery(results: list[Battery], state_path: Path = STATE_FILE,
                      notify: Callable[[str, str], None] = send_notification) -> list[str]:
    """Notify once per device when the battery falls to the device's own warning level.

    The flag resets when the device charges or recovers above the level. Asleep devices keep their state.
    Returns the devices that were notified."""
    state = _load_state(state_path)
    original = dict(state)
    notified = []
    for b in results:
        if b.percent is None:
            continue
        level = b.low_warning if b.low_warning is not None else DEFAULT_LOW_WARNING
        if level == 0 or b.charging or b.percent > level:          # 0: the device is set to never warn
            state.pop(b.device, None)
        elif not state.get(b.device):
            notify(f"{b.name}: battery low", f"{b.percent}% left (device warning level {level}%)")
            state[b.device] = True
            notified.append(b.device)
    if state != original:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state))
        except OSError:
            pass
    return notified


SF_ICONS = {"keyboard": ":keyboard:", "mouse": ":computermouse:"}  # SwiftBar renders :name: as SF Symbol


def color_for(b: Battery) -> str:
    if b.percent is None:
        return "gray"
    if b.charging:
        return "green"
    return "red" if b.percent <= 15 else ("orange" if b.percent <= 30 else "")


def label(b: Battery, bolt: bool = True) -> str:
    if b.percent is None:
        return "–"
    return f"{b.percent}%" + (" :bolt.fill:" if b.charging and bolt else "")


def swiftbar(results: list[Battery]) -> str:
    if not results:
        return ":keyboard: – :computermouse: – | color=gray\n---\nNo ASUS devices found\nRefresh | refresh=true"
    title = "  ".join(f"{SF_ICONS[b.device]} {label(b)}" for b in results)
    lowest = min((b for b in results if b.percent is not None), key=lambda b: b.percent, default=None)
    title_color = color_for(lowest) if lowest and color_for(lowest) in ("red", "orange") else ""
    lines = [title + (f" | color={title_color}" if title_color else ""), "---"]
    status_text = {"asleep": "asleep / not connected", "no-response": "no response", "error": "error",
                   "no-access": "no access (udev rule missing)"}
    for b in results:
        detail = label(b, bolt=False) if b.percent is not None else status_text.get(b.status, b.status)
        extra = " (charging)" if b.charging else ""
        c = color_for(b)
        lines.append(f"{b.name}: {detail}{extra} | sfimage={'keyboard' if b.device == 'keyboard' else 'computermouse'}"
                     + (f" color={c}" if c else ""))
        lines.append(f"--Connection: {b.connection}")
    lines += ["---", "Refresh | refresh=true"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="JSON output")
    mode.add_argument("--swiftbar", action="store_true", help="SwiftBar/xbar menu bar output")
    mode.add_argument("--list", action="store_true", help="List ASUS HID collections; sends nothing")
    ap.add_argument("--notify", action="store_true",
                    help="Send a desktop notification when a battery reaches the device's low-battery warning level")
    ap.add_argument("--set-led", metavar="ZONE", choices=list(LED_ZONE_INDEX),
                    help="WRITE: set one LED zone of the mouse (logo, wheel, side); use with --mode, "
                         "--brightness, --color; --dry-run prints the bytes only; --save also stores it")
    ap.add_argument("--set-power", nargs=2, metavar=("OFF", "WARN"),
                    help="WRITE: auto power-off (1, 2, 3, 5, 10 min or never) and low-battery warning "
                         "(10-50 %%), e.g. --set-power 5 20; --dry-run / --save as for --set-led")
    ap.add_argument("--set-angle-snapping", choices=["on", "off"], help="WRITE: angle snapping on or off")
    ap.add_argument("--set-angle-tuning", type=int, metavar="DEG",
                    help="WRITE: angle tuning in degrees, -20..20 in steps of 5")
    ap.add_argument("--set-dpi", nargs=2, type=int, metavar=("STAGE", "DPI"),
                    help="WRITE: DPI of one stage (1-4), X and Y alike, 100-42000 in steps of 50; --color also "
                         "sets the stage colour")
    ap.add_argument("--set-dpi-stage", type=int, metavar="N", help="WRITE: the active DPI stage (1-4)")
    ap.add_argument("--set-dpi-count", type=int, metavar="N", help="WRITE: stages on the DPI button (1-4)")
    ap.add_argument("--set-polling", type=int, choices=sorted(POLLING_WRITE_HZ), metavar="HZ",
                    help="WRITE: polling rate, 125, 250, 500 or 1000 Hz")
    ap.add_argument("--set-surface", metavar="ID",
                    help="WRITE: surface (mouse pad) preset by hex id (see --list-surfaces) or 'none'; keeps the lift-off")
    ap.add_argument("--list-surfaces", action="store_true", help="List the surface presets; sends nothing")
    ap.add_argument("--set-liftoff", choices=list(LIFTOFF_CODES),
                    help="WRITE: lift-off distance, keeps the surface (mouse pad) setting; --dry-run reads it "
                         "and prints the bytes")
    ap.add_argument("--set-kb-light", metavar="EFFECT", choices=KEYBOARD_EFFECTS,
                    help="WRITE: keyboard lighting effect (" + ", ".join(KEYBOARD_EFFECTS) + "); with "
                         "--brightness, --color, --speed and the effect's options (--gradient, --direction, "
                         "--thickness, --color-mode, --color2, --background); only the effect can be read back")
    ap.add_argument("--gradient", metavar="NAME|POS:RRGGBB,...",
                    help="keyboard rainbow / ripple colours: " + ", ".join(KEYBOARD_GRADIENTS) + " (default spectrum)"
                         " or 2-7 stops like 14:ff0000,100:0000ff")
    ap.add_argument("--direction", choices=sorted({d for ds in KEYBOARD_DIRECTIONS.values() for d in ds}
                                                  | set(KEYBOARD_DIRECTION_ALIASES)),
                    help="keyboard rainbow direction (default right = left to right), quicksand down / up")
    ap.add_argument("--thickness", choices=list(KEYBOARD_THICKNESS), help="keyboard rainbow / ripple (default strong)")
    ap.add_argument("--color-mode", choices=sorted(set(KEYBOARD_COLOUR_MODES) | set(KEYBOARD_QUICKSAND_MODES)),
                    help="keyboard starry night / current / raindrop: single, double, random; quicksand: scale, random")
    ap.add_argument("--color2", help="#rrggbb: second keyboard colour (double / random; default #ff00ff)")
    ap.add_argument("--background", help="#rrggbb or none: keyboard background (starry night, current, raindrop)")
    ap.add_argument("--speed", choices=["fast", "normal", "slow", "none"],
                    help="keyboard effect speed (default: normal, or the effect's only speed)")
    ap.add_argument("--set-kb-keys", metavar="KEY=RRGGBB,...",
                    help="WRITE (test): per-key colours, hex key id = column * 8 + row, rows 1..5 (Esc 01, Q 0a) or all; "
                         "lost at off/on, cannot be saved, nothing read back; @NAME sends a key preset")
    ap.add_argument("--save-kb-preset", metavar="NAME",
                    help="store --set-kb-keys' colours as a key preset in Squire (sends nothing)")
    ap.add_argument("--delete-kb-preset", metavar="NAME", help="delete a key preset (sends nothing)")
    ap.add_argument("--kb-presets", action="store_true", help="list the key presets; sends nothing")
    ap.add_argument("--set-kb-sleep", metavar="MIN", help="WRITE: keyboard sleep after 1, 2, 3, 5, 10 min or never")
    ap.add_argument("--set-kb-warning", metavar="PCT",
                    help="WRITE: keyboard low-battery warning in %% (" + ", ".join(map(str, LOW_WARNING_LEVELS))
                         + ") or never")
    ap.add_argument("--mode", choices=list(LED_WRITE_MODES), default="static")
    ap.add_argument("--brightness", type=int, default=100)
    ap.add_argument("--color", help="#rrggbb: LED colour (default #ffffff), or the DPI stage colour (default: keep)")
    ap.add_argument("--dry-run", action="store_true", help="print the bytes a write would send, send nothing")
    ap.add_argument("--save", action="store_true", help="after a write, store it in the device (mouse 50 03, keyboard 50 55)")
    ap.add_argument("--settings", action="store_true",
                    help="Read the mouse settings (DPI, LEDs, sensor, power); read-only, add --json for JSON")
    ap.add_argument("--keyboard-settings", action="store_true",
                    help="Read the keyboard settings (profile, layout, lighting effect); read-only, add --json")
    ap.add_argument("--debug", action="store_true", help="Print raw replies to stderr")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()
    try:
        if args.list:
            list_devices()
            return 0
        if args.kb_presets or args.save_kb_preset or args.delete_kb_preset:
            try:
                if args.save_kb_preset:
                    if not args.set_kb_keys or args.set_kb_keys.startswith("@"):
                        raise ValueError("--save-kb-preset needs --set-kb-keys KEY=RRGGBB,...")
                    keys = {k: "#" + bytes(rgb).hex() for k, rgb in keyboard_keys_value(args.set_kb_keys).items()}
                    save_key_preset(args.save_kb_preset, keys)
                    print(f"saved key preset {args.save_kb_preset!r} ({len(keys)} keys); nothing sent")
                elif args.delete_kb_preset:
                    if not delete_key_preset(args.delete_kb_preset):
                        raise ValueError(f"no key preset {args.delete_kb_preset!r}")
                    print(f"deleted key preset {args.delete_kb_preset!r}")
                else:
                    for name, keys in key_presets().items():
                        print(f"  {name}  ({len(keys)} keys)")
                    for name in builtin_key_presets():
                        print(f"  {name}  (Squire default)")
            except (ValueError, OSError) as error:
                print(f"refused: {error}", file=sys.stderr)
                return 2
            return 0
        if args.list_surfaces:
            for sid in sorted(SURFACE_PARAMS, key=lambda i: (i == NO_SURFACE, surface_name(i))):
                print(f"  {'none' if sid == NO_SURFACE else f'{sid:02x}'}  {surface_name(sid)}")
            return 0
        writes = [w for w in (args.set_led, args.set_power, args.set_angle_snapping,
                              args.set_angle_tuning is not None or None, args.set_liftoff, args.set_polling,
                              args.set_surface, args.set_dpi, args.set_dpi_stage, args.set_dpi_count,
                              args.set_kb_light, args.set_kb_sleep, args.set_kb_warning,
                              args.set_kb_keys) if w]
        if len(writes) > 1:
            print("refused: one write per call", file=sys.stderr)
            return 2
        if writes:
            options = dict(save=args.save, dry_run=args.dry_run, debug=args.debug)
            try:
                if args.set_led:
                    result = set_led(args.set_led, args.mode, args.brightness, args.color or "#ffffff", **options)
                elif args.set_power:
                    off, warn = args.set_power
                    minutes = None if off == "never" else int(off) if off.isdigit() else -1
                    result = set_power(minutes, int(warn) if warn.isdigit() else -1, **options)
                elif args.set_angle_snapping:
                    result = set_angle_snapping(args.set_angle_snapping == "on", **options)
                elif args.set_liftoff:
                    result = set_liftoff(args.set_liftoff, **options)
                elif args.set_dpi:
                    result = set_dpi(*args.set_dpi, color=args.color, **options)
                elif args.set_dpi_stage:
                    result = set_active_stage(args.set_dpi_stage, **options)
                elif args.set_dpi_count:
                    result = set_stage_count(args.set_dpi_count, **options)
                elif args.set_polling:
                    result = set_polling(args.set_polling, **options)
                elif args.set_kb_light:
                    background = None if (args.background or "").lower() in ("", "none") else args.background
                    result = set_keyboard_light(args.set_kb_light, args.brightness, args.color or "#ffffff",
                                                args.speed, args.gradient, args.direction, thickness=args.thickness,
                                                mode=args.color_mode, color2=args.color2, background=background,
                                                **options)
                elif args.set_kb_keys:
                    result = set_keyboard_keys(args.set_kb_keys, **options)
                elif args.set_kb_sleep:
                    off = args.set_kb_sleep
                    result = set_keyboard_sleep(None if off == "never" else int(off) if off.isdigit() else -1, **options)
                elif args.set_kb_warning:
                    warn = args.set_kb_warning.lower()
                    result = set_keyboard_warning(0 if warn == "never" else int(warn) if warn.isdigit() else -1,
                                                  **options)
                elif args.set_surface:
                    sid = NO_SURFACE if args.set_surface.lower() == "none" else int(args.set_surface, 16)
                    result = set_surface(sid, **options)
                else:
                    result = set_angle_tuning(args.set_angle_tuning, **options)
            except ValueError as error:
                print(f"refused: {error}", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(asdict(result), indent=2))
            else:
                print(f"{result.status}{': ' + result.message if result.message else ''}")
                for line in result.sent:
                    print(f"  > {line}")
                if result.before is not None:
                    print(f"  before: {result.before}")
                if result.after is not None:
                    print(f"  after:  {result.after}")
                if result.saved:
                    print("  saved (keyboard 50 55)" if args.set_kb_light or args.set_kb_sleep or args.set_kb_warning or args.set_kb_keys
                          else "  saved in the mouse (50 03)")
            if args.save and result.status == "ok" and not result.saved:
                return 1
            return 0 if result.status in ("ok", "dry-run") else 1
        if args.keyboard_settings:
            settings = read_keyboard_settings(args.debug)
            if args.json:
                print(json.dumps(asdict(settings), indent=2, ensure_ascii=False))
            else:
                print_keyboard_settings(settings)
            return 0
        if args.settings:
            settings = read_mouse_settings(args.debug)
            if args.json:
                print(json.dumps(asdict(settings), indent=2, ensure_ascii=False))
            else:
                print_mouse_settings(settings)
            return 0
        results = read_all(args.debug)
    except ImportError:
        msg = "Python module 'hid' is missing: pip install hidapi"
        print(f":keyboard: ! | color=red\n---\n{msg}" if args.swiftbar else msg, file=sys.stdout if args.swiftbar else sys.stderr)
        return 0 if args.swiftbar else 1
    if args.notify:
        check_low_battery(results)
    if args.json:
        print(json.dumps([asdict(b) for b in results], indent=2, ensure_ascii=False))
    elif args.swiftbar:
        print(swiftbar(results))
    else:
        if not results:
            print("No ASUS devices found.")
        for b in results:
            print(f"{b.name:<22} {label(b, bolt=False):>5}{' charging' if b.charging else ''}  ({b.connection}, {b.status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

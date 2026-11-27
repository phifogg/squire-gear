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
import select
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

VID = 0x0B05
PID_OMNI = 0x1ACE           # ROG Omni receiver (keyboard + mouse share it)
PID_FALCHION_RX_LP = 0x1B04  # keyboard, wired
PID_KERIS_II_ORIGIN = 0x1C0C  # mouse, wired
# Paired directly over Bluetooth LE (macOS 2026-11-26, Linux 2026-11-27): only the normal input collections, no
# vendor page, so no 12 xx query. Squire reads only the standard battery level (GATT 180F / 2A19) the OS holds.
BLUETOOTH_PIDS = {0x1B08: "keyboard", 0x1C0F: "mouse"}

KEYBOARD_QUERY = bytes([0x12, 0x01])
MOUSE_QUERY = bytes([0x12, 0x07])
READ_TIMEOUT_MS = 400
DRAIN_TIMEOUT_MS = 5   # hidapi: timeout 0 means "block forever", so never pass 0
MAX_READS = 8
DEFAULT_LOW_WARNING = 20   # used when a device does not report a plausible warning level
IS_LINUX = sys.platform.startswith("linux")


def state_dir() -> Path:
    if IS_LINUX:
        return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "squire-gear"
    return Path.home() / "Library" / "Application Support" / "squire-gear"


STATE_FILE = state_dir() / "notified.json"


@dataclass
class Battery:
    device: str              # "keyboard" | "mouse"
    name: str
    connection: str          # "omni" | "wired" | "bluetooth" (level only: no charging flag, no warning level)
    percent: Optional[int]   # None = not reachable
    charging: bool = False
    status: str = "ok"       # ok | asleep | no-response | no-access | error
    low_warning: Optional[int] = None  # device-configured low-battery warning level in %
    raw: str = ""


@dataclass(frozen=True)
class Target:
    device: str
    name: str
    pid: int
    report_id: Optional[int]   # None = unnumbered report (leading 0x00, 64-byte payload)
    query: bytes
    report_lens: tuple         # candidate total write sizes (incl. report-ID byte), tried in order


TARGETS = [
    Target("keyboard", "ROG Falchion RX LP", PID_OMNI, 0x02, KEYBOARD_QUERY, (64,)),
    Target("mouse", "ROG Keris II Origin", PID_OMNI, 0x03, MOUSE_QUERY, (64,)),
    Target("keyboard", "ROG Falchion RX LP", PID_FALCHION_RX_LP, None, KEYBOARD_QUERY, (65,)),
    # wired mouse: report size unverified on macOS, try both
    Target("mouse", "ROG Keris II Origin", PID_KERIS_II_ORIGIN, None, MOUSE_QUERY, (65, 64)),
]


# Read-only commands for the Keris II Origin settings ("get" requests all start with 0x12; writes start with
# 0x50/0x51 and are not on any list here). Sources, facts only: libratbag src/asus.c + driver-asus.c (MIT,
# confirmed on this mouse in the owner's libratbag fork) and G-Helper app/Peripherals/Mouse/AsusMouse.cs (GPL-3.0).
MOUSE_SETTINGS_QUERIES = {
    "profile": bytes([0x12, 0x00]),             # active profile, active DPI stage, firmware
    "settings": bytes([0x12, 0x04, 0x00]),      # DPI stages, polling rate, debounce, angle snapping/tuning
    "dpi_xy": bytes([0x12, 0x04, 0x02]),        # DPI stages with separate X and Y
    "dpi_colors": bytes([0x12, 0x04, 0x03]),    # colour per DPI stage
    "led_logo": bytes([0x12, 0x03, 0x00]),      # LED zone 0 = logo
    "led_wheel": bytes([0x12, 0x03, 0x01]),     # LED zone 1 = scroll wheel
    "led_side": bytes([0x12, 0x03, 0x02]),      # LED zone 2 = side strip (G-Helper/libratbag: "underglow")
    "liftoff": bytes([0x12, 0x06]),             # lift-off distance + surface (mouse pad) preset
}
# Read-only commands for the Falchion RX Low Profile settings (approved by the owner 2026-11-13). Source, facts only:
# G-Helper app/Peripherals/Keyboard/AsusKeyboard.cs (GPL-3.0); rogdriver (no license) sends the same two.
KEYBOARD_SETTINGS_QUERIES = {
    "profile": bytes([0x12, 0x00]),             # firmware, current lighting effect, active profile
    "layout": bytes([0x12, 0x12]),              # factory layout id (ISO / ANSI)
}
# The transport refuses anything not on the list of the device it talks to: the keyboard never gets a mouse
# command and the other way round (same bytes can mean something else on the other device).
ALLOWED_READS = {
    "keyboard": frozenset([KEYBOARD_QUERY, *KEYBOARD_SETTINGS_QUERIES.values()]),
    "mouse": frozenset([MOUSE_QUERY, *MOUSE_SETTINGS_QUERIES.values()]),
}
ALLOWED_COMMANDS = ALLOWED_READS["keyboard"] | ALLOWED_READS["mouse"]   # all reads, for checks over both
# Report ID of each device on the Omni receiver; wired, both use unnumbered reports (None).
REPORT_IDS = {"keyboard": 0x02, "mouse": 0x03}


# --- Writes (allowlist) ---
# Every write is built by a function below and checked again by is_allowed_write() before it is sent.
# Sources, facts only: libratbag asus_set_led()/asus_save_profile() (MIT, tested on this mouse in the owner's
# fork) and G-Helper GetUpdateLightingModePacket()/GetSaveProfilePacket() (GPL-3.0).
LED_WRITE = bytes([0x51, 0x28])          # 51 28 zone 00 mode brightness r g b dir random speed
SAVE_COMMAND = bytes([0x50, 0x03])       # stores the current settings in the mouse's flash
LED_ZONE_INDEX = {"logo": 0, "wheel": 1, "side": 2}
# Effects the Keris II Origin supports (G-Helper KerisIIOrigin); f0 = off. No rainbow/comet: they need
# direction/speed bytes that Squire does not send.
LED_WRITE_MODES = {"static": 0x00, "breathing": 0x01, "color-cycle": 0x02, "react": 0x04, "battery": 0x06,
                   "off": 0xF0}
# No speed byte: tested 2026-11-10, the Keris II Origin stores [11] = 05/09 but breathing and colour cycle keep
# the same tempo (3 pulses in 10 s either way). Bytes [9..11] stay 00.


def led_command(zone: str, mode: str, brightness: int, color: str = "#000000") -> bytes:
    """51 28 command for one LED zone. Raises ValueError for anything outside the allowlist."""
    if zone not in LED_ZONE_INDEX:
        raise ValueError(f"unknown LED zone {zone!r}")
    if mode not in LED_WRITE_MODES:
        raise ValueError(f"LED effect {mode!r} is not supported")
    if not 0 <= int(brightness) <= 100:
        raise ValueError("brightness must be 0-100 %")
    rgb = bytes.fromhex(color.lstrip("#"))
    if len(rgb) != 3:
        raise ValueError(f"colour {color!r} is not #rrggbb")
    command = LED_WRITE + bytes([LED_ZONE_INDEX[zone], 0x00, LED_WRITE_MODES[mode], int(brightness)]) + rgb \
        + bytes([0x00, 0x00, 0x00])
    assert is_allowed_write(command, "mouse")
    return command


# Power and sensor settings. Source, facts only: G-Helper AsusMouse.cs GetUpdateEnergySettingsPacket(),
# GetUpdateAngleSnappingPacket(), GetUpdateAngleAdjustmentPacket() and Models/KerisIIOrigin.cs (HasAutoPowerOff,
# HasLowBatteryWarning, HasAngleSnapping, HasAngleTuning, AngleTuningStep 5) (GPL-3.0). Angle snapping also in
# libratbag asus_set_angle_snapping() (51 31 06 00 0/1). Bytes approved by the owner 2026-11-12.
POWER_WRITE = bytes([0x51, 0x37])        # 51 37 00 00 <power-off code> 00 <low-battery warning %>
SENSOR_WRITE = bytes([0x51, 0x31])       # 51 31 <field> 00 <value lo> <value hi>
ANGLE_SNAPPING_FIELD = 0x06
ANGLE_TUNING_FIELD = 0x0B
POLLING_FIELD = 0x04     # 51 31 04 00 <index>: Armoury Crate capture 2026-11-12 (index 3), approved 2026-11-13
POLLING_WRITE_HZ = {125: 0x00, 250: 0x01, 500: 0x02, 1000: 0x03}   # what Armoury Crate offers for the Keris
POWER_OFF_CODES = {1: 0x00, 2: 0x01, 3: 0x02, 5: 0x03, 10: 0x04, None: 0xFF}   # minutes (None = never) -> byte
LOW_WARNING_LEVELS = (10, 20, 30, 40, 50)                                     # % (G-Helper: step 10, max 50)
ANGLE_TUNING_DEGREES = tuple(range(-20, 21, 5))                               # G-Helper: -20..20, step 5


def power_command(power_off_minutes: Optional[int], low_warning: int) -> bytes:
    """51 37: auto power-off and low-battery warning, always both. Raises ValueError outside the allowlist."""
    if power_off_minutes not in POWER_OFF_CODES:
        raise ValueError(f"power-off {power_off_minutes!r} min is not one of 1, 2, 3, 5, 10 or never")
    if low_warning not in LOW_WARNING_LEVELS:
        raise ValueError(f"low-battery warning {low_warning!r} % is not one of {LOW_WARNING_LEVELS}")
    command = POWER_WRITE + bytes([0x00, 0x00, POWER_OFF_CODES[power_off_minutes], 0x00, low_warning])
    assert is_allowed_write(command, "mouse")
    return command


def angle_snapping_command(on: bool) -> bytes:
    command = SENSOR_WRITE + bytes([ANGLE_SNAPPING_FIELD, 0x00, 0x01 if on else 0x00])
    assert is_allowed_write(command, "mouse")
    return command


def angle_tuning_command(degrees: int) -> bytes:
    if degrees not in ANGLE_TUNING_DEGREES:
        raise ValueError(f"angle tuning {degrees!r}° is not one of -20..20 in steps of 5")
    command = SENSOR_WRITE + bytes([ANGLE_TUNING_FIELD, 0x00]) + (degrees & 0xFFFF).to_bytes(2, "little")
    assert is_allowed_write(command, "mouse")
    return command


# Lift-off distance and surface share one command. Source: Armoury Crate captures on Windows (USBPcap, 2026-11-12,
# protocol.json mouse_writes.armoury_crate); G-Helper writes only the "no surface" form. Approved by the owner
# 2026-11-13. A lift-off write always carries the surface id and parameters read from 12 06 just before, so the
# surface stays as it is (G-Helper's 51 35 ff 00 ff <lift-off> drops it: the "calibration reset").
LIFTOFF_WRITE = bytes([0x51, 0x35])      # 51 35 00 00 <surface id> <lift-off> 00 <p1 p2 p3>
NO_SURFACE = 0xFF                        # 51 35 ff 00 ff <lift-off>: "Default (No calibration)" in Armoury Crate
LIFTOFF_CODES = {"low": 0x00, "high": 0x01}
# Armoury Crate's preset surfaces (id -> name). Most share the parameters 1c 34 16. Ids 02..06 and 09 never appeared.
SURFACES = {
    0x00: "ROG Whetstone", 0x01: "ROG Sheath", 0x07: "ROG Scabbard", 0x08: "ROG Strix Edge", 0x0A: "Balteus",
    0x0B: "Balteus Qi", 0x0C: "ROG Scabbard II", 0x0D: "ROG Sheath Gundam LTD",
    0x0E: "ROG Strix Edge Call of Duty Black Ops 4 Edition", 0x0F: "ROG Sheath BLK LTD", 0x10: "ROG Sheath PNK LTD",
    0x11: "ROG Sheath Electro Punk", 0x12: "ROG Hone Ace Aim Lab Edition", 0x13: "ROG Scabbard II EVA Edition",
    0x14: "ROG Moonstone Ace L", 0x15: "ROG Moonstone Ace L (2)", 0x16: "ROG Hone Ace XXL",
    0x17: "ROG Scabbard II Arctic Grey XXL", 0x18: "TUF Gaming P1 Hatsune Miku Edition", 0x19: "ROG Sheath II XXL",
}


# Parameters Armoury Crate writes with each preset (captured 2026-11-12); None for "no surface".
SURFACE_PARAMS = {
    0x00: "1c 34 16", 0x01: "1c 34 16", 0x07: "1c 34 16", 0x08: "1c 34 16", 0x0A: "1a 32 20", 0x0B: "1a 32 20",
    0x0C: "1c 34 16", 0x0D: "1c 34 16", 0x0E: "1c 34 16", 0x0F: "1c 34 16", 0x10: "1c 34 16", 0x11: "1c 34 16",
    0x12: "1c 34 16", 0x13: "1c 34 16", 0x14: "37 2b 0e", 0x15: "18 35 23", 0x16: "1c 34 16", 0x17: "21 23 12",
    0x18: "1c 42 24", 0x19: "1e 34 19", NO_SURFACE: None,
}


def surface_name(surface_id: Optional[int]) -> Optional[str]:
    if surface_id is None:
        return None
    if surface_id == NO_SURFACE:
        return "None (no calibration)"
    return SURFACES.get(surface_id, f"unknown (0x{surface_id:02x})")


def liftoff_command(liftoff: str, surface_id: Optional[int], surface_params: Optional[str]) -> bytes:
    """51 35: lift-off low/high, keeping the surface (id + parameters as read from 12 06). Raises ValueError for an
    unknown surface, so a surface Squire does not know (a custom calibration?) is never overwritten."""
    if liftoff not in LIFTOFF_CODES:
        raise ValueError(f"lift-off {liftoff!r} is not low or high")
    if surface_id == NO_SURFACE:
        command = LIFTOFF_WRITE + bytes([NO_SURFACE, 0x00, NO_SURFACE, LIFTOFF_CODES[liftoff]])
    elif surface_id in SURFACES and surface_params is not None and len(bytes.fromhex(surface_params)) == 3:
        command = LIFTOFF_WRITE + bytes([0x00, 0x00, surface_id, LIFTOFF_CODES[liftoff], 0x00]) \
            + bytes.fromhex(surface_params)
    else:
        raise ValueError(f"surface {surface_name(surface_id) or 'not read'}: lift-off not written, so the "
                         "surface setting is kept")
    assert is_allowed_write(command, "mouse")
    return command


def surface_command(surface_id: int, liftoff: str) -> bytes:
    """51 35 with a preset surface (or NO_SURFACE) and its Armoury Crate parameters, keeping `liftoff`."""
    if surface_id not in SURFACE_PARAMS:
        raise ValueError(f"surface {surface_id!r} is not one of Armoury Crate's presets")
    return liftoff_command(liftoff, surface_id, SURFACE_PARAMS[surface_id])


# DPI. Source: Armoury Crate captures (USBPcap 2026-11-12, mouse_writes.armoury_crate) and G-Helper AsusMouse.cs
# GetUpdateDPIPacket / GetChangeDPIProfilePacket2 / GetSetDPIProfileCountPacket + Models/KerisIIOrigin.cs (MaxDPI
# 42000, 4 stages, CanChangeDPICount, DPI colours) (GPL-3.0, facts only). Approved by the owner 2026-11-13.
# 51 31 <stage 0-based> 00 <lo> <hi> <r g b>: DPI (X and Y alike) + the stage colour, value = (DPI - 50) / 50.
ACTIVE_STAGE_FIELD = 0x09     # 51 31 09 00 <stage, 1-based>
STAGE_COUNT_FIELD = 0x0A      # 51 31 0a 00 <1..4>
DPI_STAGES = 4
DPI_MIN, DPI_MAX, DPI_STEP = 100, 42000, 50


def dpi_command(stage: int, dpi: int, color: str) -> bytes:
    """One DPI stage (1-based): DPI for X and Y alike, plus the stage colour (#rrggbb)."""
    if not 1 <= stage <= DPI_STAGES:
        raise ValueError(f"DPI stage {stage!r} is not 1-{DPI_STAGES}")
    if not (DPI_MIN <= dpi <= DPI_MAX and dpi % DPI_STEP == 0):
        raise ValueError(f"{dpi!r} DPI is not {DPI_MIN}-{DPI_MAX} in steps of {DPI_STEP}")
    rgb = bytes.fromhex(color.lstrip("#"))
    if len(rgb) != 3:
        raise ValueError(f"colour {color!r} is not #rrggbb")
    command = SENSOR_WRITE + bytes([stage - 1, 0x00]) + ((dpi - 50) // 50).to_bytes(2, "little") + rgb
    assert is_allowed_write(command, "mouse")
    return command


def active_stage_command(stage: int) -> bytes:
    if not 1 <= stage <= DPI_STAGES:
        raise ValueError(f"DPI stage {stage!r} is not 1-{DPI_STAGES}")
    command = SENSOR_WRITE + bytes([ACTIVE_STAGE_FIELD, 0x00, stage])
    assert is_allowed_write(command, "mouse")
    return command


def stage_count_command(count: int) -> bytes:
    if not 1 <= count <= DPI_STAGES:
        raise ValueError(f"DPI stage count {count!r} is not 1-{DPI_STAGES}")
    command = SENSOR_WRITE + bytes([STAGE_COUNT_FIELD, 0x00, count])
    assert is_allowed_write(command, "mouse")
    return command


def polling_command(hz: int) -> bytes:
    if hz not in POLLING_WRITE_HZ:
        raise ValueError(f"polling rate {hz!r} Hz is not one of {sorted(POLLING_WRITE_HZ)}")
    command = SENSOR_WRITE + bytes([POLLING_FIELD, 0x00, POLLING_WRITE_HZ[hz]])
    assert is_allowed_write(command, "mouse")
    return command


# Lighting effect ids in 12 00 [9] (G-Helper KeyboardLightingMode); 0xf1 = the host drives the LEDs directly.
KEYBOARD_LIGHT_MODES = {0x00: "static", 0x01: "breathing", 0x02: "color-cycle", 0x03: "reactive", 0x04: "rainbow",
                        0x05: "ripple", 0x06: "starry-night", 0x07: "quicksand", 0x08: "current", 0x09: "raindrop",
                        0xF1: "host-driven"}
# Factory layout ids in 12 12 [5]: 3/4/5/17 from G-Helper, 25 = CH-DE confirmed by the owner (2026-11-13).


# --- Keyboard writes (Falchion RX Low Profile) ---
# Source: Armoury Crate captures on Windows (USBPcap via the Omni, 2026-11-21 and 2026-11-22, protocol.json
# keyboard_writes, capture_4 has every option); G-Helper AsusKeyboard.cs (GPL-3.0, facts only) has the same 51 2c
# layout and 50 55, but 51 37 for the warning, which is wrong for the Falchion (AC sends 51 3a). Only byte values AC sent.
KEYBOARD_SAVE_COMMAND = bytes([0x50, 0x55])      # the keyboard's save (the mouse uses 50 03)
KEYBOARD_LIGHT_WRITE = bytes([0x51, 0x2C])       # 51 2c <effect> 00 <speed> <brightness %> <mode> ff ff <r g b> ...
KEYBOARD_SLEEP_WRITE = bytes([0x51, 0x38])       # 51 38 00 00 <power-off code>, read back 12 01 [6]
KEYBOARD_WARNING_WRITE = bytes([0x51, 0x3A])     # 51 3a 00 00 <%>, read back 12 01 [9]
# Per-key colours ("direct mode"), TEST ONLY (branch keyboard-direct, 2026-11-25). Sources, facts only: G-Helper
# (GPL-3.0) AsusKeyboard.cs SetLedColors + AuraKeyboardLayouts.cs "FalchionRX"/"FalchionRXISO" (its model
# FalchionRXLowProfileOmni = our keyboard via the Omni, report ID 02); OpenRGB (GPL-2.0) has the same packet for the
# original Falchion. c0 81 <n> 00 then n x <key id> <r g b>, no init packet. At most 14 keys per packet: 15 would be 65
# bytes with the report ID, one over the Omni's 64 (G-Helper). Key id = column * 8 + row, rows 1..5 on the RX / RX LP
# (Esc 01, Tab 02, Caps 03, LShift 04, LCtrl 05, 1 = 09, Q = 0a); the original Falchion starts at row 0 (OpenRGB),
# which is why 00 lit nothing (2026-11-25: the keyboard replied c0 82 01 00 and starry night stopped, no key lit).
# Nothing can be read back and it is never saved (no 50 55 after it).
KEYBOARD_DIRECT_WRITE = bytes([0xC0, 0x81])
KEYBOARD_DIRECT_PER_PACKET = 14
KEYBOARD_DIRECT_ACK = bytes([0xC0, 0x82])          # the keyboard answers c0 81 <n> 00 with c0 82 <n> 00 (2026-11-25)
KEYBOARD_DIRECT_KEYS = tuple(col * 8 + row for col in range(15) for row in range(1, 6))   # 15 columns x rows 1..5 (01..75)
# Key id of each key, row by row, left to right, as the settings windows draw them (KEYBOARD_ROWS there: same count per
# row). From G-Helper AuraKeyboardLayouts.cs "FalchionRX" / "FalchionRXISO" (facts only). ISO Enter spans two rows and
# has one id (6b), listed in both. Right column: Ins 71, Del 72, PgUp 73, PgDn 74 (Insert confirmed by the owner).
_KB_ID_ROW0 = [0x01, 0x09, 0x11, 0x19, 0x21, 0x29, 0x31, 0x39, 0x41, 0x49, 0x51, 0x59, 0x61, 0x69, 0x71]
_KB_ID_ROW4 = [0x05, 0x0D, 0x15, 0x35, 0x4D, 0x55, 0x5D, 0x65, 0x6D, 0x75]
KEYBOARD_KEY_IDS = {
    "iso": [_KB_ID_ROW0,
            [0x02, 0x0A, 0x12, 0x1A, 0x22, 0x2A, 0x32, 0x3A, 0x42, 0x4A, 0x52, 0x5A, 0x62, 0x6B, 0x72],
            [0x03, 0x0B, 0x13, 0x1B, 0x23, 0x2B, 0x33, 0x3B, 0x43, 0x4B, 0x53, 0x5B, 0x63, 0x6B, 0x73],
            [0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C, 0x44, 0x4C, 0x54, 0x5C, 0x64, 0x6C, 0x74],
            _KB_ID_ROW4],
    "ansi": [_KB_ID_ROW0,
             [0x02, 0x0A, 0x12, 0x1A, 0x22, 0x2A, 0x32, 0x3A, 0x42, 0x4A, 0x52, 0x5A, 0x62, 0x6A, 0x72],
             [0x03, 0x0B, 0x13, 0x1B, 0x23, 0x2B, 0x33, 0x3B, 0x43, 0x4B, 0x53, 0x5B, 0x6B, 0x73],
             [0x04, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C, 0x44, 0x4C, 0x54, 0x5C, 0x64, 0x6C, 0x74],
             _KB_ID_ROW4],
}


def keyboard_key_ids(iso: Optional[bool]) -> list:
    """Every key id of the layout once, in drawing order (ISO unless the keyboard reported ANSI)."""
    ids = []
    for row in KEYBOARD_KEY_IDS["ansi" if iso is False else "iso"]:
        ids += [key for key in row if key not in ids]
    return ids
# Effect -> {speed name: byte}: AC's fast / normal / slow, own values per effect, larger = slower (capture_4 block 3;
# breathing and raindrop from capture_2 / 3). Static has no speed (ff).
KEYBOARD_LIGHT_SPEEDS = {
    "static": {"none": 0xFF},
    "breathing": {"fast": 0x0F, "normal": 0x1E, "slow": 0x2A},
    "color-cycle": {"fast": 0x12, "normal": 0x1F, "slow": 0x3E},
    "reactive": {"fast": 0x08, "normal": 0x0F, "slow": 0x16},
    "rainbow": {"fast": 0x23, "normal": 0x3E, "slow": 0x55},
    "ripple": {"fast": 0x50, "normal": 0x64, "slow": 0x78},
    "starry-night": {"fast": 0x0A, "normal": 0x0F, "slow": 0x19},
    "quicksand": {"fast": 0x0C, "normal": 0x0F, "slow": 0x14},
    "current": {"fast": 0x07, "normal": 0x0A, "slow": 0x0C},
    "raindrop": {"fast": 0x0E, "normal": 0x14, "slow": 0x1B},
}
KEYBOARD_LIGHT_IDS = {name: i for i, name in KEYBOARD_LIGHT_MODES.items() if name in KEYBOARD_LIGHT_SPEEDS}
# "off" is not an effect of its own: AC's lighting switch sends static, 4 %, black (capture_4 block 6), and the
# keyboard then reads 00 static. (AC also sends an unknown 51 36 to the mouse; Squire does not.)
KEYBOARD_OFF = "off"
KEYBOARD_OFF_BRIGHTNESS = 4
KEYBOARD_EFFECTS = list(KEYBOARD_LIGHT_SPEEDS) + [KEYBOARD_OFF]   # what Squire can set, in the keyboard's id order
# Effects written as one 51 2c: [6] 00, [7..8] ff ff, [9..11] colour.
KEYBOARD_SINGLE_COLOUR = ("static", "breathing", "color-cycle", "reactive", "starry-night", "current", "raindrop")
# Starry night, current and raindrop also have (capture_4 block 2): [6] colour mode, [12..14] second colour
# (00 00 00 unless double), [15..17] background (00 00 00 = off). Random keeps both colours in the packet.
KEYBOARD_OPTION_EFFECTS = ("starry-night", "current", "raindrop")
KEYBOARD_COLOUR_MODES = {"single": 0x00, "double": 0x10, "random": 0x01}
KEYBOARD_SECOND_COLOUR = "#ff00ff"               # AC's second colour when switching to double
# Colour gradients (rainbow, ripple): three 51 2c chunks, [3] = 02, 01, 00. First chunk: <speed> <brightness> 00
# <direction> <thickness> <stop count>, then 4 bytes per stop (position %, R, G, B): 2 stops in the first chunk, up to
# 4 in the next one, the rest in the last (AC sends every chunk, unused ones as zeros).
KEYBOARD_GRADIENT_EFFECTS = ("rainbow", "ripple")
# AC's 7 gradient presets in AC's order (capture_4 block 4). AC shows them without names; the names are Squire's.
KEYBOARD_GRADIENTS = {
    "red": ((14, "ff0029"), (100, "ff0029")),
    "blue-red": ((14, "0018f0"), (100, "ff0031")),
    "sunset": ((14, "febe3f"), (57, "fe3f7d"), (100, "f91d1d")),
    "lime-cyan": ((14, "d1fe3f"), (45, "00dcff"), (72, "00dcff"), (100, "d1fe3f")),
    "yellow-magenta": ((14, "f1ff00"), (36, "ff00ed"), (57, "f1ff00"), (79, "ff00ed"), (100, "f1ff00")),
    "pink-purple": ((14, "ff0091"), (32, "8c00ff"), (49, "4b00d9"), (66, "4b00d9"), (83, "8c00ff"), (100, "ff0091")),
    "spectrum": ((14, "f500ff"), (29, "0006ff"), (43, "00faff"), (57, "01ff00"), (72, "fff600"), (86, "ff7807"),
                 (100, "ff000d")),                                     # AC's default
}
KEYBOARD_GRADIENT_STOPS = (2, 7)                 # AC: 2..7 stops; positions 1..100 %, ascending
# Direction byte, per effect. Rainbow: a compass in 45 degree steps (capture_4 block 4: 00 right, 02 down, 04 left,
# 06 up, 08 from the centre outwards; the diagonals 01/03/05/07 are assumed to be in between, clockwise). Ripple has
# none (ff). Quicksand uses the same coding for down / up (block 5). The first one is the default.
KEYBOARD_DIRECTIONS = {
    "rainbow": {"right": 0x00, "down-right": 0x01, "down": 0x02, "down-left": 0x03, "left": 0x04, "up-left": 0x05,
                "up": 0x06, "up-right": 0x07, "outwards": 0x08},
    "ripple": {"none": 0xFF},
    "quicksand": {"down": 0x02, "up": 0x06},
}
KEYBOARD_DIRECTION_ALIASES = {"left-to-right": "right", "right-to-left": "left"}    # names used before 2026-11-22
# Thickness (rainbow, ripple): byte [8] of chunk 02; AC's Low / Normal / Strong, Strong is AC's default.
KEYBOARD_THICKNESS = {"strong": 0x02, "normal": 0x01, "low": 0x00}
# Quicksand: two chunks ([3] = 01, 00): <speed> <brightness> <mode> <direction> ff, then 6 RGB triples without
# positions, 3 per chunk. Mode 00 = the colour scale, 01 = random (AC still sends the scale). Only AC's default scale.
KEYBOARD_QUICKSAND_SCALE = ("fff600", "01ff00", "00faff", "0006ff", "f500ff", "ff000d")
KEYBOARD_QUICKSAND_MODES = {"scale": 0x00, "random": 0x01}
KEYBOARD_COLOURLESS = frozenset(["color-cycle", "rainbow", "ripple", "quicksand", KEYBOARD_OFF])  # no colour to pick
# Warning levels: AC offers exactly 10..50 % and "never" (00, capture_4 block 1). 0 = never.
KEYBOARD_WARNING_LEVELS = (0,) + LOW_WARNING_LEVELS
# The fields of a lighting value (keyboard_light_value); a field the effect does not use is None.
KEYBOARD_LIGHT_FIELDS = ("effect", "brightness", "color", "speed", "gradient", "direction", "thickness", "mode",
                         "color2", "background")


def keyboard_effect_read(effect: str) -> str:
    """The effect 12 00 [9] reports after `effect` was written ("off" is a black static)."""
    return "static" if effect == KEYBOARD_OFF else effect


def keyboard_speed(effect: str, speed: Optional[str]) -> str:
    """The speed name for an effect: `speed`, or "normal" if it has one, else its only value."""
    speeds = KEYBOARD_LIGHT_SPEEDS.get(keyboard_effect_read(effect))
    if speeds is None:
        raise ValueError(f"keyboard effect {effect!r} is not one Squire writes ({', '.join(KEYBOARD_EFFECTS)})")
    if speed is None:
        speed = "normal" if "normal" in speeds else next(iter(speeds))
    if speed not in speeds:
        raise ValueError(f"speed {speed!r} for {effect}: only {', '.join(speeds)}")
    return speed


def keyboard_direction(effect: str, direction: Optional[str]) -> str:
    directions = KEYBOARD_DIRECTIONS[effect]
    direction = KEYBOARD_DIRECTION_ALIASES.get(direction, direction) or next(iter(directions))
    if direction not in directions:
        raise ValueError(f"direction {direction!r} for {effect}: only {', '.join(directions)}")
    return direction


def _rgb(color: Optional[str], what: str = "colour") -> str:
    """#rrggbb, lower case; raises ValueError otherwise."""
    try:
        raw = bytes.fromhex((color or "").lstrip("#"))
    except ValueError:
        raw = b""
    if len(raw) != 3:
        raise ValueError(f"{what} {color!r} is not #rrggbb")
    return "#" + raw.hex()


def keyboard_gradient_stops(gradient: Optional[str]) -> tuple:
    """((position %, "rrggbb"), ...) for a preset name or a custom gradient "POS:RRGGBB,POS:RRGGBB,..." (2..7 stops,
    positions 1..100 % ascending, like AC's editor)."""
    gradient = gradient or "spectrum"
    if gradient in KEYBOARD_GRADIENTS:
        return KEYBOARD_GRADIENTS[gradient]
    if ":" not in gradient:
        raise ValueError(f"gradient {gradient!r}: one of {', '.join(KEYBOARD_GRADIENTS)} or POS:RRGGBB,... ")
    try:
        stops = tuple((int(pos), _rgb(rgb, "gradient colour")[1:])
                      for pos, rgb in (part.split(":") for part in gradient.split(",")))
    except ValueError as error:
        raise ValueError(f"gradient {gradient!r}: {error}") from None
    low, high = KEYBOARD_GRADIENT_STOPS
    positions = [pos for pos, _ in stops]
    if not low <= len(stops) <= high:
        raise ValueError(f"gradient {gradient!r}: {low} to {high} stops")
    if not all(1 <= p <= 100 for p in positions) or positions != sorted(set(positions)):
        raise ValueError(f"gradient {gradient!r}: positions 1..100 %, ascending")
    return stops


def keyboard_gradient_name(gradient: Optional[str]) -> str:
    """A preset name, or the custom gradient written canonically (lower case, no spaces)."""
    gradient = gradient or "spectrum"
    if gradient in KEYBOARD_GRADIENTS:
        return gradient
    return ",".join(f"{pos}:{rgb}" for pos, rgb in keyboard_gradient_stops(gradient))


def _pick(name: str, value: Optional[str], choices: dict) -> str:
    value = value or next(iter(choices))
    if value not in choices:
        raise ValueError(f"{name} {value!r}: only {', '.join(choices)}")
    return value


def keyboard_light_value(effect: str, brightness: int, color: Optional[str] = "#ffffff", speed: Optional[str] = None,
                         gradient: Optional[str] = None, direction: Optional[str] = None, *,
                         thickness: Optional[str] = None, mode: Optional[str] = None, color2: Optional[str] = None,
                         background: Optional[str] = None) -> dict:
    """The lighting as Squire writes it, validated and normalised (keys KEYBOARD_LIGHT_FIELDS): colours #rrggbb lower
    case (#000000 for an effect without one), the speed actually sent, and None for every field the effect does not
    use. `mode`: single / double / random (starry night, current, raindrop) or scale / random (quicksand).
    `background` None or #000000 = off. "off" is AC's black static at 4 %. Options the effect does not use are
    ignored (the window keeps them for a later effect)."""
    if effect == KEYBOARD_OFF:
        brightness, color = KEYBOARD_OFF_BRIGHTNESS, "#000000"
    speed = keyboard_speed(effect, speed)          # also refuses an effect Squire does not write
    if not 0 <= int(brightness) <= 100:
        raise ValueError("brightness must be 0-100 %")
    value = dict.fromkeys(KEYBOARD_LIGHT_FIELDS)
    value.update(effect=effect, brightness=int(brightness), speed=speed,
                 color="#000000" if effect in KEYBOARD_COLOURLESS else _rgb(color or "#ffffff"))
    if effect in KEYBOARD_GRADIENT_EFFECTS:
        value.update(gradient=keyboard_gradient_name(gradient), direction=keyboard_direction(effect, direction),
                     thickness=_pick("thickness", thickness, KEYBOARD_THICKNESS))
    elif effect == "quicksand":
        value.update(direction=keyboard_direction(effect, direction),
                     mode=_pick("colour mode", mode, KEYBOARD_QUICKSAND_MODES))
    elif effect in KEYBOARD_OPTION_EFFECTS:
        value["mode"] = _pick("colour mode", mode, KEYBOARD_COLOUR_MODES)
        if value["mode"] != "single":
            value["color2"] = _rgb(color2 or KEYBOARD_SECOND_COLOUR, "second colour")
        bg = _rgb(background, "background") if background else None
        value["background"] = None if bg == "#000000" else bg
    return value


def keyboard_light_from(stored: dict) -> dict:
    """keyboard_light_value from a dict with (some of) the KEYBOARD_LIGHT_FIELDS, e.g. a remembered or pending value."""
    options = {f: stored.get(f) for f in KEYBOARD_LIGHT_FIELDS[2:]}
    return keyboard_light_value(stored["effect"], stored["brightness"], **options)


def keyboard_light_packets(value: dict) -> list:
    """Every 51 2c chunk for a normalised lighting value, in the order they are sent (one for a single-colour
    effect, three for a gradient, two for quicksand)."""
    effect = keyboard_effect_read(value["effect"])
    eid = KEYBOARD_LIGHT_IDS[effect]
    speed = KEYBOARD_LIGHT_SPEEDS[effect][value["speed"]]
    brightness = value["brightness"]
    rgb = lambda c: bytes.fromhex((c or "#000000").lstrip("#"))     # noqa: E731
    if effect in KEYBOARD_SINGLE_COLOUR:
        command = KEYBOARD_LIGHT_WRITE + bytes([eid, 0x00, speed, brightness, 0x00, 0xFF, 0xFF]) + rgb(value["color"])
        if effect in KEYBOARD_OPTION_EFFECTS:
            command = (command[:6] + bytes([KEYBOARD_COLOUR_MODES[value["mode"]]]) + command[7:]
                       + rgb(value["color2"]) + rgb(value["background"]))
        commands = [command]
    elif effect == "quicksand":
        scale = [bytes.fromhex(c) for c in KEYBOARD_QUICKSAND_SCALE]
        commands = [KEYBOARD_LIGHT_WRITE + bytes([eid, 0x01, speed, brightness, KEYBOARD_QUICKSAND_MODES[value["mode"]],
                                                  KEYBOARD_DIRECTIONS[effect][value["direction"]], 0xFF])
                    + b"".join(scale[:3]),
                    KEYBOARD_LIGHT_WRITE + bytes([eid, 0x00]) + b"".join(scale[3:])]
    else:
        stops = keyboard_gradient_stops(value["gradient"])
        packed = [bytes([pos]) + bytes.fromhex(c) for pos, c in stops]
        commands = [KEYBOARD_LIGHT_WRITE + bytes([eid, 0x02, speed, brightness, 0x00,
                                                  KEYBOARD_DIRECTIONS[effect][value["direction"]],
                                                  KEYBOARD_THICKNESS[value["thickness"]], len(stops)])
                    + b"".join(packed[:2]),
                    KEYBOARD_LIGHT_WRITE + bytes([eid, 0x01]) + b"".join(packed[2:6]),
                    KEYBOARD_LIGHT_WRITE + bytes([eid, 0x00]) + b"".join(packed[6:10])]
    for command in commands:
        assert is_allowed_write(command, "keyboard")
    return commands


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


def keyboard_sleep_command(minutes: Optional[int]) -> bytes:
    """51 38: sleep after 1/2/3/5/10 min, None = never (mouse power-off coding, AC 2026-11-21)."""
    if minutes not in POWER_OFF_CODES:
        raise ValueError(f"sleep {minutes!r} min is not one of 1, 2, 3, 5, 10 or never")
    command = KEYBOARD_SLEEP_WRITE + bytes([0x00, 0x00, POWER_OFF_CODES[minutes]])
    assert is_allowed_write(command, "keyboard")
    return command


def keyboard_warning_command(percent: int) -> bytes:
    """51 3a: low-battery warning at 10..50 %, 0 = never."""
    if percent not in KEYBOARD_WARNING_LEVELS:
        raise ValueError(f"low-battery warning {percent!r} % is not one of {LOW_WARNING_LEVELS} or never")
    command = KEYBOARD_WARNING_WRITE + bytes([0x00, 0x00, percent])
    assert is_allowed_write(command, "keyboard")
    return command


def warning_text(level: Optional[int]) -> str:
    """A low-battery warning level for people: "20 %", "never" (0) or "–" (unknown)."""
    return "–" if level is None else "never" if level == 0 else f"{level} %"


def _stops_ok(stops: bytes) -> bool:
    return len(stops) % 4 == 0 and all(1 <= stops[i] <= 100 for i in range(0, len(stops), 4))


def is_allowed_keyboard_write(command: bytes) -> bool:
    if command == KEYBOARD_SAVE_COMMAND:
        return True
    if command[:2] == KEYBOARD_SLEEP_WRITE:
        return len(command) == 5 and command[2:4] == b"\0\0" and command[4] in POWER_OFF_CODES.values()
    if command[:2] == KEYBOARD_WARNING_WRITE:
        return len(command) == 5 and command[2:4] == b"\0\0" and command[4] in KEYBOARD_WARNING_LEVELS
    if command[:2] == KEYBOARD_DIRECT_WRITE and len(command) >= 4:
        n = command[2]
        return (1 <= n <= KEYBOARD_DIRECT_PER_PACKET and command[3] == 0 and len(command) == 4 + 4 * n
                and all(command[4 + 4 * i] in KEYBOARD_DIRECT_KEYS for i in range(n)))
    if command[:2] == KEYBOARD_LIGHT_WRITE and len(command) >= 4:
        effect = KEYBOARD_LIGHT_MODES.get(command[2])
        if effect not in KEYBOARD_LIGHT_SPEEDS:
            return False
        speeds = KEYBOARD_LIGHT_SPEEDS[effect].values()
        if effect in KEYBOARD_OPTION_EFFECTS:
            return (len(command) == 18 and command[3] == 0 and command[4] in speeds and command[5] <= 100
                    and command[6] in KEYBOARD_COLOUR_MODES.values() and command[7:9] == b"\xff\xff"
                    and (command[6] != KEYBOARD_COLOUR_MODES["single"] or command[12:15] == b"\0\0\0"))
        if effect in KEYBOARD_SINGLE_COLOUR:
            return (len(command) == 12 and command[3] == 0 and command[4] in speeds and command[5] <= 100
                    and command[6:9] == bytes([0x00, 0xFF, 0xFF])
                    and (effect not in KEYBOARD_COLOURLESS or command[9:] == b"\0\0\0"))
        if effect == "quicksand":
            if command[3] == 0x01:
                return (len(command) == 18 and command[4] in speeds and command[5] <= 100
                        and command[6] in KEYBOARD_QUICKSAND_MODES.values()
                        and command[7] in KEYBOARD_DIRECTIONS["quicksand"].values() and command[8] == 0xFF)
            return command[3] == 0x00 and len(command) == 13
        # gradient chunk: 02 = header + 2 stops, 01 / 00 = up to 4 stops each; positions 1..100 %
        chunk = command[3]
        if chunk == 0x02:
            head, stops = command[4:10], command[10:]
            low, high = KEYBOARD_GRADIENT_STOPS
            return (len(head) == 6 and head[0] in speeds and head[1] <= 100 and head[2] == 0
                    and head[3] in KEYBOARD_DIRECTIONS[effect].values() and head[4] in KEYBOARD_THICKNESS.values()
                    and low <= head[5] <= high and len(stops) == 8 and _stops_ok(stops))
        if chunk in (0x01, 0x00):
            return len(command[4:]) <= 16 and _stops_ok(command[4:])
        return False
    return False


def reply_expected(command: bytes) -> Optional[bytes]:
    """The reply that acknowledges a command that is not echoed: per-key colours c0 81 <n> 00 -> c0 82 <n> 00."""
    if command[:2] == KEYBOARD_DIRECT_WRITE and len(command) >= 4:
        return KEYBOARD_DIRECT_ACK + command[2:4]
    return None


def is_allowed_write(command: bytes, device: str) -> bool:
    """Writes are allowed per device: a mouse command is never sent to the keyboard and the other way round."""
    if device == "mouse":
        return is_allowed_mouse_write(command)
    if device == "keyboard":
        return is_allowed_keyboard_write(command)
    return False


def is_allowed_mouse_write(command: bytes) -> bool:
    if command == SAVE_COMMAND:
        return True
    if command[:2] == LIFTOFF_WRITE:
        if len(command) == 6:
            return command[2:5] == bytes([NO_SURFACE, 0x00, NO_SURFACE]) and command[5] in LIFTOFF_CODES.values()
        return (len(command) == 10 and command[2:4] == b"\0\0" and command[4] in SURFACES
                and command[5] in LIFTOFF_CODES.values() and command[6] == 0)
    if command[:2] == POWER_WRITE:
        return (len(command) == 7 and command[2:4] == b"\0\0" and command[4] in POWER_OFF_CODES.values()
                and command[5] == 0 and command[6] in LOW_WARNING_LEVELS)
    if command[:2] == SENSOR_WRITE and len(command) >= 4 and command[3] == 0:
        if command[2] == ANGLE_SNAPPING_FIELD:
            return len(command) == 5 and command[4] in (0, 1)
        if command[2] == POLLING_FIELD:
            return len(command) == 5 and command[4] in POLLING_WRITE_HZ.values()
        if command[2] in (ACTIVE_STAGE_FIELD, STAGE_COUNT_FIELD):
            return len(command) == 5 and 1 <= command[4] <= DPI_STAGES
        if command[2] < DPI_STAGES:
            dpi = int.from_bytes(command[4:6], "little") * 50 + 50
            return len(command) == 9 and DPI_MIN <= dpi <= DPI_MAX and dpi % DPI_STEP == 0
        if command[2] == ANGLE_TUNING_FIELD and len(command) == 6:
            value = int.from_bytes(command[4:6], "little", signed=True)
            return value in ANGLE_TUNING_DEGREES
        return False   # debounce and anything else: not approved
    return (len(command) == 12 and command[:2] == LED_WRITE and command[2] in LED_ZONE_INDEX.values()
            and command[3] == 0 and command[4] in LED_WRITE_MODES.values() and command[5] <= 100
            and command[9:] == b"\0\0\0")


def packet(report_id: Optional[int], command: bytes, length: int, write: bool = False, *, device: str) -> bytes:
    """The only place packets are built. Reads must be on ALLOWED_READS[device]; writes must pass
    is_allowed_write(command, device) AND be sent with write=True, so a read path can never send a write.
    The report ID must be the device's own on the Omni (or None, wired)."""
    if device not in REPORT_IDS:
        raise ValueError(f"unknown device {device!r}")
    if report_id is not None and report_id != REPORT_IDS[device]:
        raise ValueError(f"report ID {report_id:02x} is not the {device}'s")
    if write:
        if not is_allowed_write(command, device):
            raise ValueError(f"write {command.hex(' ')} is not on the {device} allowlist")
    elif command not in ALLOWED_READS[device]:
        raise ValueError(f"command {command.hex(' ')} is not on the {device} allowlist")
    rid = 0x00 if report_id is None else report_id
    return (bytes([rid]) + command).ljust(length, b"\0")


def build_packet(target: Target, length: int) -> bytes:
    return packet(target.report_id, target.query, length, device=target.device)


def find_echo(reply: bytes, query: bytes) -> Optional[int]:
    """Return index where the echoed command starts (0 or 1, depending on report-ID byte)."""
    for base in (0, 1):
        if reply[base:base + len(query)] == query:
            return base
    return None


def is_error(reply: bytes, report_id: Optional[int]) -> bool:
    for base in (0, 1):
        if reply[base:base + 2] == b"\xff\xaa":
            if base == 1 and report_id is not None and reply[0] != report_id:
                continue
            return True
    return False


def parse(device: str, reply: bytes, base: int) -> tuple[Optional[int], bool]:
    p = reply[base:]
    if device == "keyboard":
        pct = p[10] if len(p) > 10 else None
        if pct is None or pct > 100:
            # [5] also carries the percentage on the Falchion RX LP (confirmed 2026-11-10)
            pct = p[5] if len(p) > 5 and p[5] <= 100 else None
        charging = len(p) > 8 and p[8] != 0
    else:
        pct = p[4] if len(p) > 4 and p[4] <= 100 else None
        charging = len(p) > 9 and p[9] != 0
    return pct, charging


def parse_warning(device: str, reply: bytes, base: int) -> Optional[int]:
    """Low-battery warning level (%) configured on the device, or None if absent/implausible."""
    p = reply[base:]
    index = 9 if device == "keyboard" else 6
    value = p[index] if len(p) > index else None
    low = 0 if device == "keyboard" else 1             # keyboard: 0 = never warn (AC, 2026-11-22)
    return value if value is not None and low <= value <= 100 else None


@dataclass
class Exchange:
    status: str                      # ok | asleep | no-response | error
    reply: bytes = b""
    base: int = 0                    # index of the echoed command in reply
    raw: str = ""

    @property
    def payload(self) -> bytes:
        """Reply from the echoed command on (indices as in protocol.json notes, without report ID)."""
        return self.reply[self.base:]


def exchange(dev, target: Target, command: bytes, debug: bool = False, match: Optional[int] = None,
             write: bool = False) -> Exchange:
    """Send one allowlisted command and wait for the reply that echoes it (first `match` bytes), or for its
    acknowledgement where the device answers with other bytes (reply_expected: per-key colours)."""
    echo = reply_expected(command) or command[:match or len(command)]
    # drain stale input (bounded, short timeout)
    for _ in range(MAX_READS):
        if not dev.read(65, DRAIN_TIMEOUT_MS):
            break
    written, last_error = -1, "write failed"
    for length in target.report_lens:
        data = packet(target.report_id, command, length, write=write, device=target.device)
        try:
            written = dev.write(data)
        except (OSError, ValueError) as error:
            written, last_error = -1, f"write failed: {error}"
        if debug:
            print(f"[{target.device}] > {data[:5].hex(' ')} … ({length} bytes) -> {written}", file=sys.stderr)
        if written is not None and written >= 0:
            break
    if written is None or written < 0:
        return Exchange("error", raw=last_error)
    saw_error, raw = False, ""
    for _ in range(MAX_READS):
        reply = bytes(dev.read(65, READ_TIMEOUT_MS))
        if not reply:
            if debug:
                print(f"[{target.device}] < (timeout)", file=sys.stderr)
            break
        if debug:
            print(f"[{target.device}] < {reply.hex(' ')}", file=sys.stderr)
        if is_error(reply, target.report_id):
            # the receiver sometimes sends ff aa right before the real reply: keep reading
            saw_error, raw = True, reply.hex(" ")
            continue
        base = find_echo(reply, echo)
        if base is None:
            continue  # other traffic (e.g. button/profile events); keep reading
        return Exchange("ok", reply, base, reply.hex(" "))
    return Exchange("asleep" if saw_error else "no-response", raw=raw)


def query(dev, target: Target, debug: bool = False) -> Battery:
    result = Battery(target.device, target.name, "omni" if target.pid == PID_OMNI else "wired", None)
    ex = exchange(dev, target, target.query, debug)
    result.raw = ex.raw
    if ex.status != "ok":
        result.status = ex.status
        return result
    pct, charging = parse(target.device, ex.reply, ex.base)
    if pct is None or (pct == 0 and not charging):
        result.status = "asleep"   # G-Helper treats 0 % + not charging as disconnected
        return result
    result.percent, result.charging = pct, charging
    result.low_warning = parse_warning(target.device, ex.reply, ex.base)
    return result


# --- Linux: hidraw backend -------------------------------------------------------------------------------

def descriptor_collections(descriptor: bytes) -> list[tuple[int, int]]:
    """(usage page, usage) of each top-level collection in a HID report descriptor."""
    result, page, usage, usage_size, depth, stack, i = [], 0, None, 0, 0, [], 0
    while i < len(descriptor):
        prefix = descriptor[i]
        if prefix == 0xFE:  # long item: [FE, size, tag, data...]
            i += 3 + (descriptor[i + 1] if i + 1 < len(descriptor) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        data = descriptor[i + 1:i + 1 + size]
        value = int.from_bytes(data, "little")
        kind, tag = (prefix >> 2) & 0x03, prefix >> 4
        i += 1 + size
        if kind == 1:            # global
            if tag == 0x0:
                page = value
            elif tag == 0xA:
                stack.append(page)
            elif tag == 0xB and stack:
                page = stack.pop()
        elif kind == 2:          # local
            if tag == 0x0 and usage is None:
                usage, usage_size = value, size
        elif kind == 0:          # main
            if tag == 0xA:
                if depth == 0:
                    if usage is not None and usage_size == 4:
                        result.append((usage >> 16, usage & 0xFFFF))  # usage carries its own page
                    else:
                        result.append((page, usage or 0))
                depth += 1
            elif tag == 0xC:
                depth = max(0, depth - 1)
            usage = None         # local items end at every main item
    return result


class HidrawDevice:
    """Same small interface as hidapi's device (open_path/write/read/close), on a /dev/hidraw node."""

    def __init__(self):
        self.fd: Optional[int] = None

    def open_path(self, path) -> None:
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def write(self, data) -> int:
        # first byte is the report ID, 0 for unnumbered reports (stripped by the kernel), like hidapi
        return os.write(self.fd, bytes(data))

    def read(self, size: int, timeout_ms: int) -> list:
        if timeout_ms <= 0:
            raise ValueError("timeout must be > 0")  # same rule as with hidapi: never block forever
        ready, _, _ = select.select([self.fd], [], [], timeout_ms / 1000)
        if not ready:
            return []
        try:
            return list(os.read(self.fd, size))
        except BlockingIOError:
            return []

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class HidrawBackend:
    """hidapi-like enumerate()/device() for Linux. One entry per hidraw node (= one USB interface).

    usage_page is the *lowest* top-level page, so a node that mixes vendor and input collections is
    filtered out by enumerate_paths(): we never open a node that also carries keyboard or mouse input."""

    def __init__(self, sysfs: Path = Path("/sys/class/hidraw"), dev: Path = Path("/dev")):
        self.sysfs, self.dev = sysfs, dev

    def enumerate(self, vid: int = 0, pid: int = 0) -> list[dict]:
        infos = []
        nodes = sorted(self.sysfs.glob("hidraw*"), key=lambda n: int(n.name[6:] or 0))
        for node in nodes:
            try:
                uevent = dict(line.split("=", 1) for line in
                              (node / "device" / "uevent").read_text().splitlines() if "=" in line)
                _, v, p = uevent.get("HID_ID", "0:0:0").split(":")
                v, p = int(v, 16), int(p, 16)
                if (vid and v != vid) or (pid and p != pid):
                    continue
                collections = descriptor_collections((node / "device" / "report_descriptor").read_bytes())
            except (OSError, ValueError):
                continue
            try:
                iface = int((node / "device" / ".." / "bInterfaceNumber").read_text().strip(), 16)
            except (OSError, ValueError):
                iface = -1
            pages = [page for page, _ in collections]
            infos.append({
                "path": os.fsencode(self.dev / node.name),
                "vendor_id": v, "product_id": p, "interface_number": iface,
                "usage_page": min(pages) if pages else 0,
                "usage": collections[0][1] if collections else 0,
                "collections": collections,
                "product_string": uevent.get("HID_NAME", ""),
            })
        return infos

    @staticmethod
    def device() -> HidrawDevice:
        return HidrawDevice()


class device_lock:
    """Serialises device sessions between Squire processes (tray, settings window, CLI) for this user.

    Each process gets its own copy of every reply, so this is about keeping request/reply pairs together,
    not about safety. Best effort: if the lock file cannot be created, run without it."""

    def __init__(self, path: Optional[Path] = None):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        self.path = path or (Path(runtime) if runtime else state_dir()) / "squire-gear-device.lock"
        self.handle = None

    def __enter__(self):
        try:
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "w")
            fcntl.flock(self.handle, fcntl.LOCK_EX)   # waits; a session takes about a second at most
        except (ImportError, OSError):
            self.handle = None
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            self.handle.close()   # closing releases the lock
        return False


def load_backend():
    if IS_LINUX:
        return HidrawBackend()
    import hid  # pip package "hidapi"
    return hid


def enumerate_paths(hid, pid: int) -> list[bytes]:
    """Unique HID paths for vendor-defined collections of a product (macOS lists one entry per collection)."""
    paths = []
    for info in hid.enumerate(VID, pid):
        if info.get("usage_page", 0) < 0xFF00:
            continue  # skip standard keyboard/mouse/consumer collections: never touch typing input
        if pid == PID_OMNI and info.get("interface_number", 2) not in (2, -1):
            continue
        if pid == PID_FALCHION_RX_LP and info.get("interface_number", 1) not in (1, -1):
            continue
        if info["path"] not in paths:
            paths.append(info["path"])
    return paths


CONNECTION_LABELS = {"omni": "Omni receiver", "wired": "Wired", "bluetooth": "Bluetooth"}


def connection_label(connection: str) -> str:
    return CONNECTION_LABELS.get(connection, connection)


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
    (same rule as Bluetooth.merge in SquireCore)."""
    merged = []
    for device in ("keyboard", "mouse"):
        wired = next((b for b in hid if b.device == device), None)
        ble = next((b for b in bluetooth if b.device == device and b.percent is not None), None)
        choice = wired if wired is not None and wired.percent is not None else \
            ble or wired or next((b for b in bluetooth if b.device == device), None)
        if choice is not None:
            merged.append(choice)
    return merged


def _modalias_pid(modalias: str) -> Optional[int]:
    """'usb:v0B05p1B08d0001' -> 0x1B08 for an ASUS device, else None. BlueZ writes 'bluetooth:v...' for
    Bluetooth SIG vendor ids; ASUS devices report the USB vendor id here."""
    try:
        kind, rest = modalias.split(":", 1)
        if kind != "usb" or not rest.startswith("v") or "p" not in rest:
            return None
        vendor, product = rest[1:].split("p", 1)
        if int(vendor[:4], 16) != VID:
            return None
        return int(product[:4], 16)
    except ValueError:
        return None


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


def bluez_objects(debug: bool = False) -> dict:
    """BlueZ's managed objects via busctl (systemd, on every Ubuntu). Read-only: asks bluetoothd for what it
    already holds; nothing is sent to the device. {} when BlueZ or busctl is missing."""
    try:
        out = subprocess.run(["busctl", "--system", "--json=short", "call", "org.bluez", "/",
                              "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        if debug:
            print(f"[bluetooth] busctl failed: {error}", file=sys.stderr)
        return {}
    if out.returncode != 0:
        if debug:
            print(f"[bluetooth] busctl: {out.stderr.strip()}", file=sys.stderr)
        return {}
    try:
        return json.loads(out.stdout)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}


def read_bluetooth(debug: bool = False) -> list[Battery]:
    """Battery levels of the devices paired over Bluetooth (Linux: BlueZ org.bluez.Battery1)."""
    if not IS_LINUX:
        return []   # macOS: CoreBluetooth in the Swift app (BluetoothReader)
    results = parse_bluez_objects(bluez_objects(debug))
    if debug:
        for b in results:
            print(f"[{b.device}] bluetooth: {b.raw} -> {b.percent} ({b.status})", file=sys.stderr)
    return results


def _read_all(debug: bool = False, hid=None) -> list[Battery]:
    hid = hid or load_backend()
    found: dict[str, Battery] = {}
    for target in TARGETS:
        if target.device in found and found[target.device].percent is not None:
            continue  # already have a value (e.g. via Omni); skip other connection
        for path in enumerate_paths(hid, target.pid):
            dev = hid.device()
            if debug:
                print(f"[{target.device}] open {path!r} (report id {target.report_id})", file=sys.stderr)
            try:
                dev.open_path(path)
            except (OSError, IOError) as error:
                if debug:
                    print(f"[{target.device}] open {path!r} failed: {error}", file=sys.stderr)
                if isinstance(error, PermissionError) and target.device not in found:
                    # Linux without the udev rule: say so instead of "not found"
                    found[target.device] = Battery(target.device, target.name,
                                                   "omni" if target.pid == PID_OMNI else "wired", None,
                                                   status="no-access", raw=str(error))
                continue
            try:
                result = query(dev, target, debug)
            finally:
                dev.close()
            previous = found.get(target.device)
            if previous is None or (previous.percent is None and result.percent is not None):
                found[target.device] = result
            if result.percent is not None:
                break
            time.sleep(0.05)
    return [found[k] for k in ("keyboard", "mouse") if k in found]


# --- Mouse settings (read-only) ------------------------------------------------------------------------------

LED_MODES = {0x00: "static", 0x01: "breathing", 0x02: "color-cycle", 0x03: "rainbow", 0x04: "react",
             0x05: "comet", 0x06: "battery", 0xF0: "off"}
POLLING_RATES_HZ = (125, 250, 500, 1000, 2000, 4000, 8000, 16000)   # index in the low 3 bits
POWER_OFF_MINUTES = {0: 1, 1: 2, 2: 3, 3: 5, 4: 10, 0xFF: None}     # None = never
LED_ZONES = ("logo", "wheel", "side")


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


def _u16(p: bytes, i: int) -> int:
    return p[i] | (p[i + 1] << 8)


def _dpi(raw_value: int) -> int:
    return raw_value * 50 + 50      # devices store (DPI - 50) / 50


def parse_mouse_settings(s: MouseSettings, name: str, p: bytes) -> None:
    """Fill s from one reply payload p (starting at the echoed command)."""
    if len(p) < 20:
        return
    if name == "battery":
        s.power_off_minutes = POWER_OFF_MINUTES.get(p[5], None)
        s.low_warning = p[6] if 1 <= p[6] <= 100 else None
    elif name == "profile":
        s.profile = p[10] + 1
        s.dpi_stage = p[11] if p[11] else None
        s.dpi_stage_count = p[18] if 1 <= p[18] <= 4 else None
        # Firmware: 3 bytes, build.minor.major (shown major first, like libratbag). On the Keris II Origin the
        # mouse's own version is at [4..6] over the cable; via the Omni [4..6] is the receiver and the mouse moves
        # to [14..16] (same bytes 09 00 01 = 01.00.09 both ways, 2026-11-12). libratbag's [13..15] is not it here.
        def version(i: int) -> Optional[str]:
            return f"{p[i + 2]:02X}.{p[i + 1]:02X}.{p[i]:02X}" if any(p[i:i + 3]) else None
        if s.connection == "wired":
            s.firmware = version(4)
        else:
            s.firmware, s.receiver_firmware = version(14), version(4)
    elif name == "settings":
        # ff ff = not in this reply: mice with separate X/Y DPI (like the Keris) report DPI only in dpi_xy
        s.dpi = [None if _u16(p, 4 + 2 * i) == 0xFFFF else _dpi(_u16(p, 4 + 2 * i)) for i in range(4)]
        s.polling_raw = p[12]
        s.polling_hz = POLLING_RATES_HZ[p[12] & 0x07]
        s.debounce_index = p[14]
        s.debounce_ms = 4 + 4 * p[14] if 1 <= p[14] <= 7 else None
        s.angle_snapping = p[16] == 1
        angle = _u16(p, 18)
        s.angle_tuning = angle - 0x10000 if angle & 0x8000 else angle
    elif name == "dpi_xy":
        s.dpi_xy = [[_dpi(_u16(p, 4 + 4 * i)), _dpi(_u16(p, 6 + 4 * i))] for i in range(4)]
        if not s.dpi or None in s.dpi:
            s.dpi = [x for x, _ in s.dpi_xy]
    elif name == "dpi_colors":
        s.dpi_colors = ["#" + p[4 + 3 * i:7 + 3 * i].hex() for i in range(4)]
    elif name.startswith("led_"):
        s.leds[name[4:]] = {"mode": LED_MODES.get(p[4], f"0x{p[4]:02x}"), "brightness": p[5],
                            "color": "#" + p[6:9].hex()}
        s.led_speed_raw[name[4:]] = p[12]    # [10..12] direction, random, speed (G-Helper); no effect on the Keris
    elif name == "liftoff":
        # [6] surface id (ff = none), [7] lift-off, [8..10] surface parameters (Armoury Crate captures 2026-11-12)
        s.liftoff = {0: "low", 1: "high"}.get(p[7], f"0x{p[7]:02x}")
        s.surface_id = p[6]
        s.surface_params = None if p[6] == NO_SURFACE else p[8:11].hex(" ")


def read_mouse_settings(debug: bool = False, hid=None) -> MouseSettings:
    """Current settings of the Keris II Origin (active profile only; never switches profiles).

    Sends only the read commands in MOUSE_SETTINGS_QUERIES plus the battery query."""
    with device_lock():
        return _read_mouse_settings(debug, hid)


def _read_mouse_settings(debug: bool = False, hid=None) -> MouseSettings:
    hid = hid or load_backend()
    result = MouseSettings("ROG Keris II Origin", "", status="not-found")
    for target in (t for t in TARGETS if t.device == "mouse"):
        for path in enumerate_paths(hid, target.pid):
            dev = hid.device()
            try:
                dev.open_path(path)
            except (OSError, IOError) as error:
                if debug:
                    print(f"[mouse] open {path!r} failed: {error}", file=sys.stderr)
                if isinstance(error, PermissionError):
                    result.status = "no-access"
                continue
            try:
                settings = MouseSettings(target.name, "omni" if target.pid == PID_OMNI else "wired")
                battery = exchange(dev, target, target.query, debug)
                if battery.status != "ok":
                    result = settings
                    result.status = battery.status
                    continue   # asleep here; maybe it is on the other connection
                settings.raw["battery"] = battery.payload.hex(" ")
                parse_mouse_settings(settings, "battery", battery.payload)
                for name, command in MOUSE_SETTINGS_QUERIES.items():
                    ex = exchange(dev, target, command, debug, match=3 if command[1] == 0x04 else 2)
                    if ex.status != "ok":
                        settings.raw[name] = ex.status
                        continue
                    settings.raw[name] = ex.payload.hex(" ")
                    parse_mouse_settings(settings, name, ex.payload)
                return settings
            finally:
                dev.close()
    return result


# --- Keyboard settings (Falchion RX Low Profile), read-only ---
KEYBOARD_LAYOUTS = {3: "DE", 4: "UK", 5: "FR", 17: "US", 25: "CH-DE"}
# Layout ids G-Helper draws as ISO (an extra key left of Z, tall Enter)
KEYBOARD_ISO_LAYOUTS = frozenset([3, 4, 5, 7, 8, 11, 12, 15, 18, 19, 20, 23, 25])


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
    if name == "battery":
        s.sleep_minutes = POWER_OFF_MINUTES.get(p[6])
        s.low_warning = p[9] if p[9] <= 100 else None          # 0 = never (AC, 2026-11-22)
    elif name == "profile":
        s.light_mode = KEYBOARD_LIGHT_MODES.get(p[9], f"0x{p[9]:02x}")
        s.profile = p[10] if 1 <= p[10] <= 5 else None
        if s.connection == "omni" and any(p[4:7]):
            s.receiver_firmware = f"{p[6]:02X}.{p[5]:02X}.{p[4]:02X}"
        fw = p[11:14] if s.connection == "omni" else p[4:7]   # cable: [4..6] (Windows, 2026-11-21)
        if any(fw):
            s.firmware = f"{fw[2]:02X}.{fw[1]:02X}.{fw[0]:02X}"
    elif name == "layout":
        s.layout_id = p[5]
        s.layout = KEYBOARD_LAYOUTS.get(p[5])
        s.iso = p[5] in KEYBOARD_ISO_LAYOUTS


def read_keyboard_settings(debug: bool = False, hid=None) -> KeyboardSettings:
    with device_lock():
        hid = hid or load_backend()
        result = KeyboardSettings("ROG Falchion RX LP", "", status="not-found")
        for target in (t for t in TARGETS if t.device == "keyboard"):
            for path in enumerate_paths(hid, target.pid):
                dev = hid.device()
                try:
                    dev.open_path(path)
                except (OSError, IOError) as error:
                    if debug:
                        print(f"[keyboard] open {path!r} failed: {error}", file=sys.stderr)
                    if isinstance(error, PermissionError):
                        result.status = "no-access"
                    continue
                try:
                    settings = KeyboardSettings(target.name, "omni" if target.pid == PID_OMNI else "wired")
                    battery = exchange(dev, target, target.query, debug)
                    if battery.status != "ok":
                        result = settings
                        result.status = battery.status
                        continue   # asleep here; maybe it is on the other connection
                    settings.raw["battery"] = battery.payload.hex(" ")
                    parse_keyboard_settings(settings, "battery", battery.payload)
                    for name, command in KEYBOARD_SETTINGS_QUERIES.items():
                        ex = exchange(dev, target, command, debug)
                        if ex.status != "ok":
                            settings.raw[name] = ex.status
                            continue
                        settings.raw[name] = ex.payload.hex(" ")
                        parse_keyboard_settings(settings, name, ex.payload)
                    return settings
                finally:
                    dev.close()
        return result


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


@dataclass
class WriteResult:
    status: str                    # ok | mismatch | asleep | no-response | no-access | not-found | error | dry-run
    sent: list = field(default_factory=list)       # hex of every packet sent (or that would be sent)
    before: Optional[dict] = None  # LED state read before the write
    after: Optional[dict] = None   # LED state read back after the write
    saved: bool = False
    message: str = ""


def led_matches(wanted: dict, actual: Optional[dict]) -> bool:
    if not actual or actual["mode"] != wanted["mode"]:
        return False
    if wanted["mode"] == "off":
        return True
    if actual["brightness"] != wanted["brightness"]:
        return False
    return wanted["mode"] not in ("static", "breathing", "react") or actual["color"] == wanted["color"]


def set_led(zone: str, mode: str, brightness: int, color: str = "#000000", save: bool = False,
            dry_run: bool = False, debug: bool = False, hid=None) -> WriteResult:
    """Set one LED zone of the Keris II Origin, then read it back. Only on an explicit request (CLI or a click).

    Without save the change lives in the mouse's RAM (lost when it powers off); save=True also sends 50 03."""
    command = led_command(zone, mode, brightness, color)
    wanted = {"mode": mode, "brightness": int(brightness), "color": "#" + command[6:9].hex()}
    return write_verified(command, f"led_{zone}", lambda s: s.leds.get(zone), lambda after, _: led_matches(wanted, after),
                          save, dry_run, debug, hid)


def set_power(power_off_minutes: Optional[int], low_warning: int, save: bool = False, dry_run: bool = False,
              debug: bool = False, hid=None) -> WriteResult:
    """Auto power-off (1/2/3/5/10 min, None = never) and low-battery warning (%), read back from 12 07."""
    command = power_command(power_off_minutes, low_warning)
    wanted = {"power_off_minutes": power_off_minutes, "low_warning": low_warning}
    return write_verified(command, "battery",
                          lambda s: {"power_off_minutes": s.power_off_minutes, "low_warning": s.low_warning},
                          lambda after, _: after == wanted, save, dry_run, debug, hid)


def set_angle_snapping(on: bool, save: bool = False, dry_run: bool = False, debug: bool = False,
                       hid=None) -> WriteResult:
    command = angle_snapping_command(on)
    return write_verified(command, "settings", lambda s: {"angle_snapping": s.angle_snapping},
                          lambda after, _: after == {"angle_snapping": bool(on)}, save, dry_run, debug, hid)


def set_angle_tuning(degrees: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                     hid=None) -> WriteResult:
    command = angle_tuning_command(degrees)
    return write_verified(command, "settings", lambda s: {"angle_tuning": s.angle_tuning},
                          lambda after, _: after == {"angle_tuning": degrees}, save, dry_run, debug, hid)


def set_liftoff(liftoff: str, save: bool = False, dry_run: bool = False, debug: bool = False,
                hid=None) -> WriteResult:
    """Lift-off low/high. Reads 12 06 first and writes the surface id + parameters it found back unchanged; the
    read-back must show the new lift-off and the same surface. --dry-run reads 12 06 (a get) to show the bytes."""
    if liftoff not in LIFTOFF_CODES:
        raise ValueError(f"lift-off {liftoff!r} is not low or high")
    def extract(s):
        return {"liftoff": s.liftoff, "surface_id": s.surface_id, "surface_params": s.surface_params}
    def matches(after, before):
        return bool(after and before) and after == {**before, "liftoff": liftoff}
    return write_verified(lambda before: liftoff_command(liftoff, before["surface_id"], before["surface_params"]),
                          "liftoff", extract, matches, save, dry_run, debug, hid)


def set_surface(surface_id: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                hid=None) -> WriteResult:
    """Pick a preset surface (mouse pad) or NO_SURFACE. Keeps the lift-off read from 12 06 just before (Armoury Crate
    resets it to low). Refused while the mouse has a surface Squire does not know: it may be a calibration."""
    if surface_id not in SURFACE_PARAMS:
        raise ValueError(f"surface {surface_id!r} is not one of Armoury Crate's presets")
    def extract(s):
        return {"liftoff": s.liftoff, "surface_id": s.surface_id, "surface_params": s.surface_params}
    def build(before):
        if before["surface_id"] not in SURFACE_PARAMS:
            raise ValueError(f"the mouse has surface {surface_name(before['surface_id'])}: not replaced, it may be "
                             "a calibration")
        return surface_command(surface_id, before["liftoff"])
    def matches(after, before):
        return bool(after and before) and after == {"liftoff": before["liftoff"], "surface_id": surface_id,
                                                    "surface_params": SURFACE_PARAMS[surface_id]}
    return write_verified(build, "liftoff", extract, matches, save, dry_run, debug, hid)


def set_polling(hz: int, save: bool = False, dry_run: bool = False, debug: bool = False, hid=None) -> WriteResult:
    """Polling rate 125/250/500/1000 Hz, read back from 12 04 00 [12] (low 3 bits)."""
    command = polling_command(hz)
    return write_verified(command, "settings", lambda s: {"polling_hz": s.polling_hz},
                          lambda after, _: after == {"polling_hz": hz}, save, dry_run, debug, hid)


def set_dpi(stage: int, dpi: int, color: Optional[str] = None, save: bool = False, dry_run: bool = False,
            debug: bool = False, hid=None) -> WriteResult:
    """One DPI stage (1-based), X and Y alike. The stage colour travels in the same command: without `color` the
    one read from 12 04 03 just before is written back. Refused for a stage with separate X/Y values."""
    dpi_command(stage, dpi, color or "#000000")       # validate before anything is sent
    color = "#" + bytes.fromhex(color.lstrip("#")).hex() if color else None
    def extract(s):
        i = stage - 1
        return {"dpi_xy": s.dpi_xy[i] if i < len(s.dpi_xy) else None,
                "color": s.dpi_colors[i] if i < len(s.dpi_colors) else None}
    def build(before):
        if not before["dpi_xy"] or not before["color"]:
            raise ValueError("DPI stage not read: nothing written")
        if before["dpi_xy"][0] != before["dpi_xy"][1]:
            raise ValueError(f"stage {stage} has X {before['dpi_xy'][0]} / Y {before['dpi_xy'][1]}: Squire would set "
                             "both alike, so it is not written")
        return dpi_command(stage, dpi, color or before["color"])
    def matches(after, before):
        return bool(after and before) and after == {"dpi_xy": [dpi, dpi], "color": (color or before["color"]).lower()}
    return write_verified(build, ("dpi_xy", "dpi_colors"), extract, matches, save, dry_run, debug, hid)


def set_active_stage(stage: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                     hid=None) -> WriteResult:
    """The DPI stage in use (1-based), read back from 12 00 [11]. Only a stage the DPI button cycles through."""
    active_stage_command(stage)
    def build(before):
        if before["dpi_stage_count"] is None or stage > before["dpi_stage_count"]:
            raise ValueError(f"stage {stage} is not one of the {before['dpi_stage_count']} on the DPI button")
        return active_stage_command(stage)
    return write_verified(build, "profile",
                          lambda s: {"dpi_stage": s.dpi_stage, "dpi_stage_count": s.dpi_stage_count},
                          lambda after, before: bool(after and before) and after == {**before, "dpi_stage": stage},
                          save, dry_run, debug, hid)


def set_stage_count(count: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                    hid=None) -> WriteResult:
    """How many stages the DPI button cycles through, read back from 12 00 [18]. Never fewer than the active stage
    (what the mouse would do then is unknown)."""
    stage_count_command(count)
    def build(before):
        if before["dpi_stage"] is None or count < before["dpi_stage"]:
            raise ValueError(f"stage {before['dpi_stage']} is active: choose it or a lower one first")
        return stage_count_command(count)
    return write_verified(build, "profile",
                          lambda s: {"dpi_stage": s.dpi_stage, "dpi_stage_count": s.dpi_stage_count},
                          lambda after, before: bool(after and before) and after == {**before, "dpi_stage_count": count},
                          save, dry_run, debug, hid)


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
    commands = keyboard_light_packets(value)
    read = keyboard_effect_read(effect)
    result = write_verified(commands, "profile", lambda s: {"light_mode": s.light_mode, "profile": s.profile},
                            lambda after, before: bool(after and before) and after == {**before, "light_mode": read},
                            save, dry_run, debug, hid, device="keyboard")
    if result.status == "ok" and result.saved and result.after.get("profile"):
        remember_keyboard_light(result.after["profile"], value)
    if result.status == "ok" and not result.message:
        result.message = "effect read back; colour and brightness cannot be read from the keyboard"
    return result


def keyboard_keys_value(spec: str) -> dict:
    """Parse "all=ff0000" or "00=ff0000,08=00ff00" (hex key ids, see KEYBOARD_DIRECT_KEYS) into {key id: (r, g, b)}."""
    keys = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        name, sep, colour = part.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=RRGGBB, got {part!r}")
        rgb = tuple(bytes.fromhex(_rgb(colour)[1:]))
        ids = KEYBOARD_DIRECT_KEYS if name.strip().lower() == "all" else (int(name, 16),)
        for key in ids:
            if key not in KEYBOARD_DIRECT_KEYS:
                raise ValueError(f"key id {key:02x} is outside the 15 x 5 matrix (01..75)")
            keys[key] = rgb
    if not keys:
        raise ValueError("no keys given")
    return keys


def keyboard_direct_packets(keys: dict) -> list:
    """c0 81 <n> 00 + n x <id> <r g b>, at most 14 keys per packet, in key id order."""
    items = sorted(keys.items())
    commands = []
    for start in range(0, len(items), KEYBOARD_DIRECT_PER_PACKET):
        chunk = items[start:start + KEYBOARD_DIRECT_PER_PACKET]
        body = b"".join(bytes([key, *rgb]) for key, rgb in chunk)
        commands.append(KEYBOARD_DIRECT_WRITE + bytes([len(chunk), 0]) + body)
    return commands


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
    commands = keyboard_direct_packets(colours)
    result = write_verified(commands, "profile", lambda s: {"light_mode": s.light_mode, "profile": s.profile},
                            lambda after, before: bool(after and before) and after["profile"] == before["profile"],
                            False, dry_run, debug, hid, device="keyboard")
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
    command = keyboard_sleep_command(minutes)
    return write_verified(command, "battery", lambda s: {"sleep_minutes": s.sleep_minutes},
                          lambda after, _: after == {"sleep_minutes": minutes}, save, dry_run, debug, hid,
                          device="keyboard")


def set_keyboard_warning(percent: int, save: bool = False, dry_run: bool = False, debug: bool = False,
                         hid=None) -> WriteResult:
    """Low-battery warning level (10..50 %, 0 = never), read back from 12 01 [9]."""
    command = keyboard_warning_command(percent)
    return write_verified(command, "battery", lambda s: {"low_warning": s.low_warning},
                          lambda after, _: after == {"low_warning": percent}, save, dry_run, debug, hid,
                          device="keyboard")


# What write_verified needs per device: the save command, how a read name maps to a get request, and how its reply
# is parsed into a fresh settings object.
WRITE_DEVICES = {
    "mouse": dict(save=SAVE_COMMAND, reads=lambda name: MOUSE_QUERY if name == "battery" else MOUSE_SETTINGS_QUERIES[name],
                  settings=lambda connection: MouseSettings("", connection), parse=lambda s, n, p: parse_mouse_settings(s, n, p)),
    "keyboard": dict(save=KEYBOARD_SAVE_COMMAND,
                     reads=lambda name: KEYBOARD_QUERY if name == "battery" else KEYBOARD_SETTINGS_QUERIES[name],
                     settings=lambda connection: KeyboardSettings("", connection),
                     parse=lambda s, n, p: parse_keyboard_settings(s, n, p)),
}


def write_verified(command, read_name, extract, matches, save: bool = False, dry_run: bool = False,
                   debug: bool = False, hid=None, device: str = "mouse") -> WriteResult:
    """The one write path: check the device is awake, read the setting, write, read it back, and save (mouse 50 03,
    keyboard 50 55) only when asked and only if the read-back matched. `extract` picks the value from parsed
    settings; `matches(after, before)` judges the read-back. `command` is the bytes, or a function of the value read
    before (lift-off keeps the surface it reads), checked against the device's allowlist once it is built. A list of
    commands (the keyboard's gradient chunks) is sent in order, each must be echoed, then read back once."""
    spec = WRITE_DEVICES[device]
    save_command = spec["save"]
    build = command if callable(command) else None
    commands = None if build else ([command] if isinstance(command, (bytes, bytearray)) else list(command))
    for c in commands or ():
        if not is_allowed_write(c, device):
            raise ValueError(f"not on the {device} write allowlist: {c.hex(' ')}")
    read_names = (read_name,) if isinstance(read_name, str) else tuple(read_name)   # DPI: X/Y and colours
    if dry_run and build is None:
        rid, sent = REPORT_IDS[device], []
        for c in commands:
            sent += [packet(rid, c, 64, write=True, device=device)[:len(c) + 1].hex(" ") + " … (Omni, 64 bytes)"]
        for c in commands:
            sent += [packet(None, c, 65, write=True, device=device)[:len(c) + 1].hex(" ") + " … (wired, 65 bytes)"]
        if save:
            sent += [packet(rid, save_command, 64, write=True, device=device)[:3].hex(" ") + " … (Omni, 64 bytes)",
                     packet(None, save_command, 65, write=True, device=device)[:3].hex(" ") + " … (wired, 65 bytes)"]
        return WriteResult("dry-run", sent, message="nothing sent")

    def read_value(dev, target):
        tmp = spec["settings"]("omni" if target.pid == PID_OMNI else "wired")
        for name in read_names:
            read_command = spec["reads"](name)
            ex = exchange(dev, target, read_command, debug, match=3 if read_command[1] == 0x04 else 2)
            if ex.status != "ok":
                return None
            spec["parse"](tmp, name, ex.payload)
        return extract(tmp)

    def sent(target, data):
        return packet(target.report_id, data, target.report_lens[0], write=True, device=device)

    with device_lock():
        hid = hid or load_backend()
        result = WriteResult("not-found")
        for target in (t for t in TARGETS if t.device == device):
            for path in enumerate_paths(hid, target.pid):
                dev = hid.device()
                try:
                    dev.open_path(path)
                except (OSError, IOError) as error:
                    if isinstance(error, PermissionError):
                        result.status = "no-access"
                    continue
                try:
                    awake = exchange(dev, target, target.query, debug)
                    if awake.status != "ok":
                        result = WriteResult(awake.status, message=f"{device} not awake: nothing written")
                        continue
                    result = WriteResult("error")
                    result.before = read_value(dev, target)
                    if build is not None:
                        if result.before is None:
                            result.message = "could not read the current value: nothing written"
                            return result
                        command = build(result.before)       # raises ValueError if not allowed
                        if not is_allowed_write(command, device):
                            raise ValueError(f"not on the {device} write allowlist: {command.hex(' ')}")
                        commands = [command]
                    if dry_run:
                        result.status, result.message = "dry-run", "nothing sent"
                        for c in commands:
                            result.sent.append(sent(target, c)[:len(c) + 1].hex(" ") + f" … ({target.report_lens[0]} bytes)")
                        if save:
                            result.sent.append(sent(target, save_command)[:3].hex(" ") + " …")
                        return result
                    for c in commands:
                        # a chunk is matched on command + effect + chunk index, so a late echo of the previous one
                        # is not taken for it
                        ex = exchange(dev, target, c, debug, match=4 if len(commands) > 1 else 2, write=True)
                        result.sent.append(sent(target, c)[:len(c) + 1].hex(" "))
                        if ex.status == "error" or ((len(commands) > 1 or reply_expected(c)) and ex.status != "ok"):
                            result.message = ex.raw or f"chunk {c[:4].hex(' ')}: {ex.status}; nothing saved"
                            return result
                    result.after = read_value(dev, target)
                    result.status = "ok" if matches(result.after, result.before) else "mismatch"
                    if result.status == "ok" and save:
                        saved = exchange(dev, target, save_command, debug, match=2, write=True)
                        result.sent.append(sent(target, save_command)[:3].hex(" "))
                        # saved only when the device echoes the save; ff aa or silence = not confirmed
                        result.saved = saved.status == "ok"
                        if not result.saved:
                            reason = {"asleep": "it answered ff aa", "no-response": "no reply"}.get(
                                saved.status, saved.raw or saved.status)
                            result.message = f"written, but the save ({save_command.hex(' ')}) was not confirmed: {reason}"
                    return result
                finally:
                    dev.close()
        return result


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

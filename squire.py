#!/usr/bin/env python3
"""Squire: battery monitor and mouse settings for ASUS ROG Falchion RX Low Profile + Keris II Origin (macOS, Linux).

Battery display and --settings only send *get* requests (keyboard 12 01, mouse 12 07 and the settings reads).
Writes (one LED zone, save) only on an explicit --set-led (+ --save) or Apply in the settings window, only from
the allowlist (is_allowed_write), each read back before it is saved. It never touches firmware or switches profiles.

Protocol facts (byte positions relative to the echoed command, i.e. without report ID):
  keyboard  12 01 -> [10] battery %, [5] battery % (copy), [8] charging, [9] low-battery warning %
  mouse     12 07 -> [4]  battery %, [9] charging, [6] low-battery warning %
  error reply: ff aa (device asleep / command not supported)
Source: G-Helper (GPL-3.0) app/Peripherals/{Keyboard,Mouse}; facts only, no code copied.

Backends: hidapi on macOS; on Linux /dev/hidraw* directly (standard library only), because Ubuntu's hidapi
package uses libusb, which reports no usage pages and would have to detach kernel drivers.
"""
__version__ = "0.3.0"

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
    connection: str          # "omni" | "wired"
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
# The transport refuses anything not on this list.
ALLOWED_COMMANDS = frozenset([KEYBOARD_QUERY, MOUSE_QUERY, *MOUSE_SETTINGS_QUERIES.values()])


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
    assert is_allowed_write(command)
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
    assert is_allowed_write(command)
    return command


def angle_snapping_command(on: bool) -> bytes:
    command = SENSOR_WRITE + bytes([ANGLE_SNAPPING_FIELD, 0x00, 0x01 if on else 0x00])
    assert is_allowed_write(command)
    return command


def angle_tuning_command(degrees: int) -> bytes:
    if degrees not in ANGLE_TUNING_DEGREES:
        raise ValueError(f"angle tuning {degrees!r}° is not one of -20..20 in steps of 5")
    command = SENSOR_WRITE + bytes([ANGLE_TUNING_FIELD, 0x00]) + (degrees & 0xFFFF).to_bytes(2, "little")
    assert is_allowed_write(command)
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
    assert is_allowed_write(command)
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
    assert is_allowed_write(command)
    return command


def active_stage_command(stage: int) -> bytes:
    if not 1 <= stage <= DPI_STAGES:
        raise ValueError(f"DPI stage {stage!r} is not 1-{DPI_STAGES}")
    command = SENSOR_WRITE + bytes([ACTIVE_STAGE_FIELD, 0x00, stage])
    assert is_allowed_write(command)
    return command


def stage_count_command(count: int) -> bytes:
    if not 1 <= count <= DPI_STAGES:
        raise ValueError(f"DPI stage count {count!r} is not 1-{DPI_STAGES}")
    command = SENSOR_WRITE + bytes([STAGE_COUNT_FIELD, 0x00, count])
    assert is_allowed_write(command)
    return command


def polling_command(hz: int) -> bytes:
    if hz not in POLLING_WRITE_HZ:
        raise ValueError(f"polling rate {hz!r} Hz is not one of {sorted(POLLING_WRITE_HZ)}")
    command = SENSOR_WRITE + bytes([POLLING_FIELD, 0x00, POLLING_WRITE_HZ[hz]])
    assert is_allowed_write(command)
    return command


def is_allowed_write(command: bytes) -> bool:
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


def packet(report_id: Optional[int], command: bytes, length: int, write: bool = False) -> bytes:
    """The only place packets are built. Reads must be on ALLOWED_COMMANDS; writes must pass
    is_allowed_write() AND be sent with write=True, so a read path can never send a write."""
    if write:
        if not is_allowed_write(command):
            raise ValueError(f"write {command.hex(' ')} is not on the allowlist")
    elif command not in ALLOWED_COMMANDS:
        raise ValueError(f"command {command.hex(' ')} is not on the allowlist")
    rid = 0x00 if report_id is None else report_id
    return (bytes([rid]) + command).ljust(length, b"\0")


def build_packet(target: Target, length: int) -> bytes:
    return packet(target.report_id, target.query, length)


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
    return value if value is not None and 1 <= value <= 100 else None


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
    """Send one allowlisted command and wait for the reply that echoes it (first `match` bytes)."""
    echo = command[:match or len(command)]
    # drain stale input (bounded, short timeout)
    for _ in range(MAX_READS):
        if not dev.read(65, DRAIN_TIMEOUT_MS):
            break
    written, last_error = -1, "write failed"
    for length in target.report_lens:
        data = packet(target.report_id, command, length, write=write)
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


def read_all(debug: bool = False, hid=None) -> list[Battery]:
    with device_lock():
        return _read_all(debug, hid)


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


def write_verified(command, read_name, extract, matches, save: bool = False, dry_run: bool = False,
                   debug: bool = False, hid=None) -> WriteResult:
    """The one write path: check the mouse is awake, read the setting, write, read it back, and save (50 03)
    only when asked and only if the read-back matched. `extract` picks the value from parsed settings;
    `matches(after, before)` judges the read-back. `command` is the bytes, or a function of the value read
    before (lift-off keeps the surface it reads), checked against the allowlist once it is built."""
    build = command if callable(command) else None
    if build is None and not is_allowed_write(command):
        raise ValueError(f"not on the write allowlist: {command.hex(' ')}")
    read_names = (read_name,) if isinstance(read_name, str) else tuple(read_name)   # DPI: X/Y and colours
    if dry_run and build is None:
        shown = len(command) + 1
        sent = [packet(0x03, command, 64, write=True)[:shown].hex(" ") + " … (Omni, 64 bytes)",
                packet(None, command, 65, write=True)[:shown].hex(" ") + " … (wired, 65 bytes)"]
        if save:
            sent += [packet(0x03, SAVE_COMMAND, 64, write=True)[:3].hex(" ") + " … (Omni, 64 bytes)",
                     packet(None, SAVE_COMMAND, 65, write=True)[:3].hex(" ") + " … (wired, 65 bytes)"]
        return WriteResult("dry-run", sent, message="nothing sent")

    def read_value(dev, target):
        tmp = MouseSettings("", "omni" if target.pid == PID_OMNI else "wired")
        for name in read_names:
            read_command = MOUSE_QUERY if name == "battery" else MOUSE_SETTINGS_QUERIES[name]
            ex = exchange(dev, target, read_command, debug, match=3 if read_command[1] == 0x04 else 2)
            if ex.status != "ok":
                return None
            parse_mouse_settings(tmp, name, ex.payload)
        return extract(tmp)

    with device_lock():
        hid = hid or load_backend()
        result = WriteResult("not-found")
        for target in (t for t in TARGETS if t.device == "mouse"):
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
                        result = WriteResult(awake.status, message="mouse not awake: nothing written")
                        continue
                    result = WriteResult("error")
                    result.before = read_value(dev, target)
                    if build is not None:
                        if result.before is None:
                            result.message = "could not read the current value: nothing written"
                            return result
                        command = build(result.before)       # raises ValueError if not allowed
                        if not is_allowed_write(command):
                            raise ValueError(f"not on the write allowlist: {command.hex(' ')}")
                    shown = len(command) + 1
                    if dry_run:
                        result.status, result.message = "dry-run", "nothing sent"
                        result.sent.append(packet(target.report_id, command, target.report_lens[0], write=True)[:shown].hex(" ")
                                           + (f" … ({target.report_lens[0]} bytes)"))
                        if save:
                            result.sent.append(packet(target.report_id, SAVE_COMMAND, target.report_lens[0], write=True)[:3].hex(" ") + " …")
                        return result
                    ex = exchange(dev, target, command, debug, match=2, write=True)
                    result.sent.append(packet(target.report_id, command, target.report_lens[0], write=True)[:shown].hex(" "))
                    if ex.status == "error":
                        result.message = ex.raw
                        return result
                    result.after = read_value(dev, target)
                    result.status = "ok" if matches(result.after, result.before) else "mismatch"
                    if result.status == "ok" and save:
                        saved = exchange(dev, target, SAVE_COMMAND, debug, match=2, write=True)
                        result.sent.append(packet(target.report_id, SAVE_COMMAND, target.report_lens[0], write=True)[:3].hex(" "))
                        # saved only when the mouse echoes 50 03; ff aa or silence = not confirmed
                        result.saved = saved.status == "ok"
                        if not result.saved:
                            reason = {"asleep": "it answered ff aa", "no-response": "no reply"}.get(
                                saved.status, saved.raw or saved.status)
                            result.message = f"written, but the save (50 03) was not confirmed: {reason}"
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
    hid = hid or load_backend()
    for info in hid.enumerate(VID, 0):
        pages = info.get("collections")
        usage = (" collections=" + ",".join(f"{p:04x}:{u:04x}" for p, u in pages) if pages is not None else
                 f" usage_page={info.get('usage_page', 0):04x} usage={info.get('usage', 0):04x}")
        print(f"pid={info['product_id']:04x} iface={info.get('interface_number')}{usage} "
              f"name={info.get('product_string')!r} path={info['path']!r}")


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
        if b.charging or b.percent > level:
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
    ap.add_argument("--mode", choices=list(LED_WRITE_MODES), default="static")
    ap.add_argument("--brightness", type=int, default=100)
    ap.add_argument("--color", help="#rrggbb: LED colour (default #ffffff), or the DPI stage colour (default: keep)")
    ap.add_argument("--dry-run", action="store_true", help="print the bytes a write would send, send nothing")
    ap.add_argument("--save", action="store_true", help="after a write, store it in the mouse (50 03)")
    ap.add_argument("--settings", action="store_true",
                    help="Read the mouse settings (DPI, LEDs, sensor, power); read-only, add --json for JSON")
    ap.add_argument("--debug", action="store_true", help="Print raw replies to stderr")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()
    try:
        if args.list:
            list_devices()
            return 0
        if args.list_surfaces:
            for sid in sorted(SURFACE_PARAMS, key=lambda i: (i == NO_SURFACE, surface_name(i))):
                print(f"  {'none' if sid == NO_SURFACE else f'{sid:02x}'}  {surface_name(sid)}")
            return 0
        writes = [w for w in (args.set_led, args.set_power, args.set_angle_snapping,
                              args.set_angle_tuning is not None or None, args.set_liftoff, args.set_polling,
                              args.set_surface, args.set_dpi, args.set_dpi_stage, args.set_dpi_count) if w]
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
                    print("  saved in the mouse (50 03)")
            if args.save and result.status == "ok" and not result.saved:
                return 1
            return 0 if result.status in ("ok", "dry-run") else 1
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

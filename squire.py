#!/usr/bin/env python3
"""Squire: read-only battery monitor for ASUS ROG Falchion RX Low Profile + Keris II Origin on macOS.

Only sends the two documented *query* commands (keyboard 12 01, mouse 12 07).
It never changes lighting, settings or firmware.

Protocol facts (byte positions relative to the echoed command, i.e. without report ID):
  keyboard  12 01 -> [10] battery %, [5] battery % (copy), [8] charging
  mouse     12 07 -> [4]  battery %, [9] charging
  error reply: ff aa (device asleep / command not supported)
Source: G-Helper (GPL-3.0) app/Peripherals/{Keyboard,Mouse}; facts only, no code copied.
"""
__version__ = "0.1.0"

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional

VID = 0x0B05
PID_OMNI = 0x1ACE           # ROG Omni receiver (keyboard + mouse share it)
PID_FALCHION_RX_LP = 0x1B04  # keyboard, wired
PID_KERIS_II_ORIGIN = 0x1C0C  # mouse, wired

KEYBOARD_QUERY = bytes([0x12, 0x01])
MOUSE_QUERY = bytes([0x12, 0x07])
READ_TIMEOUT_MS = 400
DRAIN_TIMEOUT_MS = 5   # hidapi: timeout 0 means "block forever", so never pass 0
MAX_READS = 8


@dataclass
class Battery:
    device: str              # "keyboard" | "mouse"
    name: str
    connection: str          # "omni" | "wired"
    percent: Optional[int]   # None = not reachable
    charging: bool = False
    status: str = "ok"       # ok | asleep | no-response | error
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


def build_packet(target: Target, length: int) -> bytes:
    rid = 0x00 if target.report_id is None else target.report_id
    return (bytes([rid]) + target.query).ljust(length, b"\0")


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


def query(dev, target: Target, debug: bool = False) -> Battery:
    result = Battery(target.device, target.name, "omni" if target.pid == PID_OMNI else "wired", None)
    # drain stale input (bounded, short timeout)
    for _ in range(MAX_READS):
        if not dev.read(65, DRAIN_TIMEOUT_MS):
            break
    written, last_error = -1, "write failed"
    for length in target.report_lens:
        try:
            written = dev.write(build_packet(target, length))
        except (OSError, ValueError) as error:
            written, last_error = -1, f"write failed: {error}"
        if debug:
            print(f"[{target.device}] > {build_packet(target, length)[:4].hex(' ')} … ({length} bytes) -> {written}",
                  file=sys.stderr)
        if written is not None and written >= 0:
            break
    if written is None or written < 0:
        result.status, result.raw = "error", last_error
        return result
    for _ in range(MAX_READS):
        reply = bytes(dev.read(65, READ_TIMEOUT_MS))
        if not reply:
            if debug:
                print(f"[{target.device}] < (timeout)", file=sys.stderr)
            break
        if debug:
            print(f"[{target.device}] < {reply.hex(' ')}", file=sys.stderr)
        if is_error(reply, target.report_id):
            result.status, result.raw = "asleep", reply.hex(" ")
            return result
        base = find_echo(reply, target.query)
        if base is None:
            continue  # other traffic (e.g. button/profile events); keep reading
        result.raw = reply.hex(" ")
        pct, charging = parse(target.device, reply, base)
        if pct is None or (pct == 0 and not charging):
            result.status = "asleep"   # G-Helper treats 0 % + not charging as disconnected
            return result
        result.percent, result.charging = pct, charging
        return result
    result.status = "no-response"
    return result


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


def read_all(debug: bool = False) -> list[Battery]:
    import hid  # pip package "hidapi"
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


def list_devices() -> None:
    import hid
    for info in hid.enumerate(VID, 0):
        print(f"pid={info['product_id']:04x} iface={info.get('interface_number')} "
              f"usage_page={info.get('usage_page', 0):04x} usage={info.get('usage', 0):04x} "
              f"name={info.get('product_string')!r} path={info['path']!r}")


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
        return ":keyboard: – :computermouse: – | color=gray\n---\nKeine ASUS-Geräte gefunden\nAktualisieren | refresh=true"
    title = "  ".join(f"{SF_ICONS[b.device]} {label(b)}" for b in results)
    lowest = min((b for b in results if b.percent is not None), key=lambda b: b.percent, default=None)
    title_color = color_for(lowest) if lowest and color_for(lowest) in ("red", "orange") else ""
    lines = [title + (f" | color={title_color}" if title_color else ""), "---"]
    status_text = {"asleep": "schläft / nicht verbunden", "no-response": "keine Antwort", "error": "Fehler"}
    for b in results:
        detail = label(b, bolt=False) if b.percent is not None else status_text.get(b.status, b.status)
        extra = " (lädt)" if b.charging else ""
        c = color_for(b)
        lines.append(f"{b.name}: {detail}{extra} | sfimage={'keyboard' if b.device == 'keyboard' else 'computermouse'}"
                     + (f" color={c}" if c else ""))
        lines.append(f"--Verbindung: {b.connection}")
    lines += ["---", "Aktualisieren | refresh=true"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="JSON output")
    mode.add_argument("--swiftbar", action="store_true", help="SwiftBar/xbar menu bar output")
    mode.add_argument("--list", action="store_true", help="List ASUS HID collections; sends nothing")
    ap.add_argument("--debug", action="store_true", help="Print raw replies to stderr")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()
    try:
        if args.list:
            list_devices()
            return 0
        results = read_all(args.debug)
    except ImportError:
        msg = "Python-Modul 'hid' fehlt: pip install hidapi"
        print(f":keyboard: ! | color=red\n---\n{msg}" if args.swiftbar else msg, file=sys.stdout if args.swiftbar else sys.stderr)
        return 0 if args.swiftbar else 1
    if args.json:
        print(json.dumps([asdict(b) for b in results], indent=2, ensure_ascii=False))
    elif args.swiftbar:
        print(swiftbar(results))
    else:
        if not results:
            print("Keine ASUS-Geräte gefunden.")
        for b in results:
            print(f"{b.name:<22} {label(b, bolt=False):>5}{' lädt' if b.charging else ''}  ({b.connection}, {b.status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

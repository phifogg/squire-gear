"""Linux Bluetooth transport: what BlueZ already holds, via busctl (systemd). Never talks to the device itself.

BlueZ reads the standard GATT Battery Level (180F / 2A19) and exposes it as org.bluez.Battery1; Squire only asks
bluetoothd for its managed objects. Mapping the objects to Squire's devices happens in squire.parse_bluez_objects."""
import json
import subprocess
import sys
from typing import Optional


def modalias_pid(modalias: str, vendor_id: int) -> Optional[int]:
    """'usb:v0B05p1B08d0001' -> 0x1B08 for a device of vendor_id, else None. BlueZ writes 'bluetooth:v...' for
    Bluetooth SIG vendor ids; ASUS devices report the USB vendor id here."""
    try:
        kind, rest = modalias.split(":", 1)
        if kind != "usb" or not rest.startswith("v") or "p" not in rest:
            return None
        vendor, product = rest[1:].split("p", 1)
        if int(vendor[:4], 16) != vendor_id:
            return None
        return int(product[:4], 16)
    except ValueError:
        return None


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

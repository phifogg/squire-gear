"""Route layer: how one device is reached (connection, PID, interface, report ID, packet lengths), from devices.json.

The Omni receiver is a route several devices share (report IDs 02 / 03 on one interface). A device has several routes
with a fixed priority: Omni, then cable (the order Squire has always tried; a device on the cable answers ff aa on the
receiver), and Bluetooth last, which only fills in a device without a HID value (best_reading)."""
from dataclasses import dataclass
from typing import Optional

from . import registry
from .transport import enumerate_paths

HID_CONNECTIONS = ("omni", "wired")   # in the order they are tried; Bluetooth has no vendor collection
CONNECTION_LABELS = {"omni": "Omni receiver", "wired": "Wired", "bluetooth": "Bluetooth"}


@dataclass(frozen=True)
class Target:
    device: str                # kind: "keyboard" | "mouse"
    name: str
    pid: int
    report_id: Optional[int]   # None = unnumbered report (leading 0x00, 64-byte payload)
    query: bytes               # the battery read, also the "is it awake" check before every session
    report_lens: tuple         # candidate total write sizes (incl. report-ID byte), tried in order
    connection: str = "omni"   # "omni" | "wired"
    interface: Optional[int] = None   # USB interface of the vendor collection (Omni 2, keyboard 1, mouse 0)


def build_targets(devices) -> list:
    """One Target per HID route: every device via the Omni first, then every device on the cable."""
    targets = []
    for connection in HID_CONNECTIONS:
        for device in devices.devices:
            route = next((r for r in device.routes() if r["connection"] == connection), None)   # Omni: pid resolved
            if route is None:
                continue
            report_id = registry.byte(route["report_id"]) if "report_id" in route else None
            query = device.read(device.capability("battery")["read"])
            targets.append(Target(device.kind, device.name, int(route["pid"], 16), report_id, query,
                                  tuple(route["report_lengths"]), connection, route.get("interface")))
    return targets


def target_paths(hid, vendor_id: int, target: Target) -> list:
    """Paths of the target's vendor collection (only the route's interface)."""
    return enumerate_paths(hid, vendor_id, target.pid, target.interface)


def connection_label(connection: str) -> str:
    return CONNECTION_LABELS.get(connection, connection)


def best_reading(hid: list, bluetooth: list, kinds=("keyboard", "mouse")) -> list:
    """Best available route per device: HID wins when it has a value; otherwise the Bluetooth reading, if any;
    otherwise HID's status (same rule as Bluetooth.merge in SquireCore). Readings need .device and .percent."""
    merged = []
    for device in kinds:
        wired = next((b for b in hid if b.device == device), None)
        ble = next((b for b in bluetooth if b.device == device and b.percent is not None), None)
        choice = wired if wired is not None and wired.percent is not None else \
            ble or wired or next((b for b in bluetooth if b.device == device), None)
        if choice is not None:
            merged.append(choice)
    return merged

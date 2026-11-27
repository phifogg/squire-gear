"""Transport layer: moves bytes and lists devices, knows nothing about ASUS commands.

hidapi on macOS, /dev/hidraw on Linux (hidraw.py), BlueZ for Bluetooth levels (bluez.py), and device_lock (lock.py).
The rules every transport keeps live here: only vendor-defined collections are opened, and reads always pass a
timeout > 0 (hidapi treats 0 as "block forever")."""
from ..state import IS_LINUX
from .hidraw import HidrawBackend, HidrawDevice, descriptor_collections  # noqa: F401
from .lock import device_lock  # noqa: F401

READ_TIMEOUT_MS = 400
DRAIN_TIMEOUT_MS = 5   # hidapi: timeout 0 means "block forever", so never pass 0
VENDOR_USAGE_PAGE = 0xFF00


def load_backend():
    if IS_LINUX:
        return HidrawBackend()
    import hid  # pip package "hidapi"
    return hid


def enumerate_paths(hid, vendor_id: int, pid: int, interface=None) -> list:
    """Unique HID paths for vendor-defined collections of a product (macOS lists one entry per collection).
    interface: the route's interface number (devices.json), None = any."""
    paths = []
    for info in hid.enumerate(vendor_id, pid):
        if info.get("usage_page", 0) < VENDOR_USAGE_PAGE:
            continue  # skip standard keyboard/mouse/consumer collections: never touch typing input
        if interface is not None and info.get("interface_number", interface) not in (interface, -1):
            continue
        if info["path"] not in paths:
            paths.append(info["path"])
    return paths

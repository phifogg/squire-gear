"""Linux transport: /dev/hidraw directly (standard library only).

Ubuntu's hidapi package uses libusb, which reports no usage pages and would have to detach kernel drivers, so Squire
reads the report descriptor itself and opens a node only through enumerate_paths() (vendor collections only)."""
import os
import select
from pathlib import Path
from typing import Optional


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

"""Capability layer: what a device can do, one module per capability (battery, led_zones, dpi, ...).

Each capability takes its parameters from the device's entry in devices.json and holds its write builders, the byte
check for its writes (its part of the allowlist), the parser for its part of the read replies and the read-back check
of a write (a Change). DeviceCapabilities puts a device's capabilities together: its write allowlist is exactly the
commands its capabilities accept, and only with a prefix devices.json marks "confirmed".

Sending is not done here: squire.write_verified() runs a Change in one device session (awake check, read, write,
read back, save only on a match), and squire.packet() checks every packet against DeviceCapabilities once more."""
from dataclasses import dataclass
from typing import Callable, Optional, Union

from .. import registry

byte = registry.byte


def rgb(color: Optional[str], what: str = "colour") -> str:
    """#rrggbb, lower case; raises ValueError otherwise."""
    try:
        raw = bytes.fromhex((color or "").lstrip("#"))
    except ValueError:
        raw = b""
    if len(raw) != 3:
        raise ValueError(f"{what} {color!r} is not #rrggbb")
    return "#" + raw.hex()


@dataclass(frozen=True)
class Change:
    """One write as its capability describes it. `command`: the bytes, a list of chunks sent in order (each echoed
    before the next), or a function of the value read just before (lift-off keeps the surface it reads). `reads`:
    the read names whose replies show the setting; `extract(settings)` picks the value from them, `matches(after,
    before)` judges the read-back."""
    command: Union[bytes, list, Callable]
    reads: tuple
    extract: Callable
    matches: Callable


class Capability:
    """Base class. `name` is the key in devices.json's capabilities; `write_commands()` names the write commands
    (devices.json commands) this capability sends, and their prefixes are the commands it answers for."""
    name = ""

    def __init__(self, device: registry.Device, owner: "DeviceCapabilities"):
        self.device = device
        self.owner = owner
        self.params = device.capability(self.name)
        self.prefixes = tuple(p for c in self.write_commands() for p in device.write_prefixes(c))

    def write_commands(self) -> tuple:
        return tuple(self.params[k] for k in ("write", "save") if k in self.params)

    def allows(self, command: bytes) -> bool:
        """Byte check of a write that starts with one of self.prefixes."""
        return False

    def parse(self, settings, read: str, payload: bytes) -> None:
        """Fill this capability's fields of a settings object from one read reply (payload from the echo on)."""

    def reply_expected(self, command: bytes) -> Optional[bytes]:
        """The reply that acknowledges a write the device does not echo (None: it is echoed)."""
        return None

    def checked(self, command: bytes) -> bytes:
        """A command this capability built: it must pass the device's whole allowlist (confirmed prefix included)."""
        assert self.owner.is_allowed_write(command), command.hex(" ")
        return command


class DeviceCapabilities:
    """The capabilities of one device model, in devices.json's order; `caps["dpi"]` is one of them."""

    def __init__(self, device: registry.Device):
        self.device = device
        self.kind = device.kind
        self.confirmed_prefixes = device.confirmed_write_prefixes()
        self.items = {name: CLASSES_BY_NAME[name](device, self) for name in device.data["capabilities"]}
        saving = [c for c in self.items.values() if "save" in c.params]
        self.save_command = device.write_prefixes(saving[0].params["save"])[0] if saving else None

    def __getitem__(self, name: str) -> Capability:
        return self.items[name]

    def __contains__(self, name: str) -> bool:
        return name in self.items

    def __iter__(self):
        return iter(self.items.values())

    def owner_of(self, command: bytes) -> Optional[Capability]:
        """The capability whose write prefix the command starts with (the longest one: 51 31 04 before 51 31)."""
        best, length = None, 0
        for cap in self.items.values():
            for prefix in cap.prefixes:
                if command.startswith(prefix) and len(prefix) > length:
                    best, length = cap, len(prefix)
        return best

    def is_allowed_write(self, command: bytes) -> bool:
        """A write is allowed only with a prefix devices.json marks confirmed for this device and when the
        capability that sends it accepts the bytes."""
        if not any(command.startswith(prefix) for prefix in self.confirmed_prefixes):
            return False
        cap = self.owner_of(command)
        return cap is not None and cap.allows(command)

    def reply_expected(self, command: bytes) -> Optional[bytes]:
        for cap in self.items.values():
            if cap.prefixes and command.startswith(cap.prefixes):
                expected = cap.reply_expected(command)
                if expected is not None:
                    return expected
        return None

    def parse(self, settings, read: str, payload: bytes) -> None:
        for cap in self.items.values():
            cap.parse(settings, read, payload)


from . import (battery, dpi, kb_layout, kb_lighting, kb_profile, kb_sleep, kb_warning, led_zones,  # noqa: E402
               per_key, polling, power, profile, sensor, surface)

CLASSES = (battery.Battery, profile.Profile, led_zones.LedZones, dpi.Dpi, polling.Polling, sensor.Sensor, surface.Surface,
           power.Power, kb_profile.KbProfile, kb_layout.KbLayout, kb_lighting.KbLighting, kb_sleep.KbSleep,
           kb_warning.KbWarning, per_key.PerKey)
CLASSES_BY_NAME = {c.name: c for c in CLASSES}


def for_device(device: registry.Device) -> DeviceCapabilities:
    return DeviceCapabilities(device)

"""Session layer: talking to one device at a time, under device_lock().

A session opens the device's vendor collection on one route after the other (Omni first, then the cable), checks the
device is awake with its battery read, then sends only what packet() lets through: reads from the device's confirmed
read list, writes that its capabilities accept (DeviceCapabilities.is_allowed_write) and only with write=True, and the
report ID of that device. Every packet of every session is built there.

- read_batteries(): battery level + charging of every device (the menu / top bar), stops at the first route with a value.
- read_settings(kind): the device's whole confirmed read list, each reply parsed by its capabilities.
- write_verified(): the one write path: awake check, read the value, write (chunks each echoed or acknowledged before
  the next), read back, and save only when asked and only if the read-back matched and the save was echoed.

squire.py builds one Sessions object from devices.json and re-exports its methods under the old names."""
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import protocol, registry, transport
from .routes import Target
from .transport import device_lock, load_backend


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


@dataclass
class WriteResult:
    status: str                    # ok | mismatch | asleep | no-response | no-access | not-found | error | dry-run
    sent: list = field(default_factory=list)       # hex of every packet sent (or that would be sent)
    before: Optional[dict] = None  # value read before the write
    after: Optional[dict] = None   # value read back after the write
    saved: bool = False
    message: str = ""


@dataclass(frozen=True)
class SettingsReader:
    """How a device's settings are collected: new(name, connection) makes an empty settings object (with .raw,
    .status), parse(settings, read name, payload) fills it from one reply."""
    new: Callable
    parse: Callable


def read_match(command: bytes) -> int:
    """How many bytes of a read's echo must match: 12 04 xx carries the sub-command in the echo, the others are
    matched on 2 bytes (12 03 xx LED zones echo only 12 03)."""
    return 3 if command[1] == 0x04 else 2


class Sessions:
    def __init__(self, devices: registry.Registry, caps: dict, targets: list, readers: dict):
        self.vendor_id = devices.vendor_id
        self.caps = caps                    # kind -> DeviceCapabilities
        self.targets = targets              # HID routes in the order they are tried
        self.readers = readers              # kind -> SettingsReader
        self.kinds = tuple(d.kind for d in devices.devices)
        # Confirmed reads per device: the transport refuses anything not on the list of the device it talks to.
        self.allowed_reads = {d.kind: frozenset(d.reads().values()) for d in devices.devices}
        # Read name -> bytes per device, the battery query included ("battery").
        self.read_commands = {d.kind: d.reads() for d in devices.devices}
        self.battery_reads = {d.kind: d.capability("battery")["read"] for d in devices.devices}
        # Report ID of each device on the Omni receiver; wired, both use unnumbered reports (None).
        self.report_ids = {d.kind: registry.byte(d.route("omni")["report_id"]) for d in devices.devices}
        # Interface of the vendor collection per HID pid (Omni 2, wired keyboard 1, wired mouse 0).
        self.interfaces = {int(r["pid"], 16): r["interface"] for d in devices.devices for r in d.routes()
                           if r["connection"] != "bluetooth"}

    # --- packets ----------------------------------------------------------------------------------------------

    def packet(self, report_id: Optional[int], command: bytes, length: int, write: bool = False, *,
               device: str) -> bytes:
        """The only place packets are built. Reads must be on the device's confirmed read list; writes must pass
        its capabilities (is_allowed_write) AND be sent with write=True, so a read path can never send a write.
        The report ID must be the device's own on the Omni (or None, wired)."""
        if device not in self.report_ids:
            raise ValueError(f"unknown device {device!r}")
        if report_id is not None and report_id != self.report_ids[device]:
            raise ValueError(f"report ID {report_id:02x} is not the {device}'s")
        if write:
            if not self.caps[device].is_allowed_write(command):
                raise ValueError(f"write {command.hex(' ')} is not on the {device} allowlist")
        elif command not in self.allowed_reads[device]:
            raise ValueError(f"command {command.hex(' ')} is not on the {device} allowlist")
        return protocol.frame(report_id, command, length)

    def build_packet(self, target: Target, length: int) -> bytes:
        return self.packet(target.report_id, target.query, length, device=target.device)

    def exchange(self, dev, target: Target, command: bytes, debug: bool = False, match: Optional[int] = None,
                 write: bool = False) -> protocol.Exchange:
        """Send one allowlisted command and wait for the reply that echoes it (first `match` bytes), or for its
        acknowledgement where the device answers with other bytes (reply_expected: per-key colours). Every packet is
        built by packet(), so the allowlist of the target's device applies to each candidate length."""
        return protocol.exchange(
            dev, target, command,
            lambda length: self.packet(target.report_id, command, length, write=write, device=target.device),
            expect=self.caps[target.device].reply_expected(command), debug=debug, match=match)

    def enumerate_paths(self, hid, pid: int) -> list:
        """Unique HID paths for vendor-defined collections of a product, on the vendor interface of its route."""
        return transport.enumerate_paths(hid, self.vendor_id, pid, self.interfaces.get(pid))

    def _open(self, hid, path, target: Target, debug: bool):
        """An opened device, or the error that kept it closed."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as error:
            if debug:
                print(f"[{target.device}] open {path!r} failed: {error}", file=sys.stderr)
            return None, error
        return dev, None

    # --- battery ----------------------------------------------------------------------------------------------

    def query(self, dev, target: Target, debug: bool = False) -> Battery:
        result = Battery(target.device, target.name, target.connection, None)
        ex = self.exchange(dev, target, target.query, debug)
        result.raw = ex.raw
        if ex.status != "ok":
            result.status = ex.status
            return result
        battery = self.caps[target.device]["battery"]
        pct, charging = battery.reading(ex.reply, ex.base)
        if pct is None or (pct == 0 and not charging):
            result.status = "asleep"   # G-Helper treats 0 % + not charging as disconnected
            return result
        result.percent, result.charging = pct, charging
        result.low_warning = battery.warning(ex.reply, ex.base)
        return result

    def read_batteries(self, debug: bool = False, hid=None) -> list:
        """Battery of every device over HID, first route with a value wins. The caller holds device_lock()."""
        hid = hid or load_backend()
        found: dict = {}
        for target in self.targets:
            if target.device in found and found[target.device].percent is not None:
                continue  # already have a value (e.g. via Omni); skip other connection
            for path in self.enumerate_paths(hid, target.pid):
                if debug:
                    print(f"[{target.device}] open {path!r} (report id {target.report_id})", file=sys.stderr)
                dev, error = self._open(hid, path, target, debug)
                if dev is None:
                    if isinstance(error, PermissionError) and target.device not in found:
                        # Linux without the udev rule: say so instead of "not found"
                        found[target.device] = Battery(target.device, target.name, target.connection, None,
                                                       status="no-access", raw=str(error))
                    continue
                try:
                    result = self.query(dev, target, debug)
                finally:
                    dev.close()
                previous = found.get(target.device)
                if previous is None or (previous.percent is None and result.percent is not None):
                    found[target.device] = result
                if result.percent is not None:
                    break
                time.sleep(0.05)
        return [found[k] for k in self.kinds if k in found]

    # --- settings ---------------------------------------------------------------------------------------------

    def read_command(self, kind: str, name: str) -> bytes:
        return self.read_commands[kind][name]

    def read_settings(self, kind: str, debug: bool = False, hid=None):
        """Current settings of one device (active profile only; never switches profiles). Sends the battery query
        (awake check) and then every other confirmed read of the device, each parsed by its capabilities."""
        reader = self.readers[kind]
        name = next(t.name for t in self.targets if t.device == kind)
        with device_lock():
            hid = hid or load_backend()
            result = reader.new(name, "")
            result.status = "not-found"
            for target in (t for t in self.targets if t.device == kind):
                for path in self.enumerate_paths(hid, target.pid):
                    dev, error = self._open(hid, path, target, debug)
                    if dev is None:
                        if isinstance(error, PermissionError):
                            result.status = "no-access"
                        continue
                    try:
                        settings = reader.new(target.name, target.connection)
                        battery = self.exchange(dev, target, target.query, debug)
                        if battery.status != "ok":
                            result = settings
                            result.status = battery.status
                            continue   # asleep here; maybe it is on the other connection
                        settings.raw["battery"] = battery.payload.hex(" ")
                        reader.parse(settings, "battery", battery.payload)
                        for read, command in self.read_commands[kind].items():
                            if read == self.battery_reads[kind]:
                                continue
                            ex = self.exchange(dev, target, command, debug, match=read_match(command))
                            if ex.status != "ok":
                                settings.raw[read] = ex.status
                                continue
                            settings.raw[read] = ex.payload.hex(" ")
                            reader.parse(settings, read, ex.payload)
                        return settings
                    finally:
                        dev.close()
            return result

    # --- writes -----------------------------------------------------------------------------------------------

    def write_verified(self, command, read_name, extract, matches, save: bool = False, dry_run: bool = False,
                       debug: bool = False, hid=None, device: str = "mouse") -> WriteResult:
        """The one write path: check the device is awake, read the setting, write, read it back, and save (mouse
        50 03, keyboard 50 55) only when asked and only if the read-back matched. `extract` picks the value from
        parsed settings; `matches(after, before)` judges the read-back. `command` is the bytes, or a function of the
        value read before (lift-off keeps the surface it reads), checked against the device's allowlist once it is
        built. A list of commands (the keyboard's gradient chunks) is sent in order, each must be echoed, then read
        back once."""
        caps, reader = self.caps[device], self.readers[device]
        save_command = caps.save_command
        build = command if callable(command) else None
        commands = None if build else ([command] if isinstance(command, (bytes, bytearray)) else list(command))
        for c in commands or ():
            if not caps.is_allowed_write(c):
                raise ValueError(f"not on the {device} write allowlist: {c.hex(' ')}")
        read_names = (read_name,) if isinstance(read_name, str) else tuple(read_name)   # DPI: X/Y and colours
        if dry_run and build is None:
            rid, sent = self.report_ids[device], []
            for c in commands:
                sent += [self.packet(rid, c, 64, write=True, device=device)[:len(c) + 1].hex(" ") + " … (Omni, 64 bytes)"]
            for c in commands:
                sent += [self.packet(None, c, 65, write=True, device=device)[:len(c) + 1].hex(" ") + " … (wired, 65 bytes)"]
            if save:
                sent += [self.packet(rid, save_command, 64, write=True, device=device)[:3].hex(" ") + " … (Omni, 64 bytes)",
                         self.packet(None, save_command, 65, write=True, device=device)[:3].hex(" ") + " … (wired, 65 bytes)"]
            return WriteResult("dry-run", sent, message="nothing sent")

        def read_value(dev, target):
            tmp = reader.new("", target.connection)
            for name in read_names:
                read_command = self.read_command(device, name)
                ex = self.exchange(dev, target, read_command, debug, match=read_match(read_command))
                if ex.status != "ok":
                    return None
                reader.parse(tmp, name, ex.payload)
            return extract(tmp)

        def sent(target, data):
            return self.packet(target.report_id, data, target.report_lens[0], write=True, device=device)

        with device_lock():
            hid = hid or load_backend()
            result = WriteResult("not-found")
            for target in (t for t in self.targets if t.device == device):
                for path in self.enumerate_paths(hid, target.pid):
                    dev, error = self._open(hid, path, target, False)
                    if dev is None:
                        if isinstance(error, PermissionError):
                            result.status = "no-access"
                        continue
                    try:
                        awake = self.exchange(dev, target, target.query, debug)
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
                            if not caps.is_allowed_write(command):
                                raise ValueError(f"not on the {device} write allowlist: {command.hex(' ')}")
                            commands = [command]
                        if dry_run:
                            result.status, result.message = "dry-run", "nothing sent"
                            for c in commands:
                                result.sent.append(sent(target, c)[:len(c) + 1].hex(" ")
                                                   + f" … ({target.report_lens[0]} bytes)")
                            if save:
                                result.sent.append(sent(target, save_command)[:3].hex(" ") + " …")
                            return result
                        for c in commands:
                            # a chunk is matched on command + effect + chunk index, so a late echo of the previous one
                            # is not taken for it
                            ex = self.exchange(dev, target, c, debug, match=4 if len(commands) > 1 else 2, write=True)
                            result.sent.append(sent(target, c)[:len(c) + 1].hex(" "))
                            if ex.status == "error" or ((len(commands) > 1 or caps.reply_expected(c))
                                                        and ex.status != "ok"):
                                result.message = ex.raw or f"chunk {c[:4].hex(' ')}: {ex.status}; nothing saved"
                                return result
                        result.after = read_value(dev, target)
                        result.status = "ok" if matches(result.after, result.before) else "mismatch"
                        if result.status == "ok" and save:
                            saved = self.exchange(dev, target, save_command, debug, match=2, write=True)
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

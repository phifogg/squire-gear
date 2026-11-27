"""Device models from devices.json: loading and checking (standard library only).

devices.json says which devices Squire knows, how each is reached (routes), which commands may be sent to it (with a
confirmation status) and the parameters of its capabilities. check() finds every problem at once; load() refuses a
file with any problem, so squire.py never runs on half-valid data. The tests and CI run the same check.
"""
import json
import re
from pathlib import Path
from typing import Optional

DEVICES_FILE = Path(__file__).resolve().parent.parent / "devices.json"   # next to squire.py, in the repo and installed
SCHEMA = 1
KINDS = ("keyboard", "mouse")
CONNECTIONS = ("omni", "wired", "bluetooth")      # also the route priority: cable / receiver first, Bluetooth last
STATUSES = ("confirmed", "unconfirmed")           # only "confirmed" is ever sent; the rest is --dry-run only

_HEX2 = re.compile(r"^[0-9a-f]{2}$")
_HEX4 = re.compile(r"^[0-9a-f]{4}$")
_BYTES = re.compile(r"^[0-9a-f]{2}( [0-9a-f]{2})*$")
_RGB = re.compile(r"^[0-9a-f]{6}$")
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DevicesError(ValueError):
    def __init__(self, problems: list):
        super().__init__("devices.json: " + "; ".join(problems))
        self.problems = problems


def byte(value: str) -> int:
    return int(value, 16)


def hex_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


# --- checker ----------------------------------------------------------------------------------------------------

class _Checker:
    def __init__(self):
        self.problems: list = []

    def fail(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def field(self, obj, key: str, where: str, kind, required: bool = True, pattern=None, nullable: bool = False,
              low=None, high=None):
        """obj[key] checked for type (bool is never an int here), pattern and range. Returns the value or None."""
        if not isinstance(obj, dict):
            self.fail(where, "is not an object")
            return None
        if key not in obj:
            if required:
                self.fail(where, f"missing {key!r}")
            return None
        value = obj[key]
        if value is None and nullable:
            return None
        kinds = kind if isinstance(kind, tuple) else (kind,)
        if isinstance(value, bool) and bool not in kinds or not isinstance(value, kinds):
            self.fail(f"{where}.{key}", f"{value!r} is not {'/'.join(k.__name__ for k in kinds)}")
            return None
        if pattern is not None and isinstance(value, str) and not pattern.match(value):
            self.fail(f"{where}.{key}", f"{value!r} has the wrong format")
            return None
        if isinstance(value, int) and ((low is not None and value < low) or (high is not None and value > high)):
            self.fail(f"{where}.{key}", f"{value} is outside {low}..{high}")
            return None
        return value

    def items(self, obj, key: str, where: str, required: bool = True, min_len: int = 1) -> list:
        value = self.field(obj, key, where, list, required)
        if value is not None and len(value) < min_len:
            self.fail(f"{where}.{key}", f"needs at least {min_len} entries")
        return value or []

    def unique(self, values: list, where: str, what: str) -> None:
        seen = set()
        for v in values:
            if v in seen:
                self.fail(where, f"duplicate {what} {v!r}")
            seen.add(v)

    def named_values(self, obj, key: str, where: str, name_key: str = "name", value_key: str = "value",
                     name_kind=str, nullable_name: bool = False) -> list:
        """A list of {name, value: "hh"} pairs with unique names and unique values."""
        entries = self.items(obj, key, where)
        names, values = [], []
        for i, e in enumerate(entries):
            w = f"{where}.{key}[{i}]"
            names.append(self.field(e, name_key, w, name_kind, nullable=nullable_name))
            values.append(self.field(e, value_key, w, str, pattern=_HEX2))
        self.unique(names, f"{where}.{key}", name_key)
        self.unique([v for v in values if v is not None], f"{where}.{key}", value_key)
        return entries

    def command_ref(self, cap: dict, key: str, where: str, commands: dict, write: bool, required: bool = True) -> None:
        name = self.field(cap, key, where, str, required)
        if name is None:
            return
        if name not in commands:
            self.fail(f"{where}.{key}", f"no command {name!r}")
        elif bool(commands[name].get("write")) != write:
            self.fail(f"{where}.{key}", f"command {name!r} is not a {'write' if write else 'read'}")

    def offset(self, cap: dict, key: str, where: str, required: bool = True) -> None:
        self.field(cap, key, where, int, required, low=0, high=63)


def _check_battery(c: _Checker, cap: dict, w: str, commands: dict) -> None:
    c.command_ref(cap, "read", w, commands, write=False)
    for i, offset in enumerate(c.items(cap, "percent", w)):
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 63:
            c.fail(f"{w}.percent[{i}]", f"{offset!r} is not an offset 0..63")
    c.offset(cap, "charging", w)
    c.offset(cap, "warning", w)
    c.field(cap, "warning_min", w, int, low=0, high=100)


def _check_power_off(c: _Checker, cap: dict, w: str) -> None:
    c.named_values(cap, "power_off", w, name_key="minutes", name_kind=int, nullable_name=True)


def _check_levels(c: _Checker, cap: dict, key: str, w: str) -> None:
    levels = c.items(cap, key, w)
    for i, level in enumerate(levels):
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            c.fail(f"{w}.{key}[{i}]", f"{level!r} is not a percentage")
    c.unique(levels, f"{w}.{key}", "level")


def _check_range(c: _Checker, obj, key: str, w: str, low: int, high: int) -> Optional[dict]:
    r = c.field(obj, key, w, dict)
    if r is None:
        return None
    lo = c.field(r, "min", f"{w}.{key}", int, low=low, high=high)
    hi = c.field(r, "max", f"{w}.{key}", int, low=low, high=high)
    if lo is not None and hi is not None and lo > hi:
        c.fail(f"{w}.{key}", "min is above max")
    return r


def _check_led_zones(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "save", w, commands, write=True)
    zones = c.items(cap, "zones", w)
    for i, z in enumerate(zones):
        c.field(z, "name", f"{w}.zones[{i}]", str)
        c.field(z, "index", f"{w}.zones[{i}]", str, pattern=_HEX2)
        c.command_ref(z, "read", f"{w}.zones[{i}]", commands, write=False)
    c.unique([z.get("name") for z in zones if isinstance(z, dict)], f"{w}.zones", "name")
    for i, m in enumerate(c.named_values(cap, "modes", w)):
        c.field(m, "writable", f"{w}.modes[{i}]", bool)


def _check_dpi(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.field(cap, "stages", w, int, low=1, high=8)
    lo = c.field(cap, "min", w, int, low=1, high=100000)
    hi = c.field(cap, "max", w, int, low=1, high=100000)
    step = c.field(cap, "step", w, int, low=1, high=1000)
    if None not in (lo, hi, step) and (lo > hi or lo % step or hi % step):
        c.fail(w, "min / max are not in order or not multiples of step")


def _check_polling(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    rates = c.items(cap, "read_rates_hz", w)
    if len(rates) != 8:
        c.fail(f"{w}.read_rates_hz", "needs 8 entries (low 3 bits of 12 04 00 [12])")
    for i, r in enumerate(c.named_values(cap, "rates", w, name_key="hz", name_kind=int)):
        if isinstance(r, dict) and r.get("hz") not in rates:
            c.fail(f"{w}.rates[{i}]", f"{r.get('hz')!r} Hz is not in read_rates_hz")


def _check_sensor(c, cap, w, commands):
    tuning = _check_range(c, cap, "angle_tuning", w, -90, 90)
    if tuning is not None:
        c.field(tuning, "step", f"{w}.angle_tuning", int, low=1, high=90)


def _check_surface(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "read", w, commands, write=False)
    c.named_values(cap, "liftoff", w)
    none = c.field(cap, "no_surface", w, str, pattern=_HEX2)
    surfaces = c.items(cap, "surfaces", w)
    for i, s in enumerate(surfaces):
        c.field(s, "id", f"{w}.surfaces[{i}]", str, pattern=_HEX2)
        c.field(s, "name", f"{w}.surfaces[{i}]", str)
        params = c.field(s, "params", f"{w}.surfaces[{i}]", str, pattern=_BYTES)
        if params is not None and len(params.split()) != 3:
            c.fail(f"{w}.surfaces[{i}].params", "needs 3 bytes")
    ids = [s.get("id") for s in surfaces if isinstance(s, dict)]
    c.unique(ids, f"{w}.surfaces", "id")
    if none in ids:
        c.fail(f"{w}.no_surface", "is also a surface id")


def _check_power(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "read", w, commands, write=False)
    c.offset(cap, "offset", w)
    _check_power_off(c, cap, w)
    _check_levels(c, cap, "warning_levels", w)


def _check_kb_profile(c, cap, w, commands):
    c.command_ref(cap, "read", w, commands, write=False)
    c.field(cap, "profiles", w, int, low=1, high=16)
    c.offset(cap, "effect", w)
    c.offset(cap, "profile", w)


def _check_kb_layout(c, cap, w, commands):
    c.command_ref(cap, "read", w, commands, write=False)
    c.offset(cap, "layout", w)
    layouts = c.items(cap, "layouts", w)
    for i, layout in enumerate(layouts):
        c.field(layout, "id", f"{w}.layouts[{i}]", int, low=0, high=255)
        c.field(layout, "name", f"{w}.layouts[{i}]", str, required=False)
        c.field(layout, "iso", f"{w}.layouts[{i}]", bool)
    c.unique([layout.get("id") for layout in layouts if isinstance(layout, dict)], f"{w}.layouts", "id")


def _check_kb_lighting(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "save", w, commands, write=True)
    effects = c.items(cap, "effects", w)
    names = []
    for i, e in enumerate(effects):
        ew = f"{w}.effects[{i}]"
        names.append(c.field(e, "name", ew, str))
        c.field(e, "id", ew, str, pattern=_HEX2)
        writable = c.field(e, "writable", ew, bool, required=False)
        if writable is False:
            if isinstance(e, dict) and "speeds" in e:
                c.fail(ew, "a read-only effect has no speeds")
        else:
            c.named_values(e, "speeds", ew)
    c.unique(names, f"{w}.effects", "name")
    c.unique([e.get("id") for e in effects if isinstance(e, dict)], f"{w}.effects", "id")
    c.field(cap, "off_brightness", w, int, low=0, high=100)
    c.named_values(cap, "colour_modes", w)
    stops = _check_range(c, cap, "gradient_stops", w, 1, 16)
    gradients = c.items(cap, "gradients", w)
    for i, g in enumerate(gradients):
        gw = f"{w}.gradients[{i}]"
        c.field(g, "name", gw, str)
        points = c.items(g, "stops", gw, min_len=1)
        if stops and isinstance(stops.get("min"), int) and isinstance(stops.get("max"), int) \
                and not stops["min"] <= len(points) <= stops["max"]:
            c.fail(gw, f"{len(points)} stops, not {stops['min']}..{stops['max']}")
        last = 0
        for j, p in enumerate(points):
            if not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], int) and not isinstance(p[0], bool)
                    and isinstance(p[1], str) and _RGB.match(p[1])):
                c.fail(f"{gw}.stops[{j}]", f"{p!r} is not [position %, \"rrggbb\"]")
            elif not last < p[0] <= 100:
                c.fail(f"{gw}.stops[{j}]", "positions must rise, 1..100")
            else:
                last = p[0]
    c.unique([g.get("name") for g in gradients if isinstance(g, dict)], f"{w}.gradients", "name")
    for i, d in enumerate(c.items(cap, "directions", w)):
        dw = f"{w}.directions[{i}]"
        effect = c.field(d, "effect", dw, str)
        if effect is not None and effect not in names:
            c.fail(f"{dw}.effect", f"no effect {effect!r}")
        c.named_values(d, "values", dw)
    c.named_values(cap, "thickness", w)
    for i, rgb in enumerate(c.items(cap, "quicksand_scale", w)):
        if not isinstance(rgb, str) or not _RGB.match(rgb):
            c.fail(f"{w}.quicksand_scale[{i}]", f"{rgb!r} is not rrggbb")
    c.named_values(cap, "quicksand_modes", w)


def _check_kb_sleep(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "read", w, commands, write=False)
    c.offset(cap, "offset", w)
    _check_power_off(c, cap, w)


def _check_kb_warning(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.command_ref(cap, "read", w, commands, write=False)
    c.offset(cap, "offset", w)
    _check_levels(c, cap, "levels", w)


def _check_per_key(c, cap, w, commands):
    c.command_ref(cap, "write", w, commands, write=True)
    c.field(cap, "per_packet", w, int, low=1, high=15)
    columns = c.field(cap, "columns", w, int, low=1, high=32)
    rows = _check_range(c, cap, "rows", w, 0, 7)
    layouts = c.field(cap, "layouts", w, dict)
    for name, layout in (layouts or {}).items():
        lw = f"{w}.layouts.{name}"
        if not isinstance(layout, list) or not layout:
            c.fail(lw, "is not a list of key rows")
            continue
        for i, row in enumerate(layout):
            for j, key in enumerate(row if isinstance(row, list) else [None]):
                if not isinstance(key, str) or not _HEX2.match(key):
                    c.fail(f"{lw}[{i}][{j}]", f"{key!r} is not a key id")
                    continue
                k = byte(key)
                if columns is not None and rows and isinstance(rows.get("min"), int) and isinstance(rows.get("max"), int) \
                        and not (k // 8 < columns and rows["min"] <= k % 8 <= rows["max"]):
                    c.fail(f"{lw}[{i}][{j}]", f"key id {key} is outside the {columns} columns x rows")


CAPABILITIES = {
    "battery": _check_battery,
    "led_zones": _check_led_zones,
    "dpi": _check_dpi,
    "polling": _check_polling,
    "sensor": _check_sensor,
    "surface": _check_surface,
    "power": _check_power,
    "kb_profile": _check_kb_profile,
    "kb_layout": _check_kb_layout,
    "kb_lighting": _check_kb_lighting,
    "kb_sleep": _check_kb_sleep,
    "kb_warning": _check_kb_warning,
    "per_key": _check_per_key,
}


def check(data) -> list:
    """Every problem in a parsed devices.json (empty list = fine)."""
    c = _Checker()
    if not isinstance(data, dict):
        return ["top level is not an object"]
    if c.field(data, "schema", "devices.json", int) not in (None, SCHEMA):
        c.fail("devices.json.schema", f"Squire reads schema {SCHEMA}")
    c.field(data, "vendor_id", "devices.json", str, pattern=_HEX4)
    pids = []

    receivers = {}
    for i, r in enumerate(c.items(data, "receivers", "devices.json", min_len=0)):
        w = f"receivers[{i}]"
        rid = c.field(r, "id", w, str, pattern=_ID)
        c.field(r, "name", w, str)
        pids.append(c.field(r, "pid", w, str, pattern=_HEX4))
        c.field(r, "interface", w, int, low=0, high=15)
        for j, n in enumerate(c.items(r, "report_lengths", w)):
            if isinstance(n, bool) or not isinstance(n, int) or not 8 <= n <= 1024:
                c.fail(f"{w}.report_lengths[{j}]", f"{n!r} is not a report length")
        if rid in receivers:
            c.fail(w, f"duplicate receiver id {rid!r}")
        receivers[rid] = r

    ids = []
    for i, d in enumerate(c.items(data, "devices", "devices.json")):
        w = f"devices[{i}]"
        did = c.field(d, "id", w, str, pattern=_ID)
        ids.append(did)
        w = f"devices[{did or i}]"
        c.field(d, "name", w, str)
        kind = c.field(d, "kind", w, str)
        if kind is not None and kind not in KINDS:
            c.fail(f"{w}.kind", f"{kind!r} is not one of {KINDS}")

        connections, report_ids = [], []
        for j, r in enumerate(c.items(d, "routes", w)):
            rw = f"{w}.routes[{j}]"
            connection = c.field(r, "connection", rw, str)
            if connection is not None and connection not in CONNECTIONS:
                c.fail(f"{rw}.connection", f"{connection!r} is not one of {CONNECTIONS}")
            connections.append(connection)
            if "receiver" in (r if isinstance(r, dict) else {}):
                receiver = c.field(r, "receiver", rw, str)
                if receiver not in receivers:
                    c.fail(f"{rw}.receiver", f"no receiver {receiver!r}")
                report_ids.append(c.field(r, "report_id", rw, str, pattern=_HEX2))
                if "pid" in r:
                    c.fail(rw, "a receiver route has the receiver's pid, not its own")
            else:
                pids.append(c.field(r, "pid", rw, str, pattern=_HEX4))
                if connection == "bluetooth":
                    c.field(r, "gatt_name", rw, str)
                else:
                    c.field(r, "interface", rw, int, low=0, high=15)
                    for k, n in enumerate(c.items(r, "report_lengths", rw)):
                        if isinstance(n, bool) or not isinstance(n, int) or not 8 <= n <= 1024:
                            c.fail(f"{rw}.report_lengths[{k}]", f"{n!r} is not a report length")
        c.unique([x for x in connections if x], f"{w}.routes", "connection")
        c.unique([x for x in report_ids if x], f"{w}.routes", "report id")

        commands = {}
        for j, cmd in enumerate(c.items(d, "commands", w)):
            cw = f"{w}.commands[{j}]"
            name = c.field(cmd, "name", cw, str)
            write = c.field(cmd, "write", cw, bool, required=False)
            if write:
                prefixes = c.items(cmd, "prefixes", cw)
                for k, p in enumerate(prefixes):
                    if not isinstance(p, str) or not _BYTES.match(p):
                        c.fail(f"{cw}.prefixes[{k}]", f"{p!r} is not hex bytes")
                    elif p.startswith("12"):
                        c.fail(f"{cw}.prefixes[{k}]", "12 xx is a get request, not a write")
                if isinstance(cmd, dict) and "bytes" in cmd:
                    c.fail(cw, "a write has prefixes, not bytes")
            else:
                exact = c.field(cmd, "bytes", cw, str, pattern=_BYTES)
                if exact is not None and not exact.startswith("12 "):
                    c.fail(f"{cw}.bytes", "a read must be a get request (12 xx)")
            c.field(cmd, "reply", cw, str, required=False, pattern=_BYTES)
            status = c.field(cmd, "status", cw, str)
            if status is not None and status not in STATUSES:
                c.fail(f"{cw}.status", f"{status!r} is not one of {STATUSES}")
            c.field(cmd, "approved", cw, str, required=status == "confirmed", pattern=_DATE)
            if name in commands:
                c.fail(cw, f"duplicate command {name!r}")
            commands[name] = cmd if isinstance(cmd, dict) else {}
        reads = [cmd.get("bytes") for cmd in commands.values() if not cmd.get("write")]
        c.unique([r for r in reads if r], f"{w}.commands", "read")
        prefixes = [p for cmd in commands.values() if cmd.get("write") for p in cmd.get("prefixes") or []]
        c.unique(prefixes, f"{w}.commands", "write prefix")

        caps = c.field(d, "capabilities", w, dict) or {}
        if "battery" not in caps:
            c.fail(f"{w}.capabilities", "every device needs 'battery'")
        for name, cap in caps.items():
            checker = CAPABILITIES.get(name)
            if checker is None:
                c.fail(f"{w}.capabilities", f"unknown capability {name!r}")
            elif not isinstance(cap, dict):
                c.fail(f"{w}.capabilities.{name}", "is not an object")
            else:
                checker(c, cap, f"{w}.capabilities.{name}", commands)

    c.unique([i for i in ids if i], "devices", "id")
    c.unique([p for p in pids if p], "devices.json", "pid")
    return c.problems


# --- model ------------------------------------------------------------------------------------------------------

class Device:
    """One device model. The raw JSON is in .data; the helpers turn hex strings into ints and bytes."""

    def __init__(self, data: dict, registry: "Registry"):
        self.data = data
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.kind: str = data["kind"]
        self.registry = registry

    def capability(self, name: str) -> dict:
        return self.data["capabilities"][name]

    def has(self, capability: str) -> bool:
        return capability in self.data["capabilities"]

    def _command(self, name: str) -> dict:
        return next(c for c in self.data["commands"] if c["name"] == name)

    def read(self, name: str) -> bytes:
        """The bytes of a read command by name."""
        command = self._command(name)
        assert not command.get("write"), name
        return hex_bytes(command["bytes"])

    def write_prefixes(self, name: str) -> tuple:
        command = self._command(name)
        assert command.get("write"), name
        return tuple(hex_bytes(p) for p in command["prefixes"])

    def reads(self, confirmed: bool = True) -> dict:
        """{name: bytes} of the read commands in file order (confirmed ones only, unless confirmed=False)."""
        return {c["name"]: hex_bytes(c["bytes"]) for c in self.data["commands"]
                if not c.get("write") and (c["status"] == "confirmed" or not confirmed)}

    def confirmed_write_prefixes(self) -> tuple:
        return tuple(hex_bytes(p) for c in self.data["commands"]
                     if c.get("write") and c["status"] == "confirmed" for p in c["prefixes"])

    def route(self, connection: str) -> Optional[dict]:
        return next((r for r in self.data["routes"] if r["connection"] == connection), None)

    def routes(self) -> list:
        """Routes with the receiver's pid / interface / lengths filled in, in priority order (Omni, cable, Bluetooth:
        the order Squire has always tried; a device on the cable answers ff aa on the receiver)."""
        out = []
        for r in sorted(self.data["routes"], key=lambda r: CONNECTIONS.index(r["connection"])):
            r = dict(r)
            if "receiver" in r:
                receiver = self.registry.receivers[r["receiver"]]
                r.update(pid=receiver["pid"], interface=receiver["interface"],
                         report_lengths=receiver["report_lengths"])
            out.append(r)
        return out


class Registry:
    def __init__(self, data: dict):
        self.data = data
        self.vendor_id = int(data["vendor_id"], 16)
        self.receivers = {r["id"]: r for r in data.get("receivers", [])}
        self.devices = [Device(d, self) for d in data["devices"]]

    def by_id(self, device_id: str) -> Device:
        return next(d for d in self.devices if d.id == device_id)

    def by_kind(self, kind: str) -> Device:
        """The one model of this kind. Until Squire supports two models of a kind (stage 5), code that still keys on
        "keyboard" / "mouse" goes through here."""
        found = [d for d in self.devices if d.kind == kind]
        if len(found) != 1:
            raise LookupError(f"{len(found)} {kind} models in devices.json, expected one")
        return found[0]


def load(path: Optional[Path] = None) -> Registry:
    """Read and check devices.json; raises DevicesError listing every problem."""
    path = Path(path or DEVICES_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DevicesError([f"{path}: {error}"]) from error
    problems = check(data)
    if problems:
        raise DevicesError(problems)
    return Registry(data)


if __name__ == "__main__":   # python3 -m squirelib.registry [path]: check a devices.json
    import sys
    try:
        registry = load(sys.argv[1] if len(sys.argv) > 1 else None)
    except DevicesError as error:
        print("\n".join(error.problems), file=sys.stderr)
        sys.exit(1)
    print(f"ok: {', '.join(f'{d.id} ({d.kind})' for d in registry.devices)}")

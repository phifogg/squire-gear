"""Mouse lift-off distance and surface (mouse pad) preset share one command: 51 35 00 00 <surface id> <lift-off> 00
<p1 p2 p3>, or 51 35 ff 00 ff <lift-off> for "no surface". Read: 12 06 [6] surface id (ff = none), [7] lift-off,
[8..10] surface parameters.

Source: Armoury Crate captures on Windows (USBPcap, 2026-11-12, protocol.json mouse_writes.armoury_crate); G-Helper
writes only the "no surface" form. Approved by the owner 2026-11-13. A lift-off write always carries the surface id and
parameters read from 12 06 just before in the same session, so the surface stays as it is (G-Helper's 51 35 ff 00 ff
<lift-off> drops it: the "calibration reset"). Only Armoury Crate's preset ids: an unknown id (a custom calibration?)
is never overwritten."""
from typing import Optional

from . import Capability, Change, byte


class Surface(Capability):
    name = "surface"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # 51 35
        self.read_name = self.params["read"]                               # liftoff (12 06)
        self.no_surface = byte(self.params["no_surface"])                  # "Default (No calibration)" in AC
        self.liftoff_codes = {x["name"]: byte(x["value"]) for x in self.params["liftoff"]}
        # Armoury Crate's presets (id -> name); most share the parameters 1c 34 16. Ids 02..06 and 09 never appeared.
        self.surfaces = {byte(x["id"]): x["name"] for x in self.params["surfaces"]}
        # Parameters Armoury Crate writes with each preset (captured 2026-11-12); None for "no surface".
        self.surface_params = {**{byte(x["id"]): x["params"] for x in self.params["surfaces"]}, self.no_surface: None}

    def surface_name(self, surface_id: Optional[int]) -> Optional[str]:
        if surface_id is None:
            return None
        if surface_id == self.no_surface:
            return "None (no calibration)"
        return self.surfaces.get(surface_id, f"unknown (0x{surface_id:02x})")

    def liftoff_command(self, liftoff: str, surface_id: Optional[int], surface_params: Optional[str]) -> bytes:
        """51 35: lift-off low/high, keeping the surface (id + parameters as read from 12 06). Raises ValueError for an
        unknown surface, so a surface Squire does not know (a custom calibration?) is never overwritten."""
        if liftoff not in self.liftoff_codes:
            raise ValueError(f"lift-off {liftoff!r} is not low or high")
        if surface_id == self.no_surface:
            command = self.write_prefix + bytes([self.no_surface, 0x00, self.no_surface, self.liftoff_codes[liftoff]])
        elif surface_id in self.surfaces and surface_params is not None and len(bytes.fromhex(surface_params)) == 3:
            command = self.write_prefix + bytes([0x00, 0x00, surface_id, self.liftoff_codes[liftoff], 0x00]) \
                + bytes.fromhex(surface_params)
        else:
            raise ValueError(f"surface {self.surface_name(surface_id) or 'not read'}: lift-off not written, so the "
                             "surface setting is kept")
        return self.checked(command)

    def surface_command(self, surface_id: int, liftoff: str) -> bytes:
        """51 35 with a preset surface (or no_surface) and its Armoury Crate parameters, keeping `liftoff`."""
        if surface_id not in self.surface_params:
            raise ValueError(f"surface {surface_id!r} is not one of Armoury Crate's presets")
        return self.liftoff_command(liftoff, surface_id, self.surface_params[surface_id])

    def allows(self, command: bytes) -> bool:
        if len(command) == 6:
            return (command[2:5] == bytes([self.no_surface, 0x00, self.no_surface])
                    and command[5] in self.liftoff_codes.values())
        return (len(command) == 10 and command[2:4] == b"\0\0" and command[4] in self.surfaces
                and command[5] in self.liftoff_codes.values() and command[6] == 0)

    def parse(self, s, read: str, p: bytes) -> None:
        if read == self.read_name:
            # [6] surface id (ff = none), [7] lift-off, [8..10] surface parameters (Armoury Crate captures 2026-11-12)
            s.liftoff = {0: "low", 1: "high"}.get(p[7], f"0x{p[7]:02x}")
            s.surface_id = p[6]
            s.surface_params = None if p[6] == self.no_surface else p[8:11].hex(" ")

    @staticmethod
    def _extract(s):
        return {"liftoff": s.liftoff, "surface_id": s.surface_id, "surface_params": s.surface_params}

    def liftoff_change(self, liftoff: str) -> Change:
        """Lift-off low/high. The build reads 12 06 first and writes the surface id + parameters it found back
        unchanged; the read-back must show the new lift-off and the same surface."""
        if liftoff not in self.liftoff_codes:
            raise ValueError(f"lift-off {liftoff!r} is not low or high")

        def matches(after, before):
            return bool(after and before) and after == {**before, "liftoff": liftoff}
        return Change(lambda before: self.liftoff_command(liftoff, before["surface_id"], before["surface_params"]),
                      (self.read_name,), self._extract, matches)

    def surface_change(self, surface_id: int) -> Change:
        """Pick a preset surface or no_surface. Keeps the lift-off read from 12 06 just before (Armoury Crate resets
        it to low). Refused while the mouse has a surface Squire does not know: it may be a calibration."""
        if surface_id not in self.surface_params:
            raise ValueError(f"surface {surface_id!r} is not one of Armoury Crate's presets")

        def build(before):
            if before["surface_id"] not in self.surface_params:
                raise ValueError(f"the mouse has surface {self.surface_name(before['surface_id'])}: not replaced, it "
                                 "may be a calibration")
            return self.surface_command(surface_id, before["liftoff"])

        def matches(after, before):
            return bool(after and before) and after == {"liftoff": before["liftoff"], "surface_id": surface_id,
                                                        "surface_params": self.surface_params[surface_id]}
        return Change(build, (self.read_name,), self._extract, matches)

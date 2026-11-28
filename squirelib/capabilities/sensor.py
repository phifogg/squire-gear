"""Mouse sensor: angle snapping 51 31 06 00 <0/1> and angle tuning 51 31 0b 00 <lo> <hi> (signed LE16, degrees), read
back from 12 04 00 [16] / [18..19]; that reply also carries the debounce index [14] (read only: not approved).

Source, facts only: G-Helper AsusMouse.cs GetUpdateAngleSnappingPacket(), GetUpdateAngleAdjustmentPacket() and
Models/KerisIIOrigin.cs (HasAngleSnapping, HasAngleTuning, AngleTuningStep 5) (GPL-3.0); angle snapping also in
libratbag asus_set_angle_snapping() (51 31 06 00 0/1). Bytes approved by the owner 2026-11-12."""
from . import Capability, Change

READ = "settings"   # 12 04 00


def u16(p: bytes, i: int) -> int:
    return p[i] | (p[i + 1] << 8)


class Sensor(Capability):
    name = "sensor"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        snapping, = device.write_prefixes("angle_snapping")
        tuning, = device.write_prefixes("angle_tuning")
        self.write_prefix = snapping[:2]                     # 51 31 <field> 00 <value lo> <value hi>
        self.snapping_field, self.tuning_field = snapping[2], tuning[2]    # 06, 0b
        t = self.params["angle_tuning"]                      # G-Helper: -20..20, step 5
        self.tuning_degrees = tuple(range(t["min"], t["max"] + 1, t["step"]))

    def write_commands(self) -> tuple:
        return ("angle_snapping", "angle_tuning")

    def snapping_command(self, on: bool) -> bytes:
        return self.checked(self.write_prefix + bytes([self.snapping_field, 0x00, 0x01 if on else 0x00]))

    def tuning_command(self, degrees: int) -> bytes:
        if degrees not in self.tuning_degrees:
            raise ValueError(f"angle tuning {degrees!r}° is not one of -20..20 in steps of 5")
        return self.checked(self.write_prefix + bytes([self.tuning_field, 0x00]) + (degrees & 0xFFFF).to_bytes(2, "little"))

    def allows(self, command: bytes) -> bool:
        if len(command) < 4 or command[3] != 0:
            return False
        if command[2] == self.snapping_field:
            return len(command) == 5 and command[4] in (0, 1)
        if command[2] == self.tuning_field and len(command) == 6:
            return int.from_bytes(command[4:6], "little", signed=True) in self.tuning_degrees
        return False

    def parse(self, s, read: str, p: bytes) -> None:
        if read == READ:
            s.debounce_index = p[14]
            s.debounce_ms = 4 + 4 * p[14] if 1 <= p[14] <= 7 else None
            s.angle_snapping = p[16] == 1
            angle = u16(p, 18)
            s.angle_tuning = angle - 0x10000 if angle & 0x8000 else angle

    def snapping_change(self, on: bool) -> Change:
        return Change(self.snapping_command(on), (READ,), lambda s: {"angle_snapping": s.angle_snapping},
                      lambda after, _: after == {"angle_snapping": bool(on)})

    def tuning_change(self, degrees: int) -> Change:
        return Change(self.tuning_command(degrees), (READ,), lambda s: {"angle_tuning": s.angle_tuning},
                      lambda after, _: after == {"angle_tuning": degrees})

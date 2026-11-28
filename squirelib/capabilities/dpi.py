"""Mouse DPI: per stage 51 31 <stage 0-based> 00 <lo> <hi> <r g b> (X and Y alike + the stage colour, value
(DPI - 50) / 50), active stage 51 31 09 00 <1..count>, stage count 51 31 0a 00 <1..4>. Read: 12 04 00 [4..11] (ff ff on
this separate-X/Y mouse), 12 04 02 X/Y per stage, 12 04 03 colour per stage, 12 00 [11] active stage / [18] count.

Source: Armoury Crate captures (USBPcap 2026-11-12, mouse_writes.armoury_crate) and G-Helper AsusMouse.cs
GetUpdateDPIPacket / GetChangeDPIProfilePacket2 / GetSetDPIProfileCountPacket + Models/KerisIIOrigin.cs (MaxDPI 42000,
4 stages, CanChangeDPICount, DPI colours) (GPL-3.0, facts only). Approved by the owner 2026-11-13. Squire writes only
the changed field, never the whole block like Armoury Crate."""
from typing import Optional

from . import Capability, Change
from .sensor import u16


def dpi_value(raw_value: int) -> int:
    return raw_value * 50 + 50      # devices store (DPI - 50) / 50


class Dpi(Capability):
    name = "dpi"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix = device.write_prefixes(self.params["write"])[0][:2]   # 51 31
        self.active_stage_field = device.write_prefixes("active_stage")[0][2]   # 09
        self.stage_count_field = device.write_prefixes("stage_count")[0][2]     # 0a
        self.stages = self.params["stages"]
        self.min, self.max, self.step = self.params["min"], self.params["max"], self.params["step"]

    def write_commands(self) -> tuple:
        return (self.params["write"], "active_stage", "stage_count")

    def command(self, stage: int, dpi: int, color: str) -> bytes:
        """One DPI stage (1-based): DPI for X and Y alike, plus the stage colour (#rrggbb)."""
        if not 1 <= stage <= self.stages:
            raise ValueError(f"DPI stage {stage!r} is not 1-{self.stages}")
        if not (self.min <= dpi <= self.max and dpi % self.step == 0):
            raise ValueError(f"{dpi!r} DPI is not {self.min}-{self.max} in steps of {self.step}")
        color_bytes = bytes.fromhex(color.lstrip("#"))
        if len(color_bytes) != 3:
            raise ValueError(f"colour {color!r} is not #rrggbb")
        return self.checked(self.write_prefix + bytes([stage - 1, 0x00]) + ((dpi - 50) // 50).to_bytes(2, "little")
                            + color_bytes)

    def active_stage_command(self, stage: int) -> bytes:
        if not 1 <= stage <= self.stages:
            raise ValueError(f"DPI stage {stage!r} is not 1-{self.stages}")
        return self.checked(self.write_prefix + bytes([self.active_stage_field, 0x00, stage]))

    def stage_count_command(self, count: int) -> bytes:
        if not 1 <= count <= self.stages:
            raise ValueError(f"DPI stage count {count!r} is not 1-{self.stages}")
        return self.checked(self.write_prefix + bytes([self.stage_count_field, 0x00, count]))

    def allows(self, command: bytes) -> bool:
        if len(command) < 4 or command[3] != 0:
            return False
        if command[2] in (self.active_stage_field, self.stage_count_field):
            return len(command) == 5 and 1 <= command[4] <= self.stages
        if command[2] < self.stages:
            dpi = int.from_bytes(command[4:6], "little") * 50 + 50
            return len(command) == 9 and self.min <= dpi <= self.max and dpi % self.step == 0
        return False

    def parse(self, s, read: str, p: bytes) -> None:
        o, n = self.params["offsets"], self.stages
        if read == "profile":
            stage, count = p[o["active_stage"]], p[o["stage_count"]]   # [11], [18]
            s.dpi_stage = stage if stage else None
            s.dpi_stage_count = count if 1 <= count <= n else None
        elif read == "settings":
            # ff ff = not in this reply: mice with separate X/Y DPI (like the Keris) report DPI only in dpi_xy
            at = o["dpi"]
            s.dpi = [None if u16(p, at + 2 * i) == 0xFFFF else dpi_value(u16(p, at + 2 * i)) for i in range(n)]
        elif read == "dpi_xy":
            at = o["dpi_xy"]
            s.dpi_xy = [[dpi_value(u16(p, at + 4 * i)), dpi_value(u16(p, at + 2 + 4 * i))] for i in range(n)]
            if not s.dpi or None in s.dpi:
                s.dpi = [x for x, _ in s.dpi_xy]
        elif read == "dpi_colors":
            at = o["dpi_colors"]
            s.dpi_colors = ["#" + p[at + 3 * i:at + 3 + 3 * i].hex() for i in range(n)]

    def change(self, stage: int, dpi: int, color: Optional[str] = None) -> Change:
        """One DPI stage (1-based), X and Y alike. The stage colour travels in the same command: without `color` the
        one read from 12 04 03 just before is written back. Refused for a stage with separate X/Y values."""
        self.command(stage, dpi, color or "#000000")       # validate before anything is sent
        color = "#" + bytes.fromhex(color.lstrip("#")).hex() if color else None

        def extract(s):
            i = stage - 1
            return {"dpi_xy": s.dpi_xy[i] if i < len(s.dpi_xy) else None,
                    "color": s.dpi_colors[i] if i < len(s.dpi_colors) else None}

        def build(before):
            if not before["dpi_xy"] or not before["color"]:
                raise ValueError("DPI stage not read: nothing written")
            if before["dpi_xy"][0] != before["dpi_xy"][1]:
                raise ValueError(f"stage {stage} has X {before['dpi_xy'][0]} / Y {before['dpi_xy'][1]}: Squire would "
                                 "set both alike, so it is not written")
            return self.command(stage, dpi, color or before["color"])

        def matches(after, before):
            return bool(after and before) and after == {"dpi_xy": [dpi, dpi], "color": (color or before["color"]).lower()}
        return Change(build, ("dpi_xy", "dpi_colors"), extract, matches)

    @staticmethod
    def _stages(s):
        return {"dpi_stage": s.dpi_stage, "dpi_stage_count": s.dpi_stage_count}

    def active_stage_change(self, stage: int) -> Change:
        """The DPI stage in use (1-based), read back from 12 00 [11]. Only a stage the DPI button cycles through."""
        self.active_stage_command(stage)

        def build(before):
            if before["dpi_stage_count"] is None or stage > before["dpi_stage_count"]:
                raise ValueError(f"stage {stage} is not one of the {before['dpi_stage_count']} on the DPI button")
            return self.active_stage_command(stage)
        return Change(build, ("profile",), self._stages,
                      lambda after, before: bool(after and before) and after == {**before, "dpi_stage": stage})

    def stage_count_change(self, count: int) -> Change:
        """How many stages the DPI button cycles through, read back from 12 00 [18]. Never fewer than the active
        stage (what the mouse would do then is unknown)."""
        self.stage_count_command(count)

        def build(before):
            if before["dpi_stage"] is None or count < before["dpi_stage"]:
                raise ValueError(f"stage {before['dpi_stage']} is active: choose it or a lower one first")
            return self.stage_count_command(count)
        return Change(build, ("profile",), self._stages,
                      lambda after, before: bool(after and before) and after == {**before, "dpi_stage_count": count})

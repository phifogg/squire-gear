"""Keyboard factory layout: 12 12 [5] layout id (3/4/5/17 from G-Helper, 25 = CH-DE confirmed by the owner,
2026-11-13), and whether G-Helper draws it as ISO (an extra key left of Z, tall Enter). Read-only."""
from . import Capability


class KbLayout(Capability):
    name = "kb_layout"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.layouts = {x["id"]: x["name"] for x in self.params["layouts"] if "name" in x}
        self.iso_layouts = frozenset(x["id"] for x in self.params["layouts"] if x["iso"])

    def parse(self, s, read: str, p: bytes) -> None:
        if read == self.params["read"]:
            layout = p[self.params["layout"]]   # [5]
            s.layout_id = layout
            s.layout = self.layouts.get(layout)
            s.iso = layout in self.iso_layouts

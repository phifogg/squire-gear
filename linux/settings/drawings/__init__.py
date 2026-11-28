"""Drawings per model (SVG strings) and the stage layout around them.

A drawing belongs to one model (devices.json id), not to a kind: a second mouse looks different. A model without an
entry here gets the plain page (a card per setting, no artwork), so a new device is usable before a drawing exists."""
DRAWINGS = {"keris-ii-origin": "mouse", "falchion-rx-lp": "keyboard"}   # model id -> module in this package


def has_drawing(model) -> bool:
    return model.id in DRAWINGS

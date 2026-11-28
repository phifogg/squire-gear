"""Device pages: the state logic per device kind, and which devices the sidebar lists.

A page belongs to one model in devices.json (page.model: id, name, kind); the page class is chosen by the model's
kind (PAGE_CLASSES in window.py). Batteries are still keyed by kind ("mouse" / "keyboard") until two models of a kind
exist (stage 5)."""
from typing import Optional


def is_present(battery, data) -> bool:
    """Whether the sidebar lists a device: the battery read found it (also asleep, without access, or over
    Bluetooth only), or its settings read did not say "not found" (a read that failed with an error is listed, so
    the page can say so). "Not found" on both = not connected."""
    if battery is not None:
        return True
    return data is not None and data.status != "not-found"


def sidebar_text(battery) -> str:
    """The line under a device's name in the sidebar."""
    import squire
    if battery is None:
        return "not found"
    if battery.percent is None:
        return {"asleep": "asleep", "no-access": "no access"}.get(battery.status, battery.status)
    return f"{squire.connection_label(battery.connection)} · {battery.percent} %" + \
        (" · charging" if battery.charging else "")


def pick(listed: list, current: Optional[str]) -> Optional[str]:
    """The page to show after a read: the one shown before while it is still listed, else the first listed."""
    if current in listed:
        return current
    return listed[0] if listed else None


NO_DEVICES = ("No devices found",
              "Squire found no supported device. Plug in the Omni receiver or the USB cable, or connect a device "
              "over Bluetooth, then read again.")

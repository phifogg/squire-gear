"""Helpers the settings window's pages share: where the repository files are, the Bluetooth notice, colour
swatches. No GTK."""
from pathlib import Path


# linux/ in the repository, /usr/share/squire-gear when installed (squire_settings.py lives there)
APP_DIR = Path(__file__).resolve().parents[1]
# protocol.json (for --demo) is in the repository root, one folder up from linux/
ROOT = APP_DIR.parent if (APP_DIR.parent / "protocol.json").exists() else APP_DIR
DEMO = False            # --demo: show the captures from protocol.json instead of asking the devices; Apply sends nothing
PLAIN = False           # --plain: the plain page (cards, no drawing) for every model, as for one without artwork
SCREENSHOT: list = []   # --screenshot PATH [ZONE] [edit ...]: save a PNG and quit (squire_settings.main sets both)


def bluetooth_status(b, device: str):
    """(title, text) for a device that has a Bluetooth battery level but no settings read, else None."""
    if b is None or b.connection != "bluetooth" or b.percent is None:
        return None
    return (f"The {device} is connected over Bluetooth",
            f"Over Bluetooth Squire can only read the battery level. To change settings, connect the {device} "
            "through the Omni receiver or the USB cable, then read again.")


def swatch_svg(color: str) -> str:
    if color in ("#000000", "none"):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"><circle cx="18" cy="18" r="15" '
                'fill="none" stroke="#7a828e" stroke-width="2" stroke-dasharray="4 3"/></svg>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36"><circle cx="18" cy="18" r="15" '
            f'fill="{color}" stroke="#00000033" stroke-width="1"/></svg>')

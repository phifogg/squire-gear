# Squire

**A lightweight companion for ASUS ROG peripherals on macOS.**

Squire shows the battery level of the **ASUS ROG Falchion RX Low Profile** keyboard and the
**ASUS ROG Keris II Origin** mouse in the macOS menu bar, without Armoury Crate.
Like a squire looking after a knight's blades, it keeps an eye on your Falchion and Keris.
The current release is a small Python script with a [SwiftBar](https://github.com/swiftbar/SwiftBar)
plugin. A native Swift menu bar app is in development, see [Native app (preview)](#native-app-preview).

```
⌨ 78%   🖱 77% ⚡
```

## Features

- Battery percentage and charging state for keyboard and mouse
- Works through the **ROG Omni receiver**, so both devices can share one dongle
- Orange below 30 %, red below 15 %, a dash when a device is asleep or off
- Works over the USB cable as well; a device on the cable is picked up automatically
- **Low-battery notification:** one macOS notification per device when it reaches the warning
  level configured on the device itself, reset after charging
- **Read-only:** sends only the two battery query commands. It never changes lighting,
  settings or firmware.
- Opens only the receiver's vendor-defined HID collections, never the regular
  keyboard or mouse input

## Status

Version 0.1.2. Tested on macOS with a Falchion RX Low Profile and a Keris II Origin.
Battery percentage and charging state are confirmed for both devices, connected through the
Omni receiver and over a USB cable.

The warning level for the notification is read from the device's reply (20 % on both test
devices). It is not yet confirmed that this level follows changes made in Armoury Crate; if a
reply carries no plausible level, 20 % is used.

Not tested yet:

- Other ROG keyboards and mice. They may work if they use the same protocol, but the
  device IDs are not included.

## Requirements

- macOS with Python 3 (`brew install python` if `python3` is missing)
- [SwiftBar](https://github.com/swiftbar/SwiftBar): `brew install --cask swiftbar`

## Installation

```sh
git clone https://github.com/phifogg/squire-gear.git
cd squire-gear
./install.sh ~/SwiftBar
```

`install.sh` creates its own Python environment with
[`hidapi`](https://pypi.org/project/hidapi/) in `~/Library/Application Support/squire-gear/`,
copies the script there and adds the plugin `squire-gear.5m.sh` (refresh every 5 minutes) to
the given folder. It creates the folder if it does not exist yet. Then set SwiftBar's plugin
folder to the same path. The plugin runs `squire.py --swiftbar --notify`.

macOS asks once whether SwiftBar may send notifications; allow it to get the low-battery alert.

## Command line

```sh
APP="$HOME/Library/Application Support/squire-gear"
"$APP/venv/bin/python" "$APP/squire.py"            # text output
"$APP/venv/bin/python" "$APP/squire.py" --json     # JSON
"$APP/venv/bin/python" "$APP/squire.py" --notify   # also send the low-battery notification
"$APP/venv/bin/python" "$APP/squire.py" --list     # list ASUS HID collections, sends nothing
"$APP/venv/bin/python" "$APP/squire.py" --debug    # show raw replies
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `Python module 'hid' is missing` | Run the script with the installed Python (see above), or run `install.sh` again |
| No devices found | Check with `--list` that `pid=1ace` (Omni receiver) appears; plug the receiver directly into the Mac |
| `open … failed` in `--debug` | System Settings → Privacy & Security → **Input Monitoring**: allow Terminal or SwiftBar |
| Mouse always shows a dash | Move the mouse and refresh; the mouse does not answer while asleep |
| No low-battery notification | System Settings → Notifications: allow SwiftBar (or the terminal you run `--notify` from) |

## Protocol

| Device | Query | Reply (positions after the echoed command) | Report ID via Omni |
|---|---|---|---|
| Keyboard | `12 01` | `[10]` battery %, `[5]` battery % (copy), `[8]` charging, `[9]` low-battery warning %, `[11..12]` probably mV | `0x02` |
| Mouse | `12 07` | `[4]` battery %, `[9]` charging, `[6]` low-battery warning %, `[7..8]` probably mV | `0x03` |

A reply of `ff aa` means the device is asleep or does not support the command. Over the
Omni receiver a device that is plugged in by cable answers `ff aa` there.

Over USB the same queries work without a report ID: the write is `00 12 01` / `00 12 07`
(65 bytes) and the reply starts directly with the echoed command. The wired keyboard is
`pid 1b04`, the wired mouse `pid 1c0c`.

[`protocol.json`](protocol.json) contains real replies captured from the hardware, including
both charging states. The unit tests run against them.

The byte positions were first taken from [G-Helper](https://github.com/seerge/g-helper)
(`app/Peripherals/Keyboard` and `app/Peripherals/Mouse`) and then confirmed with the
captures above. G-Helper is GPL-3.0; this project uses only those protocol facts, no code.

## Tests

No hardware needed:

```sh
python3 -m unittest -v        # Python
cd macos && swift test        # Swift core, reads the same protocol.json
```

## Native app (preview)

The `macos/` folder holds a native Swift menu bar app. It is in development and not part of
the 0.1.x release: `install.sh` still installs the Python and SwiftBar version.

It shows keyboard and mouse in the menu bar and in a popover (battery bar, connection,
device warning level), polls every 60 seconds, sends the low-battery notification itself
and can launch at login. It uses the same read-only queries and the same `protocol.json`
test vectors as the Python version. There is no desktop widget yet.

Building needs Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`). The build is ad-hoc signed for local use only:

```sh
cd macos
xcodegen generate
xcodebuild -project Squire.xcodeproj -scheme Squire -configuration Debug \
  -derivedDataPath .build/xcode build
open .build/xcode/Build/Products/Debug/Squire.app
```

`swift run squire-probe` prints what IOKit sees and the parsed batteries without starting the app.

## Roadmap

- Native macOS app: finish the menu bar app, add a WidgetKit widget for the desktop
- Linux support
- Other ROG devices (send `--list` and `--debug` output to help)

## Disclaimer

Not affiliated with or endorsed by ASUS. ROG, Falchion and Keris are trademarks of ASUSTeK
Computer Inc.

## License

[MIT](LICENSE)

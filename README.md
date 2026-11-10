# Squire

**A lightweight companion for ASUS ROG peripherals on macOS.**

Squire shows the battery level of the **ASUS ROG Falchion RX Low Profile** keyboard and the
**ASUS ROG Keris II Origin** mouse in the macOS menu bar, without Armoury Crate.
Like a squire looking after a knight's blades, it keeps an eye on your Falchion and Keris.

```
⌨ 78%⚡︎  🖱 77%
```

## Features

- Battery percentage and charging state for keyboard and mouse
- Works through the **ROG Omni receiver**, so both devices can share one dongle, and over the
  **USB cable**; a device on the cable is picked up automatically
- Menu bar item plus a popover with battery bars, connection and warning level
- **Low-battery notification:** one macOS notification per device when it reaches the warning
  level configured on the device itself, reset after charging
- Optional launch at login
- **Read-only:** sends only the two battery query commands. It never changes lighting,
  settings or firmware.
- Opens only vendor-defined HID collections, never the regular keyboard or mouse input
- No Python, Homebrew or SwiftBar needed

## Status

Version 0.2.0. Tested on macOS with a Falchion RX Low Profile and a Keris II Origin.
Battery percentage and charging state are confirmed for both devices, connected through the
Omni receiver and over a USB cable.

The warning level for the notification is read from the device's reply (20 % on both test
devices). It is not yet confirmed that this level follows changes made in Armoury Crate; if a
reply carries no plausible level, 20 % is used.

Not tested yet:

- Other ROG keyboards and mice. They may work if they use the same protocol, but the
  device IDs are not included.
- Launch at login from a downloaded copy of the app.

## Installation

Requires macOS 13 or newer (Apple silicon and Intel).

1. Download `Squire-<version>.zip` from the
   [latest release](https://github.com/phifogg/squire-gear/releases/latest) and unzip it.
2. Move `Squire.app` to `/Applications` and open it.
3. Allow notifications when macOS asks.

**The app is not notarized yet.** It is signed ad hoc, not with an Apple Developer ID, so macOS
blocks the first start. Either open **System Settings → Privacy & Security** and click
**Open Anyway** next to the Squire message, or remove the quarantine flag once:

```sh
xattr -dr com.apple.quarantine /Applications/Squire.app
```

Squire has no Dock icon; look for it in the menu bar. Click it for the popover with
**Notify when low**, **Launch at login**, **Refresh now** and **Quit**. It reads the devices
every 60 seconds.

You can verify the download against the `.sha256` file on the release page:
`shasum -a 256 -c Squire-<version>.zip.sha256`.

### Build from source

Needs Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`):

```sh
git clone https://github.com/phifogg/squire-gear.git
cd squire-gear/macos
./scripts/build-release.sh          # universal build, writes dist/Squire-<version>.zip
```

For a quick local build and run:

```sh
cd macos
xcodegen generate
xcodebuild -project Squire.xcodeproj -scheme Squire -configuration Debug \
  -derivedDataPath .build/xcode build
open .build/xcode/Build/Products/Debug/Squire.app
```

`swift run squire-probe` prints what IOKit sees and the parsed batteries without starting the
app. `swift test` runs the core tests.

## Alternative: command line and SwiftBar

The original Python version still works and is where new protocol ideas are tried out first.
It is a small script with a [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin.

Requirements: Python 3 (`brew install python`) and SwiftBar (`brew install --cask swiftbar`).

```sh
git clone https://github.com/phifogg/squire-gear.git
cd squire-gear
./install.sh ~/SwiftBar
```

`install.sh` creates its own Python environment with
[`hidapi`](https://pypi.org/project/hidapi/) in `~/Library/Application Support/squire-gear/`,
copies the script there and adds the plugin `squire-gear.5m.sh` (refresh every 5 minutes) to
the given folder. It creates the folder if it does not exist yet. Then set SwiftBar's plugin
folder to the same path. The plugin runs `squire.py --swiftbar --notify`; allow notifications
for SwiftBar when macOS asks. In this version the menu bar text is orange below 30 %, red
below 15 %, and a dash when a device is asleep or off.

Command line:

```sh
APP="$HOME/Library/Application Support/squire-gear"
"$APP/venv/bin/python" "$APP/squire.py"            # text output
"$APP/venv/bin/python" "$APP/squire.py" --json     # JSON
"$APP/venv/bin/python" "$APP/squire.py" --list     # list ASUS HID collections, sends nothing
"$APP/venv/bin/python" "$APP/squire.py" --debug    # show raw replies
"$APP/venv/bin/python" "$APP/squire.py" --notify   # also send the low-battery notification
```

## Troubleshooting

| Problem | Fix |
|---|---|
| macOS says the app is from an unidentified developer or "damaged" | Not notarized yet: see [Installation](#installation) |
| No devices found | Check that the receiver (`pid=1ace`) or the cable-connected device appears (Python: `--list`, app: `swift run squire-probe` from `macos/`); plug the receiver directly into the Mac |
| A device always shows a dash | Move it and refresh; keyboard and mouse do not answer while asleep |
| No low-battery notification | System Settings → Notifications: allow Squire (or SwiftBar for the plugin) |
| Python: `Python module 'hid' is missing` | Run the script with the installed Python (see above), or run `install.sh` again |
| Python: `open … failed` in `--debug` | System Settings → Privacy & Security → **Input Monitoring**: allow Terminal or SwiftBar |

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
both charging states and both connection types. The Python and Swift tests run against them.

The byte positions were first taken from [G-Helper](https://github.com/seerge/g-helper)
(`app/Peripherals/Keyboard` and `app/Peripherals/Mouse`) and then confirmed with the
captures above. G-Helper is GPL-3.0; this project uses only those protocol facts, no code.

## Tests

No hardware needed:

```sh
python3 -m unittest -v        # Python
cd macos && swift test        # Swift core, reads the same protocol.json
```

## Roadmap

- Desktop widget (WidgetKit)
- Optional icon-only menu bar mode
- Notarized builds, depending on interest
- Linux support
- Other ROG devices (send `--list` / `--debug` output or `swift run squire-probe` output to help)

## Disclaimer

Not affiliated with or endorsed by ASUS. ROG, Falchion and Keris are trademarks of ASUSTeK
Computer Inc.

## License

[MIT](LICENSE)

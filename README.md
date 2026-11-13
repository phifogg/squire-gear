# Squire

**A lightweight companion for ASUS ROG peripherals on macOS and Linux.**

Squire shows the battery level of the **ASUS ROG Falchion RX Low Profile** keyboard and the
**ASUS ROG Keris II Origin** mouse in the macOS menu bar or the Linux top bar, without Armoury Crate.
Like a squire looking after a knight's blades, it keeps an eye on your Falchion and Keris.

```
⌨ 78%⚡︎  🖱 77%
```

## Features

- Battery percentage and charging state for keyboard and mouse
- Works through the **ROG Omni receiver**, so both devices can share one dongle, and over the
  **USB cable**; a device on the cable is picked up automatically
- Menu bar item plus a popover with battery bars, connection and warning level
- **Low-battery notification:** one notification per device when it reaches the warning
  level configured on the device itself, reset after charging
- Optional launch at login
- **Mouse settings window** (Keris II Origin, coming in 0.3): shows DPI stages, polling rate,
  sensor and power settings, and changes the three lights (logo, scroll wheel, side: effect,
  brightness and colour, optionally the same on all three), the auto power-off time, the
  low-battery warning level, angle snapping and angle tuning
- **Careful with your devices:** the battery display only reads. Squire writes only when you
  click **Apply** in the settings window, only commands on a fixed allowlist (light zone,
  power-off and warning, angle snapping and tuning, save), reads every change back and saves it to the mouse only if it matched. It never touches
  firmware or switches profiles.
- Opens only vendor-defined HID collections, never the regular keyboard or mouse input
- macOS: no Python, Homebrew or SwiftBar needed. Linux: only packages that Ubuntu ships by default

## Status

Version 0.2.1. Tested on macOS with a Falchion RX Low Profile and a Keris II Origin.
Battery percentage and charging state are confirmed for both devices, connected through the
Omni receiver and over a USB cable.

Linux (new in 0.2.1) is tested on Ubuntu 26.04 with GNOME, through the Omni receiver. The USB
cable connection is not tested on Linux yet.

The warning level for the notification is read from the device's reply (20 % on both test
devices). It is not yet confirmed that this level follows changes made in Armoury Crate; if a
reply carries no plausible level, 20 % is used.

The settings window (in development for 0.3, not in a release yet) is tested with the Keris II
Origin on Linux through the Omni receiver and on macOS through the Omni receiver and the USB
cable: reading all settings, and changing and saving all three lights. Power-off time, warning
level, angle snapping and angle tuning are confirmed on the device through the Omni receiver.
Polling rate, surface (mouse pad preset) and lift-off distance can be changed too; DPI is shown
only so far.

Not tested yet:

- Other ROG keyboards and mice. They may work if they use the same protocol, but the
  device IDs are not included.
- Launch at login from a downloaded copy of the app.
- Linux: wired keyboard and mouse, desktops other than GNOME (KDE Plasma should work, it
  supports the same tray protocol).

## Installation

### macOS

Requires macOS 13 or newer (Apple silicon and Intel).

1. Download `Squire-<version>.zip` from the
   [latest release](https://github.com/phifogg/squire-gear/releases/latest) and unzip it.
2. Move `Squire.app` to `/Applications` and open it. If your account cannot write to
   `/Applications` (no admin rights), `~/Applications` works the same way.
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

Linux packages (.deb and .rpm from one [nfpm](https://nfpm.goreleaser.com) config,
`linux/packaging/nfpm.yaml`):

```sh
linux/build-packages.sh             # writes linux/dist/*.deb and *.rpm with .sha256 files
```

For a quick local build and run on macOS:

```sh
cd macos
xcodegen generate
xcodebuild -project Squire.xcodeproj -scheme Squire -configuration Debug \
  -derivedDataPath .build/xcode build
open .build/xcode/Build/Products/Debug/Squire.app
```

`swift run squire-probe` prints what IOKit sees and the parsed batteries without starting the
app; `swift run squire-probe --settings` prints the mouse settings (it only reads). `swift test` runs the core tests.

### Linux

Tested on Ubuntu 26.04 (GNOME). Download the package for your distribution from the
[latest release](https://github.com/phifogg/squire-gear/releases/latest) and install it:

```sh
sudo apt install ./squire-gear_<version>-1_all.deb       # Ubuntu, Debian
sudo dnf install ./squire-gear-<version>-1.noarch.rpm    # Fedora (not tested yet)
```

Then start **Squire** from the app grid. The package installs the dependencies, the commands
`squire` (command line) and `squire-tray` (top bar app) and a udev rule
(`/usr/lib/udev/rules.d/70-squire-gear.rules`) that lets the logged-in user read the vendor
interfaces of the Omni receiver and the wired devices without root. The keyboard and mouse input
interfaces stay untouched. On Fedora, GNOME also needs the
[AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) for
the top bar icon; Ubuntu enables it by default.

#### From source

Needs `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1` and `libnotify-bin`;
Ubuntu Desktop has them installed, and the installer tells you if one is missing.

```sh
git clone https://github.com/phifogg/squire-gear.git
cd squire-gear
./linux/install.sh
```

The installer copies Squire to `~/.local/share/squire-gear/`, adds a **Squire** launcher to the
app grid and the command `~/.local/bin/squire`, and starts the app. It asks before it uses `sudo`
for one thing: the udev rule `/etc/udev/rules.d/70-squire-gear.rules`, which lets the logged-in
user read the vendor interfaces of the Omni receiver and the wired devices without root. The
keyboard and mouse input interfaces stay untouched.

Squire shows one entry in the top bar, a small shield followed by both devices
(`⌨ 78%  🖱 80%`), like the macOS menu bar. Its menu has battery bars, connection, warning level, **Notify when low**, **Launch at login**,
**Refresh now** and **Quit**, plus a **Debug** submenu (notification service, last update, test
notification). It reads the devices every 60 seconds. On GNOME the top bar icons need the
AppIndicator extension, which Ubuntu enables by default.

On Linux Squire talks to `/dev/hidraw*` directly and needs no `hidapi`. It only opens a hidraw
node if every collection in its report descriptor is vendor-defined.

Use either the package or `install.sh`, not both.

To uninstall the package: `sudo apt remove squire-gear` (or `sudo dnf remove squire-gear`).
To uninstall the `install.sh` version: turn off **Launch at login**, quit Squire, then

```sh
rm -r ~/.local/share/squire-gear ~/.local/bin/squire ~/.local/share/applications/squire-gear.desktop
rm ~/.local/share/icons/hicolor/*/apps/squire-gear.png
sudo rm /etc/udev/rules.d/70-squire-gear.rules
```

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

Command line (on Linux: `~/.local/bin/squire` with the same options, no virtual environment):

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
| Linux: "No access: install the udev rule" | Run `./linux/install.sh` again and allow the udev rule, then re-plug the receiver |
| Linux: no icons in the top bar | GNOME needs the AppIndicator extension (`gnome-extensions list --enabled` should show `ubuntu-appindicators@ubuntu.com`) |
| Linux: app does not start from the VS Code snap terminal | Squire cleans the snap environment itself; if it still fails, start it from the app grid |

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
python3 -m unittest -v        # Python core and Linux tray app
cd macos && swift test        # Swift core, reads the same protocol.json
```

## Roadmap

- Device configuration on Linux and macOS (0.3): lights, power, sensor settings first, then DPI;
  0.2.x stays read-only
- Desktop widget (WidgetKit)
- Optional icon-only menu bar mode
- Notarized builds, depending on interest
- Linux: Waybar/Polybar module using `--json`, a PPA for automatic updates
- Other ROG devices (send `--list` / `--debug` output or `swift run squire-probe` output to help)

## Disclaimer

Not affiliated with or endorsed by ASUS. ROG, Falchion and Keris are trademarks of ASUSTeK
Computer Inc.

## License

[MIT](LICENSE)

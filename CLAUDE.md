# Squire (squire-gear) – notes for Claude Code

Squire is a lightweight companion for ASUS ROG peripherals. Version 0.2.0 is a native macOS menu bar app
(Swift) that shows battery level and charging state of the keyboard and mouse; the original Python script with
a SwiftBar plugin is kept as a testbed and alternative. 0.2.1 adds a Linux top bar app (Python, AppIndicator)
on the same Python core; primary Linux target is Ubuntu 26.04 with GNOME.
Public repo: https://github.com/phifogg/squire-gear (MIT, author Daniel Draes).
Private working notes (log, contacts, plans) live in `CLAUDE.local.md` (git-ignored, may be missing).

## Supported hardware
- Keyboard: ASUS ROG Falchion RX Low Profile (wired PID `0b05:1b04`)
- Mouse: ASUS ROG Keris II Origin (wired PID `0b05:1c0c`)
- Both also via the ROG Omni receiver `0b05:1ace`
- Confirmed on macOS: Omni and wired, both devices, Python and Swift. Linux (Ubuntu 26.04, GNOME 50): Omni,
  both devices; wired not tested yet.

## Layout
```
squire.py          Python core: device discovery, battery queries, CLI
                   (--json, --swiftbar, --notify, --list, --settings, --debug, --version)
                   backends: hidapi on macOS, /dev/hidraw directly on Linux (HidrawBackend, stdlib only)
test_squire.py     unit tests with a fake HID device, no hardware needed (imports squire as rb)
test_squire_tray.py  tests for the Linux tray logic (no GTK needed)
test_squire_settings.py  tests for the Linux settings window logic (no GTK needed)
protocol.json      real replies captured from hardware = shared test vectors (Python and Swift tests read it)
install.sh         on Linux hands over to linux/install.sh. macOS: venv + hidapi into ~/Library/Application Support/squire-gear, writes SwiftBar plugin
                   squire-gear.5m.sh (runs --swiftbar --notify) with xbar metadata
README.md          English, public
linux/             squire_settings.py (GTK 4 + libadwaita settings window, own process, opened from the tray's
                   "Settings…"; read-only so far; --demo shows protocol.json captures, --screenshot PATH [ZONE]
                   saves a PNG and quits), squire_tray.py (AppIndicator + GTK 3 top bar app: one indicator, shield icon from
                   linux/icons/ + text label "⌨︎ 78%  🖱︎ 80%" with U+FE0E so no colour emoji; same menu items
                   as the Mac popover; one indicator = one menu, so never split it per device), install.sh (to ~/.local/share/squire-gear, launcher, ~/.local/bin/
                   squire, asks before sudo for the udev rule), 70-squire-gear.rules (uaccess, vendor ifaces),
                   build-packages.sh + packaging/nfpm.yaml (ONE config for .deb and .rpm, arch all; /usr/share/
                   squire-gear, /usr/bin/squire + squire-tray wrappers, rule in /usr/lib/udev/rules.d)
macos/             Swift: SwiftPM package (Package.swift) + XcodeGen app project (project.yml)
  Sources/SquireCore   pure logic: targets/packets, reply parsing, low-battery rules (Swift Testing tests)
  Sources/SquireHID    IOKit HIDReader (vendor pages ff00-ff02 only)
  Sources/squire-probe CLI diagnostics, --repeat N stress test
  App/                 SwiftUI MenuBarExtra app (BatteryStore, Views), Assets
  scripts/             build-release.sh (universal, ad-hoc signed zip), make-icon.swift
```

## Commands
```sh
python3 -m unittest -v                         # Python tests, no hardware
cd macos && swift test                         # Swift core tests (macOS only)
cd macos && swift run squire-probe             # what IOKit sees + parsed batteries
./install.sh ~/SwiftBar                        # install / update the SwiftBar plugin (macOS)
APP="$HOME/Library/Application Support/squire-gear"
"$APP/venv/bin/python" "$APP/squire.py" --list   # sends nothing
"$APP/venv/bin/python" "$APP/squire.py" --debug  # read-only battery queries, raw replies
```
macOS: do not run `squire.py` with system python: it lacks `hidapi`. Use the venv.
Linux: system python3 is fine (no hidapi needed):
```sh
python3 squire.py --list / --debug             # from the repo
python3 linux/squire_tray.py --debug           # tray app from the repo, prints each read
python3 squire.py --settings [--json] [--debug] # mouse DPI / LEDs / sensor / power, read-only
python3 linux/squire_settings.py [--demo]       # settings window from the repo (--demo: no device access)
./linux/install.sh [--no-udev] [--no-start]    # install for the current user
```
On Linux only the Python part and its tests apply; `macos/` needs macOS + Xcode.

## Direction
- 0.2.x stays read-only. From 0.3 Squire also configures devices (DPI, LEDs, ...) on **Linux and macOS**,
  Linux first (owner's decision 2026-11-10). Helping falchion-light with PRs stays welcome, but Squire has its own
  configuration too; both use the same protocol facts.
- Linux settings window: a separate GTK 4 + libadwaita process (`squire-settings`), started from the tray menu.
  squire.device_lock() (flock in XDG_RUNTIME_DIR) keeps tray, window and CLI from interleaving requests.
  The tray is GTK 3 (AppIndicator) and GTK 3 and 4 cannot share a process. All three are on Ubuntu 26.04 by default.
- Settings UI (proposal agreed in direction): devices listed on the left; the selected device drawn in the middle
  (own neutral artwork) with clickable zones and callouts; settings for the selected zone on the right; an apply bar
  with "Show bytes", Revert and Apply. Keris II Origin LED zones: scroll wheel, logo, side (G-Helper calls the side
  zone "Underglow"; the mouse has no light underneath). The DPI button is on the underside: the top-view
  drawing has no DPI hotspot; DPI is a chip below it, like sensor and power.
- Writes only via an allowlist in SquireCore: command model with an `isWrite` flag, the transport refuses
  anything not on the allowlist, tests assert the exact bytes, `--dry-run` prints bytes without sending.
- Order of work: (1) read-only queries for current DPI / stages / LED state, (2) find the write bytes in public
  sources (G-Helper GPL facts only, falchion-light, rogdriver; check licenses), (3) confirm with Windows
  captures of Armoury Crate into protocol.json write vectors, (4) settings window in the app, writes only on
  an explicit click, never in the background. Show the owner the bytes before the first send of any new command.
- README claims "read-only": reword before the first release that writes.
- Icon is a shield with a blade (generated by `swift scripts/make-icon.swift` in `macos/`), neutral so it
  fits configuration too. No ASUS/ROG marks, fonts or artwork; similar colour mood only.

## Rules
- **Read-only by default.** Squire only sends commands on `ALLOWED_COMMANDS` in squire.py, and the transport
  refuses anything else: `12 01` (keyboard battery), `12 07` (mouse battery) and the mouse settings reads
  `12 00`, `12 04 00`, `12 04 02`, `12 04 03`, `12 03 00..02`, `12 06` (approved by the owner 2026-11-10).
  All are get requests (first byte `12`); writes start with `50`/`51`. Never send `50 02` (switch profile):
  read the active profile only. Any new command, read or write, must be shown to the owner before it is sent,
  and only once the bytes are known from a source or a capture.
- Only open vendor-defined HID collections (usage page >= 0xFF00). Never open the normal
  keyboard/mouse input collections. Linux: a hidraw node is a whole interface, so it is opened only if
  *every* top-level collection in its report descriptor is vendor-defined (Omni iface 3 mixes them: skipped).
- Linux: do not use Ubuntu's `python3-hid`/hidapi: it is the libusb backend (no usage pages, detaches
  kernel drivers). Use HidrawBackend.
- Linux GUI: the VS Code snap injects GSETTINGS_SCHEMA_DIR etc., which makes GTK 3 abort;
  `unsnap_environment()` restores the session values before GTK loads. Keep it.
- hidapi: `read(n, 0)` blocks forever. Always pass a timeout > 0 (tests assert this).
- IOKit (Swift): never reuse an IOHIDDevice for more than one query. Report callbacks live in a per-device
  set and unregistering (nil callback, with or without the context) does not remove them, so a reused object
  calls freed contexts: use-after-free crashes. HIDReader creates a fresh device from the service
  per query (like hidapi), never opens the manager, and frees box/buffer only after that object is released.
  Regression check: `swift run squire-probe --repeat 60` (reads on a GCD thread like the app) must not crash.
- `ff aa` is provisional: the receiver sometimes sends it right before the real reply. Only "asleep" if no
  valid reply arrives within the timeout (Python and Swift, tests cover it).
- G-Helper is GPL-3.0: use protocol facts only, never copy code. Cite the source in the README.
- Every new real capture goes into `protocol.json` with `expect` values and notes; tests read it.
- Keep README, code, commits and issues in English. Menu text and installer output are English too.
- Versions: `__version__` in `squire.py` and `MARKETING_VERSION` in `macos/project.yml` stay equal;
  install.sh picks up the Python one for the plugin metadata.
- `.pcapng` captures stay out of the repo (may contain keystrokes); `.gitignore` covers them.
- Commit, push, tag and release only when the owner asks.

## Protocol (confirmed on macOS)
macOS/hidapi lists interface 2 of the Omni receiver as three collections (usage pages ff00, ff01,
ff02) with the same path. Write 64 bytes: report ID + command, zero-padded. Replies include
the report ID at [0]. Indices below are relative to the echoed command (no report ID).

| Device | Report ID | Query | Reply |
|---|---|---|---|
| Keyboard | `0x02` | `12 01` | [5] battery %, [10] battery %, [8] charging (01), [6] sleep step?, [9] low-battery warning % (0x14 = 20), [11..12] LE mV? (00 00 while charging) |
| Mouse | `0x03` | `12 07` | [4] battery %, [5] auto power-off step, [6] low-battery warning %, [7..8] LE mV? (rises while charging), [9] charging (01) |

Error reply `ff aa` = asleep / unsupported (also on the Omni path while the device is on the cable).
0 % and not charging = treat as asleep.
Wired keyboard: write `00 12 01` as 65 bytes to pid `1b04` interface 1 (ff00); the reply has no report ID
(echo at [0]), same layout as Omni, plus [11..12] = LE mV (~4187 while charging).
Wired mouse: write `00 12 07` as 65 bytes (the 64-byte fallback is not needed but harmless) to pid `1c0c`;
the vendor collection is interface 0, usage page ff01. Reply has no report ID, same layout as Omni;
[7..8] = LE mV (~4167 while charging).
IOKit: unnumbered report = report ID 0 with the leading 00 dropped (64-byte payload).
Mouse settings reads (Keris II Origin, report ID 03 on the Omni; layout from libratbag SETTINGS_V2/LED_V2 and
G-Helper, confirmed with captures in protocol.json `mouse_settings`; indices relative to the echo):
`12 00` [10] profile (0-based), [11] active DPI stage (1-based), [18] stage count, [13..15] firmware.
`12 04 00` [4..11] DPI (ff ff on this separate-X/Y mouse), [12] polling (low 3 bits: 125/250/500/1000), [14]
debounce (n*4+4 ms), [16] angle snapping, [18..19] angle tuning (signed LE16). `12 04 02` X/Y per stage from [4]
(lo/hi each, DPI = v*50+50). `12 04 03` RGB per stage from [4]. `12 03 NN` zone NN (0 logo, 1 wheel, 2 side):
[4] mode (0 static, 1 breathing, 2 cycle, 3 rainbow, 4 react, 5 comet, 6 battery, f0 off), [5] brightness %,
[6..8] RGB. `12 06` [7] lift-off (0 low, 1 high). `12 07` also: [5] power-off (0..4 = 1/2/3/5/10 min, ff never).
Linux hidraw: the Omni's interface 2 is one node with three collections (report IDs 1-3, pages
ff02/ff00/ff01); same writes and replies as hidapi on macOS (report ID at [0], 0x00 prefix for unnumbered).
Sources: G-Helper `app/Peripherals/Keyboard/AsusKeyboard.cs`, `Models/Falchion.cs`,
`app/Peripherals/Mouse/AsusMouse.cs`, `Models/KerisIIOrigin.cs`.

## Roadmap
- [x] English menu text + installer output (0.1.1)
- [x] Low-battery notification (0.1.2, synced to the device warning level; unconfirmed whether the byte
      follows changes made in Armoury Crate)
- [x] Wired mode for keyboard and mouse
- [x] Native macOS menu bar app (0.2.0): MenuBarExtra, keyboard + mouse, notifications, launch at login,
      hidden debug section (hold Option in the popover)
- [ ] Widget (WidgetKit): shares values with the app through an App Group; the widget must not do USB itself.
      Needs a development team for the App Group.
- [ ] Backlog (Swift app): About screen (version + build from the bundle, GitHub link, MIT license,
      credits/disclaimer: G-Helper protocol facts, not affiliated with ASUS); reachable from the popover.
      Maybe later a "check for updates" against the GitHub releases.
- [ ] Backlog (Swift app): battery history log (CSV in Application Support: time, percent, charging, mV per
      poll), to check whether the percentage steps down while the voltage drifts; later "time remaining".
      Observed: the keyboard stayed at 78 % for a day while mV went ~4088 -> ~4080.
- [ ] Backlog (Swift app): optional icon-only menu bar mode (a single Squire icon instead of one entry
      per device; setting in the popover, maybe tint/badge when a device is low)
- [ ] Device configuration (0.3), Linux first, then macOS; see Direction. First step: branch `mouse-read`
      (read current DPI / LED / sensor settings of the Keris II Origin)
- [x] Linux top bar app (0.2.1, `linux/`): hidraw backend, AppIndicator tray, udev rule, installer
- [ ] Settings window polish (collect small UI findings here, work through them before or with the write step):
  - [x] DPI button is on the underside: no hotspot on the top view, DPI is a chip (2026-11-10)
  - [x] No colour row for multi-colour effects (cycle, rainbow, battery) (2026-11-10)
  - [x] Logo zone drawn as the Squire shield in the LED colour, with glow; dark imprint when off (2026-11-10)
  - [ ] Design proposal artifact still shows the DPI button on top (the app is the reference now)
  - [ ] Polling rate: confirm 1000 vs 8000 Hz in Armoury Crate (raw 0x63), then drop the "not confirmed" note
- [ ] Linux: test wired keyboard + mouse (does the Keris iface 0 node mix input and vendor collections? then it
      is skipped by design), KDE Plasma, Waybar/Polybar module using `--json`, maybe a .deb
- [ ] More devices: collect `--list` / `--debug` / `squire-probe` output from users

## Related projects
- falchion-light (Linux lighting, wired): https://github.com/ChipGillespie/falchion-light.
  PRs there are welcome alongside Squire's own configuration.
- G-Helper (GPL-3.0, Windows): https://github.com/seerge/g-helper
- rogdriver (Zig, Linux): https://github.com/rahulrajaram/rogdriver
- SwiftBar: https://github.com/swiftbar/SwiftBar

## Release
Automated with GitHub Actions (`.github/workflows/release.yml`): pushing a tag `vX.Y.Z` tests and builds on a
macOS and a Linux runner and creates a **draft** release with all six files (Mac zip, .deb, .rpm, each + .sha256).
The owner fills in "What's new" in the draft and publishes it. Do not publish the draft yourself unless asked.
```sh
# bump __version__ in squire.py and MARKETING_VERSION in macos/project.yml (the workflow refuses a mismatch), merge, then:
git tag -a vX.Y.Z -m "Squire X.Y.Z" && git push origin vX.Y.Z
```
- Release notes template: `.github/release-notes.md` (`{{VERSION}}` is replaced). Re-running the workflow for an
  existing release replaces its files.
- PRs that touch the build files run the same builds without a release (files under the run's Artifacts).
- `.github/workflows/ci.yml`: Python tests (Linux + macOS) and `swift test` on every push and PR.
- nfpm is pinned in release.yml (version + sha256 of the .deb); update both together.
- Local builds still work: `macos/scripts/build-release.sh` -> `macos/dist/`, `linux/build-packages.sh` -> `linux/dist/`.
- The app is not notarized (ad hoc signed, no Developer ID). Unverified: launch at login from a downloaded copy
  after logout/restart. rpm not tested on Fedora yet.

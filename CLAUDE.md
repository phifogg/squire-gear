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
  Sources/SquireCore   pure logic: targets, command allowlist + packets (Commands.swift), reply parsing,
                       mouse settings parsing (MouseSettings.swift), settings window logic (SettingsEditing.swift),
                       low-battery rules (Swift Testing tests, read protocol.json)
  Sources/SquireHID    IOKit HIDReader (vendor pages ff00-ff02 only): batteries, readMouseSettings, setLED;
                       DeviceLock = same flock file as squire.device_lock
  Sources/squire-probe CLI diagnostics, --repeat N stress test, --settings (mouse settings, read-only)
  App/                 SwiftUI MenuBarExtra app (BatteryStore, Views), settings window (SettingsWindow,
                       SettingsStore, MouseDrawing; same layout as the Linux window), Assets
  scripts/             build-release.sh (universal, ad-hoc signed zip), make-icon.swift
```

## Commands
```sh
python3 -m unittest -v                         # Python tests, no hardware
cd macos && swift test                         # Swift core tests (macOS only)
cd macos && swift run squire-probe             # what IOKit sees + parsed batteries
cd macos && swift run squire-probe --settings  # mouse DPI / LEDs / sensor / power, read-only
Squire.app/Contents/MacOS/Squire --demo --settings          # settings window with protocol.json captures, sends nothing
Squire.app/Contents/MacOS/Squire --demo --screenshot PATH [ZONE] [edit] [linked]   # saves a PNG and quits (sidebar stays blank;
                                               # `screencapture -o -l<window id>` shows it, needs Screen Recording)
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
- macOS settings window: same layout and behaviour as the Linux one (SwiftUI in an AppKit-managed NSWindow, so it
  never opens at launch), opened with "Settings…" in the popover. Same rules: Apply writes each changed zone, reads
  it back, saves (50 03) once with the last one and only if all matched. protocol.json is bundled for --demo.
  Reads and LED Apply (all three zones) confirmed on macOS via Omni (owner, 2026-11-11) and over the cable
  (2026-11-12). The colour row is an
  NSColorWell wrapper (LEDColorWell), not SwiftUI's ColorPicker: that one wrote each edit back as clamped sRGB,
  so the panel's cursor jumped and parts of the wheel seemed unreachable.
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
- README reworded for writes (2026-11-12): battery display reads only, settings window writes on Apply (0.3, unreleased).
- Icon is a shield with a blade (generated by `swift scripts/make-icon.swift` in `macos/`), neutral so it
  fits configuration too. No ASUS/ROG marks, fonts or artwork; similar colour mood only.

## Rules
- **Read-only by default.** Squire only sends commands on `ALLOWED_COMMANDS` in squire.py, and the transport
  refuses anything else: `12 01` (keyboard battery), `12 07` (mouse battery) and the mouse settings reads
  `12 00`, `12 04 00`, `12 04 02`, `12 04 03`, `12 03 00..02`, `12 06` (approved by the owner 2026-11-10).
  All are get requests (first byte `12`); writes start with `50`/`51`. Never send `50 02` (switch profile):
  read the active profile only.
- **Writes** go through `packet(..., write=True)` and must pass `is_allowed_write()`. Approved and confirmed so far
  (2026-11-10): `51 28 <zone> 00 <mode> <brightness %> <r g b> 00 00 00` (set one LED zone; modes 00 static,
  01 breathing, 02 cycle, 04 react, 06 battery, f0 off), and `50 03` (save to flash, approved 2026-11-10) only
  with an explicit --save / Apply click. set_led() checks the mouse is awake, reads the zone before, writes, reads back, and saves only on a match.
  Confirmed: an unsaved change (no 50 03) is lost when the mouse is switched off and on; it lives in RAM only.
  Confirmed: with 50 03 the change survives off/on (owner, settings window, logo blue, 2026-11-10).
  No LED speed: byte [11] is stored but ignored by the Keris (breathing 3 pulses/10 s at 05 and 09); not sent.
  Wheel (zone 01) and side (zone 02) use the same command; confirmed on the device with the macOS window
  (owner, all three zones, 2026-11-11). Apply writes all changed zones and saves once, only if all matched.
  Approved 2026-11-12 (bytes from G-Helper AsusMouse.cs + Models/KerisIIOrigin.cs; confirmed on the device via
  Omni with the CLI, unsaved, original values written back; protocol.json `mouse_writes.settings_writes`):
  `51 37 00 00 <power-off> 00 <warning %>` (power-off 00..04 = 1/2/3/5/10 min, ff never; warning 10..50 step 10;
  always both, read back with 12 07 [5],[6]), `51 31 06 00 <0/1>` (angle snapping, read back 12 04 00 [16]),
  `51 31 0b 00 <lo> <hi>` (angle tuning, signed LE16, -20..20 step 5, read back 12 04 00 [18..19]). One generic
  path: squire.write_verified() / HIDReader.write(SettingChange).
  Approved 2026-11-13 (bytes from the Armoury Crate captures, `mouse_writes.armoury_crate`): lift-off
  `51 35 00 00 <surface id> <0/1> 00 <p1 p2 p3>` with surface id + params taken from a 12 06 read made right before
  the write in the same device session (never from a window's older read), or `51 35 ff 00 ff <0/1>` only when that
  read shows no surface (ff). Only surface ids from Armoury Crate's preset list (squire.SURFACES); an unknown id is
  refused (could be a custom calibration). The read-back must show the new lift-off AND the same surface + params.
  Also approved 2026-11-13: surface preset `51 35 00 00 <id> <lift-off as read> 00 <AC params for id>` (or the ff
  form for none; params from squire.SURFACE_PARAMS; refused while the mouse has an unknown surface; read-back must
  show the new surface and the unchanged lift-off) and polling `51 31 04 00 <0..3>` (125/250/500/1000 Hz, index only,
  read back 12 04 00 [12] low 3 bits). Also approved 2026-11-13 (AC capture + G-Helper KerisIIOrigin: 100..42000
  step 50, 4 stages): DPI `51 31 <stage 0-based> 00 <lo> <hi> <r g b>` (value (DPI-50)/50, X and Y alike + the
  stage colour; colour kept from a 12 04 03 read unless the owner picks one; refused for a stage with X != Y; read
  back 12 04 02 + 12 04 03), active stage `51 31 09 00 <1..count>` and stage count `51 31 0a 00 <1..4>` (read back
  12 00 [11] / [18]; never an active stage beyond the count, never a count below the active stage). Squire writes
  only the changed field, never the whole block like Armoury Crate. Not approved: debounce
  `51 31 05` (G-Helper offers none for the Keris), DPI `51 31 <stage>` (X/Y unclear), stage count `51 31 0a`,
  active stage `51 31 09`. Any new command, read or write, must be shown to the owner before it is sent,
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
`12 00` [10] profile (0-based), [11] active DPI stage (1-based), [18] stage count; firmware 3 bytes build/minor/major:
mouse at [4..6] over the cable, via the Omni [4..6] = receiver and the mouse at [14..16] (libratbag's [13..15] does
not fit the Keris).
`12 04 00` [4..11] DPI (ff ff on this separate-X/Y mouse), [12] polling (low 3 bits: 125/250/500/1000), [14]
debounce (n*4+4 ms), [16] angle snapping, [18..19] angle tuning (signed LE16). `12 04 02` X/Y per stage from [4]
(lo/hi each, DPI = v*50+50). `12 04 03` RGB per stage from [4]. `12 03 NN` zone NN (0 logo, 1 wheel, 2 side):
[4] mode (0 static, 1 breathing, 2 cycle, 3 rainbow, 4 react, 5 comet, 6 battery, f0 off), [5] brightness %,
[6..8] RGB. `12 06` [6] surface id (ff = none selected), [7] lift-off (0 low, 1 high), [8..10] surface parameters. `12 07` also: [5] power-off (0..4 = 1/2/3/5/10 min, ff never).
Linux hidraw: the Omni's interface 2 is one node with three collections (report IDs 1-3, pages
ff02/ff00/ff01); same writes and replies as hidapi on macOS (report ID at [0], 0x00 prefix for unnumbered).
Sources: G-Helper `app/Peripherals/Keyboard/AsusKeyboard.cs`, `Models/Falchion.cs`,
`app/Peripherals/Mouse/AsusMouse.cs`, `Models/KerisIIOrigin.cs`.

## Roadmap
- [x] English menu text + installer output (0.1.1)
- [x] Low-battery notification (0.1.2, synced to the device warning level; the mouse byte follows
      Armoury Crate: 20 -> 30 % read back as 0x1e, owner, Windows, 2026-11-12)
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
  - [x] macOS settings window, same as Linux (branch `macos-settings-window`): reads, LED edit + Apply
  - [x] macOS reads confirmed on the device via Omni (`squire-probe --settings`, 2026-11-11); `--repeat 30` no crash
  - [x] macOS LED Apply confirmed on the device, all three zones via Omni (2026-11-11)
  - [x] macOS: reads + LED Apply over the cable (pid 1c0c, iface 0; change survives off/on, 2026-11-12)
  - [x] Power-off + low-battery warning, angle snapping + tuning: CLI (--set-power, --set-angle-snapping,
        --set-angle-tuning) and both windows (Power / Sensor chips), branch `mouse-settings-writes` (2026-11-12).
        CLI writes confirmed on the device via Omni; Apply in the macOS window confirmed incl. off/on and an LED
        change through the new write path (owner, 2026-11-12); Linux window not tried yet
  - [x] Lift-off write, keeping the surface (2026-11-13): CLI `--set-liftoff low|high` (--dry-run reads 12 06 and
        prints the exact bytes), both windows (Sensor chip: Low/High picker + Surface row; shown only for an unknown
        surface). CLI write confirmed on the device via Omni (high, unsaved, surface ROG Sheath kept, then back to
        low; same bytes as AC; protocol.json settings_writes). Apply in the macOS window confirmed (owner,
        2026-11-13); Linux window not tried yet.
        Reads with firmware 01.00.10 / 07.00.08 parse fine on macOS (2026-11-13)
  - [x] Polling rate + surface preset writes (2026-11-13): CLI `--set-polling HZ`, `--set-surface ID|none`,
        `--list-surfaces`; both windows (Sensor chip pickers; surface written before lift-off when both change).
        CLI writes confirmed on the device via Omni (500 Hz and back, Balteus Qi and back to ROG Sheath with lift-off
        kept; unsaved; protocol.json settings_writes). Apply in the macOS window confirmed (owner, 2026-11-13);
        Linux window not tried yet
  - [x] DPI per stage (value + colour), active stage, stage count (2026-11-13): CLI `--set-dpi STAGE DPI [--color]`,
        `--set-dpi-stage N`, `--set-dpi-count N`; both windows (DPI chip: value + colour per stage, active stage and
        count pickers that keep each other consistent; planned bytes simulate the sequence). Dry runs on macOS via
        Omni match AC's format. CLI writes confirmed on the device via Omni (1650 DPI, colour #ffaa00, stage 2,
        count 3, each unsaved and restored; protocol.json settings_writes). Not yet: window Apply, Linux window
  - [x] Armoury Crate capture on Windows (USBPcap, 2026-11-12, protocol.json `mouse_writes.armoury_crate`): one
        Apply rewrites the whole sensor block from AC's cached state (tuning, snapping, polling 51 31 04 = index,
        stage count, active stage, 51 31 <stage> 00 <lo> <hi> <r g b> = DPI X=Y + stage colour), then 50 03.
        It silently reset angle snapping changed by Squire. Firmware now mouse 01.00.10, receiver 07.00.08
        (updated by the owner in AC); reads parse the same on Windows/hidapi, recheck on macOS and Linux
  - [x] Lift-off + surface captured (2026-11-12): `51 35 00 00 <surface id> <lift-off> 00 <3 surface params>`;
        12 06 reads [6] surface id (ff = none), [7] lift-off, [8..10] params. All 21 AC presets captured (ids 00,01,07,08,0a..19 +
        ff "Default (No calibration)"; table in protocol.json): 01 ROG Sheath, 0b Balteus Qi, most share params
        1c 34 16. G-Helper's `51 35 ff 00 ff <0/1>` drops the surface. A Squire
        lift-off write reads 12 06 and keeps surface + params (approved 2026-11-13). AC resets lift-off to low
        on a surface change. Armoury Crate has no debounce setting for the Keris.
  - [x] Firmware from `12 00` fixed (2026-11-12): mouse 01.00.09 (cable [4..6], Omni [14..16]), receiver 07.00.02
        (Omni [4..6]); cable reply in protocol.json `mouse_settings_wired`
- [x] Linux top bar app (0.2.1, `linux/`): hidraw backend, AppIndicator tray, udev rule, installer
- [ ] Settings window polish (collect small UI findings here, work through them before or with the write step):
  - [x] DPI button is on the underside: no hotspot on the top view, DPI is a chip (2026-11-10)
  - [x] No colour row for multi-colour effects (cycle, rainbow, battery) (2026-11-10)
  - [x] Logo zone drawn as the Squire shield in the LED colour, with glow; dark imprint when off (2026-11-10)
  - [x] Connection, profile, firmware only in the Device group, not again under the title (2026-11-10)
  - [ ] Design proposal artifact still shows the DPI button on top (the app is the reference now)
  - [x] "Same effect on all zones" switch in the LED editor (Linux + macOS, 2026-11-12): turning it on copies the
        selected zone to the others, then every edit sets all three; Apply writes only the zones that differ from
        the mouse and saves once; Revert turns it off; disabled for an effect Squire cannot write.
        Confirmed on the device with the macOS window (owner, 2026-11-12); Linux window not tried yet
  - [x] Polling rate: Armoury Crate shows 1000 Hz for raw 0x63 (low 3 bits), so the high nibble is not a rate;
        "not confirmed" note dropped (owner, 2026-11-12)
  - [x] LED speed: Armoury Crate has no speed control for the Keris (owner, 2026-11-12); none in Squire either
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
- PRs that touch the build files run the same builds without a release (files under the run's Artifacts, kept
  7 days). Their file names carry `+pr<N>.<YYYYMMDD>.<sha7>` (BUILD_SUFFIX) so they are not mixed up with releases.
- `.github/workflows/ci.yml`: Python tests (Linux + macOS) and `swift test` on every push and PR.
- nfpm is pinned in release.yml (version + sha256 of the .deb); update both together.
- Local builds still work: `macos/scripts/build-release.sh` -> `macos/dist/`, `linux/build-packages.sh` -> `linux/dist/`.
- The app is not notarized (ad hoc signed, no Developer ID). Unverified: launch at login from a downloaded copy
  after logout/restart. rpm not tested on Fedora yet.

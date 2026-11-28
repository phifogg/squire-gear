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
  both devices; wired keyboard and mouse confirmed (2026-11-24). Bluetooth (battery level only): keyboard `0b05:1b08`,
  mouse `0b05:1c0f`, both on macOS (2026-11-26) and Linux (2026-11-27).

## Layout
```
squire.py          Python core: device discovery, battery queries, CLI
                   (--json, --swiftbar, --notify, --list, --settings, --debug, --version)
                   backends: hidapi on macOS, /dev/hidraw directly on Linux (HidrawBackend, stdlib only)
test_squire.py     unit tests with a fake HID device, no hardware needed (imports squire as rb)
test_squire_tray.py  tests for the Linux tray logic (no GTK needed)
test_squire_settings.py  tests for the Linux settings window logic (no GTK needed)
protocol.json      real replies captured from hardware = shared test vectors (Python and Swift tests read it)
devices.json       device models (ids, routes, commands + confirmation status, capability parameters); squire.py
                   derives its tables from it, the Swift tests hold SquireCore's tables to it (DeviceModels.swift)
squirelib/         registry.py: loads devices.json and checks it (stdlib only; `python3 -m squirelib.registry`);
                   transport/ (hidraw.py, lock.py = device_lock, bluez.py = busctl; load_backend, enumerate_paths,
                   timeouts), protocol.py (frame, echo search, ff aa, exchange), routes.py (Target per route from
                   devices.json, connection labels, best_reading = Bluetooth merge), state.py (state_dir),
                   capabilities/ (one module per devices.json capability: builders, byte check, reply parser,
                   read-back Change; DeviceCapabilities = a device's allowlist, squire.CAPABILITIES[kind]);
                   squire.py re-exports every moved name. Installers copy squirelib/ (with subpackages, `find`) +
                   devices.json next to squire.py; nfpm.yaml lists each file (a test checks it)
test_devices.py    checker + model tests (broken files refused, unconfirmed commands never sent, installers copy it)
install.sh         on Linux hands over to linux/install.sh. macOS: venv + hidapi into ~/Library/Application Support/squire-gear, writes SwiftBar plugin
                   squire-gear.5m.sh (runs --swiftbar --notify) with xbar metadata
README.md          English, public
linux/             squire_settings.py (GTK 4 + libadwaita settings window, own process, opened from the tray's
                   "Settings…"; mouse + keyboard editing with Apply; --demo shows protocol.json captures, --screenshot PATH [ZONE]
                   saves a PNG and quits), squire_tray.py (AppIndicator + GTK 3 top bar app: one indicator, shield icon from
                   linux/icons/ + text label "⌨︎ 78%  🖱︎ 80%" with U+FE0E so no colour emoji; same menu items
                   as the Mac popover; one indicator = one menu, so never split it per device), install.sh (to ~/.local/share/squire-gear, launcher, ~/.local/bin/
                   squire, asks before sudo for the udev rule), 70-squire-gear.rules (uaccess, vendor ifaces),
                   build-packages.sh + packaging/nfpm.yaml (ONE config for .deb and .rpm, arch all; /usr/share/
                   squire-gear, /usr/bin/squire + squire-tray wrappers, rule in /usr/lib/udev/rules.d)
macos/             Swift: SwiftPM package (Package.swift) + XcodeGen app project (project.yml)
  Sources/SquireCore   pure logic: targets, command allowlist + packets (Commands.swift), reply parsing,
                       mouse settings parsing (MouseSettings.swift), settings window logic (SettingsEditing.swift),
                       keyboard writes + allowlist + remembered lighting (KeyboardWrites.swift), keyboard page logic
                       (KeyboardEditing.swift),
                       low-battery rules (Swift Testing tests, read protocol.json)
  Sources/SquireHID    IOKit HIDReader (vendor pages ff00-ff02 only): batteries, readMouseSettings, readKeyboardSettings, write,
                       writeKeyboard; BluetoothReader (CoreBluetooth, GATT 180F/2A19 battery level only);
                       DeviceLock = same flock file as squire.device_lock
  Sources/squire-probe CLI diagnostics, --repeat N stress test, --settings / --keyboard-settings (read-only),
                       --bluetooth (battery level over BLE; Info.plist embedded via -sectcreate, CoreBluetooth needs it)
  App/                 SwiftUI MenuBarExtra app (BatteryStore, Views), settings window (SettingsWindow,
                       SettingsStore, MouseDrawing, KeyboardDrawing; same layout as the Linux window), Assets
  scripts/             build-release.sh (universal, ad-hoc signed zip), make-icon.swift
```

## Commands
```sh
python3 -m unittest -v                         # Python tests, no hardware
cd macos && swift test                         # Swift core tests (macOS only)
cd macos && swift run squire-probe             # what IOKit sees + parsed batteries
cd macos && swift run squire-probe --settings  # mouse DPI / LEDs / sensor / power, read-only
cd macos && swift run squire-probe --bluetooth # battery level of devices paired over Bluetooth (GATT 2A19 only)
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
python3 squire.py --keyboard-settings [--json]   # keyboard profile / layout / lighting effect, read-only
python3 linux/squire_settings.py [--demo]       # settings window from the repo (--demo: no device access)
./linux/install.sh [--no-udev] [--no-start]    # install for the current user
```
On Linux only the Python part and its tests apply; `macos/` needs macOS + Xcode.

## Architecture (agreed with the owner 2026-11-26; stage 1 merged 2026-11-27 (PR #13), stage 2 on `refactor-transport` (PR #14), checked on Linux + Mac)
Stage 3 (branch `refactor-capabilities`, Python, 2026-11-28, not committed yet): squirelib/capabilities/ has one class per
devices.json capability (battery, led_zones, dpi, polling, sensor, surface, power, kb_profile, kb_layout, kb_lighting,
kb_sleep, kb_warning, per_key), each built from the Device with its params. A capability holds its write builders
(asserting the whole device allowlist), `allows()` = the byte check for the writes whose prefix it owns (longest
prefix wins: 51 31 04 polling, 51 31 06/0b sensor, 51 31 00..03/09/0a dpi), `parse()` for its part of the read
replies, `reply_expected()` (per_key c0 82) and `change(...)` -> Change(command | chunks | build(before), reads,
extract, matches) = the read-back check. DeviceCapabilities: is_allowed_write = confirmed prefix + owning capability's
allows(); save_command from the capability with "save". squire.py keeps every old name (constants and functions point
into the capabilities), the settings dataclasses, the mouse profile/firmware parse (no capability in devices.json),
packet(), write_verified() (the session; set_* = _apply(cap.change(...))), key presets and keyboard-lighting.json.
Checked: 258 tests green; a behaviour snapshot (96k allowlist decisions on protocol.json bytes + mutations, every
builder incl. error messages, every parse of the captures) is identical to main; on the device (Omni, read-only) --json,
--settings, --keyboard-settings, --list and three --dry-runs print the same as main; --demo window renders.
Next in stage 3: move write_verified / the read sessions out of squire.py (squirelib/session.py), mouse profile +
firmware offsets into devices.json; then the Swift side (SquireCore capability types, CI builds it).
Stage 2 state (Python, 2026-11-27): transport / route / protocol split into squirelib (see Layout).
protocol.exchange() writes only what its `build` callable returns; squire.exchange() passes squire.packet() (the
allowlist), so the allowlist, reply_expected, battery parsing, the per-device read/write sessions and
write_verified stay in squire.py until stage 3 (capabilities). Target got `connection` + `interface` (no more
`pid == PID_OMNI` checks). Tests: 256 green, incl. a layer test (transport + protocol import without the registry).
Checked on the device (Omni, read-only): --json, --settings, --keyboard-settings, --list and six --dry-run writes
print the same as main. Owner installed the .deb on Ubuntu 26.04: tray + window work.
Stage 2 Swift (same PR #14, written on Linux without a compiler; CI builds swift test + the app): SquireCore
Routes.swift (Target), Protocol.swift (SquireProtocol.frame = the one framing, used by Target.packet and
Packet.build; ReplyParser, Exchange); SquireHID IOKitTransport.swift (listDevices, candidates, request/transact with
the fresh-IOHIDDevice rule; gets finished packets + a `done` check, knows no commands), DeviceLock.swift, and
HIDReader.swift = sessions only (builds every packet via Packet.build). No SwiftPM target split (pragmatic scope).
Checked on the Mac (2026-11-27, Omni, read-only): swift test (118 tests in 20 suites) green, `squire-probe`, `--settings`,
`--keyboard-settings` parse as before, `--repeat 60` no crash; the app (menu bar, popover, settings window reads)
confirmed by the owner. Next: merge #14, then stage 3 (capabilities).
Stage 1 state: devices.json + squirelib/registry.py (checker) exist; squire.py reads VID/PIDs, TARGETS, interfaces,
report IDs, the read allowlist (confirmed reads only), confirmed write prefixes (is_allowed_write requires one),
battery offsets (wired mouse now filtered to interface 0 like the others; confirmed on the Mac 2026-11-27:
battery + every --settings read over the cable, keyboard via Omni) and the capability tables (LED zones/modes, DPI, polling, power, surfaces, keyboard effects/speeds/
gradients/directions, layouts, key ids, sleep/warning) from it; all names stay as module constants. Still hard-coded:
mouse settings reply offsets (parse_mouse_settings), packet builders and byte checks (stage 3). Swift: DeviceModels.swift
decodes strictly and tests compare every SquireCore table to the file; the app does not load it at runtime yet.
Where we are (2026-11-27): stage 1 merged (PR #13, 7862be0) after the owner's Linux test (local .deb of c325c95 on
Ubuntu 26.04: tray + settings window work). Owner's decision: merge each stage via PR, no release until the whole
refactoring is done. Test each stage like stage 1: build the .deb (`linux/build-packages.sh deb`, or the PR's `linux`
artifact), check /usr/share/squire-gear has every new file, run squire --version / --list / --debug / --settings /
--keyboard-settings (read-only), tray + Settings… window with both devices. Stage 2 (PR #14) checked on Linux and
the Mac; NEXT: merge #14, then stage 3.
Stage 1b (Swift runtime loading) postponed by the owner, Linux first: copy devices.json to
macos/Sources/SquireCore/Resources/ as a SwiftPM resource (Bundle.module; SwiftPM cannot reach ../devices.json, a
symlink is unreliable), a test that the copy equals the root file, then build Target.all / Allowlist / Command tables
from the loaded model with a before/after comparison like the Python one. Needs a Mac build to confirm.
Goal: support more devices without touching code everywhere. Today device knowledge (PIDs, report IDs, zones,
ranges, effect tables) is spread as constants, "keyboard"/"mouse" are hard-wired (`ALLOWED_READS[device]`,
`Battery.Device`), `Target` mixes device and connection, Omni and Bluetooth are special cases, and both settings
windows are one big file each.
Layers (same in Python and Swift):
1. **Transport**: moves bytes and lists devices, knows nothing about ASUS: hidraw, hidapi, IOKit, BLE GATT
   (CoreBluetooth / BlueZ), device_lock. The "vendor collections only" and timeout rules live here.
2. **Route**: how one device is reached: transport, PID, interface / usage page, report ID, packet length. The Omni is
   a receiver several routes go through (report IDs 02 / 03). A device has several routes with a fixed priority
   (cable, Omni, Bluetooth); `Bluetooth.merge` becomes the general "best available route" rule.
3. **Protocol**: the ASUS v2 framing once for all devices: echo search, provisional `ff aa`, exchange,
   write_verified, save only on a match. Later maybe a second framing for BLE FFF0. Must handle the three patterns
   today's devices already need: multi-chunk writes (`51 2c` gradients / quicksand, each chunk echoed before the
   next), non-echo acknowledgements (`reply_expected`: `c0 81` -> `c0 82`), and reads a write depends on, made in the
   same device session right before it (lift-off / surface read `12 06`, DPI reads `12 04 03` for the stage colour).
4. **Model = data** (owner: data-driven): `devices.json` at the repo root, read by Python and Swift like
   protocol.json: id, name, kind, routes, capabilities with parameters (battery offsets, LED zones + modes, DPI
   range/stages, polling rates, effect speed tables, key layouts), and per command a confirmation status. Without
   "confirmed" Squire only does --dry-run, so "show the bytes before the first send" stays enforced for new models.
   The model id (`keris-ii-origin`) is not `kind` (`mouse` / `keyboard`): state dicts and the UI selection key on the
   id; `kind` is only for icons, symbols and wording, so two mice can coexist (today ~15 places key on the kind).
   The data must be checked: Swift decodes it strictly (Codable, tests fail on a wrong type); Python gets a
   stdlib-only checker (required fields, types, ranges, unique PIDs, every capability has its parameters) that the
   tests and CI run.
5. **Capabilities = code modules**: battery, led_zones, dpi, polling, sensor, surface, power, kb_lighting, kb_sleep,
   kb_warning, per_key. Each takes its parameters from the model and holds its reads, parser, write builders,
   read-back check and allowlist entries. The allowlist of a device = the commands of its capabilities.
UI: a device page is built from one specialized editor per capability plus a drawing per model / kind with the
zones from the model. devices.json holds model / protocol parameters, not a generic form schema (per-key painting
or linked LED zones stay hand-written editors). A model without a drawing gets a plain parameter / chip page, so a
new device is usable before artwork exists. The sidebar lists the devices found (covers the "only connected
devices" backlog item), still shows restricted states (Bluetooth only, asleep, udev no-access) and a hint when
none is connected.
A new device then = an entry in devices.json + captures in protocol.json (+ maybe a drawing); code only for a new
capability.
Layout: Python package `squirelib/` (transport/, protocol.py, routes.py, capabilities/, registry.py, state.py, cli.py);
`squire.py` stays the entry point and re-exports, so `import squire`, `squire.py --swiftbar`, the installers and
tests keep working; it keeps `__version__` (release.yml, install.sh and build-packages.sh read it with `sed`).
Checklist for every new file / folder (squirelib/ subpackages, linux/settings/, devices.json): copy it in
linux/install.sh, linux/packaging/nfpm.yaml (one entry per file) and the macOS install.sh (SwiftBar venv folder);
add build inputs to `.github/workflows/release.yml` `paths:` (devices.json + squirelib/** are there). Stage 1b:
devices.json as a resource in macos/Package.swift (SquireCore, Bundle.module) and macos/project.yml (App). Decided by the owner 2026-11-26, for as long as the
Python core lives (until the Rust-or-similar decision). Linux window: linux/settings/ (window.py, editors/, drawings/). Swift: SquireCore split into SquireProtocol,
SquireDevices (registry + capabilities), SquireTransport (IOKit, CoreBluetooth); App gets Settings/Editors and
Settings/Drawings.
Order (decided: in stages, layers first; Rust later on top of the finished shape), each stage with green tests and
unchanged behaviour: `bluetooth-battery` merged into main (done), then (1) devices.json for today's two devices + checker;
code reads PIDs, routes, parameters from it, (2) split transport / route / protocol, Python + Linux first, then
Swift, (3) capabilities one by one, (4) windows on editors, (5) a second model as the proof, ideally another ASUS
mouse on the same protocol (a Reddit user may test it; the owner will ask once we are there).
Pragmatic Swift scope: `squire.py` and `linux/squire_settings.py` (>2100 lines each) have the urgent monolith pain,
whereas `SquireCore` is already split into small files; if a shared Rust core remains on the horizon, prioritize
wiring `devices.json` + slimming `SettingsWindow.swift` / `HIDReader.swift` before splitting `SquireCore` into three
separate SwiftPM targets.

## Direction
- 0.2.x stays read-only. From 0.3 Squire also configures devices (DPI, LEDs, ...) on **Linux and macOS**,
  Linux first (owner's decision 2026-11-10). Helping falchion-light with PRs stays welcome, but Squire has its own
  configuration too; both use the same protocol facts.
- macOS settings window: same layout and behaviour as the Linux one (SwiftUI in an AppKit-managed NSWindow, so it
  never opens at launch), opened with "Settings…" in the popover. Same rules: Apply writes each changed zone, reads
  it back, saves (50 03) once with the last one and only if all matched. protocol.json is bundled for --demo.
  Reads and LED Apply (all three zones) confirmed on macOS via Omni (owner, 2026-11-11) and over the cable
  (2026-11-12). While the window is open the app switches from LSUIElement (accessory) to a regular app, so the
  window is in the Dock and Cmd-Tab; closing it goes back to menu bar only (owner confirmed, 2026-11-27). The colour row is an
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
- Next after 0.3 (owner, 2026-11-13): Linux window test, release 0.3 (both done, 0.3.0 published 2026-11-13), then
  keyboard configuration (Falchion): branch `keyboard-read` first (reads only).
- Rust (decided 2026-11-13: not now): today the core (protocol, allowlist, parsing, write_verified, edit logic)
  exists twice, Python and Swift, held together by protocol.json; the UIs (GTK, SwiftUI) stay native either way.
  Decide when the keyboard configuration starts, as that is the first big growth of the core. Preferred option then:
  a Rust core + CLI (uniffi/swift-bridge into the Swift app; gtk4-rs window + ksni tray on Linux, Python retired),
  introduced alongside squire.py and tested against the same protocol.json vectors. Not a full Rust UI (GPUI/Tauri):
  it would lose the native Mac popover and colour well. Cost to weigh: re-confirming every device behaviour,
  per-arch Linux packages instead of `arch all`.
- Windows (decided 2026-11-13: no app now): G-Helper already covers the Keris on Windows, unsigned .exe files
  meet SmartScreen / AV false positives, Armoury Crate rewrites settings alongside. At most a cheap CLI port
  (device_lock without flock, %LOCALAPPDATA% paths; hidapi reads already parse on Windows), mainly as the owner's
  capture/compare tool. A Windows app is decided together with Rust (tray-icon + egui/Slint would avoid a fourth
  codebase).
- Icon is a shield with a blade (generated by `swift scripts/make-icon.swift` in `macos/`), neutral so it
  fits configuration too. No ASUS/ROG marks, fonts or artwork; similar colour mood only.

## Rules
- **Read-only by default.** Squire only sends commands on `ALLOWED_COMMANDS` in squire.py, and the transport
  refuses anything else: `12 01` (keyboard battery), `12 07` (mouse battery) and the mouse settings reads
  `12 00`, `12 04 00`, `12 04 02`, `12 04 03`, `12 03 00..02`, `12 06` (approved by the owner 2026-11-10); keyboard settings reads `12 00` and `12 12` (approved 2026-11-13).
  All are get requests (first byte `12`); writes start with `50`/`51`. Never send `50 02` (switch profile):
  read the active profile only.
- **Allowlist per device** (2026-11-22): `packet(..., device=...)` / `Packet.build(device:...)` check the device's own
  reads (`ALLOWED_READS[device]`) and writes (`is_allowed_write(command, device)`), and the report ID must be the
  device's (02 keyboard, 03 mouse, or none wired). A mouse command never reaches the keyboard and the other way round.
- **Writes** go through `packet(..., write=True, device=...)` and must pass `is_allowed_write(command, device)`. Approved and confirmed so far
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
  active stage `51 31 09`.
  Keyboard (Falchion RX LP), approved and confirmed on the device via Omni 2026-11-22 (bytes from the AC captures,
  protocol.json `keyboard_writes.squire_writes`): sleep `51 38 00 00 <power-off code>` (read back 12 01 [6]), warning
  `51 3a 00 00 <%>` (12 01 [9]; 10/20 confirmed, 30..50 assumed), lighting `51 2c <effect> 00 <speed> <brightness>
  00 ff ff <r g b>` for static/breathing/color-cycle/reactive with AC's speed values only (read back: effect only,
  12 00 [9]; colour not readable), save `50 55` (only with --save / Apply, counts only when echoed). Unsaved changes
  are lost at off/on. Also approved and confirmed 2026-11-22: starry night 06, current 08, raindrop 09 (same single-
  colour layout, AC's speeds), rainbow 04 / ripple 05 as three chunks `51 2c <id> 02 <speed> <brightness> 00 <dir> 02
  <n> <stops>`, `51 2c <id> 01 <stops>`, `51 2c <id> 00 <stops>` (stop = pos % + RGB; only AC's two gradients;
  rainbow dir 00/04, ripple ff), quicksand 07 as `51 2c 07 01 0f <brightness> 00 02 ff <3 RGB>` + `51 2c 07 00 <3 RGB>`
  (AC's scale). Each chunk must be echoed before the next.
  Also approved and confirmed 2026-11-22 (capture_4 values, owner watched each unsaved send; protocol.json
  `keyboard_writes.squire_writes`): warning never `51 3a 00 00 00` (reads back 00), fast/normal/slow speeds of every
  effect, colour mode [6] 00/10/01 + second colour [12..14] + background [15..17] for starry night / current /
  raindrop, gradient direction 00..08 (all four diagonals seen: 01 down-right, 03 down-left, 05 up-left, 07
  up-right) + thickness [8] 00/01/02 + AC's 7 presets + custom stops (2..7, positions 1..100 ascending), quicksand
  [6] 00/01 + [7] 02/06, off = `51 2c 00 00 ff 04 00 ff ff 00 00 00` (AC's mouse `51 36` is never sent).
  Per-key colours (test, branch `keyboard-direct`, each send approved by the owner 2026-11-25; bytes from G-Helper
  AsusKeyboard.cs SetLedColors + AuraKeyboardLayouts.cs FalchionRX(ISO), OpenRGB has the same packet for the original
  Falchion): `c0 81 <n> 00` + n x `<key id> <r g b>`, n 1..14 (15 would overflow the Omni's 64 bytes), key id =
  column * 8 + row, columns 0..14, rows 1..5 (Esc 01, LCtrl 05, 1 = 09, Q = 0a, Insert 71, bottom right 75; the
  original Falchion's row 0 does not exist here). Reply `c0 82 <n> 00` (not an echo); each packet must be acknowledged
  before the next. Confirmed via Omni and over the cable (protocol.json `keyboard_writes.direct`): single keys and
  all 75 ids. The effect animation stops, the other keys keep its last frame, 12 00 [9] still reports the saved effect
  (not f1); profile switching on the keyboard is blocked meanwhile; an effect write (51 2c) ends it. RAM only: off/on brings the effect back, and
  `50 55` stores nothing (tested on profile 2, echoed, profile unchanged), so Squire refuses --save with it.
  CLI `--set-kb-keys KEY=RRGGBB,...|all=RRGGBB` only; Swift has none.
  Any new command, read or write, must be shown to the owner before it is sent,
  and only once the bytes are known from a source or a capture.
- **Bluetooth** (owner, 2026-11-26; macOS 2026-11-26, Linux 2026-11-27): paired directly, the devices are `0b05:1b08` (keyboard) and
  `0b05:1c0f` (mouse) with HID-over-GATT input collections only (no vendor page, so no 12 xx). Squire reads only the
  standard GATT Battery Level (180F / 2A19) of peripherals macOS already holds connected: never scans, pairs or
  subscribes. No charging flag or warning level over Bluetooth. The vendor service FFF0 (FFF1 write, FFF2 / FFF4
  read+notify) is probably the command channel; never send anything there until the bytes are known (capture of
  Armoury Crate over Bluetooth on Windows). The CBCentralManager (Bluetooth permission prompt) is only created when
  IOKit lists one of the Bluetooth PIDs. HID values (Omni, cable) win over Bluetooth (`Bluetooth.merge`,
  `squire.merge_bluetooth`). Linux: BlueZ reads 2A19 itself and exposes it as `org.bluez.Battery1`; squire.py asks
  bluetoothd once per poll (`busctl --json=short call org.bluez / org.freedesktop.DBus.ObjectManager
  GetManagedObjects`, stdlib + systemd's busctl, no new dependency), only when a device has no HID value, and maps
  connected devices by Modalias `usb:v0B05p1B08` / `p1C0F` (squire.BLUETOOTH_PIDS). Nothing is sent to the device.
  The Bluetooth hidraw node (bus 0005, input collections only) is skipped by the vendor-collections rule.
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
Keyboard settings reads (Falchion RX LP, report ID 02 on the Omni; G-Helper AsusKeyboard.cs, captures in protocol.json
`keyboard_settings`, 2026-11-13): `12 00` [4..6] receiver firmware, [9] current lighting effect (G-Helper ids: 0 static,
1 breathing, 2 cycle, 3 reactive, 4 rainbow (G-Helper: wave), 5 ripple, 6 starry night, 7 quicksand, 8 current, 9 raindrop, f1 host-driven;
all ten confirmed against Armoury Crate on Windows, 2026-11-21; Squire names 04 rainbow like AC, DE Regenbogen; 08 is "Fliessen" in AC), [10] active profile 1-based (profiles 1, 2
and 5 of 5 confirmed; G-Helper's "reported == count -> profile 0" does not apply), [11..13] keyboard firmware via the Omni, [4..6] over the cable (05.00.13 = AC, 2026-11-21; wired replies in
`keyboard_settings.captures_wired`, Windows).
`12 12` [5] factory layout id (25 = CH-DE confirmed, ISO). `12 01` [6] sleep timeout, mouse power-off coding 00..04 = 1/2/3/5/10 min, ff never (AC offers exactly these; 1, 3 and never confirmed on Windows, 2026-11-21, protocol.json `keyboard_settings.sleep_captures`).
No source reads the keyboard's LED colour/brightness; G-Helper keeps them in its own config. `12 03 00` (the mouse's LED read)
was probed once (owner's go): echoed, but no colour in the reply (protocol.json keyboard_settings.probes). Keyboard writes (in squire.py
with --dry-run since 2026-11-22, branch `keyboard-writes`; approved + confirmed 2026-11-22, see Rules; Swift allowlist has none yet): lighting `51 2c`, save `50 55`, sleep `51 38 00 00 <step>`, warning `51 3a 00 00 <%>` (AC; G-Helper's
`51 37` is wrong for the Falchion). AC captures (2026-11-21, protocol.json `keyboard_writes`):
`51 2c <effect> 00 <speed> <brightness %> 00 ff ff <r g b>`, then `50 55`; first reply ff aa. Speed: 3 steps per effect, own
values each, fast/normal/slow, larger = slower: breathing 0f/1e/2a, raindrop 0e/14/1b, reactive 08/0f/16, starry night
0a/0f/19, current 07/0a/0c, colour cycle 12/1f/3e, rainbow 23/3e/55, ripple 50/64/78, quicksand 0c/0f/14; ff for static.
Brightness [5] = % (AC slider 0..100 step 5). Colour cycle has no colour. Starry night, current, raindrop: [6] colour mode
00 single / 10 double / 01 random, [9..11] colour 1, [12..14] colour 2 (00 00 00 when single), [15..17] background
(00 00 00 = off). AC's "lighting off" = static, 4 %, black (plus `03 51 36 00 00 01` to the MOUSE, unknown; "on" sends
nothing to the mouse). Ripple = gradient like rainbow, [7] ff. Quicksand = 6 RGB triples in 2 chunks, [6] 00 scale / 01
random, [7] direction 02 down / 06 up. Rainbow (04) and ripple (05) are gradients in 3 chunks ([3] = 02, 01, 00): [7]
direction, compass in 45 degree steps (00 right = l->r, 01 down-right, 02 down, 03 down-left, 04 left, 05 up-left, 06 up,
07 up-right, 08 centre outwards; AC's button order 06 02 04 00 05 07 03 01 08), [8] thickness (00 low, 01 normal, 02 strong;
AC default 02), [9] stop count (2..7), then <position %> <r g b> per stop: 2 in chunk 02, 4 in 01, the rest in 00 (unused
chunks all zero). AC: 7 unnamed presets (2..7 stops, first at 14 %, last at 100 %) + custom; all in protocol.json
`keyboard_writes.capture_4` (Windows session 2026-11-22).
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
- [x] About + icon-only mode, branch `about-and-icon-mode` (2026-11-23, Mac and Linux; Mac confirmed by the owner incl. switching back and forth, Linux confirmed by the owner on Ubuntu 26.04 from the repo, 2026-11-24: About dialog, toggle off/on):
      "About" in the popover (standard About panel: version + build, GitHub link, MIT, credits/disclaimer) and
      "About Squire" in the tray menu (Gtk.AboutDialog, text from squire_tray.about_info()); toggle "Show percentages
      in menu bar" / "... in top bar" (default on; off = only the shield; Mac UserDefaults `showPercentages`,
      Linux settings.json `show_percentages`). Mac label: each mode is its own view with its own .id and both render
      18 pt high; one reused Image kept the shield's size and squeezed the percentages after switching back. Later maybe a "check for updates" against the GitHub releases.
- [ ] Backlog (Swift app): battery history log (CSV in Application Support: time, percent, charging, mV per
      poll), to check whether the percentage steps down while the voltage drifts; later "time remaining".
      Observed: the keyboard stayed at 78 % for a day while mV went ~4088 -> ~4080.
- [ ] Backlog (owner, 2026-11-22): settings window lists only the devices that are connected (today both are always
      listed, even when nothing is found); keep a hint when none is connected
- [ ] Backlog (owner, 2026-11-22): animate the keyboard effects in the settings drawing (especially raindrop,
      rainbow, ripple, starry night, ...), using the chosen speed / direction / colours; static effects stay a glow
- [ ] Backlog (owner, 2026-11-22): refactoring: linux/squire_settings.py (~1800 lines) and macos/App/SettingsWindow.swift
      have grown large with only two devices. Split per device (mouse page, keyboard page, shared apply bar / editors),
      pure logic in its own module; do it before adding a third device. Ties in with the Rust-core decision.
- [ ] Device configuration (0.3), Linux first, then macOS; see Direction. First step: branch `mouse-read`
  - [x] macOS settings window, same as Linux (branch `macos-settings-window`): reads, LED edit + Apply
  - [x] macOS reads confirmed on the device via Omni (`squire-probe --settings`, 2026-11-11); `--repeat 30` no crash
  - [x] macOS LED Apply confirmed on the device, all three zones via Omni (2026-11-11)
  - [x] macOS: reads + LED Apply over the cable (pid 1c0c, iface 0; change survives off/on, 2026-11-12)
  - [x] Power-off + low-battery warning, angle snapping + tuning: CLI (--set-power, --set-angle-snapping,
        --set-angle-tuning) and both windows (Power / Sensor chips), branch `mouse-settings-writes` (2026-11-12).
        CLI writes confirmed on the device via Omni; Apply in the macOS window confirmed incl. off/on and an LED
        change through the new write path (owner, 2026-11-12); Linux window confirmed 2026-11-13
  - [x] Lift-off write, keeping the surface (2026-11-13): CLI `--set-liftoff low|high` (--dry-run reads 12 06 and
        prints the exact bytes), both windows (Sensor chip: Low/High picker + Surface row; shown only for an unknown
        surface). CLI write confirmed on the device via Omni (high, unsaved, surface ROG Sheath kept, then back to
        low; same bytes as AC; protocol.json settings_writes). Apply in the macOS window confirmed (owner,
        2026-11-13); Linux window confirmed 2026-11-13.
        Reads with firmware 01.00.10 / 07.00.08 parse fine on macOS (2026-11-13)
  - [x] Polling rate + surface preset writes (2026-11-13): CLI `--set-polling HZ`, `--set-surface ID|none`,
        `--list-surfaces`; both windows (Sensor chip pickers; surface written before lift-off when both change).
        CLI writes confirmed on the device via Omni (500 Hz and back, Balteus Qi and back to ROG Sheath with lift-off
        kept; unsaved; protocol.json settings_writes). Apply in the macOS window confirmed (owner, 2026-11-13);
        Linux window confirmed 2026-11-13
  - [x] DPI per stage (value + colour), active stage, stage count (2026-11-13): CLI `--set-dpi STAGE DPI [--color]`,
        `--set-dpi-stage N`, `--set-dpi-count N`; both windows (DPI chip: value + colour per stage, active stage and
        count pickers that keep each other consistent; planned bytes simulate the sequence). Dry runs on macOS via
        Omni match AC's format. CLI writes confirmed on the device via Omni (1650 DPI, colour #ffaa00, stage 2,
        count 3, each unsaved and restored; protocol.json settings_writes). Apply in the macOS window
        confirmed (owner, 2026-11-13); Linux window confirmed 2026-11-13
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
        Confirmed on the device with the macOS window (owner, 2026-11-12); Linux window confirmed 2026-11-13
  - [x] Polling rate: Armoury Crate shows 1000 Hz for raw 0x63 (low 3 bits), so the high nibble is not a rate;
        "not confirmed" note dropped (owner, 2026-11-12)
  - [x] LED speed: Armoury Crate has no speed control for the Keris (owner, 2026-11-12); none in Squire either
- [x] Linux full test for 0.3 (2026-11-13, Omni, firmware 01.00.10 / 07.00.08): reads, every --dry-run, and a
      no-change CLI round trip of every write (LED, power, angle snapping + tuning, DPI value/colour, stage, count,
      polling, surface, lift-off): written, read back, matched, settings unchanged. 50 03 is echoed (LED, --save).
      A save now counts only when echoed. Linux window Apply confirmed by the owner, no errors: DPI value/colour,
      stage/count, polling + surface + lift-off in one Apply, power-off, "same effect on all zones" (2026-11-13).
- [ ] Linux: KDE Plasma, Waybar/Polybar module using `--json`, maybe a .deb
- [x] Keyboard reads (Falchion RX LP), branch `keyboard-read`, PR #7 merged 2026-11-22: `--keyboard-settings` reads
      profile, layout, lighting effect, sleep, warning, firmware; read-only keyboard page with an own 65 % drawing in
      both windows (macOS confirmed on the Mac 2026-11-13). Windows session 2026-11-21: effect names (= G-Helper ids
      00..09), sleep coding, keyboard firmware, three AC write captures (`keyboard_writes`); AC reads no LED colour (it
      caches it), so the keyboard's colour stays unreadable. AC re-flashed the keyboard firmware that day (failed once,
      worked after a reboot): versions and replies unchanged (05.00.13 / receiver 07.00.08, mouse 01.00.10).
- [x] Keyboard writes on Linux, branch `keyboard-writes` (2026-11-22, all confirmed on the device via Omni, see Rules):
      allowlist per device (Python + Swift); CLI `--set-kb-light EFFECT [--brightness --color --speed --gradient
      --direction]` (all ten effects), `--set-kb-sleep MIN|never`, `--set-kb-warning 10..50`, `--save` = 50 55; reads
      back the effect (12 00 [9]) or 12 01 [6] / [9]. Unsaved changes are lost at off/on; 50 55 survives it.
      Linux window: keyboard page edits lighting (effect, brightness, colour, speed, gradient, direction), sleep and
      warning; Apply writes sleep, warning, lighting in that order and saves once with the last. The colour is
      remembered per profile in state_dir()/keyboard-lighting.json when saved (CLI too) and shown only while 12 00 [9]
      still has that effect. Owner applied and saved in the window (profile 4: reactive 75 % #f6f5f4). Run the window
      from the repo only with the installed one closed (same app id: a second start just raises the open window).
      Each new effect was sent unsaved and seen by the owner: starry night (single keys flash, no background),
      current (key + short tail to the right), raindrop (slow vs fast clearly differ), rainbow (spectrum l->r,
      sunset r->l), ripple, quicksand (starts top centre, spreads out, one colour per row).
      Not done yet: Apply of the new effects in the window (owner may try it; Apply saves), macOS (Swift core has the
      per-device allowlist but no keyboard writes; page is read-only).
- [x] Windows session 2026-11-22 (USBPcap of AC via the Omni, protocol.json `keyboard_writes.capture_4`, 7 blocks):
      warning levels (10..50 + never = 00), starry night / current / raindrop options (double, random, background),
      fast/normal/slow for every effect, rainbow directions (9) + thickness + 7 presets + custom stops, ripple
      thickness + presets, quicksand random + up/down, brightness slider, lighting off. AC has no per-key mode.
      Restored afterwards: reactive 75 %, normal, sleep 3 min, warning 20 % (colour AC's blue 00 02 ff, not #f6f5f4).
      Capture setup on the owner's PC: Omni = \\.\USBPcap2, device 8; USBPcapCMD started via Start-Process (hidden).
- [x] Use the session in Squire (Linux first, 2026-11-22, branch `keyboard-writes`): in squire.py, CLI and the Linux
      window, every capture_4 packet rebuilt byte for byte in the tests. Warning never (0 = 00; Squire's notification
      stays quiet too), fast/normal/slow for every effect (reactive 0f is "normal" now; keyboard-lighting.json v2
      migrates the old "fast"), rainbow direction (9, compass names right/down/.../outwards; old left-to-right /
      right-to-left still accepted) + thickness low/normal/strong (rainbow, ripple), AC's 7 gradient presets (names
      are Squire's: red, blue-red, sunset, lime-cyan, yellow-magenta, pink-purple, spectrum) + custom stops via the CLI
      (`--gradient 14:ff0000,100:0000ff`), starry night / current / raindrop `--color-mode single|double|random`,
      `--color2`, `--background`, quicksand `--color-mode scale|random` + `--direction down|up`, `--set-kb-light off`
      (static 4 % black; reads back static; AC's mouse 51 36 is not sent). One normalised value
      (squire.KEYBOARD_LIGHT_FIELDS) drives packets, memory and the window.
      Confirmed on the device via Omni (owner approved, each sent unsaved and seen): warning never, off (no light),
      raindrop double + background fast, starry night random, rainbow pink-purple up low slow, all four diagonals,
      quicksand random up, reactive slow. Restored and saved: reactive normal #f6f5f4 75 % (remembered for profile 4).
      Linux window Apply of the new options confirmed by the owner, no errors (2026-11-22); profile 4 is now
      reactive normal 75 % #e01b24 (saved from the window). Open: thickness not judged by eye; Swift still reads
      keyboard warning 00 as "none".
- [x] Keyboard on macOS, branch `keyboard-macos` (2026-11-22): SquireCore KeyboardWrites.swift (KeyboardLight =
      squire.KEYBOARD_LIGHT_FIELDS, KeyboardLighting.commands, Command.keyboardSleep/Warning/Save, allowlist,
      KeyboardChange, KeyboardLightMemory = the same keyboard-lighting.json as squire.py on the Mac) and
      KeyboardEditing.swift (port of the kb_* window logic); HIDReader.writeKeyboard (chunks echoed on 4 bytes, read
      back once, 50 55 only when all matched and echoed); editable keyboard page (lighting with every option, sleep,
      warning incl. never; Apply/Revert/Show bytes per device; drawing glows in the known colour); warning 00 = never
      in the Swift reads, popover and notification. Tests read the same protocol.json vectors (squire_writes, every
      one-colour AC packet, the 7 presets). Written on Linux without a Swift compiler: CI (`swift test`) checks the
      core; the app target only builds in Xcode. Owner built the app on the Mac and tested it: works (2026-11-22).
      squire-probe has no keyboard writes.
- [x] Linux wired keyboard + mouse (2026-11-24, Ubuntu 26.04, reads only): keyboard pid 1b04 iface 1 and mouse
      pid 1c0c iface 0 are their own hidraw nodes with only ff00 / ff01 (the mixed ifaces are skipped); battery,
      charging, --keyboard-settings and --settings read over the cable, same replies as on Windows/macOS
      (protocol.json keyboard_settings.captures_wired, mouse_settings_wired). Write over the cable confirmed on Linux:
      mouse logo LED via the CLI (static green, unsaved, owner saw it, restored; protocol.json mouse_writes.writes)
- [x] Bluetooth, branch `bluetooth-battery` (2026-11-26, merged into main, branch deleted): battery level over BLE (GATT 2A19) in the macOS menu bar,
      popover ("Bluetooth · level only") and settings sidebar; `squire-probe --bluetooth` reads both devices on the
      Mac (60 % / 52 %, protocol.json `bluetooth`). App tried by the owner on the Mac (menu bar, popover, settings window shows a Bluetooth page instead of "not found").
      Linux (2026-11-27, keyboard paired directly, Ubuntu 26.04): squire.read_bluetooth() reads BlueZ's Battery1 via
      busctl (protocol.json `bluetooth.bluez_linux`); CLI, --json, --list, tray ("Bluetooth · level only, no charging
      state") and the settings window (sidebar level, Bluetooth page + banner instead of "not found") show 58 % from
      the repo. Mouse paired directly too (same day): Modalias 1c0f, BlueZ 51 %, the Omni answers ff aa, so Squire
      shows Bluetooth for both. Later: settings over FFF0 once captured.
- [ ] More devices: collect `--list` / `--debug` / `squire-probe` output from users
- [x] Per-key colours (Falchion RX LP), branch `keyboard-direct`, PR #11 merged, released in 0.4.1 (2026-11-25): `c0 81` works via the Omni and over
      the cable (single keys, all keys; see Rules). Lost at off/on, cannot be saved (50 55 tested), blocks profile
      switching. Owner's decision (2026-11-25): a per-key editor in the window that sends only on Apply (no resend in
      the background), plus key presets kept by Squire (state_dir()/keyboard-keys.json, {name: {key id: colour}};
      a preset sends every key, unnamed ones black). Linux window done, not yet tried on the device: Effect row
      "Per-key colors", click/drag paints with a brush colour, Fill / Off, preset list, Save as / Delete; Apply sends
      saved changes first (50 55 with the last), then c0 81, never saved. CLI: `--set-kb-keys @NAME`,
      `--save-kb-preset NAME`, `--delete-kb-preset NAME`, `--kb-presets`. Key ids per drawn key:
      squire.KEYBOARD_KEY_IDS (G-Helper FalchionRX/ISO; ISO 69 keys, ANSI 68). Drawing legends fixed: Esc top left,
      right column Ins / Del / PgUp / PgDn. An effect write (51 2c) ends per-key mode (confirmed wired 2026-11-25, protocol.json): the window
      switches back by Apply. Window confirmed by the owner on the device, wired and via Omni (2026-11-25).
      Squire default presets (squire.builtin_key_presets, read-only, per layout from the key ids, their names cannot be
      saved over; shown as "Squire default"): Fixed rainbow, Gaming, Typing, Modifiers, Ocean, Fixed sunset. macOS port next.
      Window polish (2026-11-25): effect row sorted by name, then Per-key colors, then Off; preset list shows
      "Squire default" as a second line. The window has its own desktop entry
      (linux/packaging/io.github.phifogg.Squire.Settings.desktop = its app id, also written by install.sh), so GNOME
      shows it in the dock / Alt+Tab with the shield and in the app grid (confirmed by the owner). Started from a
      VS Code terminal it shows the VS Code icon (GNOME goes by the systemd scope); test from the app grid.
      macOS port (2026-11-25, written on Linux; built on the Mac the same day, swift test green, --demo per-key editor
      checked: 5 c0 81 packets for ISO match squire.keyboard_direct_packets; Apply confirmed on the device by the owner the same day;
      `--demo --screenshot PATH keyboard keys` shows the editor with "Fixed rainbow"): SquireCore KeyboardKeys.swift
      (KeyboardDirect = c0 81 packets + allowlist, Command.reply = the c0 82 acknowledgement, KeyPresets = the same
      keyboard-keys.json + Squire defaults, halves rounded up in both ports), KeyboardChange.keys (never saved),
      KeyboardLayout ids + fixed legends, KeyboardEditing per-key logic; app: drawing paints on click/drag, effect
      picker sorted with Per-key colors, KeyboardKeysEditor (brush, Fill / Off, presets in sections, Save as /
      Delete). Tests: KeyboardKeysTests (protocol.json direct vectors, Python's default colours).
- [ ] OpenRGB upstream (owner, 2026-11-25): OpenRGB (GPL-2.0, facts only both ways) knows the older Keris
      (195c..1a68) and the original Falchion (193c/193e), not the Keris II Origin 1c0c, Falchion RX LP 1b04 or the Omni
      1ace. Its packets match ours (`51 28` zone LEDs + `50 03`, keyboard `51 2c`, `12 00` firmware), so contribute
      the devices: first check GitLab for an open MR/issue, then (1) wired Keris II Origin (zones logo/wheel/side,
      modes 0/1/2/4/6 as confirmed in protocol.json), (2) wired Falchion RX LP, (3) the Omni last (shared receiver,
      report IDs 02/03 on one interface; talk to the maintainers first). OpenRGB also has `51 29` (per-LED direct
      mode): never sent by Squire, not approved.
- [ ] Coexist with OpenRGB (owner, 2026-11-25): squire.device_lock / DeviceLock do not cover OpenRGB, so once it
      supports these devices its requests could interleave with Squire's. Show a hint when OpenRGB is running or
      its SDK port 6742 is open (at the latest when the upstream support lands).
- [ ] Local API for Home Assistant & co. (owner, 2026-11-25; the pattern exists elsewhere: OpenRGB SDK TCP 6742,
      OpenRazer and ratbagd on D-Bus, Razer Chroma REST, SteelSeries GameSense HTTP). In this order:
  - [ ] MQTT with HA discovery: keyboard + mouse battery % and charging as HA sensors, read-only, fits the
        read-only rule; can start as a small script on top of `--json`
  - [ ] OpenRGB-SDK-compatible server (lighting): HA's OpenRGB integration, the OpenRGB app and openrgb-python
        would work unchanged. Writes from a client break "writes only on an explicit click": needs a new rule
        from the owner first; decide with the Rust core
  - [ ] Maybe a D-Bus service on Linux (like ratbagd / OpenRazer), only if other Linux apps want Squire's data

## Related projects
- falchion-light (Linux lighting, wired): https://github.com/ChipGillespie/falchion-light.
  PRs there are welcome alongside Squire's own configuration.
- G-Helper (GPL-3.0, Windows): https://github.com/seerge/g-helper
- rogdriver (Zig, Linux): https://github.com/rahulrajaram/rogdriver
- SwiftBar: https://github.com/swiftbar/SwiftBar
- OpenRGB (GPL-2.0, C++, Linux/Windows/macOS): https://gitlab.com/CalcProgrammer1/OpenRGB (GitHub mirror
  CalcProgrammer1/OpenRGB). ASUS packets in `Controllers/AsusAuraUSBController/`; SDK protocol in
  `Documentation/OpenRGBSDK.md` (TCP 6742, lighting only). Upstream device support planned, see Roadmap.
- OpenLogi (MIT/Apache-2.0, Rust + GPUI, macOS/Linux/Windows): https://github.com/AprilNEA/OpenLogi. The same idea
  for Logitech (HID++): an alternative to Options+. Different protocol, so it is a source of UX and process ideas,
  not of bytes. Worth borrowing: quick settings in the menu bar without opening the window (DPI preset, lights),
  a "Device support" issue template that asks for `list` / diag output, a fixture workflow for user captures
  (synthetic ids, no serials, privacy review), low-battery notice once per discharge, a JSON CLI for scripting,
  per-route write ordering (write_order.rs: newest request wins vs. strict order, held through the read-back).

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

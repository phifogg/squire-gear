# Contributing to Squire

Thanks for your interest! Squire is a small project for ASUS ROG keyboards and mice on Linux and
macOS. The most helpful contribution right now is **information about devices** that Squire does not
support yet.

## Reporting a new device

Open an issue with the [**New device**](https://github.com/phifogg/squire-gear/issues/new?template=new-device.yml) template. It asks for:

1. **The exact model name** and how it connects (cable, ROG Omni receiver, its own dongle, Bluetooth).
2. **`squire --list` output**, once for each way the device connects, with the device awake (move the
   mouse or press a key first). This lists the ASUS HID interfaces (product ID, interface, usage
   pages) and **sends nothing to the device**.
   - Linux: `squire --list` (from the .deb / .rpm or `linux/install.sh`), or `python3 squire.py --list`
     in a checkout.
   - macOS: after `./install.sh`,
     ```sh
     APP="$HOME/Library/Application Support/squire-gear"
     "$APP/venv/bin/python" "$APP/squire.py" --list
     ```
     or `swift run squire-probe` from `macos/` in a checkout.
3. **Linux only:** `lsusb -d 0b05:` (one line per ASUS USB device).
4. **What you would like to see**: battery, DPI, LED zones (and what they are called), polling rate,
   per-key lighting, ...
5. **Other tools** that support the device (Armoury Crate, G-Helper, OpenRGB, ...). Their device
   definitions often tell us the product IDs and which protocol the device speaks.

### What happens next

Squire never sends anything to a device it does not know. Adding one goes step by step:

1. **Model**: we add the device to [`devices.json`](devices.json) (product IDs, connections, LED zones,
   DPI range, ...). Commands start out *unconfirmed*, so Squire only prints their bytes (`--dry-run`) and
   does not send them.
2. **Reads**: we ask you to run a few read-only commands (battery, settings) and post the replies. Every
   new command is shown to you as bytes before you run it. The replies go into
   [`protocol.json`](protocol.json) as test vectors.
3. **Writes** (optional): once the bytes are known from a public source or a capture, you try a change
   without saving it, tell us what the device did, and the command is marked confirmed.

Devices that speak the same protocol as the Keris II Origin and Falchion RX Low Profile (most recent
ROG mice and keyboards) usually need only steps 1 and 2.

### Privacy

- `--list` and `lsusb -d 0b05:` show no serial numbers. Please remove anything you consider private
  before posting.
- **Do not attach Wireshark / USBPcap captures (`.pcapng`)**: they can contain your keystrokes. If a
  capture is needed, we will ask for a specific short one and how to make it.

## Reporting a bug

Open an issue with the [**Bug report**](https://github.com/phifogg/squire-gear/issues/new?template=bug-report.yml)
template: your OS, Squire version, the device(s) and how they are connected, and what you expected. The output of `squire --debug` (read-only, prints the raw replies) helps a lot.

## Code

Pull requests are welcome; for anything bigger than a small fix, please open an issue first.

- Run the tests (no hardware needed): `python3 -m unittest -v`, and on macOS `cd macos && swift test`.
- Squire is read-only by default and writes only commands on a per-device allowlist. A new command, read
  or write, needs its bytes from a public source or a capture and a confirmation on a real device.
- G-Helper is GPL-3.0 and OpenRGB GPL-2.0: use protocol facts from them only, never copy code.
- Code, comments, commits and issues are in English.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).

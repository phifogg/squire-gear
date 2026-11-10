## What's new
- TODO: fill in before publishing.

Still **read-only**. Squire only sends the two battery query commands and never changes lighting, settings or firmware.

## Install on macOS
1. Download `Squire-{{VERSION}}.zip`, unzip it and move `Squire.app` to `/Applications` (or `~/Applications` if you have no admin rights).
2. Open it. Squire appears in the menu bar, with no Dock icon.
3. Allow notifications when asked.

**This build is not notarized** (it is signed ad hoc, not with an Apple Developer ID), so macOS blocks the first start. Either click **Open Anyway** in **System Settings → Privacy & Security**, or run:

```sh
xattr -dr com.apple.quarantine /Applications/Squire.app
```

Verify the download: `shasum -a 256 -c Squire-{{VERSION}}.zip.sha256`

## Install on Linux
Tested on Ubuntu 26.04 with GNOME.

```sh
sudo apt install ./squire-gear_{{VERSION}}-1_all.deb       # Ubuntu, Debian
sudo dnf install ./squire-gear-{{VERSION}}-1.noarch.rpm    # Fedora
```

Then start **Squire** from the app grid. On Fedora, GNOME needs the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) for the top bar icon. Ubuntu enables it by default.

Verify the download: `sha256sum -c squire-gear_{{VERSION}}-1_all.deb.sha256`

## Feedback wanted
Output from `squire --list` / `squire --debug` (Linux, Python) or `swift run squire-probe` (macOS, in `macos/`) helps add other ROG devices.

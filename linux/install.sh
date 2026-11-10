#!/bin/bash
# Installs Squire for the current user on Linux (tested on Ubuntu 26.04, GNOME):
#   ~/.local/share/squire-gear/       squire.py (core + CLI), squire_tray.py (top bar app),
#                                     squire_settings.py (settings window)
#   ~/.local/bin/squire               command line wrapper (--json, --list, --debug, ...)
#   ~/.local/share/applications/      "Squire" launcher
#   /etc/udev/rules.d/70-squire-gear.rules   access to the vendor HID interfaces (asks before using sudo)
# Usage: ./linux/install.sh [--no-udev] [--no-start]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/squire-gear"
BIN_DIR="$HOME/.local/bin"
RULE=/etc/udev/rules.d/70-squire-gear.rules
DO_UDEV=1
DO_START=1
for arg in "$@"; do
  case "$arg" in
    --no-udev) DO_UDEV=0 ;;
    --no-start) DO_START=0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

# Dependencies: Ubuntu desktop ships them; check without loading GTK.
missing=()
python3 -c 'import gi' 2>/dev/null || missing+=(python3-gi)
python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || missing+=(gir1.2-gtk-3.0)
python3 -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null \
  || missing+=(gir1.2-ayatanaappindicator3-0.1)
python3 -c 'import gi; gi.require_version("Gtk", "4.0")' 2>/dev/null || missing+=(gir1.2-gtk-4.0)
python3 -c 'import gi; gi.require_version("Adw", "1")' 2>/dev/null || missing+=(gir1.2-adw-1)
command -v notify-send >/dev/null || missing+=(libnotify-bin)
if [ ${#missing[@]} -gt 0 ]; then
  echo "→ Missing packages: ${missing[*]}"
  echo "    sudo apt install ${missing[*]}"
  exit 1
fi

if [ -x /usr/bin/squire-tray ]; then
  echo "→ Note: the squire-gear package is installed too. Use one or the other, not both"
  echo "  (remove the package: sudo apt remove squire-gear, or sudo dnf remove squire-gear)."
fi

echo "→ Installing to: $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DATA/applications"
install -m 0644 "$REPO/squire.py" "$APP_DIR/squire.py"
install -m 0755 "$HERE/squire_tray.py" "$APP_DIR/squire_tray.py"
install -m 0755 "$HERE/squire_settings.py" "$APP_DIR/squire_settings.py"
install -D -m 0644 "$HERE/icons/squire-gear-symbolic.svg" "$APP_DIR/icons/squire-gear-symbolic.svg"

cat > "$BIN_DIR/squire" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$APP_DIR/squire.py" "\$@"
EOF
chmod 0755 "$BIN_DIR/squire"

for size in 32 64 128 256 512; do
  icon="$REPO/macos/App/Assets.xcassets/AppIcon.appiconset/icon_$size.png"
  [ -f "$icon" ] || continue
  install -D -m 0644 "$icon" "$DATA/icons/hicolor/${size}x${size}/apps/squire-gear.png"
done
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true

cat > "$DATA/applications/squire-gear.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Squire
Comment=Battery of ROG keyboard and mouse in the top bar
Exec=/usr/bin/python3 "$APP_DIR/squire_tray.py"
Icon=squire-gear
Terminal=false
Categories=Utility;
Keywords=battery;keyboard;mouse;ROG;ASUS;
EOF
update-desktop-database -q "$DATA/applications" 2>/dev/null || true

# An existing autostart entry points to the old location if it was enabled from the repository.
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/squire-gear.desktop"
if [ -f "$AUTOSTART" ]; then
  sed -i "s|^Exec=.*|Exec=\"/usr/bin/python3\" \"$APP_DIR/squire_tray.py\"|" "$AUTOSTART"
fi

if [ "$DO_UDEV" = 1 ]; then
  if cmp -s "$HERE/70-squire-gear.rules" "$RULE"; then
    echo "→ udev rule already installed: $RULE"
  else
    echo
    echo "Squire needs a udev rule so it can read the battery without root. It grants the logged-in"
    echo "user access to the vendor interfaces of the Omni receiver and the wired devices only:"
    echo "  $RULE"
    read -r -p "Install it now with sudo? [Y/n] " answer
    if [[ ! "$answer" =~ ^[Nn] ]]; then
      sudo install -m 0644 "$HERE/70-squire-gear.rules" "$RULE"
      sudo udevadm control --reload-rules
      sudo udevadm trigger --subsystem-match=hidraw --action=change
      echo "→ udev rule installed. Re-plug the receiver if Squire still reports 'No access'."
    else
      echo "→ Skipped. Later: sudo install -m 0644 \"$HERE/70-squire-gear.rules\" $RULE"
    fi
  fi
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "→ Note: $BIN_DIR is not in PATH; the command line tool is $BIN_DIR/squire" ;;
esac

if [ "$DO_START" = 1 ] && [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
  pkill -f "python3 .*squire_tray\.py" 2>/dev/null || true
  sleep 0.5
  # setsid: keep running after this terminal closes; env -i-like cleanup happens inside the app
  setsid /usr/bin/python3 "$APP_DIR/squire_tray.py" >/dev/null 2>&1 < /dev/null &
  echo "→ Squire started. Look for the shield with both batteries in the top bar."
  echo "  Turn on 'Launch at login' in its menu to start it automatically."
fi

echo
echo "Test:"
echo "  $BIN_DIR/squire --list    # sends nothing"
echo "  $BIN_DIR/squire --debug   # read-only battery queries, raw replies"

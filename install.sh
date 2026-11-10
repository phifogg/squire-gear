#!/bin/bash
# Installs Squire into ~/Library/Application Support/squire-gear (own venv with hidapi)
# and, if a SwiftBar plugin folder is known, adds the menu bar plugin.
# Usage: ./install.sh [SwiftBar-plugin-folder]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Library/Application Support/squire-gear"
PLUGIN_DIR="${1:-}"

if [ -z "$PLUGIN_DIR" ]; then
  PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
elif [ ! -d "$PLUGIN_DIR" ]; then
  echo "→ Creating plugin folder: $PLUGIN_DIR"
  mkdir -p "$PLUGIN_DIR"
fi

echo "→ Installing to: $APP_DIR"
mkdir -p "$APP_DIR"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip hidapi
install -m 0755 "$HERE/squire.py" "$APP_DIR/squire.py"

if [ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ]; then
  PLUGIN="$PLUGIN_DIR/squire-gear.5m.sh"
  VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/squire.py")"
  cat > "$PLUGIN" <<EOF
#!/bin/bash
# <xbar.title>Squire</xbar.title>
# <xbar.version>v$VERSION</xbar.version>
# <xbar.author>Daniel Draes</xbar.author>
# <xbar.author.github>phifogg</xbar.author.github>
# <xbar.desc>Battery level and charging state of ASUS ROG Falchion RX Low Profile and Keris II Origin (Omni receiver), read-only.</xbar.desc>
# <xbar.dependencies>python3,hidapi</xbar.dependencies>
# <xbar.abouturl>https://github.com/phifogg/squire-gear</xbar.abouturl>
exec "$APP_DIR/venv/bin/python" "$APP_DIR/squire.py" --swiftbar
EOF
  chmod 0755 "$PLUGIN"
  echo "→ SwiftBar plugin: $PLUGIN"
  if [ -e "$PLUGIN_DIR/rog-battery.5m.sh" ]; then
    echo "→ Note: old version found. Remove it with:"
    echo "    rm \"$PLUGIN_DIR/rog-battery.5m.sh\""
    echo "    rm -r \"$HOME/Library/Application Support/rog-battery\""
  fi
else
  echo "→ No SwiftBar plugin folder found. Later run: ./install.sh \"<plugin-folder>\""
fi

echo
echo "Test:"
echo "  \"$APP_DIR/venv/bin/python\" \"$APP_DIR/squire.py\" --list"
echo "  \"$APP_DIR/venv/bin/python\" \"$APP_DIR/squire.py\" --debug"

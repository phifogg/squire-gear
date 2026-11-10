#!/bin/bash
# Builds a universal (arm64 + x86_64) Squire.app, ad-hoc signed and NOT notarized, and zips it.
# Output: macos/dist/Squire-<version>.zip and a .sha256 file.
# Needs Xcode and XcodeGen (brew install xcodegen). Usage: ./scripts/build-release.sh
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="$(sed -n 's/^    MARKETING_VERSION: "\(.*\)"/\1/p' project.yml)"
BUILD_DIR=".build/release"
DIST="dist"

echo "→ Squire $VERSION: generating project"
xcodegen generate >/dev/null

echo "→ Building universal Release"
rm -rf "$BUILD_DIR"
xcodebuild -project Squire.xcodeproj -scheme Squire -configuration Release \
  -derivedDataPath "$BUILD_DIR" ARCHS="arm64 x86_64" ONLY_ACTIVE_ARCH=NO build -quiet

APP="$BUILD_DIR/Build/Products/Release/Squire.app"
[ -d "$APP" ] || { echo "Build failed: $APP not found" >&2; exit 1; }

echo "→ Ad-hoc signing"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "→ Packaging"
mkdir -p "$DIST"
ZIP="$DIST/Squire-$VERSION.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
( cd "$DIST" && shasum -a 256 "Squire-$VERSION.zip" > "Squire-$VERSION.zip.sha256" )

echo "→ Archs: $(lipo -archs "$APP/Contents/MacOS/Squire")"
echo "→ Done: $ZIP"
cat "$DIST/Squire-$VERSION.zip.sha256"

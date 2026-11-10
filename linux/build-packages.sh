#!/bin/bash
# Builds the Linux packages from linux/packaging/nfpm.yaml (one config for both):
#   linux/dist/squire-gear_<version>-1_all.deb   (+ .sha256)
#   linux/dist/squire-gear-<version>-1.noarch.rpm (+ .sha256)
# Needs nfpm: https://nfpm.goreleaser.com/install/
# Usage: linux/build-packages.sh [deb|rpm ...]
set -euo pipefail

cd "$(dirname "$0")/.."
if ! command -v nfpm >/dev/null; then
  echo "nfpm not found. Install it from https://nfpm.goreleaser.com/install/"
  exit 1
fi

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' squire.py)"
export VERSION
PACKAGERS=("$@")
[ ${#PACKAGERS[@]} -gt 0 ] || PACKAGERS=(deb rpm)

rm -rf linux/dist
mkdir -p linux/dist
for packager in "${PACKAGERS[@]}"; do
  nfpm package --config linux/packaging/nfpm.yaml --packager "$packager" --target linux/dist/
done

cd linux/dist
for file in squire-gear*; do
  sha256sum "$file" > "$file.sha256"
done
ls -l

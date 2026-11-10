#!/bin/sh
# Apply the udev rule right away so the receiver works without re-plugging.
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=hidraw --action=change || true
fi
exit 0

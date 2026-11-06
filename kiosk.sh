#!/usr/bin/env bash
# Starts the full-screen browser. Run by kiosk.service on tty1 as the kiosk user.
# cage is a tiny Wayland compositor that shows one app full screen.

URL="http://127.0.0.1:8080/"

# Wait (up to a minute) for calsync.py to start serving.
for _ in $(seq 1 60); do
  (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null && break
  sleep 1
done

# Browser profile lives in RAM (/run/user/<uid>), so nothing is written to the
# SD card and every boot starts clean.
PROFILE="${XDG_RUNTIME_DIR:-/tmp}/chromium-kiosk"
rm -rf "$PROFILE"

CHROMIUM="$(command -v chromium || command -v chromium-browser)"

# Invisible mouse cursor (installed by install.sh). Without it cage draws an
# arrow in the middle of the screen whenever a mouse or touchpad is plugged in.
export XCURSOR_THEME=caldisplay-blank
export XCURSOR_SIZE=24

exec cage -d -s -- "$CHROMIUM" \
  --kiosk \
  --ozone-platform=wayland \
  --user-data-dir="$PROFILE" \
  --disk-cache-dir=/dev/null \
  --disk-cache-size=1 \
  --no-first-run \
  --no-default-browser-check \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=Translate,MediaRouter \
  --password-store=basic \
  --renderer-process-limit=2 \
  --overscroll-history-navigation=0 \
  --disable-pinch \
  --check-for-update-interval=31536000 \
  "$URL"

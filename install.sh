#!/usr/bin/env bash
# Installs the calendar kiosk on Armbian (Debian Trixie) for the Orange Pi Zero 2W.
# Run from this folder:  sudo bash install.sh
# Safe to run again after you change files; it updates and restarts everything.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run with sudo:  sudo bash install.sh"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/caldisplay

step() { printf '\n==> %s\n' "$*"; }

step "Installing packages (the first time takes a few minutes)"
apt-get update
apt-get install -y --no-install-recommends \
  cage chromium python3 python3-google-auth python3-requests \
  v4l-utils ddcutil fonts-noto-core
apt-get install -y --no-install-recommends fonts-inter \
  || echo "(fonts-inter not available, the UI will use Noto Sans)"

step "Creating the kiosk user"
if ! id kiosk >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/kiosk --shell /usr/sbin/nologin kiosk
fi
for g in video render input; do
  getent group "$g" >/dev/null && usermod -aG "$g" kiosk
done

step "Copying files to $DEST"
mkdir -p "$DEST"
cp -r "$SRC/calsync.py" "$SRC/kiosk.sh" "$SRC/hdmi-source.sh" "$SRC/config.example.json" "$DEST/"
rm -rf "$DEST/web" && cp -r "$SRC/web" "$DEST/web"
for f in config.json service-account.json; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DEST/"
done
# Fix Windows line endings, in case the files passed through a Windows PC.
sed -i 's/\r$//' "$DEST/kiosk.sh" "$DEST/hdmi-source.sh"
chmod 755 "$DEST/calsync.py" "$DEST/kiosk.sh" "$DEST/hdmi-source.sh"
chown -R kiosk:kiosk "$DEST"
[ -f "$DEST/service-account.json" ] && chmod 600 "$DEST/service-account.json"
[ -f "$DEST/config.json" ] && chmod 640 "$DEST/config.json"

if [ ! -f /etc/default/caldisplay ]; then
  sed 's/\r$//' "$SRC/caldisplay.default" > /etc/default/caldisplay
else
  echo "Keeping your existing /etc/default/caldisplay"
fi

step "Enabling DDC/CI (i2c-dev)"
echo i2c-dev > /etc/modules-load.d/caldisplay.conf
modprobe i2c-dev || true

step "Setting the time zone"
TZNAME="America/Chicago"
if [ -f "$DEST/config.json" ]; then
  TZNAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("timezone","America/Chicago"))' "$DEST/config.json")"
fi
timedatectl set-timezone "$TZNAME" || true
timedatectl set-ntp true 2>/dev/null || true
echo "Time zone: $TZNAME"

step "Installing services"
for f in "$SRC"/systemd/*.service; do
  sed 's/\r$//' "$f" > "/etc/systemd/system/$(basename "$f")"
done
systemctl daemon-reload
systemctl set-default graphical.target
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl enable caldisplay.service kiosk.service hdmi-source.service

step "Checking your setup"
problems=0
if [ ! -f "$DEST/config.json" ]; then
  echo "  ! config.json is missing. Copy config.example.json to config.json, fill in your calendars,"
  echo "    put it next to install.sh and run this again."
  problems=1
fi
if [ ! -f "$DEST/service-account.json" ]; then
  echo "  ! service-account.json is missing (the Google key file). Put it next to install.sh and run this again."
  problems=1
fi
[ "$problems" = 0 ] && echo "  config.json and service-account.json found"

if systemctl is-active --quiet kiosk.service; then
  step "Restarting with the new files"
  systemctl restart caldisplay.service kiosk.service
  echo "Done. The screen will reload in a few seconds."
else
  printf '\nDone. Reboot to start the kiosk:  sudo reboot\n'
fi

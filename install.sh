#!/bin/bash
# Installs the calendar kiosk on the Orange Pi (Armbian / Debian Trixie).
#   sudo ./install.sh            sync server + kiosk browser
#   sudo ./install.sh --no-kiosk sync server only (test the data first)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo: sudo ./install.sh"; exit 1; fi
SRC="$(cd "$(dirname "$0")" && pwd)"
RUN_AS="${SUDO_USER:-$(logname)}"
RUN_UID="$(id -u "$RUN_AS")"
DEST=/opt/caldisplay
KIOSK=1; [ "${1:-}" = "--no-kiosk" ] && KIOSK=0

for f in config.json service-account.json; do
  [ -f "$SRC/$f" ] || { echo "Missing $f in $SRC (see the README)."; exit 1; }
done

echo "==> Installing packages"
apt-get update
apt-get install -y python3 python3-google-auth python3-requests curl tzdata
if [ "$KIOSK" = 1 ]; then
  apt-get install -y cage chromium fonts-dejavu-core
fi

echo "==> Copying files to $DEST"
mkdir -p "$DEST"
cp -r "$SRC/calsync.py" "$SRC/web" "$SRC/config.json" "$SRC/service-account.json" "$DEST/"
chown -R "$RUN_AS:" "$DEST"
chmod 600 "$DEST/service-account.json"

echo "==> Testing Google access"
sudo -u "$RUN_AS" python3 "$DEST/calsync.py" --once || {
  echo "Google fetch failed. Fix config.json / sharing, then run install.sh again."; exit 1; }

echo "==> Installing services (running as $RUN_AS)"
install_unit() {
  sed -e "s/__USER__/$RUN_AS/g" -e "s/__UID__/$RUN_UID/g" "$SRC/systemd/$1" > "/etc/systemd/system/$1"
}
install_unit caldisplay.service
systemctl daemon-reload
systemctl enable --now caldisplay.service

if [ "$KIOSK" = 1 ]; then
  install_unit kiosk.service
  usermod -aG video,render,input "$RUN_AS"
  systemctl daemon-reload
  systemctl enable kiosk.service
  systemctl set-default graphical.target
  echo "==> Done. Reboot to start the display: sudo reboot"
else
  echo "==> Done. Check it with: curl -s localhost:8080/api/events | head -c 400"
fi

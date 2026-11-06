#!/usr/bin/env bash
# Copies this folder to the Orange Pi and reinstalls, so the display updates.
# Run from the laptop:  bash deploy.sh
# You'll be asked for the Pi's password (up to three times).
set -euo pipefail

PI="${PI:-calendar@192.168.50.72}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Copying files to $PI:~/caldisplay"
COPYFILE_DISABLE=1 tar -C "$HERE" \
  --exclude .git --exclude .superpowers --exclude .vscode --exclude cache.json \
  -cf - . | ssh "$PI" 'mkdir -p ~/caldisplay && tar -C ~/caldisplay -xf -'

echo "==> Installing on the Pi"
ssh -t "$PI" 'cd ~/caldisplay && sudo bash install.sh'

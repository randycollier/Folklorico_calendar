# Calendar Kiosk: Starter Workflow

Four small stages. Each one ends with a check, so you know it works before
moving on. Input switching, read-only root and the fancier UI come later.

```
caldisplay-starter/
  calsync.py            fetches Google Calendar, serves the page + /api/events
  web/                  index.html, style.css, app.js (the simple display)
  config.example.json   copy to config.json and fill in
  systemd/              caldisplay.service (sync), kiosk.service (browser)
  install.sh            installs everything on the Pi
```

## Stage 1: Set up the workflow (laptop)

1. Put the folder somewhere you work, and make it a git repo:
   ```bash
   cd caldisplay-starter
   git init
   printf "config.json\nservice-account.json\n__pycache__/\n" > .gitignore
   git add . && git commit -m "Starter kiosk"
   ```
2. Your loop from here on: edit on the laptop, test with `--demo`, commit,
   copy to the Pi.

**Check:** `git status` is clean and the two secret files are ignored.

## Stage 2: Build and test the script (laptop)

1. Try the display with fake events (no Google needed):
   ```bash
   python3 calsync.py --demo
   ```
   Open http://127.0.0.1:8080 and make the window full screen. Events change
   every 12 seconds.
2. Set up Google access (service account) as in `calendar-kiosk-setup.md`
   section 2, and put `service-account.json` in this folder.
3. `cp config.example.json config.json` and put in your calendar IDs.
4. Test the real fetch:
   ```bash
   pip install google-auth requests     # laptop only; the Pi uses apt
   python3 calsync.py --once
   ```

**Check:** `--once` prints your real upcoming events. If it says "not found",
the calendar isn't shared with the service account email yet.

## Stage 3: Install on the Pi

1. Flash Armbian (Debian Trixie, minimal) for the Orange Pi Zero 2W onto the
   SD card with balenaEtcher or Raspberry Pi Imager ("Use custom").
2. Boot it on a phone charger with a keyboard and the monitor attached. Set the
   user and password, then join WiFi: `sudo nmtui`. Get the IP: `hostname -I`.
3. From the laptop, copy the folder (with config.json and
   service-account.json inside):
   ```bash
   scp -r caldisplay-starter <user>@<pi-ip>:~
   ```
4. On the Pi (over SSH is easiest), install just the sync server first:
   ```bash
   cd ~/caldisplay-starter
   chmod +x install.sh
   sudo ./install.sh --no-kiosk
   ```

**Check:**
```bash
systemctl status caldisplay            # active (running)
curl -s localhost:8080/api/events | head -c 400
```
You should see your events as JSON.

## Stage 4: Show the simple display

```bash
sudo ./install.sh        # adds cage + Chromium and the kiosk service
sudo reboot
```

**Check:** after boot, the monitor shows one event at a time, full screen,
no mouse pointer. Add an event in Google Calendar; it shows up within about a
minute.

## Updating later

Edit on the laptop, then:
```bash
scp -r web calsync.py <user>@<pi-ip>:~/caldisplay-starter/
ssh <user>@<pi-ip> 'cd caldisplay-starter && sudo cp -r web calsync.py /opt/caldisplay/ \
  && sudo systemctl restart caldisplay kiosk'
```

## When something's wrong

| Symptom | Look at |
|---|---|
| Blank or "Loading calendar" forever | `journalctl -u caldisplay -f` |
| Black screen, no browser | `journalctl -u kiosk -b` |
| "Offline" badge | WiFi, or the error in the caldisplay log |
| Wrong times | `timezone` in config.json, and `timedatectl` on the Pi |

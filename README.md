# Calendar Kiosk for Orange Pi Zero 2W

A WiFi calendar display. The Orange Pi is powered from the monitor's USB port,
so it boots whenever the monitor turns on, switches the monitor to its own HDMI
input, and shows upcoming events from a private Google Calendar one at a time:
the date, then the event's details, then the next event a few seconds later.
New, edited, moved and deleted events show up within about a minute.

```
monitor powers on --USB power--> Pi boots --> hdmi-source.sh: "switch to my input" (CEC / DDC-CI)

Google Calendar API --(WiFi, every 60s)--> calsync.py --> cache.json
                                              |
                                  http://127.0.0.1:8080
                                              |
                          cage + Chromium (kiosk.sh) --HDMI--> monitor
```

## Files

| File | What it does |
|---|---|
| `calsync.py` | Polls Google Calendar, caches results, serves the UI and `/api/events` |
| `web/` | The kiosk UI (plain HTML/CSS/JS, no build step, no internet needed) |
| `kiosk.sh` | Starts Chromium full screen inside the cage compositor |
| `hdmi-source.sh` | Makes the monitor switch to the Pi's input at boot and on wake |
| `caldisplay.default` | Input switching settings, installed to `/etc/default/caldisplay` |
| `config.example.json` | Calendars, colors, poll interval, timing of the event rotation |
| `systemd/` | Services for the sync server, kiosk browser, and input switching |
| `install.sh` | Installs everything on the Pi (safe to run again after changes) |

## Quick start

1. On your laptop: `python3 calsync.py --demo`, open http://127.0.0.1:8080.
2. Set up Google access (section 2) to get `service-account.json` and the calendar IDs.
3. `cp config.example.json config.json` and fill in your calendars.
4. Flash Armbian, connect the Pi to WiFi (section 3).
5. Copy this folder to the Pi, then on the Pi: `cd ~/caldisplay && sudo bash install.sh && sudo reboot`.

## 1. Design the UI on your laptop

No Google setup needed, and nothing to install besides Python 3.9+. Demo mode
generates fake events:

```bash
python3 calsync.py --demo
# open http://127.0.0.1:8080 and size the browser window to 1920x1080
```

Edit the files in `web/` and refresh the browser.

- **`web/index.html`** holds the markup in `<template id="event-template">`.
  Any element with a `data-field` attribute gets filled in: `relative`
  ("Today", "Tomorrow", "In 3 days"), `weekday`, `day`, `month`, `calendar`,
  `title`, `time`, `location`, `description`, and `nowBadge` (shown only while
  the event is happening). Empty fields are hidden automatically. Move, remove
  or restyle them freely.
- **`web/style.css`** is all the look. Each event sets `--event-color` from its
  calendar's color in `config.json`, and gets `.is-now` while in progress.
  Sizes are in `vh`/`vw`, so it scales to any monitor resolution. Each new
  event fades in with the `enter` animation on `.event`.
- **`web/app.js`** is the logic: builds the list of the next `max_events`
  events, shows one for `seconds_per_event`, loops, and redraws when the
  calendar changes. You shouldn't need to touch it for visual changes.

Each event's data (from `/api/events`):

```json
{ "id": "...", "calendar": "Rehearsals", "color": "#f2a93b",
  "title": "Rehearsal: Jalisco", "location": "Studio A",
  "description": "Bring your practice skirt and zapatos.",
  "allDay": false, "start": "2026-09-29T18:00:00-05:00", "end": "2026-09-29T19:30:00-05:00" }
```

## 2. Google Calendar access (service account)

A service account is the simplest way for a headless device to read a private
calendar. There's no login screen and no token that expires.

1. Go to console.cloud.google.com, create a project (for example "Calendar Kiosk").
2. **APIs and Services > Library**: enable **Google Calendar API**.
3. **IAM and Admin > Service Accounts > Create**. No roles needed.
4. Open the service account, **Keys > Add key > JSON**. Save it as
   `service-account.json` in this folder.
5. Copy the service account's email (ends in `iam.gserviceaccount.com`).
6. In Google Calendar on the web, for each calendar: **Settings and sharing >
   Share with specific people > Add** the service account email with
   **See all event details**.
7. On the same settings page, copy the **Calendar ID** under "Integrate calendar"
   (your main calendar's ID is your Gmail address).
8. `cp config.example.json config.json` and fill in the IDs, names and colors.

Keep `service-account.json` out of git. It only has read access to the
calendars you shared with it.

## 3. Set up the Orange Pi

1. Flash **Armbian (Debian Trixie, minimal)** for the Orange Pi Zero 2W from
   armbian.com to a microSD card with balenaEtcher or Raspberry Pi Imager
   ("Use custom"). Use a 16 GB+ high-endurance card from a good brand.
2. Connect a mini-HDMI to HDMI cable to the monitor and a USB keyboard (through
   a USB-C OTG adapter). For setup, power it from a phone charger rather than
   the monitor.
3. On first boot, log in as `root` with password `1234`. Armbian asks you to
   set a new root password and create your own user. It may offer to connect
   WiFi; if not, run `nmtui`, choose "Activate a connection" and pick your
   network.
4. Find the Pi's IP address: `hostname -I`. From now on you can work from your
   laptop over SSH: `ssh <your-user>@<pi-ip>`.

## 4. Copy the code to the Pi and install

From your laptop, in the folder that contains `caldisplay/` (works on Mac,
Linux, and Windows PowerShell):

```bash
scp -r caldisplay <your-user>@<pi-ip>:~
```

Make sure `config.json` and `service-account.json` are inside the folder. Then
on the Pi:

```bash
ssh <your-user>@<pi-ip>
cd ~/caldisplay
sudo bash install.sh
sudo reboot
```

After the reboot the calendar comes up on its own. Set up input switching next
(section 5).

**Updating later:** edit on your laptop, `scp -r` the folder again, then run
`sudo bash install.sh` on the Pi. It copies the new files and restarts the
screen. No reboot needed.

Useful commands over SSH:

```bash
journalctl -u caldisplay -f                 # sync log ("Calendar changed: ...", errors)
journalctl -u kiosk -b                      # browser log
journalctl -u hdmi-source -b                # did the input switch work?
curl -s localhost:8080/api/events | head -c 600   # raw data
sudo systemctl restart kiosk                # reload the browser
```

## 5. Switching the monitor to the Pi's input

At boot, `hdmi-source.service` asks the monitor to switch to the Pi's HDMI
port. It keeps trying for about a minute, since the monitor may still be
starting up. See what the Pi can do with your monitor:

```bash
sudo /opt/caldisplay/hdmi-source.sh status
```

**HDMI-CEC (TVs, some monitors).** The same "One Touch Play" a Roku or Fire TV
uses. If `status` shows a CEC device, test it:

```bash
sudo /opt/caldisplay/hdmi-source.sh claim
```

**DDC/CI (most PC monitors).** Turn on "DDC/CI" in the monitor's menu if it has
the option, then:

```bash
sudo ddcutil detect
sudo ddcutil capabilities | grep -A8 "Feature: 60"   # lists the input codes
sudo nano /etc/default/caldisplay                     # set DDC_INPUT=0x12 (for example)
sudo /opt/caldisplay/hdmi-source.sh claim             # test it
```

Some monitors only answer DDC on the input that's already showing, in which
case this can't pull the monitor over from another input.

**If neither works:** most monitors have an "Auto source" or "Input auto
switch" option in the menu. With it on, the monitor jumps to whichever input
has a signal. Or just leave the monitor set to the Pi's input.

If the monitor keeps USB power on in standby (so the Pi stays running), the
service watches the monitor's power state and switches it back to the Pi's
input each time the monitor wakes up.

## 6. Running on monitor USB power

- **Current.** Monitor USB ports often give only 500 mA (USB 2.0) or 900 mA
  (USB 3.0). The Pi running Chromium can draw more than that at peaks. If you
  see random reboots or a frozen screen, try a USB 3.0 port or a port labeled
  for charging. A powered USB port with a 1 A+ rating is the safe choice.
- **Sudden power-off.** Turning the monitor off cuts the Pi's power with no
  shutdown. The setup keeps SD card writes low: the event cache is only written
  when the calendar changes (atomically, with fsync), Chromium's profile lives
  in RAM, and Armbian keeps logs in RAM. For extra safety, turn on Armbian's
  read-only root (overlayroot) in `armbian-config` once everything works. With
  that on, changes to the SD card don't persist, so the event cache resets each
  boot and the screen fills in after the first sync.

## How updates work

`calsync.py` asks Google for every event from the start of today through 45
days ahead, every 60 seconds, and hashes the result. When anything changes
(new event, edit, time change, delete), the version changes and the page picks
it up on its next 15 second check. If the event on screen still exists, it
stays; otherwise the rotation starts over from the next event. That's about
1,440 API calls a day per calendar, far under Google's free quota.

If WiFi drops, the screen keeps showing the last good data with a small
"Offline" badge, and syncing retries with backoff.

## Configuration (`config.json`)

| Key | Default | Notes |
|---|---|---|
| `timezone` | America/Chicago | Also set as the Pi's time zone by install.sh |
| `poll_seconds` | 60 | How often to check Google |
| `days_ahead` | 45 | How far ahead to look for events |
| `calendars` | | List of `{ "id", "name", "color" }` |
| `display.title` | "Calendar" | Header text |
| `display.seconds_per_event` | 12 | Time each event stays on screen |
| `display.max_events` | 10 | How many upcoming events to cycle through |
| `display.hour12` | true | `false` for 24-hour times |

## Troubleshooting

| Symptom | Check |
|---|---|
| Black screen or console text | `journalctl -u kiosk -b` |
| "No upcoming events" | `journalctl -u caldisplay -f`. HTTP 404 means the calendar ID is wrong or not shared with the service account |
| "Offline" badge stays | WiFi (`nmcli dev status`) or the clock (`timedatectl`, Google rejects requests if the time is far off) |
| Random reboots | USB power, see section 6 |

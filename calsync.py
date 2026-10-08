#!/usr/bin/env python3
"""Calendar kiosk, starter version.

Fetches upcoming events from Google Calendar every `poll_seconds` and serves:
  /              the display page (web/)
  /api/events    the events as JSON

  python3 calsync.py --demo     fake events, no Google setup (for UI work)
  python3 calsync.py --once     fetch once, print the events, exit (tests access)
  python3 calsync.py            normal mode (what the Pi runs)
"""
import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
KEY_FILE = os.path.join(BASE, "service-account.json")
API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

DEFAULTS = {
    "timezone": "America/Chicago",
    "poll_seconds": 60,
    "days_ahead": 45,
    "seconds_per_event": 12,
    "port": 8080,
    "calendars": [],
}

log = logging.getLogger("calsync")
lock = threading.Lock()
state = {"updated": None, "error": None, "seconds_per_event": 12, "events": []}


def load_config(path, required=True):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    elif required:
        raise SystemExit(f"Missing {path}. Copy config.example.json to config.json and fill it in.")
    return cfg


def now_in(cfg):
    return dt.datetime.now(ZoneInfo(cfg["timezone"])).replace(microsecond=0)


def when(value, tz):
    """Event start/end as a datetime. All-day events are plain dates."""
    d = dt.datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=tz)


def clean(text):
    """Google event descriptions can contain HTML. Keep plain text."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# ---------- Google Calendar ----------

def google_session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    if not os.path.exists(KEY_FILE):
        raise SystemExit(f"Missing {KEY_FILE} (the service account key from Google Cloud).")
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    return AuthorizedSession(creds)


def fetch_events(cfg, session):
    tz = ZoneInfo(cfg["timezone"])
    now = now_in(cfg)
    params = {
        "timeMin": now.isoformat(),  # anything that hasn't ended yet
        "timeMax": (now + dt.timedelta(days=cfg["days_ahead"])).isoformat(),
        "singleEvents": "true",      # expand repeating events
        "orderBy": "startTime",
        "maxResults": 250,
        "timeZone": cfg["timezone"],
    }
    events = []
    for cal in cfg["calendars"]:
        url = f"{API}/calendars/{quote(cal['id'], safe='')}/events"
        r = session.get(url, params=params, timeout=20)
        if r.status_code == 404:
            raise RuntimeError(f"Calendar {cal['id']} not found. Is it shared with the service account?")
        if r.status_code == 403:
            raise RuntimeError(f"Access denied for {cal['id']}. Is the Google Calendar API enabled? {r.text[:200]}")
        r.raise_for_status()
        for e in r.json().get("items", []):
            if e.get("status") == "cancelled":
                continue
            all_day = "date" in e["start"]
            events.append({
                "id": e["id"],
                "calendar": cal.get("name", cal["id"]),
                "color": cal.get("color", "#5b8def"),
                "title": e.get("summary", "(No title)"),
                "location": e.get("location", ""),
                "description": clean(e.get("description", "")),
                "allDay": all_day,
                "start": e["start"]["date"] if all_day else e["start"]["dateTime"],
                "end": e["end"]["date"] if all_day else e["end"]["dateTime"],
            })
    events.sort(key=lambda e: when(e["start"], tz))
    return events


def poll_forever(cfg):
    session = None
    last = None
    while True:
        try:
            session = session or google_session()
            events = fetch_events(cfg, session)
            with lock:
                state.update(events=events, error=None, updated=now_in(cfg).isoformat())
            if events != last:
                log.info("Calendar changed: %d upcoming events", len(events))
                last = events
        except Exception as ex:
            log.warning("Fetch failed: %s", ex)
            with lock:
                state["error"] = str(ex)
        time.sleep(cfg["poll_seconds"])


# ---------- Demo data ----------

def demo_events(cfg):
    tz = ZoneInfo(cfg["timezone"])
    today = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    samples = [
        # (days from today, start hour or None for all-day, minutes, title, location, description, calendar, color)
        (0, 18, 90, "Rehearsal: Jalisco", "Studio A", "Bring your practice skirt and zapatos.", "Rehearsals", "#f2a93b"),
        (1, None, 0, "Costume fitting", "", "", "Company", "#5b8def"),
        (2, 17, 60, "Kids class", "Studio B", "", "Classes", "#4caf7d"),
        (4, 10, 120, "Performance: Fall Festival", "Festival main stage", "Call time 9:15.", "Performances", "#e05a5a"),
        (6, 19, 60, "Board meeting", "Online", "", "Company", "#5b8def"),
    ]
    events = []
    for i, (days, hour, mins, title, loc, desc, cal, color) in enumerate(samples):
        day = today + dt.timedelta(days=days)
        if hour is None:
            start, end, all_day = day.date().isoformat(), (day + dt.timedelta(days=1)).date().isoformat(), True
        else:
            s = day.replace(hour=hour)
            start, end, all_day = s.isoformat(), (s + dt.timedelta(minutes=mins)).isoformat(), False
        events.append({"id": f"demo{i}", "calendar": cal, "color": color, "title": title,
                       "location": loc, "description": desc, "allDay": all_day,
                       "start": start, "end": end})
    return events


# ---------- Web server ----------

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/api/events":
            with lock:
                body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        pass  # keep the journal quiet


def main():
    ap = argparse.ArgumentParser(description="Calendar kiosk sync server")
    ap.add_argument("--config", default=os.path.join(BASE, "config.json"))
    ap.add_argument("--demo", action="store_true", help="fake events, no Google needed")
    ap.add_argument("--once", action="store_true", help="fetch once, print, exit")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 lets other computers on your WiFi open the page")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config(args.config, required=not args.demo)
    state["seconds_per_event"] = cfg["seconds_per_event"]

    if args.once:
        events = fetch_events(cfg, google_session())
        print(f"{len(events)} upcoming events:")
        for e in events:
            print(f"  {e['start'][:16].replace('T', ' '):<16}  {e['calendar']:<14}  {e['title']}")
        return

    if args.demo:
        state.update(events=demo_events(cfg), updated=now_in(cfg).isoformat())
    else:
        threading.Thread(target=poll_forever, args=(cfg,), daemon=True).start()

    server = ThreadingHTTPServer((args.host, cfg["port"]), partial(Handler, directory=WEB))
    log.info("Serving http://%s:%d", args.host, cfg["port"])
    server.serve_forever()


if __name__ == "__main__":
    main()

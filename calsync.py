#!/usr/bin/env python3
"""Calendar kiosk sync server.

Polls Google Calendar with a service account, keeps the upcoming events in
memory (and in cache.json so the screen has something to show right after
boot), and serves the kiosk UI plus /api/events on http://127.0.0.1:8080.

    python3 calsync.py --demo     # fake events, no Google setup (for UI work)
    python3 calsync.py            # real calendars from config.json

Demo mode only needs the Python standard library. Real mode needs the
google-auth and requests packages (install.sh installs them on the Pi).
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{}/events"
DISPLAY_DEFAULTS = {
    "title": "Calendar",
    "seconds_per_event": 12,
    "max_events": 10,
    "hour12": True,
    "qr_url": "",     # link the QR code opens; empty hides the QR code
    "qr_text": "Dancers: scan to sign up for the private calendar",
}

log = logging.getLogger("calsync")
lock = threading.Lock()
state = {
    "version": "",
    "events": [],
    "display": dict(DISPLAY_DEFAULTS),
    "timezone": "America/Chicago",
    "synced": None,   # time of the last successful sync
    "error": None,    # last sync error, None when healthy
}

for ext, ctype in ((".js", "text/javascript"), (".css", "text/css"),
                   (".svg", "image/svg+xml"), (".woff2", "font/woff2"),
                   (".json", "application/json")):
    mimetypes.add_type(ctype, ext)


# ---------------------------------------------------------------- config

def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("timezone", "America/Chicago")
    cfg.setdefault("poll_seconds", 60)
    cfg.setdefault("days_ahead", 45)
    cfg.setdefault("service_account_file", "service-account.json")
    cfg.setdefault("calendars", [])
    cfg["display"] = {**DISPLAY_DEFAULTS, **cfg.get("display", {})}
    ZoneInfo(cfg["timezone"])  # fail early on a typo
    return cfg


# ---------------------------------------------------------- google calendar

class _TextOnly:
    """Turns the HTML that Google Calendar puts in descriptions into text."""

    BREAKS = re.compile(r"<\s*(br|/p|/div|/li|/h\d)\s*/?\s*>", re.I)
    TAGS = re.compile(r"<[^>]+>")

    @classmethod
    def clean(cls, text):
        if not text:
            return ""
        text = cls.BREAKS.sub("\n", text)
        text = cls.TAGS.sub("", text)
        text = html.unescape(text).replace("\xa0", " ")
        lines = [ln.strip() for ln in text.splitlines()]
        text = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(item, cal, tz):
    """One Google event -> the small dict the UI uses (None to skip it)."""
    if item.get("status") == "cancelled":
        return None
    s, e = item.get("start") or {}, item.get("end") or {}
    all_day = "date" in s
    if all_day:
        start = dt.datetime.combine(dt.date.fromisoformat(s["date"]), dt.time(), tz)
        end = dt.datetime.combine(dt.date.fromisoformat(e.get("date", s["date"])), dt.time(), tz)
        if end <= start:
            end = start + dt.timedelta(days=1)
    elif "dateTime" in s:
        start = dt.datetime.fromisoformat(s["dateTime"]).astimezone(tz)
        end = dt.datetime.fromisoformat(e.get("dateTime", s["dateTime"])).astimezone(tz)
    else:
        return None
    return {
        "id": f"{cal['id']}/{item.get('id', '')}",
        "uid": item.get("iCalUID", item.get("id", "")),
        "calendar": cal.get("name", ""),
        "color": cal.get("color", ""),
        "title": (item.get("summary") or "(No title)").strip(),
        "location": (item.get("location") or "").strip(),
        "description": _TextOnly.clean(item.get("description")),
        "allDay": all_day,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def make_session(cfg):
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("Missing packages. On the Pi run install.sh; elsewhere: "
                 "pip install google-auth requests (or use --demo)")
    key = HERE / cfg["service_account_file"]
    if not key.is_file():
        raise FileNotFoundError(f"service account key not found: {key}")
    creds = service_account.Credentials.from_service_account_file(str(key), scopes=SCOPES)
    return AuthorizedSession(creds)


def fetch_google(cfg, session):
    if not cfg["calendars"]:
        raise ValueError("no calendars listed in config.json")
    tz = ZoneInfo(cfg["timezone"])
    now = dt.datetime.now(tz)
    # timeMin filters on an event's END, so anything still in progress today
    # is included.
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Always reach the end of next month, so both calendars on screen are complete.
    first_of_next = (now.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    end_of_next = (first_of_next + dt.timedelta(days=32)).replace(day=1)
    time_max = max(now + dt.timedelta(days=cfg["days_ahead"]),
                   end_of_next.replace(hour=0, minute=0, second=0, microsecond=0))
    events, seen = [], set()
    for cal in cfg["calendars"]:
        params = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
            "fields": "items(id,iCalUID,status,summary,location,description,start,end),nextPageToken",
        }
        url = EVENTS_URL.format(quote(cal["id"], safe=""))
        while True:
            r = session.get(url, params=params, timeout=20)
            if r.status_code != 200:
                hint = ""
                if r.status_code == 404:
                    hint = " (check the calendar ID and that it is shared with the service account)"
                raise RuntimeError(f"{cal.get('name') or cal['id']}: HTTP {r.status_code}{hint}")
            data = r.json()
            for item in data.get("items", []):
                ev = normalize(item, cal, tz)
                if not ev:
                    continue
                key = (ev["uid"], ev["start"])  # same event on two shared calendars
                if key in seen:
                    continue
                seen.add(key)
                events.append(ev)
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
    return sort_events(events)


def sort_events(events):
    def key(ev):
        return (dt.datetime.fromisoformat(ev["start"]), not ev["allDay"], ev["title"])
    return sorted(events, key=key)


# ------------------------------------------------------------------- demo

DEMO_CALENDARS = [
    {"id": "demo-rrbf", "name": "RRBF", "color": "#e4572e"},
    {"id": "demo-rehearsals", "name": "Rehearsals", "color": "#f2a93b"},
    {"id": "demo-shows", "name": "Performances", "color": "#3fa7d6"},
]

# (days from today, "HH:MM" or None for all day, minutes or days, calendar, title, location, description)
DEMO_EVENTS = [
    (0, "now", 90, 1, "Rehearsal: Jalisco", "Studio A",
     "Bring your practice skirt and zapatos. We will run the full suite twice."),
    (0, "+180", 60, 0, "Costume fitting", "Wardrobe room", "Veracruz dresses for the spring show."),
    (1, None, 1, 0, "Studio closed", "", ""),
    (1, "18:00", 90, 1, "Rehearsal: Veracruz", "Studio B", "Zapateado drills, then La Bamba."),
    (2, "10:00", 120, 2, "Community festival", "Round Rock Old Settlers Park",
     "Call time 9:15. Full costume. Families welcome."),
    (4, "18:30", 60, 0, "Board meeting", "Zoom", "Budget review and fall show planning."),
    (6, None, 3, 2, "Folklórico workshop weekend", "Austin", "Guest maestra from Guadalajara."),
    (9, "17:00", 120, 1, "Dress rehearsal", "Main stage", ""),
    (11, "19:00", 150, 2, "Fall showcase", "Round Rock Performing Arts Center",
     "Doors at 6:30. Dancers arrive by 5:00 for makeup."),
    (15, "12:00", 60, 0, "Volunteer lunch", "Studio lobby", "Tamales and thank-you cards."),
]


def demo_events(cfg, anchor):
    tz = ZoneInfo(cfg["timezone"])
    cals = cfg["calendars"] or DEMO_CALENDARS
    today = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    events = []
    for i, (days, at, length, ci, title, loc, desc) in enumerate(DEMO_EVENTS):
        cal = cals[ci % len(cals)]
        day = today + dt.timedelta(days=days)
        if at is None:
            item = {"start": {"date": day.date().isoformat()},
                    "end": {"date": (day + dt.timedelta(days=length)).date().isoformat()}}
        else:
            if at == "now":
                start = anchor.replace(second=0, microsecond=0) - dt.timedelta(minutes=20)
            elif at.startswith("+"):
                start = anchor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(minutes=int(at[1:]))
            else:
                hh, mm = map(int, at.split(":"))
                start = day.replace(hour=hh, minute=mm)
            end = start + dt.timedelta(minutes=length)
            item = {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        item.update(id=f"demo{i}", summary=title, location=loc, description=desc)
        events.append(normalize(item, cal, tz))
    return sort_events(events)


# ------------------------------------------------------------ state, cache

def write_cache(path, snapshot):
    """Atomic write with fsync, so a power cut can't leave a half file."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def load_cache(path, cfg):
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        with lock:
            state["events"] = cached.get("events", [])
            state["version"] = cached.get("version", "")
            state["synced"] = cached.get("synced")
        log.info("Loaded %d cached events from %s", len(state["events"]), path.name)
    except FileNotFoundError:
        pass
    except Exception as e:  # corrupt cache: ignore, first sync replaces it
        log.warning("Ignoring unreadable cache (%s)", e)


def publish(cfg, events, cache_path):
    blob = json.dumps(events, sort_keys=True)
    version = hashlib.sha1(blob.encode()).hexdigest()[:12]
    synced = dt.datetime.now(ZoneInfo(cfg["timezone"])).isoformat(timespec="seconds")
    with lock:
        changed = version != state["version"]
        state.update(version=version, events=events, synced=synced, error=None)
        snapshot = {"version": version, "events": events, "synced": synced}
    # Only write to the SD card when the calendar actually changed.
    if changed and cache_path:
        try:
            write_cache(cache_path, snapshot)
        except OSError as e:
            log.warning("Could not write cache: %s", e)
    return changed, version


def sync_loop(cfg, demo, cache_path):
    session, failures = None, 0
    anchor = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    while True:
        try:
            if demo:
                events = demo_events(cfg, anchor)
            else:
                if session is None:
                    session = make_session(cfg)
                events = fetch_google(cfg, session)
            changed, version = publish(cfg, events, cache_path)
            if changed:
                log.info("Calendar changed: %d events (version %s)", len(events), version)
            if failures:
                log.info("Sync recovered")
            failures, wait = 0, cfg["poll_seconds"]
        except Exception as e:
            failures += 1
            msg = f"{type(e).__name__}: {e}"
            with lock:
                state["error"] = msg
            wait = min(10 * 2 ** (failures - 1), 300)
            log.warning("Sync failed (%s). Retrying in %ss", msg, wait)
            if isinstance(e, (FileNotFoundError, ValueError)):
                session = None
        time.sleep(wait)


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "calsync"

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/events":
            with lock:
                body = json.dumps(state).encode()
            return self._send(200, body, "application/json")
        if path in ("", "/"):
            path = "/index.html"
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            return self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _send(self, code, body, ctype):
        if ctype.startswith("text/") or ctype.endswith("json") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="config file (default: config.json)")
    ap.add_argument("--demo", action="store_true", help="show fake events, no Google needed")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg_path = Path(args.config) if args.config else HERE / "config.json"
    if not cfg_path.is_file() and args.demo:
        cfg_path = HERE / "config.example.json"
    if not cfg_path.is_file():
        sys.exit(f"{cfg_path} not found. Copy config.example.json to config.json and fill it in.")
    cfg = load_config(cfg_path)
    if args.demo:
        cfg["calendars"] = [c for c in cfg["calendars"] if c.get("name")] or DEMO_CALENDARS
        cfg["poll_seconds"] = 15
    with lock:
        state["display"] = cfg["display"]
        state["timezone"] = cfg["timezone"]

    cache_path = None if args.demo else HERE / "cache.json"
    if cache_path:
        load_cache(cache_path, cfg)

    threading.Thread(target=sync_loop, args=(cfg, args.demo, cache_path), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Serving on http://%s:%d%s", args.host, args.port, " (demo mode)" if args.demo else "")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

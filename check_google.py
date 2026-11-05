#!/usr/bin/env python3
"""Checks that the kiosk can reach Google Calendar with your config.

    python check_google.py              # uses config.json in this folder
    python check_google.py --config other.json

Tests, in order: the key file, signing in to Google, and each calendar in
config.json (lists its next few events). Read-only: changes nothing.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{}/events"

OK, FAIL = "  OK  ", "  FAIL"


def fail(msg, hint=None):
    print(f"{FAIL} {msg}")
    if hint:
        print(f"         {hint}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.json"))
    args = ap.parse_args()

    # 1. packages
    try:
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2 import service_account
    except ImportError:
        fail("google-auth / requests not installed",
             "Run: ~/.venvs/caldisplay/bin/pip install google-auth requests")

    # 2. config.json
    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        fail(f"{cfg_path.name} not found",
             "Copy config.example.json to config.json and fill in your calendars.")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{cfg_path.name} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}",
             "Usually a missing or extra comma, or a missing quote.")
    cals = cfg.get("calendars") or []
    if not cals:
        fail("config.json has no calendars listed")
    tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
    print(f"{OK} config.json: {len(cals)} calendar(s), time zone {tz.key}")

    # 3. key file
    key = HERE / cfg.get("service_account_file", "service-account.json")
    if not key.is_file():
        fail(f"{key.name} not found in {HERE}",
             "Download the JSON key for the service account and save it here with this name.")
    try:
        creds = service_account.Credentials.from_service_account_file(str(key), scopes=SCOPES)
    except Exception as e:
        fail(f"{key.name} could not be read: {e}",
             "Make sure it's the JSON key you downloaded, not a different file.")
    print(f"{OK} key file: service account {creds.service_account_email}")

    # 4. sign in to Google
    try:
        creds.refresh(Request())
    except Exception as e:
        text = str(e)
        hint = None
        if "invalid_grant" in text:
            hint = ("Either the computer's clock is wrong, or this key was deleted in "
                    "Google Cloud. Check the time, or create a new key.")
        fail(f"could not sign in to Google: {text[:200]}", hint)
    print(f"{OK} signed in to Google")

    # 5. each calendar
    session = AuthorizedSession(creds)
    now = dt.datetime.now(tz)
    problems = 0
    for cal in cals:
        name = cal.get("name") or cal.get("id")
        cid = cal.get("id", "")
        params = {
            "timeMin": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "timeMax": (now + dt.timedelta(days=cfg.get("days_ahead", 45))).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "250",
        }
        try:
            r = session.get(EVENTS_URL.format(quote(cid, safe="")), params=params, timeout=20)
        except Exception as e:
            print(f"{FAIL} {name}: could not connect ({e})")
            problems += 1
            continue

        if r.status_code == 200:
            items = [i for i in r.json().get("items", []) if i.get("status") != "cancelled"]
            print(f"{OK} {name}: {len(items)} upcoming event(s)")
            for item in items[:3]:
                s = item.get("start", {})
                when = s.get("dateTime") or s.get("date", "")
                if "dateTime" in s:
                    when = dt.datetime.fromisoformat(when).astimezone(tz).strftime("%a %b %d %I:%M %p")
                else:
                    when = dt.date.fromisoformat(when).strftime("%a %b %d") + " (all day)"
                print(f"         {when}  {item.get('summary', '(No title)')}")
            continue

        problems += 1
        try:
            reason = r.json()["error"]["message"]
        except Exception:
            reason = r.text[:200]
        print(f"{FAIL} {name}: HTTP {r.status_code}: {reason}")
        if r.status_code == 404:
            print(f"         Calendar ID '{cid}' wasn't found for this service account. Either the ID is")
            print(f"         wrong, or the calendar isn't shared with {creds.service_account_email}")
            print("         (Google Calendar > Settings and sharing > Share with specific people).")
        elif r.status_code == 403 and ("has not been used" in reason or "disabled" in reason):
            print("         The Google Calendar API isn't turned on for this Cloud project.")
            print("         Google Cloud console > APIs and Services > Library > Google Calendar API > Enable.")
        elif r.status_code == 403:
            print("         The service account doesn't have permission to read this calendar's events.")
            print("         Share it with 'See all event details', not just free/busy.")

    print()
    if problems:
        print(f"{problems} calendar(s) failed. Fix those and run this again.")
        sys.exit(1)
    print("All good. The kiosk can read your calendars.")


if __name__ == "__main__":
    main()
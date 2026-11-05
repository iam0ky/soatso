#!/usr/bin/env python3
"""
Solar Units Dashboard for Deye Cloud
------------------------------------
Pulls your solar generation from the Deye Cloud OpenAPI and builds a friendly,
self-contained HTML dashboard (solar_dashboard.html) you can open on any
phone or laptop.

What the dashboard shows:
  * Units generated today, this month, this year and all-time (1 unit = 1 kWh)
  * Charts: today hour-by-hour, last 30 days, month-by-month, year-by-year
  * Panel health: each inverter input (PV1, PV2 ...) and each individual string
    is checked against the others and against its own normal, to catch a
    failed panel, a dead string, shading or dirt early
  * Optional alert by Telegram and/or SMS (Fast2SMS) when health changes

Setup (one time):
  1. Log in at https://developer.deyecloud.com/ with your Deye Cloud account
     -> Application -> Create App -> copy App ID and App Secret.
  2. Fill in the CONFIG block below (APP_ID, APP_SECRET, EMAIL/MOBILE, PASSWORD).
  3. pip install requests          (optional on Windows: pip install tzdata)

Options:
  python solar_chart.py               fetch data, build dashboard, open it
  python solar_chart.py --demo        sample data with a simulated fault (no keys)
  python solar_chart.py --stations    check login; list plants, inverter, inputs
  python solar_chart.py --probe       print raw inverter API replies (troubleshooting)
  python solar_chart.py --test-alert  send a test Telegram/SMS alert
  python solar_chart.py --no-open     build dashboard without opening the browser
  python solar_chart.py -h            help

Files it writes (next to where you run it):
  solar_dashboard.html   the dashboard
  solar_history.csv      daily units since the system started (grows each run)
  strings_history.json   last 30 days of per-input / per-string readings + wiring
  alert_state.json       last alert sent (so you aren't messaged twice)

Run it once every evening (Task Scheduler / cron) so health checks use full days.
"""

from __future__ import annotations

import argparse
import os
import csv
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from datetime import timezone

# Fallback offsets used when the OS has no time-zone database (common on
# Windows unless `pip install tzdata` is done).
_FIXED_OFFSETS = {"Asia/Kolkata": 5.5, "Asia/Calcutta": 5.5, "Asia/Dubai": 4,
                  "Asia/Singapore": 8, "UTC": 0}


def get_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    except Exception:
        if TIMEZONE in _FIXED_OFFSETS:
            return timezone(timedelta(hours=_FIXED_OFFSETS[TIMEZONE]), TIMEZONE)
        print(f"Note: time zone '{TIMEZONE}' not found; using this computer's local time. "
              "Run `pip install tzdata` to fix.")
        return datetime.now().astimezone().tzinfo

# ============================== CONFIG ======================================
APP_ID = ""                 # from developer.deyecloud.com -> your app
APP_SECRET = ""
EMAIL = ""                  # Deye Cloud login email ...
MOBILE = ""                 # ... or mobile number (leave EMAIL blank then)
COUNTRY_CODE = "91"
PASSWORD = ""               # plain password; the script SHA-256 hashes it

# Most accounts outside the Americas live on eu1. If login fails with a
# "user not found"-type error, try "https://us1-developer.deyecloud.com".
BASE_URL = "https://eu1-developer.deyecloud.com"

STATION_ID = ""             # blank = your only/first station
START_DATE = ""             # DD/MM/YYYY, e.g. "01/01/2024"; blank = station's commissioning date
TIMEZONE = "Asia/Kolkata"

DISPLAY_NAME = ""           # dashboard heading + browser tab title; blank = plant name from Deye
# Dashboard colours: "clean" (white + blue), "leaf" (soft green), "sunny"
# (warm cream + orange) are always light; "night" is dark. The dashboard also
# has a Theme picker in the top-right corner to try them live.
THEME = "clean"

# ---- String health -----------------------------------------------------------
CHECK_STRINGS = True
# Panels on each string, if you know them (installer sheet). Makes the
# "one panel failed" detection sharper. Leave {} to auto-judge.
# Panel layout per input. Each input (PV1, PV2…) has one or more strings wired
# side by side; each string is a chain of panels in series.
#   STRING_PANELS    = panels IN SERIES in one string on that input
#   PARALLEL_STRINGS = how many such strings share that input
# Example for 60 panels as 2 strings of 15 on each of PV1 and PV2:
#   STRING_PANELS = {"PV1": 15, "PV2": 15};  PARALLEL_STRINGS = {"PV1": 2, "PV2": 2}
# Leave them {} and the script estimates them (shown with ≈).
STRING_PANELS = {}
PARALLEL_STRINGS = {}
# Voltage of one panel on a hot midday, used for the estimate:
# ~39 V for 600–630 W panels, ~37.5 V for 530–550 W, ~31 V for 330–400 W.
PANEL_VOLTS_HOT = 37.5
PANEL_WATTS = 0             # e.g. 620 — needed to estimate parallel strings, and cross-checks system size
STRING_DAYS = 30            # days of per-string history to keep/show
MIDDAY = (11, 14)           # hours used for comparisons (strongest, steadiest sun)

# ---- Alerts (optional; leave blank to disable) ---------------------------------
TELEGRAM_BOT_TOKEN = ""     # from @BotFather
TELEGRAM_CHAT_ID = ""       # message your bot, then see getUpdates for chat id
FAST2SMS_API_KEY = ""       # Fast2SMS dashboard -> Dev API
FAST2SMS_NUMBERS = ""       # "98xxxxxxxx" or comma-separated list
ALERT_ON_RECOVERY = True    # also notify when things go back to normal

# ---- Public publishing (GitHub) -------------------------------------------------
# If set, the dashboard's data is encrypted with this password: the page shows
# a password box and only decrypts in your browser. Needs `pip install cryptography`.
DASHBOARD_PASSWORD = ""
REQUIRE_PASSWORD = False    # True = refuse to write an unlocked dashboard
MASK_IDS = False            # True = shorten plant name / IDs in printed output (public logs)

# ---- Morning update for residents ------------------------------------------------
# Each morning (first run between 06:00 and 11:00) a friendly summary is written:
# yesterday's units, today's weather outlook for the rooftop and a closing line.
RESIDENT_UPDATE = True
COMMUNITY_NAME = ""         # e.g. "Sumukha Opal"; blank = DISPLAY_NAME
DASHBOARD_URL = ""          # your GitHub Pages URL (only used if UPDATE_DASHBOARD_LINK is on)
UPDATE_CLOSING_LINE = False # add a friendly closing line ("Clean energy, made right on our own roof 💚")
UPDATE_DASHBOARD_LINK = False  # end the message with the dashboard link
RESIDENT_TELEGRAM_CHAT = "" # a Telegram channel for residents, e.g. "@sumukhasolar"
                            # (add your bot to the channel as an admin). Blank = the update
                            # is sent to TELEGRAM_CHAT_ID (you) with a one-tap WhatsApp share link.
SHOW_HEALTH_TO_RESIDENTS = False  # panel health stays on the dashboard + your private alerts
WEATHER_PLACE = "Bengaluru" # name shown in the forecast line
LATITUDE = None             # weather location; None = taken from Deye, else Bengaluru
LONGITUDE = None
MORNING_WINDOW = (6, 11)    # hours in which the daily update is sent (once per day)

HISTORY_CSV = "solar_history.csv"
STRINGS_JSON = "strings_history.json"
ALERT_STATE = "alert_state.json"
FORECAST_JSON = "forecast_log.json"      # forecast vs actual, to sharpen "expected units"
ALERTS_JSON = "inverter_alerts.json"      # the inverter's own fault/warning log (last 90 days)
ALERT_DAYS = 30                           # how far back the dashboard lists inverter alerts
UPDATE_TXT = "resident_update.txt"        # the latest resident message, ready to copy
OUTPUT_HTML = "solar_dashboard.html"
REFRESH_LAST_DAYS = 3       # re-fetch these recent days each run (late data)
OPEN_IN_BROWSER = True
# When the dashboard is refreshed automatically, in your local time (24-hour HH:MM).
# Shown on the page as "Next update around …". Leave "" if you run it by hand.
RUN_TIMES = ""              # e.g. "08:00,14:00,20:00"
# ============================================================================

# ---- Environment overrides (used by GitHub Actions; keeps secrets out of the file)
# Any CONFIG name above can be set as an environment variable of the same name,
# e.g. APP_ID, APP_SECRET, EMAIL, PASSWORD, STRING_PANELS='{"PV1":10,"PV2":8}'.
def _apply_env_overrides():
    g = globals()
    for name in ("APP_ID", "APP_SECRET", "EMAIL", "MOBILE", "COUNTRY_CODE", "PASSWORD", "BASE_URL",
                 "STATION_ID", "START_DATE", "TIMEZONE", "DISPLAY_NAME", "THEME", "RUN_TIMES",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FAST2SMS_API_KEY", "FAST2SMS_NUMBERS",
                 "HISTORY_CSV", "STRINGS_JSON", "ALERT_STATE", "OUTPUT_HTML", "DASHBOARD_PASSWORD",
                 "COMMUNITY_NAME", "DASHBOARD_URL", "RESIDENT_TELEGRAM_CHAT", "FORECAST_JSON", "UPDATE_TXT",
                 "ALERTS_JSON"):
        if os.environ.get(name):
            g[name] = os.environ[name]
    for name in ("PANEL_VOLTS_HOT", "PANEL_WATTS"):
        if os.environ.get(name):
            g[name] = float(os.environ[name])
    for name in ("STRING_PANELS", "PARALLEL_STRINGS"):
        if os.environ.get(name):
            g[name] = json.loads(os.environ[name])
    for name in ("REQUIRE_PASSWORD", "MASK_IDS"):
        if os.environ.get(name):
            g[name] = os.environ[name].strip().lower() in ("1", "true", "yes")
    if os.environ.get("CI"):                       # never try to open a browser on a server
        g["OPEN_IN_BROWSER"] = False
    if os.environ.get("WEATHER_PLACE"):
        g["WEATHER_PLACE"] = os.environ["WEATHER_PLACE"]
    for name in ("LATITUDE", "LONGITUDE"):
        if os.environ.get(name):
            g[name] = float(os.environ[name])
    for name in ("RESIDENT_UPDATE", "SHOW_HEALTH_TO_RESIDENTS"):
        if os.environ.get(name):
            g[name] = os.environ[name].strip().lower() in ("1", "true", "yes")
    for path in (g["HISTORY_CSV"], g["STRINGS_JSON"], g["ALERT_STATE"], g["OUTPUT_HTML"],
                 g["FORECAST_JSON"], g["UPDATE_TXT"], g["ALERTS_JSON"]):
        Path(path).parent.mkdir(parents=True, exist_ok=True)


_apply_env_overrides()

SUCCESS_CODE = "1000000"
DAILY_CHUNK_DAYS = 30       # API rejects daily spans of 31+ days
HISTORY_FIELDS = ["date", "generation", "consumption", "bought", "exported"]
PV_KEY = re.compile(r"^DC(Power|Voltage|Current)PV(\d+)$", re.I)
BRANCH_KEY = re.compile(r"^(?:CurrentBranch|BranchCurrent)(\d+)$", re.I)


# ------------------------------------------------------------------ API client
class DeyeError(RuntimeError):
    pass


class Deye:
    def __init__(self):
        import requests  # imported here so --demo works without it
        self.http = requests.Session()
        self.base = BASE_URL.rstrip("/")
        self.token = None
        self.token_expires = 0.0

    def _post(self, path, body, auth=True, _retried=False):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._get_token()}"
        for attempt in range(4):
            try:
                r = self.http.post(f"{self.base}/v1.0/{path}", json=body,
                                   headers=headers, timeout=30)
            except Exception as e:  # network hiccup
                if attempt == 3:
                    raise DeyeError(f"{path}: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401 and auth and not _retried:
                self.token = None
                return self._post(path, body, auth, _retried=True)
            if r.status_code >= 400:
                try:
                    j = r.json()
                    why = j.get("msg") or j.get("message") or j.get("error") or r.text[:200]
                    code = j.get("code", "")
                except ValueError:
                    why, code = r.text[:200], ""
                raise DeyeError(f"{path}: HTTP {r.status_code} code={code} msg={why}")
            data = r.json()
            code = str(data.get("code", SUCCESS_CODE))
            # 2101019 = token invalid, returned as HTTP 200 + success:false
            if code == "2101019" and auth and not _retried:
                self.token = None
                return self._post(path, body, auth, _retried=True)
            if data.get("success") is False or code != SUCCESS_CODE:
                raise DeyeError(f"{path}: code={code} msg={data.get('msg')}")
            time.sleep(0.4)  # be gentle with the API
            return data
        raise DeyeError(f"{path}: gave up after retries")

    def _get_token(self):
        if self.token and time.time() < self.token_expires - 60:
            return self.token
        if not (APP_ID and APP_SECRET and PASSWORD and (EMAIL or MOBILE)):
            sys.exit("Fill APP_ID, APP_SECRET, PASSWORD and EMAIL (or MOBILE) "
                     "in the CONFIG block — or run with --demo.")
        body = {"appSecret": APP_SECRET,
                "password": hashlib.sha256(PASSWORD.encode()).hexdigest()}
        if EMAIL:
            body["email"] = EMAIL
        else:
            body.update(mobile=MOBILE, countryCode=COUNTRY_CODE)
        data = self._post(f"account/token?appId={APP_ID}", body, auth=False)
        tok = data.get("accessToken") or (data.get("data") or {}).get("accessToken")
        if not tok:
            raise DeyeError(f"No token returned: {data.get('msg')}")
        self.token = tok[7:] if tok.lower().startswith("bearer ") else tok
        self.token_expires = time.time() + float(data.get("expiresIn") or 3600)
        return self.token

    # station level
    def stations(self):
        return self._post("station/list", {"page": 1, "size": 100}).get("stationList") or []

    def daily(self, station_id, start: date, end_excl: date):
        data = self._post("station/history", {
            "stationId": station_id, "granularity": 2,
            "startAt": start.isoformat(), "endAt": end_excl.isoformat()})
        return data.get("stationDataItems") or []

    def frames(self, station_id, day: date):
        data = self._post("station/history", {
            "stationId": station_id, "granularity": 1,
            "startAt": day.isoformat(),
            "endAt": (day + timedelta(days=1)).isoformat()})
        return data.get("stationDataItems") or []

    # device level (per-string data lives here)
    def alerts(self, station_id, start_ts, end_ts, page=1, size=50):
        data = self._post("station/alertList", {"stationId": station_id, "startTimestamp": int(start_ts),
                                                "endTimestamp": int(end_ts), "page": page, "size": size})
        return data.get("stationAlertItems") or data.get("alertList") or []

    def devices(self, station_id):
        data = self._post("station/device", {"stationIds": [station_id], "page": 1, "size": 50})
        return data.get("deviceListItems") or []

    def measure_points(self, sn, dtype=None):
        body = {"deviceSn": sn}
        if dtype:
            body["deviceType"] = dtype
        return self._post("device/measurePoints", body).get("measurePoints") or []

    def _history_bodies(self, sn, day, points, dtype):
        d0, d1 = day.isoformat(), (day + timedelta(days=1)).isoformat()
        base = {"deviceSn": sn, "granularity": 1, "startAt": d0, "measurePoints": points}
        yield "start=end", {**base, "endAt": d0}
        yield "end=next day", {**base, "endAt": d1}
        if dtype:
            yield "with deviceType", {**base, "endAt": d0, "deviceType": dtype}

    def _history_once(self, sn, day, points, dtype):
        variants = list(self._history_bodies(sn, day, points, dtype))
        good = getattr(self, "_dh_good", None)
        if good:
            variants.sort(key=lambda v: v[0] != good)
        errors = []
        for name, body in variants:
            try:
                data = self._post("device/history", body)
            except DeyeError as e:
                if "2101007" in str(e):          # too many points in one request
                    raise
                errors.append(f"[{name}] {e}")
                continue
            self._dh_good = name
            return data.get("dataList") or []
        raise DeyeError("device/history rejected every request format:\n    " + "\n    ".join(errors))

    def device_day(self, sn, day: date, points, dtype=None):
        """Fetch one day of readings, splitting the point list into small batches
        (Deye answers 'list too long' when too many are asked for at once)."""
        merged = {}
        i = 0
        while i < len(points):
            size = getattr(self, "_chunk", 5)
            batch = points[i:i + size]
            try:
                rows = self._history_once(sn, day, batch, dtype)
            except DeyeError as e:
                if "2101007" in str(e) and size > 1:
                    self._chunk = max(1, size // 2)
                    continue
                raise
            for fr in rows:
                t = fr.get("time")
                slot = merged.setdefault(t, {"time": t, "itemList": []})
                slot["itemList"].extend(fr.get("itemList") or [])
            i += len(batch)
        return [merged[k] for k in sorted(merged, key=str)]


# ------------------------------------------------------------------ helpers
def masked(value, keep=4):
    """Shorten identifiers in printed output when MASK_IDS is on (public logs)."""
    text = str(value or "")
    if not MASK_IDS:
        return text
    return "..." + text[-keep:] if len(text) > keep else "***"


def parse_date_in(text):
    """Read a date written the Indian way (DD/MM/YYYY or DD-MM-YYYY); YYYY-MM-DD also works."""
    t = str(text).strip().replace(".", "/").replace("-", "/")
    parts = t.split("/")
    try:
        if len(parts) == 3 and len(parts[0]) == 4:          # YYYY/MM/DD
            y, m, d = map(int, parts)
        elif len(parts) == 3:                               # DD/MM/YYYY
            d, m, y = map(int, parts)
        else:
            raise ValueError
        return date(y, m, d)
    except ValueError:
        sys.exit(f'START_DATE "{text}" isn\'t a valid date — use DD/MM/YYYY, e.g. "01/01/2024".')


def num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_ts(v, tz):
    """Epoch seconds/ms (number or string) or a 'YYYY-MM-DD HH:MM[:SS]' string."""
    if v in (None, ""):
        return None
    try:
        n = float(v)
        if n > 1e10:
            n /= 1000
        return datetime.fromtimestamp(n, tz)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(v).replace("/", "-"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        return None


def load_history(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def save_history(path: Path, rows: dict):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in HISTORY_FIELDS})
    tmp.replace(path)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(path)


def pick_station(api: Deye):
    stations = api.stations()
    if not stations:
        sys.exit("No stations found on this account.")
    if STATION_ID:
        st = next((s for s in stations if str(s.get("id")) == str(STATION_ID)), None)
        if not st:
            sys.exit(f"Station {STATION_ID} not found. Run with --stations to list them.")
        return st
    if len(stations) > 1:
        print(f"Note: {len(stations)} stations found, using the first. "
              "Set STATION_ID to choose another.")
    return stations[0]


# ------------------------------------------------------------------ string data
def item_pairs(item_list):
    """device/history itemList: [{key,value,unit}, ...] or [{k: v, ...}]."""
    for m in item_list or []:
        if not isinstance(m, dict):
            continue
        if "key" in m and "value" in m:
            yield m["key"], m["value"]
        else:
            for k, v in m.items():
                if k not in ("unit", "name"):
                    yield k, v


def frames_to_strings(rows, label):
    """rows: [(datetime, {key: value})] -> {string: [(dt, P_W, V, I)]}"""
    out = {}
    for dt, kv in rows:
        idx = {}
        for k, v in kv.items():
            m = PV_KEY.match(k)
            if m:
                idx.setdefault(m.group(2), {})[m.group(1).lower()] = num(v)
        for n, d in idx.items():
            v, i = d.get("voltage", 0.0), d.get("current", 0.0)
            p = d.get("power") if d.get("power") else v * i
            out.setdefault(label(n), []).append((dt, p, v, i))
    return out


def summarise_day(series):
    """[(dt, P, V, I)] -> {kwh, p, v, i} with midday averages."""
    series = sorted(series)
    kwh = 0.0
    for (t0, p0, *_), (t1, p1, *_) in zip(series, series[1:]):
        gap = (t1 - t0).total_seconds()
        if 0 < gap <= 3600:
            kwh += (p0 + p1) / 2 * gap / 3600 / 1000
    mid = [s for s in series if MIDDAY[0] <= s[0].hour < MIDDAY[1]]
    avg = lambda k: round(statistics.fmean(s[k] for s in mid), 1) if mid else 0.0
    imax = max((x[3] for x in series if x[2] > 30), default=0.0)   # peak current of the day
    return {"kwh": round(kwh, 2), "p": avg(1), "v": avg(2),
            "i": round(statistics.fmean(s[3] for s in mid), 2) if mid else 0.0, "imax": round(imax, 2)}


def frames_to_branches(rows):
    """rows -> {"S<n>": [(dt, amps)]} from CurrentBranch<n> readings."""
    out = {}
    for dt, kv in rows:
        for k, v in kv.items():
            m = BRANCH_KEY.match(k)
            if m:
                out.setdefault(f"S{int(m.group(1))}", []).append((dt, num(v)))
    return out


def summarise_branch(series):
    mid = [a for dt, a in series if MIDDAY[0] <= dt.hour < MIDDAY[1]]
    return {"i": round(statistics.fmean(mid), 2) if mid else 0.0}


def discover_points(api, sn, dtype, all_pts, tz, today):
    """Find which inputs/strings are actually wired, using yesterday's data."""
    pv = [p for p in all_pts if PV_KEY.match(p)]
    br = [p for p in all_pts if BRANCH_KEY.match(p)]
    probe_day = today - timedelta(days=1)
    rows = api.device_day(sn, probe_day, [p for p in pv if "Voltage" in p] + br, dtype)
    peak = {}
    for fr in rows:
        for k, v in item_pairs(fr.get("itemList")):
            peak[k] = max(peak.get(k, 0.0), num(v))
    live_pv = sorted({PV_KEY.match(k).group(2) for k, v in peak.items()
                      if PV_KEY.match(k) and v > 30}, key=int)
    live_br = [p for p in br if peak.get(p, 0) > 0.3]
    pts = [p for p in pv if PV_KEY.match(p).group(2) in live_pv] + live_br
    print(f"  inverter ...{sn[-4:]}: inputs in use {', '.join('PV' + n for n in live_pv) or 'none'}; "
          f"{len(live_br)} individual string current(s)")
    return pts


def collect_strings(api: Deye, sid, tz, today):
    devs = [d for d in api.devices(sid)
            if "INVERTER" in str(d.get("deviceType", "")).upper() or not d.get("deviceType")]
    if not devs:
        print("  no inverter found for string data")
        return None
    multi = len(devs) > 1
    store = load_json(Path(STRINGS_JSON), {"days": {}})
    store.setdefault("bdays", {})
    channels = store.setdefault("channels", {})
    if channels.get("checked") != today.isoformat() and today.day == 1:
        channels.clear()                       # re-check wiring once a month
    plan = []
    for d in devs:
        sn, dtype = d["deviceSn"], d.get("deviceType")
        pts = channels.get(sn)
        if not pts:
            try:
                all_pts = [p for p in api.measure_points(sn, dtype)
                           if PV_KEY.match(p) or BRANCH_KEY.match(p)]
            except DeyeError as e:
                print(f"  ...{sn[-4:]}: measure points unavailable ({e}); guessing names")
                all_pts = []
            if not all_pts:
                all_pts = [f"DC{k}PV{n}" for n in range(1, 5) for k in ("Power", "Voltage", "Current")]
            try:
                pts = discover_points(api, sn, dtype, all_pts, tz, today)
            except DeyeError as e:
                print(f"  ...{sn[-4:]}: couldn't check wiring ({e}); using all inputs")
                pts = [p for p in all_pts if PV_KEY.match(p)]
            if pts:
                channels[sn] = pts
                channels["checked"] = today.isoformat()
        tail = sn[-4:]
        plan.append((sn, dtype, pts, tail))
    days, bdays = store["days"], store["bdays"]
    want = [today - timedelta(days=k) for k in range(STRING_DAYS)]
    curve = {}
    fails = 0
    for day in sorted(want):
        if fails >= 2 and not getattr(api, "_dh_good", None):
            print("  per-string history isn't available for this inverter right now; "
                  "run with --probe and share the output.")
            break
        key = day.isoformat()
        if key in days and day < today - timedelta(days=1) and \
                all("imax" in (x or {}) for x in days[key].values()):
            continue
        merged, bmerged = {}, {}
        for sn, dtype, pts, tail in plan:
            if not pts:
                continue
            label = (lambda n, t=tail: f"{t}-PV{n}") if multi else (lambda n: f"PV{n}")
            try:
                raw = api.device_day(sn, day, pts, dtype)
            except DeyeError as e:
                print(f"  {sn} {key}: {e}")
                fails += 1
                continue
            rows = []
            for fr in raw:
                dt = parse_ts(fr.get("time"), tz)
                if dt:
                    rows.append((dt, dict(item_pairs(fr.get("itemList")))))
            merged.update(frames_to_strings(rows, label))
            for b, ser in frames_to_branches(rows).items():
                bmerged[f"{tail}-{b}" if multi else b] = ser
        if not merged:
            continue
        days[key] = {s: summarise_day(v) for s, v in merged.items()}
        if bmerged:
            bdays[key] = {b: summarise_branch(v) for b, v in bmerged.items()}
        if day == today:
            curve = merged
        print(f"  strings {key}: {', '.join(sorted(merged))}")
    cutoff = (today - timedelta(days=STRING_DAYS)).isoformat()
    store["days"] = {k: v for k, v in days.items() if k > cutoff}
    store["bdays"] = {k: v for k, v in bdays.items() if k > cutoff}
    save_json(Path(STRINGS_JSON), store)
    return package_strings(store["days"], curve, today, datetime.now(tz), store["bdays"])


def natkey(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def package_strings(days, curve, today, now, bdays=None):
    names = sorted({s for d in days.values() for s in d},
                   key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)])
    # drop strings that are never used (empty MPPT inputs)
    names = [s for s in names if any(d.get(s, {}).get("v", 0) > 30 for d in days.values())]
    tc = {"labels": [], "series": {s: [] for s in names}}
    if curve:
        stamps = sorted({dt.strftime("%H:%M") for s in names for dt, *_ in curve.get(s, [])})
        lookup = {s: {dt.strftime("%H:%M"): p for dt, p, *_ in curve.get(s, [])} for s in names}
        tc["labels"] = stamps
        for s in names:
            tc["series"][s] = [round(lookup[s].get(t, 0) / 1000, 2) for t in stamps]
    rows = [{"date": k, **{s: days[k].get(s) for s in names}} for k in sorted(days)]
    panels = estimate_panels(days, names)
    health = analyse(days, names, today, now, panels=panels)
    branches = None
    bdays = bdays or {}
    bnames = sorted({b for d in bdays.values() for b in d}, key=natkey)
    bnames = [b for b in bnames if any((d.get(b) or {}).get("i", 0) > 0.3 for d in bdays.values())]
    if bnames:
        pseudo = {k: {b: {"p": (v.get(b) or {}).get("i", 0), "v": 0, "i": (v.get(b) or {}).get("i", 0)}
                      for b in bnames} for k, v in bdays.items()}
        bh = analyse(pseudo, bnames, today, now, kind="branch")
        health = merge_health(health, bh)
        branches = {"names": bnames,
                    "days": [{"date": k, **{b: (bdays[k].get(b) or {}).get("i") for b in bnames}}
                             for k in sorted(bdays)],
                    "flags": {i["string"]: i["status"] for i in bh["items"]}}
    return {"names": names, "today": tc, "days": rows, "branches": branches, "health": health,
            "panels": panels, "panel_watts": PANEL_WATTS}


def label_of(s):
    m = re.match(r"^(?:(\w{4})-)?S(\d+)$", s)
    if m:
        return (f"{m.group(1)} " if m.group(1) else "") + f"String {m.group(2)}"
    return s


def merge_health(a, b):
    rank = {"critical": 0, "warning": 1, "good": 2, "learning": 3, "unknown": 4}
    items = sorted(a["items"] + b["items"], key=lambda i: rank[i["status"]])
    if a["status"] in ("learning", "unknown") and not items:
        return a
    out = {"status": "good", "items": items, "checked": a.get("checked") or b.get("checked")}
    n_in, n_str = a.get("count", 0), b.get("count", 0)
    if not items:
        out["headline"] = f"All {n_in} inputs and {n_str} strings healthy and in balance."
    else:
        out["status"] = items[0]["status"]
        out["headline"] = ("Needs attention: " if out["status"] == "critical" else "Worth watching: ") + \
            "; ".join(f"{label_of(i['string'])} — {lc_first(i['title'])}" for i in items)
    return out


# ------------------------------------------------------------------ health
CAPACITY_KW = 0.0           # filled in from Deye's plant size at run time


def estimate_panels(days, names):
    """Work out the panel layout of each input from the history.

    n (panels in series)    STRING_PANELS, else midday voltage ÷ PANEL_VOLTS_HOT
    p (strings in parallel) PARALLEL_STRINGS, else peak current ÷ one panel's
                            current (a single string can't exceed ~1 panel's Isc),
                            else plant size ÷ PANEL_WATTS ÷ series counts
    Also returns hints: panels implied by the plant size, and whether one input
    runs consistently lower in voltage (one string a panel shorter, e.g. 15 + 14)."""
    med = statistics.median
    out = {}
    for s in names:
        volts = sorted(v for v in ((d.get(s) or {}).get("v", 0) for d in days.values()) if v > 30)
        v = volts[int(0.75 * (len(volts) - 1))] if volts else 0   # a typical sunny-day value
        if STRING_PANELS.get(s):
            out[s] = {"n": int(STRING_PANELS[s]), "est": False}
        elif len(volts) >= 3 and PANEL_VOLTS_HOT > 0:
            out[s] = {"n": max(1, round(v / PANEL_VOLTS_HOT)), "est": True}
        else:
            continue
        out[s]["v"] = round(v)
        peaks = sorted(x for x in ((d.get(s) or {}).get("imax", 0) for d in days.values()) if x > 0)
        out[s]["ipeak"] = round(peaks[int(0.9 * (len(peaks) - 1))], 1) if peaks else 0

    isc = PANEL_WATTS / 40 if PANEL_WATTS else 0           # ≈ one panel's short-circuit current
    cap_panels = round(CAPACITY_KW * 1000 / PANEL_WATTS) if (PANEL_WATTS and CAPACITY_KW) else None
    series_total = sum(x["n"] for x in out.values())
    cap_p = max(1, round(cap_panels / series_total)) if (cap_panels and series_total) else None
    for s, x in out.items():
        if PARALLEL_STRINGS.get(s):
            x["p"], x["est_p"], x["p_how"] = int(PARALLEL_STRINGS[s]), False, "config"
        elif isc and x["ipeak"]:
            x["p"], x["est_p"], x["p_how"] = max(1, round(x["ipeak"] / (0.88 * isc))), True, "peak current"
        elif cap_p:
            x["p"], x["est_p"], x["p_how"] = cap_p, True, "plant size"
        else:
            x["p"], x["est_p"], x["p_how"] = None, True, ""
        x["total"] = x["n"] * x["p"] if x["p"] else None

    # voltage of each input relative to the others, day by day (same sun, same heat)
    if len(out) > 1:
        for s, x in out.items():
            ratios = []
            for d in days.values():
                mine = (d.get(s) or {}).get("v", 0)
                others = [(d.get(o) or {}).get("v", 0) for o in out if o != s]
                others = [o for o in others if o > 30]
                if mine > 30 and others:
                    ratios.append(mine / med(others))
            x["vrel"] = round((med(ratios) - 1) * 100, 1) if len(ratios) >= 5 else None
    meta = {"cap_panels": cap_panels, "sum": sum(x["total"] or 0 for x in out.values()) or None,
            "panel_watts": PANEL_WATTS}
    out["_meta"] = meta
    return out


def analyse(days, names, today, now, kind="input", panels=None):
    """Compare each input/string with the others and with its own normal."""
    res = {"status": "unknown", "headline": "", "items": [], "checked": [], "count": len(names)}
    branch = kind == "branch"
    if not names:
        res["headline"] = "No string data yet."
        return res
    # use complete days only (today counts after 4 pm)
    usable = [k for k in sorted(days)
              if k < today.isoformat() or (k == today.isoformat() and now.hour >= 16)]
    tot = {k: sum((days[k].get(s) or {}).get("p", 0) for s in names) for k in usable}
    vals = sorted(tot.values())
    p90 = vals[int(0.9 * (len(vals) - 1))] if vals else 0
    sunny = [k for k in usable if p90 and tot[k] >= 0.6 * p90]
    if len(sunny) < 6:
        res.update(status="learning",
                   headline=f"Learning your system's normal — {len(sunny)} sunny day(s) collected, "
                            "checks start after about 6.")
        return res
    check, base = sunny[-3:], sunny[-17:-3]
    res["checked"] = check
    med = statistics.median

    def metric(day, s, k):
        return (days[day].get(s) or {}).get(k, 0.0)

    items, dead = [], set()
    for s in names:
        p_now = med(metric(d, s, "p") for d in check)
        p_base = med(metric(d, s, "p") for d in base)
        if p_base > (0.5 if branch else 50) and p_now < 0.05 * p_base:
            dead.add(s)
            items.append({"string": s, "status": "critical",
                          "title": "No current" if branch else "Producing nothing",
                          "detail": ("This string has stopped carrying current. Usually a blown string "
                                     "fuse or a loose/burnt MC4 connector. Call your installer."
                                     if branch else
                                     "This input was working and has stopped. Usually a tripped "
                                     "DC breaker/fuse or a burnt/loose connector. Call your installer.")})
    alive = [s for s in names if s not in dead]

    def relative(day, s, k):
        others = [metric(day, o, k) for o in alive if o != s]
        ref = med(others) if others else 0.0
        return metric(day, s, k) / ref if ref else 1.0

    own_drop = {}
    for s in alive:
        p_now = med(metric(d, s, "p") for d in check)
        p_base = med(metric(d, s, "p") for d in base)
        own_drop[s] = p_now / p_base - 1 if p_base else 0.0
        if len(alive) > 1:
            dp = med(relative(d, s, "p") for d in check) / (med(relative(d, s, "p") for d in base) or 1) - 1
            dv = med(relative(d, s, "v") for d in check) / (med(relative(d, s, "v") for d in base) or 1) - 1
            di = med(relative(d, s, "i") for d in check) / (med(relative(d, s, "i") for d in base) or 1) - 1
        else:  # single string: compare with its own normal only
            dp = p_now / (p_base or 1) - 1
            dv = med(metric(d, s, "v") for d in check) / (med(metric(d, s, "v") for d in base) or 1) - 1
            di = 0.0
        if branch:
            if dp <= -0.20:
                items.append({"string": s, "status": "critical", "title": f"Current down {-dp:.0%}",
                              "detail": BRANCH_DETAIL})
            elif dp <= -0.10:
                items.append({"string": s, "status": "warning", "title": f"Current down {-dp:.0%}",
                              "detail": BRANCH_DETAIL + " Keep an eye on it for a few more days."})
            continue
        n = ((panels or {}).get(s) or {}).get("n")
        v_red = 0.6 / n if n else 0.09
        v_amber = 0.35 / n if n else 0.05
        if dv <= -v_red:
            lost = f" — about {max(1, round(-dv * n))} of {n} panels" if n else ""
            items.append({"string": s, "status": "critical",
                          "title": f"Voltage down {-dv:.0%}{lost}",
                          "detail": "A drop in string voltage means part of the string stopped "
                                    "contributing: most likely a failed panel, bypass diode or "
                                    "connector. Ask your installer to check this string's panels "
                                    "(a thermal camera finds it fast)."})
        elif dp <= -0.25:
            items.append({"string": s, "status": "critical", "title": f"Output down {-dp:.0%}",
                          "detail": cause(dv, di, v_amber)})
        elif dp <= -0.12 or dv <= -v_amber:
            items.append({"string": s, "status": "warning",
                          "title": f"Output down {-dp:.0%}" if dp <= -0.12 else f"Voltage down {-dv:.0%}",
                          "detail": cause(dv, di, v_amber) + " Keep an eye on it for a few more days."})
    # Imbalance check on recent *bright* days, not only sunny ones. Clouds hit every
    # input alike, so one input's share of current dropping is a real signal even on
    # a hazy day. Catches a string that has dropped out (blown fuse, loose connector).
    if len(alive) > 1:
        flagged = {i["string"] for i in items}
        bright = [k for k in usable if p90 and tot[k] >= 0.25 * p90]
        recent = bright[-3:]
        for s in alive:
            if s in flagged or not recent:
                continue
            ref = med(relative(d, s, "i") for d in base) or 1
            drops = [(d, relative(d, s, "i") / ref - 1) for d in recent]
            bad = [(d, x) for d, x in drops if x <= -0.30]
            if not bad:
                continue
            worst = min(x for _, x in bad)
            others = ", ".join(o for o in alive if o != s)
            if branch:
                what = "this string"
                why = BRANCH_DETAIL
            else:
                n_p = ((panels or {}).get(s) or {}).get("p")
                what = s
                why = (f"Clouds affect every input alike, so {s} carrying far less current than {others} "
                       "points to part of it being off — " +
                       ("with two or more strings on one input, a drop like this usually means one string "
                        "has stopped (blown string fuse or loose/burnt connector). "
                        if (n_p or 2) >= 2 else "shading, heavy dirt or a fault. ") +
                       "If the inputs face different directions, a passing cloud can also cause it once.")
            when = ", ".join(datetime.fromisoformat(d).strftime("%d %b") for d, _ in bad)
            if len(bad) >= 2:
                items.append({"string": s, "status": "critical",
                              "title": f"Current {-worst:.0%} below its usual share on {when}",
                              "detail": why + " It has happened on more than one day — ask your installer to check it."})
            elif bad[0][0] == recent[-1]:
                items.append({"string": s, "status": "warning",
                              "title": f"Current {-worst:.0%} below its usual share on {when}",
                              "detail": why + f" Seen on one day so far; if it repeats, the card turns red."})

    # all strings down together on sunny days -> dust
    if len(base) >= 6 and not branch:
        t_now, t_base = med(tot[d] for d in check), med(tot[d] for d in base)
        all_down = own_drop and all(v <= -0.12 for v in own_drop.values())
        if t_base and t_now / t_base - 1 <= -0.18 and all_down and not any(i["status"] == "critical" for i in items):
            items.append({"string": "All", "status": "warning",
                          "title": f"Whole system {1 - t_now / t_base:.0%} below its usual sunny-day output",
                          "detail": "All strings dropped together, which usually means dust on the panels "
                                    "(or hazy weather). A wash often brings it back."})
    order = {"critical": 0, "warning": 1}
    items.sort(key=lambda i: order[i["status"]])
    res["items"] = items
    if not items:
        noun = "string" if branch else "input"
        head = (f"Both inputs ({', '.join(names)}) healthy and in balance." if (not branch and len(names) == 2)
                else f"All {len(names)} {noun}{'s' if len(names) > 1 else ''} healthy and in balance.")
        res.update(status="good", headline=head)
    else:
        res["status"] = items[0]["status"]
        res["headline"] = ("Needs attention: " if res["status"] == "critical" else "Worth watching: ") + \
            "; ".join(f"{i['string']} — {lc_first(i['title'])}" for i in items)
    return res


BRANCH_DETAIL = ("This string carries less current than its usual share compared with the others. "
                 "Check it first for new shade, bird droppings or dirt; if it's clean, a cracked or "
                 "failed panel in this string is likely — ask your installer to test it.")


def cause(dv, di, v_amber):
    if dv > -v_amber and di < -0.08:
        return "Voltage is normal but current is lower, which points to shading, dust or bird droppings on this string."
    return "Lower than this string's usual share of the system."


# ------------------------------------------------------------------ alerts
def send_alerts(name, health, force=False):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) and not (FAST2SMS_API_KEY and FAST2SMS_NUMBERS):
        return
    if health["status"] in ("unknown", "learning") and not force:
        return
    sig = health["status"] + "|" + ",".join(sorted(f"{i['string']}:{i['status']}" for i in health["items"]))
    state = load_json(Path(ALERT_STATE), {})
    prev = state.get("sig")
    if not force:
        if sig == prev:
            return
        if health["status"] == "good" and (not prev or not ALERT_ON_RECOVERY):
            save_json(Path(ALERT_STATE), {"sig": sig})
            return
    icon = {"good": "✅", "warning": "⚠️", "critical": "⛔"}.get(health["status"], "ℹ️")
    lines = [f"{icon} {name} solar: {health['headline'] or 'test alert'}"]
    for i in health["items"]:
        lines.append(f"• {label_of(i['string'])}: {i['title']}. {i['detail']}")
    msg = "\n".join(lines)
    import requests
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=20).raise_for_status()
            print("  alert sent on Telegram")
        except Exception as e:
            print(f"  Telegram alert failed: {e}")
    if FAST2SMS_API_KEY and FAST2SMS_NUMBERS:
        short = lines[0] if len(lines) == 1 else lines[0] + " " + lines[1][2:]
        try:
            requests.post("https://www.fast2sms.com/dev/bulkV2",
                          headers={"authorization": FAST2SMS_API_KEY},
                          data={"route": "q", "message": short[:300], "numbers": FAST2SMS_NUMBERS},
                          timeout=20).raise_for_status()
            print("  alert sent by SMS")
        except Exception as e:
            print(f"  SMS alert failed: {e}")
    save_json(Path(ALERT_STATE), {"sig": sig, "sent": datetime.now().isoformat()})


# ------------------------------------------------------------------ real data
def collect_real():
    tz = get_tz()
    now = datetime.now(tz)
    today = now.date()
    api = Deye()
    st = pick_station(api)
    sid = int(st["id"])
    global CAPACITY_KW
    CAPACITY_KW = num(st.get("installedCapacity"))
    print(f"Station: {'(hidden)' if MASK_IDS else st.get('name')} (id {masked(sid)})")

    hist_path = Path(HISTORY_CSV)
    hist = load_history(hist_path)

    if hist:
        start = date.fromisoformat(max(hist)) - timedelta(days=REFRESH_LAST_DAYS - 1)
    elif START_DATE:
        start = parse_date_in(START_DATE)
    else:
        began = parse_ts(st.get("startOperatingTime"), tz)
        start = began.date() if began else today - timedelta(days=365)
        print(f"First run: backfilling from {start}")

    cur = start
    while cur <= today:
        end_excl = min(cur + timedelta(days=DAILY_CHUNK_DAYS), today + timedelta(days=1))
        try:
            items = api.daily(sid, cur, end_excl)
        except DeyeError as e:
            print(f"  {cur}: skipped ({e})")
            cur = end_excl
            continue
        for it in items:
            y, m, d = it.get("year"), it.get("month"), it.get("day")
            if not (y and m and d):
                continue
            key = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            if start.isoformat() <= key <= today.isoformat():
                hist[key] = {
                    "date": key,
                    "generation": round(num(it.get("generationValue")), 2),
                    "consumption": round(num(it.get("consumptionValue")), 2),
                    "bought": round(num(it.get("purchaseValue")), 2),
                    "exported": round(num(it.get("gridValue")), 2),
                }
        print(f"  fetched {cur} → {end_excl - timedelta(days=1)}")
        cur = end_excl
    save_history(hist_path, hist)

    curve = []
    try:
        for fr in api.frames(sid, today):
            ts = parse_ts(fr.get("timeStamp"), tz)
            if ts and ts.date() == today:
                curve.append([ts.strftime("%H:%M"), round(num(fr.get("generationPower")) / 1000, 2)])
        curve.sort()
    except DeyeError as e:
        print(f"  today's curve unavailable ({e})")

    strings = None
    if CHECK_STRINGS:
        try:
            strings = collect_strings(api, sid, tz, today)
        except Exception as e:
            print(f"  string data unavailable ({e})")

    alerts = None
    try:
        alerts = collect_alerts(api, sid, tz, datetime.now(tz))
    except Exception as e:
        print(f"  inverter alerts unavailable ({e})")
    if strings and alerts:
        strings["health"] = merge_alerts_into_health(strings.get("health"), alerts)

    return {
        "alerts": alerts,
        # on a public page, don't show the Deye plant name unless DISPLAY_NAME is set
        "name": DISPLAY_NAME or ("My Solar" if MASK_IDS else st.get("name")) or "My Solar",
        "capacity": num(st.get("installedCapacity")),
        "daily": [[k, num(v["generation"])] for k, v in sorted(hist.items())],
        "curve": curve,
        "strings": strings,
        "lat": num(st.get("locationLat")) or None,
        "lng": num(st.get("locationLng")) or None,
    }


# ------------------------------------------------------------------ inverter alerts
def lc_first(t):
    """Lower-case the first letter for mid-sentence use, but keep acronyms like PV2 / AC."""
    return t if (len(t) > 1 and t[1].isupper()) or t[:1].isdigit() else t[:1].lower() + t[1:]


def sentence(t):
    t = (t or "").strip()
    return t if not t or t[-1] in ".!?" else t + "."


def in_date(ts):
    """'2026-11-03 03:32' -> '03/11 03:32'"""
    return f"{ts[8:10]}/{ts[5:7]} {ts[11:16]}" if ts and len(ts) >= 16 else ts

ALERT_LEVEL = {2: "error", 1: "warning"}


def normalise_alert(a, tz):
    start = parse_ts(a.get("alertStartTime"), tz)
    end = parse_ts(a.get("alertEndTime"), tz)
    if end and start and end < start:
        end = None
    sn = str(a.get("deviceSn") or "")
    dtype = str(a.get("deviceType") or "").replace("_", " ").title()
    return {
        "id": str(a.get("alertId") or f"{a.get('alertCode')}-{a.get('alertStartTime')}"),
        "code": str(a.get("alertCode") or a.get("protocolName") or ""),
        "name": a.get("alertName") or a.get("description") or "Inverter alert",
        "level": ALERT_LEVEL.get(int(num(a.get("level"))), "info"),
        "start": start.strftime("%Y-%m-%d %H:%M") if start else "",
        "end": end.strftime("%Y-%m-%d %H:%M") if end else "",
        "ongoing": end is None,
        "device": (dtype or "Device") + (f" …{sn[-4:]}" if sn else ""),
        "reason": (a.get("reason") or "").strip(),
        "solution": (a.get("solution") or "").strip(),
    }


def collect_alerts(api, sid, tz, now):
    """Fetch the inverter's alert log; keep 90 days locally so short API windows still add up."""
    store = load_json(Path(ALERTS_JSON), {})
    window = max(ALERT_DAYS, 3)
    items, err = [], None
    for days in (window, 7):                       # fall back to a shorter window if refused
        try:
            start = now - timedelta(days=days)
            for page in range(1, 6):
                batch = api.alerts(sid, start.timestamp(), now.timestamp(), page=page)
                items += batch
                if len(batch) < 50:
                    break
            err = None
            break
        except DeyeError as e:
            err, items = e, []
    if err:
        print(f"  inverter alerts unavailable ({err})")
    for a in items:
        n = normalise_alert(a, tz)
        store[n["id"]] = n
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    store = {k: v for k, v in store.items() if (v.get("start") or "9999") >= cutoff}
    save_json(Path(ALERTS_JSON), store)
    shown_from = (now - timedelta(days=ALERT_DAYS)).strftime("%Y-%m-%d")
    out = sorted((v for v in store.values() if (v.get("start") or "") >= shown_from),
                 key=lambda v: (not v["ongoing"], v["start"]), reverse=False)
    out.sort(key=lambda v: v["start"], reverse=True)
    out.sort(key=lambda v: not v["ongoing"])
    active = sum(1 for v in out if v["ongoing"])
    print(f"  inverter alerts: {len(out)} in the last {ALERT_DAYS} days, {active} still active")
    return {"items": out, "available": err is None}


def alert_health_items(alerts):
    """Active inverter alerts become health-card items (errors red, warnings amber)."""
    items = []
    for a in (alerts or {}).get("items", []):
        if not a["ongoing"] or a["level"] == "info":
            continue
        detail = " ".join(x for x in (
            f"Reported by the inverter since {in_date(a['start'])}.",
            a["reason"] and f"Likely cause: {sentence(a['reason'])}",
            a["solution"] and f"Suggested fix: {sentence(a['solution'])}") if x)
        items.append({"string": "Inverter", "status": "critical" if a["level"] == "error" else "warning",
                      "title": a["name"] + (f" (code {a['code']})" if a["code"] else ""), "detail": detail})
    return items


def merge_alerts_into_health(health, alerts):
    extra = alert_health_items(alerts)
    if not extra:
        return health
    health = dict(health or {"status": "good", "headline": "", "items": []})
    rank = {"critical": 0, "warning": 1}
    items = sorted(list(health.get("items") or []) + extra, key=lambda i: rank.get(i["status"], 2))
    health["items"] = items
    health["status"] = items[0]["status"]
    health["headline"] = ("Needs attention: " if health["status"] == "critical" else "Worth watching: ") + \
        "; ".join(f"{label_of(i['string'])} — {lc_first(i['title'])}" for i in items)
    return health


# ------------------------------------------------------------------ demo data
def collect_demo():
    tz = get_tz()
    now = datetime.now(tz)
    today = now.date()
    random.seed(7)
    cap = 5.0
    daily = []
    d = today - timedelta(days=540)
    while d <= today:
        doy = d.timetuple().tm_yday
        season = 4.6 + 0.6 * math.cos((doy - 80) / 365 * 2 * math.pi)
        if 152 <= doy <= 273:
            season -= 1.0
        cloud = random.choice([1, 1, 1, 0.9, 0.8, 0.6, 0.4])
        daily.append([d.isoformat(), round(cap * season * cloud * random.uniform(0.92, 1.03), 2)])
        d += timedelta(days=1)

    # three strings: PV1 8 panels, PV2 8 panels, PV3 6 panels.
    # Simulated fault: one panel on PV2 fails 6 days ago.
    panels = {"PV1": 8, "PV2": 8, "PV3": 6}
    fault_from = today - timedelta(days=6)

    def frame_set(day, sky, limit=None):
        out = {s: [] for s in panels}
        for k in range(6 * 12, 19 * 12):
            h = k / 12
            dt = datetime.combine(day, datetime.min.time(), tz) + timedelta(hours=h)
            if limit and dt > limit:
                break
            sun = max(0.0, math.sin((h - 6.2) / 12.3 * math.pi)) ** 1.3 * sky
            for s, n in panels.items():
                live = n - (1 if s == "PV2" and day >= fault_from else 0)
                v = (37.5 * live) * (0.92 + 0.08 * min(1, sun * 3)) if sun > 0.02 else 0.0
                i = 9.8 * sun * random.uniform(0.97, 1.0)
                out[s].append((dt, v * i, v, i))
        return out

    days, curve = {}, {}
    for k in range(STRING_DAYS - 1, -1, -1):
        day = today - timedelta(days=k)
        sky = random.choice([1, 1, 1, 0.95, 0.85, 0.5])
        fs = frame_set(day, sky, now if day == today else None)
        days[day.isoformat()] = {s: summarise_day(v) for s, v in fs.items()}
        if day == today:
            curve = fs
    global STRING_PANELS, PARALLEL_STRINGS
    STRING_PANELS = panels
    PARALLEL_STRINGS = {"PV1": 2, "PV2": 2, "PV3": 1}
    # two parallel strings per input (PV3 has one); String 3 picks up a cracked panel
    wiring = {"S1": "PV1", "S2": "PV1", "S3": "PV2", "S4": "PV2", "S5": "PV3"}
    bdays = {}
    for k, (key, dd) in enumerate(sorted(days.items())):
        day_i = {}
        for b, pv in wiring.items():
            share = 0.5 if pv != "PV3" else 1.0
            amps = dd[pv]["i"] * share * random.uniform(0.98, 1.02)
            if b == "S3" and date.fromisoformat(key) >= fault_from:
                amps *= 0.78
            day_i[b] = {"i": round(amps, 2)}
        bdays[key] = day_i
    strings = package_strings(days, curve, today, now, bdays)

    flat = []
    for t in sorted({dt for v in curve.values() for dt, *_ in v}):
        p = sum(next((x[1] for x in curve[s] if x[0] == t), 0) for s in panels)
        flat.append([t.strftime("%H:%M"), round(p / 1000, 2)])
    peak = max((p for _, p in flat), default=0)
    if peak:  # scale the demo curve to a 5 kW system
        flat = [[t, round(p * cap * 0.8 / peak, 2)] for t, p in flat]
    daily[-1][1] = round(sum(p for _, p in flat) / 12, 2)
    t = lambda d, h, m=0: (datetime.combine(today - timedelta(days=d), datetime.min.time())
                           + timedelta(hours=h, minutes=m)).strftime("%Y-%m-%d %H:%M")
    alerts = {"available": True, "items": [
        {"id": "d1", "code": "F13", "name": "Grid over-voltage", "level": "warning", "start": t(3, 13, 5),
         "end": t(3, 13, 40), "ongoing": False, "device": "Inverter …E4E6",
         "reason": "Grid voltage went above the allowed limit for a short time.",
         "solution": "Usually clears by itself. If frequent, ask the electricity board or installer to check grid voltage."},
        {"id": "d2", "code": "F35", "name": "No AC grid", "level": "error", "start": t(9, 6, 50),
         "end": t(9, 7, 25), "ongoing": False, "device": "Inverter …E4E6",
         "reason": "Grid power cut — the inverter stops for safety.", "solution": "Restarts automatically when power returns."},
    ]}
    strings["health"] = merge_alerts_into_health(strings.get("health"), alerts)
    return {"name": "Demo Rooftop (5 kW)", "capacity": cap, "daily": daily,
            "curve": flat, "strings": strings, "alerts": alerts}


# ------------------------------------------------------------------ HTML
def build_html(data: dict) -> str:
    payload = dict(data)
    now = datetime.now(get_tz())
    times = []
    for t in str(RUN_TIMES).replace(" ", "").split(","):
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            h, m = map(int, t.split(":"))
            if 0 <= h < 24 and 0 <= m < 60:
                times.append(h * 60 + m)
    payload.update(alert_days=ALERT_DAYS, generated=now.strftime("%d %b %Y, %I:%M %p"),
                   generated_ts=int(now.timestamp()),
                   tz_offset=int(now.utcoffset().total_seconds() // 60),
                   run_times=sorted(set(times)))
    theme = THEME if THEME in THEMES else "clean"
    options = "".join(f'<option value="{k}">{v}</option>' for k, v in THEME_LABELS.items())
    import html as _html
    page = (HTML_TEMPLATE.replace("__THEMECSS__", theme_css())
            .replace("__THEME__", theme).replace("__THEMEOPTS__", options))
    if not DASHBOARD_PASSWORD:
        if REQUIRE_PASSWORD:
            sys.exit("REQUIRE_PASSWORD is on but DASHBOARD_PASSWORD is empty — "
                     "refusing to publish an unlocked dashboard. Add the DASHBOARD_PASSWORD secret.")
        nm = data.get('name') or 'My Solar'
        title = _html.escape(nm if "solar" in nm.lower() else f"{nm} · Solar")
        return (page.replace("<title>Solar Units</title>", f"<title>{title}</title>")
                .replace("__DATA__", json.dumps(payload)))
    return lock_page(page, payload)


PBKDF2_ROUNDS = 310_000


def encrypt_payload(payload: dict, password: str) -> dict:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        sys.exit("DASHBOARD_PASSWORD needs the 'cryptography' package: pip install cryptography")
    import base64
    # salt stays the same between runs (so "remember on this device" keeps working);
    # it is derived from your private App ID, the IV is fresh every run
    salt = hashlib.sha256(f"solar-dashboard:{APP_ID or 'local'}".encode()).digest()[:16]
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS, dklen=32)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "rounds": PBKDF2_ROUNDS}


def lock_page(page: str, payload: dict) -> str:
    """Wrap the app so its data only exists encrypted until the right password is entered."""
    start = page.index("<script>\nconst D = __DATA__;")
    end = page.index("</script>", start)
    app_js = page[start + len("<script>\nconst D = __DATA__;"):end]
    box = json.dumps(encrypt_payload(payload, DASHBOARD_PASSWORD))
    lock_html = LOCK_TEMPLATE.replace("__BOX__", box).replace("__APP__", json.dumps(app_js).replace("</", "<\\/"))
    page = page[:start] + lock_html + page[end + len("</script>"):]
    return page.replace("<title>Solar Units</title>", "<title>Solar dashboard</title>").replace(
        '<div class="wrap">', '<div class="wrap" id="app" hidden>', 1)


LOCK_TEMPLATE = r"""<div class="lock" id="lock">
  <form class="lockbox" id="lockForm">
    <svg viewBox="0 0 48 48" width="44" height="44" aria-hidden="true"><g fill="none" stroke="#f6a609" stroke-width="3" stroke-linecap="round"><circle cx="24" cy="24" r="9" fill="#f6a609"/><path d="M24 4v6M24 38v6M4 24h6M38 24h6M9.9 9.9l4.2 4.2M33.9 33.9l4.2 4.2M9.9 38.1l4.2-4.2M33.9 14.1l4.2-4.2"/></g></svg>
    <h1>Solar dashboard</h1>
    <p class="sub">Enter the dashboard password.</p>
    <input type="password" id="pw" autocomplete="current-password" placeholder="Password" required>
    <label class="remember"><input type="checkbox" id="remember"> Remember on this device</label>
    <button type="submit">Unlock</button>
    <p class="err" id="err" hidden>That password didn't work.</p>
  </form>
</div>
<style>
.lock{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.lockbox{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:28px 24px;width:100%;max-width:340px;
  display:flex;flex-direction:column;gap:12px;align-items:stretch;text-align:center;box-shadow:var(--shadow)}
.lockbox svg{align-self:center}
.lockbox h1{font-size:1.3rem}
.lockbox input[type=password]{font:inherit;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.lockbox button{font:inherit;font-weight:800;padding:10px;border-radius:12px;border:0;background:var(--sun);color:var(--on-accent);cursor:pointer}
.remember{display:flex;gap:8px;align-items:center;justify-content:center;color:var(--muted);font-size:.9rem}
.err{color:var(--critical);margin:0;font-weight:700}
</style>
<script>
(() => {
  const BOX = __BOX__, APP = __APP__;
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const KEY_STORE = 'solarKey:' + BOX.salt.slice(0, 8);
  async function keyFrom(pw){
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveBits']);
    return new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2', hash:'SHA-256', salt:b64(BOX.salt), iterations:BOX.rounds}, base, 256));
  }
  async function open(raw){
    const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
    const plain = await crypto.subtle.decrypt({name:'AES-GCM', iv:b64(BOX.iv)}, key, b64(BOX.ct));
    return JSON.parse(new TextDecoder().decode(plain));
  }
  function start(D){
    document.getElementById('lock').remove();
    document.getElementById('app').hidden = false;
    const nm = D.name || 'My Solar'; document.title = /solar/i.test(nm) ? nm : nm + ' · Solar';
    (new Function('D', APP))(D);
  }
  // a remembered key is tied to this password's salt; if it fails we just ask again
  try {
    const saved = localStorage.getItem(KEY_STORE);
    if (saved) open(b64(saved)).then(start).catch(() => { try { localStorage.removeItem(KEY_STORE); } catch(e) {} });
  } catch(e) {}
  document.getElementById('lockForm').onsubmit = async ev => {
    ev.preventDefault();
    const err = document.getElementById('err'); err.hidden = true;
    try {
      const raw = await keyFrom(document.getElementById('pw').value);
      const D = await open(raw);
      if (document.getElementById('remember').checked){
        try { localStorage.setItem(KEY_STORE, btoa(String.fromCharCode(...raw))); } catch(e) {}
      }
      start(D);
    } catch(e) { err.hidden = false; }
  };
})();
</script>"""


# light tokens, dark tokens  (bg, card, ink, muted, line, accent, accent-soft, on-accent)
THEMES = {
    "clean": (("#f5f7fa", "#ffffff", "#14181f", "#5b6472", "#e3e7ee", "#2a78d6", "#dbe8fa", "#ffffff"),
              ("#0e1116", "#171b22", "#eef1f5", "#9aa3b2", "#262c36", "#3987e5", "#183457", "#ffffff")),
    "leaf":  (("#f3f7f2", "#ffffff", "#17231b", "#566659", "#dde7da", "#1f8a57", "#d3ebdb", "#ffffff"),
              ("#0f1611", "#18211a", "#ecf3ed", "#9db0a2", "#26332a", "#3fb37a", "#173a26", "#06210f")),
    "night": (("#0b1220", "#121a2b", "#e8edf6", "#93a0b8", "#1f2a40", "#22c3a6", "#0f3a3a", "#062420"),
              ("#0b1220", "#121a2b", "#e8edf6", "#93a0b8", "#1f2a40", "#22c3a6", "#0f3a3a", "#062420")),
    "sunny": (("#fffaf0", "#ffffff", "#2b2a28", "#6f6a61", "#efe7d8", "#f6a609", "#fde7b4", "#2b2000"),
              ("#17150f", "#221f17", "#f3eee3", "#a39d90", "#332e22", "#ffb72b", "#4a3a12", "#2b2000")),
}
THEME_LABELS = {"clean": "Clean", "leaf": "Leaf", "night": "Night", "sunny": "Sunny"}


def theme_css():
    names = ("bg", "card", "ink", "muted", "line", "sun", "sun-soft", "on-accent")
    series_l = "--s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#6250d6;"
    series_d = "--s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9;"
    out = []
    for key, (light, dark) in THEMES.items():
        dark_always = light == dark
        lt = " ".join(f"--{n}:{v};" for n, v in zip(names, light))
        dk = " ".join(f"--{n}:{v};" for n, v in zip(names, dark))
        shadow = "--shadow:none;" if dark_always else "--shadow:0 1px 3px rgba(16,24,40,.06),0 1px 2px rgba(16,24,40,.04);"
        out.append(f':root[data-palette="{key}"]{{{lt} {shadow} {series_d if dark_always else series_l}}}')
        out.append(f':root[data-palette="{key}"][data-theme="dark"]{{{dk} --shadow:none; {series_d}}}')
    return "\n".join(out)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en" data-palette="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Solar Units</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@500;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--good:#0ca30c; --warning:#fab219; --critical:#d03b3b}
__THEMECSS__
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Nunito,system-ui,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;align-items:center;gap:14px;margin-bottom:18px}
.sunicon{width:48px;height:48px;flex:none}
h1{font-size:1.45rem;margin:0;font-weight:800}
h2{font-size:1.05rem;margin:0 0 10px;font-weight:800}
.sub{color:var(--muted);font-size:.9rem}
.nextrun{font-size:.82rem;margin-top:2px}
.themepick{margin-left:auto;display:flex;align-items:center;gap:6px;color:var(--muted);font-size:.85rem;font-weight:700}
.themepick select{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 10px}
.hero{background:linear-gradient(135deg,var(--sun-soft),transparent 70%),var(--card);
  border-radius:20px;padding:22px;box-shadow:var(--shadow);border:1px solid var(--line);margin-bottom:14px}
.hero .label{color:var(--muted);font-weight:700}
.hero .big{font-size:3.2rem;font-weight:800;line-height:1.05;margin:4px 0}
.hero .big small{font-size:1.1rem;color:var(--muted);font-weight:700}
.hero .note{font-size:.95rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}
.card .k{color:var(--muted);font-size:.85rem;font-weight:700}
.card .v{font-size:1.6rem;font-weight:800;margin-top:2px}
.card .v small{font-size:.85rem;color:var(--muted)}
.health{border-left:6px solid var(--line);margin-bottom:14px}
.health.good{border-left-color:var(--good)} .health.warning{border-left-color:var(--warning)} .health.critical{border-left-color:var(--critical)}
.hhead{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.grid > *, .facts > *, .rangesum > div{min-width:0}
@media (max-width: 640px){
  .wrap{padding:14px 12px 32px}
  header{flex-wrap:wrap;gap:10px}
  header > div{flex:1 1 0;min-width:0}
  h1{font-size:1.2rem;line-height:1.25;overflow-wrap:anywhere}
  .sunicon{width:36px;height:36px}
  .themepick{margin-left:0;flex-basis:100%}
  .hero{padding:18px}
  .hero .big{font-size:2.6rem}
  .card .v{font-size:1.35rem}
  .chartbox{height:260px}
  /* tables become stacked cards: one block per row, labels above values */
  table.stack thead{display:none}
  table.stack, table.stack tbody, table.stack tr{display:block;width:100%}
  table.stack tr{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;padding:10px 0;border-bottom:1px solid var(--line)}
  table.stack td{border:0;padding:0;text-align:left}
  table.stack td::before{content:attr(data-label);display:block;color:var(--muted);font-size:.75rem;font-weight:700}
  table.stack td:first-child{grid-column:1 / -1;font-weight:800;font-size:1rem}
  table.stack td:first-child::before{content:none}
}
.badge{flex:none;display:inline-flex;align-items:center;gap:6px;font-weight:800;font-size:.8rem;padding:3px 10px;border-radius:999px;border:1px solid var(--line)}
.badge i{width:10px;height:10px;border-radius:50%;display:inline-block}
.issue{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line)}
.issue b{display:block}
.issue p{margin:4px 0 0;color:var(--muted)}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:.9rem}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:700}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:16px;box-shadow:var(--shadow)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tabs button{font:inherit;font-weight:700;border:1px solid var(--line);background:transparent;color:var(--ink);
  padding:7px 14px;border-radius:999px;cursor:pointer}
.tabs button.on{background:var(--sun);border-color:var(--sun);color:var(--on-accent)}
.subtabs{display:none;gap:6px;margin:-2px 0 10px}
.subtabs.show{display:flex;flex-wrap:wrap}
.subtabs button{font:inherit;font-size:.85rem;font-weight:700;border:0;background:transparent;color:var(--muted);padding:4px 8px;cursor:pointer;border-bottom:2px solid transparent}
.subtabs button.on{color:var(--ink);border-bottom-color:var(--sun)}
.rangebar,.rangesum{display:none}
.rangebar.show{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:-2px 0 10px;font-size:.88rem;font-weight:700;color:var(--muted)}
.datebox{position:relative;display:inline-flex;align-items:center;gap:6px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:10px;padding:5px 10px;cursor:pointer;font-variant-numeric:tabular-nums}
.datebox::after{content:"📅";font-size:.85rem}
.datebox:focus-within{border-color:var(--sun)}
/* the real date input sits invisibly on top so tapping opens the phone's calendar */
.datebox input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;border:0;padding:0;color-scheme:light}
:root[data-palette="night"] .datebox input{color-scheme:dark}
.presets{display:flex;flex-wrap:wrap;gap:6px}
.presets button{font:inherit;font-size:.82rem;font-weight:700;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:999px;padding:4px 10px;cursor:pointer}
.presets button:hover{border-color:var(--sun)}
.rangesum.show{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:12px}
.rangesum > div{border:1px solid var(--line);border-radius:12px;padding:8px 12px}
.rangesum .k{color:var(--muted);font-size:.8rem;font-weight:700}
.rangesum .v{font-size:1.25rem;font-weight:800}
.chartbox{position:relative;height:320px}
.caption{color:var(--muted);font-size:.9rem;margin-top:8px;min-height:1.2em}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:14px}
.fact{display:flex;gap:10px;align-items:flex-start}
.fact .e{font-size:1.4rem;line-height:1}
.alertsbox{margin-bottom:14px}
.alertsbox h2{margin-bottom:4px}
.alist{list-style:none;margin:10px 0 0;padding:0}
.alist li{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--line)}
.alist li .sw{margin-top:6px;flex:none}
.alist li.on b{color:var(--ink)}
.alist details{margin-top:4px;font-size:.88rem}
.alist summary{cursor:pointer;color:var(--muted);font-weight:700}
.alist details p{margin:4px 0;color:var(--ink)}
.update{margin-top:14px}
.update pre{white-space:pre-wrap;font:inherit;line-height:1.5;margin:8px 0 12px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.ubtns{display:flex;flex-wrap:wrap;gap:8px}
.ubtn{font:inherit;font-weight:800;font-size:.9rem;padding:8px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;text-decoration:none}
.ubtn.wa{background:#1fa855;border-color:#1fa855;color:#fff}
footer{color:var(--muted);font-size:.8rem;margin-top:18px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <svg class="sunicon" viewBox="0 0 48 48" aria-hidden="true"><g fill="none" stroke="#f6a609" stroke-width="3" stroke-linecap="round">
      <circle cx="24" cy="24" r="9" fill="#f6a609"/><path d="M24 4v6M24 38v6M4 24h6M38 24h6M9.9 9.9l4.2 4.2M33.9 33.9l4.2 4.2M9.9 38.1l4.2-4.2M33.9 14.1l4.2-4.2"/></g></svg>
    <div><h1 id="name">My Solar</h1><div class="sub" id="updated"></div></div>
    <label class="themepick">Theme <select id="themeSel">__THEMEOPTS__</select></label>
  </header>

  <section class="hero">
    <div class="label">Today so far</div>
    <div class="big" id="today">0 <small>units</small></div>
    <div class="note" id="todayNote"></div>
  </section>

  <section class="card health" id="health" hidden></section>

  <section class="card alertsbox" id="alertsCard" hidden></section>

  <section class="grid">
    <div class="card"><div class="k">This month</div><div class="v" id="month">–</div></div>
    <div class="card"><div class="k">This year</div><div class="v" id="year">–</div></div>
    <div class="card"><div class="k">All time</div><div class="v" id="all">–</div></div>
  </section>

  <section class="panel">
    <div class="tabs" role="tablist">
      <button data-v="today" class="on">Today</button>
      <button data-v="days">Last 30 days</button>
      <button data-v="weeks">By week</button>
      <button data-v="months">By month</button>
      <button data-v="years">By year</button>
      <button data-v="strings" id="stringsTab" hidden>By string</button>
      <button data-v="range">Date range</button>
    </div>
    <div class="rangebar" id="rangebar">
      <label>From <span class="datebox"><span class="dtext" id="rFromText">dd/mm/yyyy</span><input type="date" id="rFrom" aria-label="From date"></span></label>
      <label>To <span class="datebox"><span class="dtext" id="rToText">dd/mm/yyyy</span><input type="date" id="rTo" aria-label="To date"></span></label>
      <span class="presets">
        <button data-p="7">Last 7 days</button><button data-p="month">This month</button>
        <button data-p="lastmonth">Last month</button><button data-p="year">This year</button><button data-p="all">All</button>
      </span>
    </div>
    <div class="rangesum" id="rangesum"></div>
    <div class="subtabs" id="subtabs">
      <button data-s="sToday" class="on">Today's power</button>
      <button data-s="sShare">Share of output</button>
      <button data-s="sVolt">Midday voltage</button>
      <button data-s="sBranch" id="branchTab" hidden>Each string's current</button>
    </div>
    <div class="chartbox"><canvas id="chart" role="img" aria-label="Solar chart"></canvas></div>
    <div class="caption" id="caption"></div>
  </section>

  <section class="facts" id="facts"></section>

  <section class="card update" id="update" hidden>
    <div class="k">Morning update for residents</div>
    <pre id="updateText"></pre>
    <div class="ubtns">
      <a class="ubtn wa" id="waShare" target="_blank" rel="noopener">Share on WhatsApp</a>
      <button class="ubtn" id="copyUpdate" type="button">Copy text</button>
    </div>
  </section>
  <footer>1 unit = 1 kWh, the same “unit” on your electricity bill · Data from Deye Cloud</footer>
</div>

<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (n, d=1) => Number(n).toLocaleString('en-IN', {maximumFractionDigits:d, minimumFractionDigits:0});
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dayLabel = iso => { const d = new Date(iso+'T00:00'); return d.getDate()+' '+MONTHS[d.getMonth()]; };

const daily = D.daily.map(([d,v]) => ({d, v:+v}));
const last = daily[daily.length-1] || {d:new Date().toISOString().slice(0,10), v:0};
const todayStr = last.d, ym = todayStr.slice(0,7), yr = todayStr.slice(0,4);
const sum = a => a.reduce((s,x)=>s+x.v,0);
const total = sum(daily);
const monthTot = sum(daily.filter(x=>x.d.startsWith(ym)));
const yearTot = sum(daily.filter(x=>x.d.startsWith(yr)));

const byMonth = {}, byYear = {};
daily.forEach(x => { byMonth[x.d.slice(0,7)] = (byMonth[x.d.slice(0,7)]||0)+x.v; byYear[x.d.slice(0,4)] = (byYear[x.d.slice(0,4)]||0)+x.v; });
const monthKeys = Object.keys(byMonth).sort().slice(-24);
// weeks run Monday–Sunday; key = the Monday's date
const weekStart = iso => { const d = new Date(iso+'T00:00'); d.setDate(d.getDate() - ((d.getDay()+6)%7));
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); };
const byWeek = {}, weekDays = {};
daily.forEach(x => { const w = weekStart(x.d); byWeek[w] = (byWeek[w]||0) + x.v; (weekDays[w] = weekDays[w] || []).push(x); });
const weekKeys = Object.keys(byWeek).sort().slice(-26);
const thisWeek = weekKeys[weekKeys.length-1];
// this week vs the same days of last week (fair comparison mid-week)
const weekCompare = (() => {
  if (weekKeys.length < 2) return null;
  const cur = weekDays[thisWeek] || [], n = cur.length;
  const prevKey = weekKeys[weekKeys.length-2], prev = (weekDays[prevKey] || []).slice(0, n);
  const a = sum(cur), b = sum(prev);
  return b ? {a, b, n, pct: (a-b)/b*100} : null;
})();
const yearKeys = Object.keys(byYear).sort();
const mLabel = k => MONTHS[+k.slice(5)-1] + " '" + k.slice(2,4);

// headline
document.getElementById('name').textContent = D.name;
document.getElementById('updated').textContent = 'Updated ' + D.generated + (D.capacity ? ' · ' + fmt(D.capacity) + ' kW system' : '');
// "Next update around …" from RUN_TIMES, worked out in the plant's time zone
(function(){
  const T = D.run_times || []; if (!T.length) return;
  const off = D.tz_offset * 60000;                       // plant time zone, e.g. IST = +330 min
  const local = ms => new Date(ms + off);                // shift, then read with getUTC*
  const nowMs = Date.now(), n = local(nowMs), mins = n.getUTCHours()*60 + n.getUTCMinutes();
  let next = T.find(t => t > mins), dayAdd = 0;
  if (next === undefined){ next = T[0]; dayAdd = 1; }
  const hh = Math.floor(next/60), mm = next%60;
  const label = ((hh+11)%12+1) + ':' + String(mm).padStart(2,'0') + (hh < 12 ? ' AM' : ' PM') + (dayAdd ? ' tomorrow' : '');
  // was a scheduled run missed? find the latest scheduled time before now
  let prev = null;
  for (let d = 0; d <= 2 && prev === null; d++){
    const cands = T.filter(t => d > 0 || t <= mins);
    if (cands.length){
      const t = cands[cands.length-1], base = Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate() - d);
      prev = base + t*60000 - off;                       // back to real epoch ms
    }
  }
  const el = document.createElement('div'); el.className = 'sub nextrun';
  const lateBy = prev ? (prev - D.generated_ts*1000) : 0;
  if (lateBy > 75*60000){
    el.innerHTML = '⚠️ <b>Update overdue</b> — a scheduled refresh hasn’t arrived. Next one around ' + label + '.';
  } else {
    el.textContent = '🔄 Next update around ' + label + ' (can be up to 30 min late)';
  }
  document.getElementById('updated').after(el);
})();
document.getElementById('today').innerHTML = fmt(last.v) + ' <small>units</small>';
document.getElementById('month').innerHTML = fmt(monthTot,0) + ' <small>units</small>';
document.getElementById('year').innerHTML = fmt(yearTot,0) + ' <small>units</small>';
document.getElementById('all').innerHTML = fmt(total,0) + ' <small>units</small>';

const prev30 = daily.slice(-31,-1);
const avg30 = prev30.length ? sum(prev30)/prev30.length : 0;
const parts = [];
if (D.curve.length){
  const peak = D.curve.reduce((a,b)=> b[1]>a[1]?b:a);
  parts.push('Peak ' + fmt(peak[1],2) + ' kW at ' + peak[0]);
}
if (avg30) parts.push('30-day average is ' + fmt(avg30) + ' units/day');
const note = parts.join(' · ');
document.getElementById('todayNote').textContent = note;

// ---------- strings / health ----------
const S = D.strings && D.strings.names && D.strings.names.length ? D.strings : null;
const B = S && S.branches && S.branches.names.length ? S.branches : null;
const lab = s => { const m = /^(?:(\w{4})-)?S(\d+)$/.exec(s); return m ? (m[1] ? m[1]+' ' : '') + 'String ' + m[2] : s; };
const bUsual = b => { const v = B.days.slice(-15,-1).map(d=>d[b]).filter(x=>x>0).sort((a,c)=>a-c); return v.length ? v[Math.floor(v.length/2)] : 0; };
const SCOL = ['--s1','--s2','--s3','--s4'];
const scol = i => css(SCOL[i % 4]);
const STATUS = {
  good:{label:'Healthy', c:'--good', icon:'✓'}, warning:{label:'Watch', c:'--warning', icon:'!'},
  critical:{label:'Needs attention', c:'--critical', icon:'✕'},
  learning:{label:'Learning', c:'--line', icon:'…'}, unknown:{label:'No data', c:'--line', icon:'?'}
};
if (S){
  document.getElementById('stringsTab').hidden = false;
  if (B) document.getElementById('branchTab').hidden = false;
  const h = S.health, st = STATUS[h.status] || STATUS.unknown;
  const box = document.getElementById('health');
  box.hidden = false; box.className = 'card health ' + h.status;
  const lastDay = S.days[S.days.length-1] || {};
  const prevDays = S.days.slice(-15,-1);
  const usual = s => { const v = prevDays.map(d => d[s] && d[s].kwh).filter(x=>x>0).sort((a,b)=>a-b); return v.length ? v[Math.floor(v.length/2)] : 0; };
  const yday = S.days.length > 1 ? S.days[S.days.length-2] : lastDay;
  let html = `<div class="hhead"><span class="badge"><i style="background:var(${st.c})"></i>${st.icon} ${st.label}</span>
    <div><h2 style="margin:0">Panel health</h2><div class="sub">${esc(h.headline)}</div></div></div>`;
  h.items.forEach(i => { html += `<div class="issue"><b>${i.status==='critical'?'⛔':'⚠️'} ${esc(lab(i.string))}: ${esc(i.title)}</b><p>${esc(i.detail)}</p></div>`; });
  html += `<div class="tablewrap"><table class="stack"><thead><tr><th>Input</th><th title="panels in series × strings in parallel">Panels (series × strings)</th><th>Yesterday</th><th>Usual day</th><th>Midday volts</th><th>Midday amps</th></tr></thead><tbody>`;
  const P = S.panels || {};
  const panelText = x => !x ? '–' : x.total
    ? `${x.est?'≈':''}${x.n} × ${x.est_p?'≈':''}${x.p} = ${x.total}`
    : `${x.est?'≈':''}${x.n} in series`;
  S.names.forEach((s,i) => { const y = yday[s] || {};
    html += `<tr><td><span class="sw" style="background:var(${SCOL[i%4]})"></span>${esc(s)}</td><td data-label="Panels (series × strings)" title="${P[s] && (P[s].est || P[s].est_p) ? 'Estimated — set STRING_PANELS / PARALLEL_STRINGS in the script for exact numbers' : ''}">${panelText(P[s])}</td><td data-label="Yesterday">${fmt(y.kwh||0)} units</td><td data-label="Usual day">${fmt(usual(s))} units</td><td data-label="Midday volts">${fmt(y.v||0,0)} V</td><td data-label="Midday amps">${fmt(y.i||0,1)} A</td></tr>`; });
  html += `</tbody></table></div>`;
  const PM = P._meta || {};
  const pn = Object.entries(P).filter(([k]) => k !== '_meta'), known = pn.length && pn.every(([,x]) => x.total);
  const tot = pn.reduce((a,[,x])=>a+(x.total||0),0);
  if (pn.length){
    const notes = [];
    if (pn.some(([,x])=>x.est||x.est_p)) notes.push('Values marked ≈ are worked out from your history: panels in series from midday voltage, strings from peak current.');
    notes.push(known ? 'Total: ' + tot + ' panels' + (PM.panel_watts ? ' × ' + PM.panel_watts + ' W = ' + fmt(tot*PM.panel_watts/1000,2) + ' kW' : '') + '.'
                     : 'Set PANEL_WATTS to also work out strings in parallel.');
    if (PM.cap_panels && known && PM.cap_panels !== tot)
      notes.push('Your plant size in Deye (' + fmt(D.capacity,2) + ' kW) points to ' + PM.cap_panels + ' panels.');
    pn.forEach(([s,x]) => { if (x.vrel != null && x.vrel <= -2 && x.p >= 2)
      notes.push(s + ' runs ' + fmt(-x.vrel,1) + '% lower in voltage than the other input every day — one of its strings may be a panel shorter (e.g. ' + x.n + ' + ' + (x.n-1) + ').'); });
    html += `<div class="sub" style="margin-top:6px">${notes.map(esc).join(' ')}</div>`;
  }
  if (B){
    const bLast = B.days.length > 1 ? B.days[B.days.length-2] : (B.days[0]||{});
    html += `<div class="tablewrap"><table class="stack"><thead><tr><th>Individual string</th><th>Midday amps (yesterday)</th><th>Usual</th><th>Status</th></tr></thead><tbody>`;
    B.names.forEach(b => { const f = B.flags[b], stt = STATUS[f||'good'];
      html += `<tr><td>${esc(lab(b))}</td><td data-label="Midday amps (yesterday)">${fmt(bLast[b]||0,1)} A</td><td data-label="Usual">${fmt(bUsual(b),1)} A</td><td data-label="Status"><span class="sw" style="background:var(${stt.c});border-radius:50%"></span>${f ? stt.label : 'OK'}</td></tr>`; });
    html += `</tbody></table></div>`;
  }
  box.innerHTML = html;
}

// ---------- inverter alerts ----------
if (D.alerts){
  const A = D.alerts, box = document.getElementById('alertsCard'); box.hidden = false;
  const fmtT = t => t ? t.slice(8,10)+'/'+t.slice(5,7)+' '+t.slice(11,16) : '';
  const dur = a => { if (!a.end || !a.start) return '';
    const m = Math.round((new Date(a.end.replace(' ','T')) - new Date(a.start.replace(' ','T'))) / 60000);
    return m < 60 ? m + ' min' : (m < 1440 ? (m/60).toFixed(1).replace('.0','') + ' h' : Math.round(m/1440) + ' days'); };
  const LV = {error:['--critical','Fault'], warning:['--warning','Warning'], info:['--muted','Info']};
  let html = `<h2>Inverter alerts <span class="sub" style="font-weight:700">· last ${D.alert_days || 30} days</span></h2>`;
  if (!A.available && !A.items.length){
    html += `<div class="sub">The inverter's alert log couldn't be read this time.</div>`;
  } else if (!A.items.length){
    html += `<div class="sub">✅ No alerts from the inverter in the last ${D.alert_days || 30} days.</div>`;
  } else {
    const active = A.items.filter(a => a.ongoing).length;
    html += `<div class="sub">${active ? '<b>' + active + ' active now</b> · ' : 'None active now · '}${A.items.length} in total</div><ul class="alist">`;
    A.items.slice(0, 15).forEach(a => {
      const [c, lbl] = LV[a.level] || LV.info;
      const when = a.ongoing ? `since ${fmtT(a.start)} · <b>still active</b>` : `${fmtT(a.start)} · cleared after ${dur(a)}`;
      const more = [a.reason && `<p><b>Cause:</b> ${esc(a.reason)}</p>`, a.solution && `<p><b>What to do:</b> ${esc(a.solution)}</p>`].filter(Boolean).join('');
      html += `<li class="${a.ongoing ? 'on' : ''}"><span class="sw" style="background:var(${c});border-radius:50%"></span>
        <div><div><b>${esc(a.name)}</b> <span class="sub">${lbl}${a.code ? ' · ' + esc(a.code) : ''} · <span style="white-space:nowrap">${esc(a.device)}</span></span></div>
        <div class="sub">${when}</div>${more ? `<details><summary>Details</summary>${more}</details>` : ''}</div></li>`;
    });
    html += `</ul>`;
    if (A.items.length > 15) html += `<div class="sub">…and ${A.items.length - 15} more.</div>`;
  }
  box.innerHTML = html;
}

// facts
const facts = [];
const completeDays = daily.slice(0,-1);
if (completeDays.length){
  const best = completeDays.reduce((a,b)=> b.v>a.v?b:a);
  const bd = new Date(best.d+'T00:00');
  facts.push(['🏆', 'Best day ever', fmt(best.v)+' units on '+bd.getDate()+' '+MONTHS[bd.getMonth()]+' '+bd.getFullYear()]);
}
const mDays = daily.filter(x=>x.d.startsWith(ym));
if (weekCompare) facts.push([weekCompare.pct>=0?'📈':'📉', 'This week vs last week',
  (weekCompare.pct>=0?'+':'')+fmt(weekCompare.pct,0)+'% ('+fmt(weekCompare.a,0)+' vs '+fmt(weekCompare.b,0)+' units, first '+weekCompare.n+' day'+(weekCompare.n>1?'s':'')+')']);
if (mDays.length) facts.push(['📅', 'Daily average this month', fmt(monthTot/mDays.length)+' units/day']);
const pm = new Date(ym+'-01T00:00'); pm.setMonth(pm.getMonth()-1);
const pmKey = pm.getFullYear()+'-'+String(pm.getMonth()+1).padStart(2,'0');
if (byMonth[pmKey]){
  const dim = new Date(pm.getFullYear(), pm.getMonth()+1, 0).getDate();
  const pmAvg = byMonth[pmKey]/dim, cur = mDays.length ? monthTot/mDays.length : 0;
  const pct = pmAvg ? (cur-pmAvg)/pmAvg*100 : 0;
  facts.push([pct>=0?'📈':'📉', 'Compared with '+MONTHS[pm.getMonth()], (pct>=0?'+':'')+fmt(pct,0)+'% per day ('+fmt(byMonth[pmKey],0)+' units last month)']);
}
if (D.capacity && avg30) facts.push(['🔆', 'Units per kW (30-day avg)', fmt(avg30/D.capacity,2)+' per day — 4+ is healthy']);
if (D.update && D.update.text){
  const u = document.getElementById('update'); u.hidden = false;
  document.getElementById('updateText').innerHTML = esc(D.update.text).replace(/\*([^*\n]+)\*/g, '<b>$1</b>');
  // api.whatsapp.com keeps emoji intact (the wa.me redirect can garble them); the invisible
  // emoji-style marker U+FE0F is dropped as some phones show it as a box
  document.getElementById('waShare').href = 'https://api.whatsapp.com/send?text=' +
    encodeURIComponent(D.update.text.replace(/\uFE0F/g, ''));
  document.getElementById('copyUpdate').onclick = async ev => {
    try { await navigator.clipboard.writeText(D.update.text); ev.target.textContent = 'Copied ✓'; }
    catch(e) { ev.target.textContent = 'Select the text above to copy'; }
  };
}
document.getElementById('facts').innerHTML = facts.map(([e,k,v]) =>
  `<div class="card fact"><span class="e">${e}</span><div><div class="k">${k}</div><div style="font-weight:800">${v}</div></div></div>`).join('');

// ---------- date range ----------
const rFrom = document.getElementById('rFrom'), rTo = document.getElementById('rTo');
const firstDay = daily.length ? daily[0].d : todayStr;
const longDate = iso => iso.slice(8,10) + '/' + iso.slice(5,7) + '/' + iso.slice(0,4);
const iso = d => d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
const ddmmyyyy = v => v ? v.slice(8,10) + '/' + v.slice(5,7) + '/' + v.slice(0,4) : 'dd/mm/yyyy';
function showDates(){
  document.getElementById('rFromText').textContent = ddmmyyyy(rFrom.value);
  document.getElementById('rToText').textContent = ddmmyyyy(rTo.value);
}
[rFrom, rTo].forEach(el => {
  el.min = firstDay; el.max = todayStr;
  el.onchange = () => { showDates(); draw(); };
  el.onclick = () => { try { el.showPicker(); } catch(e) {} };   // desktop browsers
});
function setRange(p){
  const t = new Date(todayStr+'T00:00'); let a = new Date(t), b = new Date(t);
  if (p === '7') a.setDate(t.getDate()-6);
  else if (p === 'month') a = new Date(t.getFullYear(), t.getMonth(), 1);
  else if (p === 'lastmonth') { a = new Date(t.getFullYear(), t.getMonth()-1, 1); b = new Date(t.getFullYear(), t.getMonth(), 0); }
  else if (p === 'year') a = new Date(t.getFullYear(), 0, 1);
  else if (p === 'all') a = new Date(firstDay+'T00:00');
  rFrom.value = iso(a) < firstDay ? firstDay : iso(a); rTo.value = iso(b);
  showDates();
}
setRange('month');
document.querySelectorAll('.presets button').forEach(b => b.onclick = () => { setRange(b.dataset.p); draw(); });

// ---------- chart ----------
let chart, view = 'today', sub = 'sToday';
function draw(){
  const sun = css('--sun'), soft = css('--sun-soft'), ink = css('--muted'), line = css('--line'), card = css('--card');
  let type='bar', labels=[], datasets=[], unit='units', cap='', yMin;
  const single = (vals, isLine, lastPartial=true) => [{data:vals, borderColor:sun, borderWidth:isLine?2:0,
      // solid bars; the last one (today / this month / this year, still in progress) is lighter
      backgroundColor: isLine ? soft : vals.map((v,i)=> (lastPartial && i===vals.length-1) ? sun+'80' : sun),
      hoverBackgroundColor:sun, borderSkipped:false, fill:isLine, tension:.35, pointRadius:0, borderRadius:4}];
  document.getElementById('subtabs').classList.toggle('show', view==='strings');
  document.getElementById('rangebar').classList.toggle('show', view==='range');
  document.getElementById('rangesum').classList.toggle('show', view==='range');
  if (view==='today'){
    type='line'; unit='kW';
    labels = D.curve.map(x=>x[0]); datasets = single(D.curve.map(x=>x[1]), true);
    cap = D.curve.length ? 'Power from your panels through the day (kW). The area under the curve is the units made.' : 'No readings for today yet.';
  } else if (view==='days'){
    const s = daily.slice(-30);
    labels = s.map(x=>dayLabel(x.d)); datasets = single(s.map(x=>+x.v.toFixed(2)));
    cap = 'Units generated each day. Total for these 30 days: '+fmt(sum(s),0)+' units.';
  } else if (view==='weeks'){
    const wl = k => { const d = new Date(k+'T00:00'); return d.getDate()+' '+MONTHS[d.getMonth()]; };
    labels = weekKeys.map(wl); datasets = single(weekKeys.map(k=>+byWeek[k].toFixed(1)));
    cap = 'Units per week (Monday–Sunday, labelled by the Monday). A slow slide over several sunny weeks often means dust — time for a wash.';
    if (weekCompare) cap += ' This week so far: ' + fmt(weekCompare.a,0) + ' units vs ' + fmt(weekCompare.b,0) +
      ' in the same ' + weekCompare.n + ' day' + (weekCompare.n>1?'s':'') + ' last week (' + (weekCompare.pct>=0?'+':'') + fmt(weekCompare.pct,0) + '%).';
  } else if (view==='months'){
    labels = monthKeys.map(mLabel); datasets = single(monthKeys.map(k=>+byMonth[k].toFixed(1)));
    cap = 'Units per month. Monsoon months usually dip — that’s clouds, not a fault.';
  } else if (view==='range'){
    let a = rFrom.value || firstDay, b = rTo.value || todayStr;
    if (a > b) [a, b] = [b, a];
    const sel = daily.filter(x => x.d >= a && x.d <= b);
    const tot = sum(sel), n = sel.length;
    const best = sel.reduce((m,x)=> x.v > (m ? m.v : -1) ? x : m, null);
    const partial = b >= todayStr;
    const span = (new Date(b) - new Date(a)) / 864e5 + 1;
    if (span <= 62){
      labels = sel.map(x=>dayLabel(x.d)); datasets = single(sel.map(x=>+x.v.toFixed(2)), false, partial);
      cap = 'Units generated each day from '+longDate(a)+' to '+longDate(b)+'.';
    } else {
      const m = {}; sel.forEach(x => m[x.d.slice(0,7)] = (m[x.d.slice(0,7)]||0) + x.v);
      const ks = Object.keys(m).sort();
      labels = ks.map(mLabel); datasets = single(ks.map(k=>+m[k].toFixed(1)), false, partial);
      cap = 'Units per month from '+longDate(a)+' to '+longDate(b)+' (long ranges are grouped by month).';
    }
    document.getElementById('rangesum').innerHTML =
      `<div><div class="k">Total</div><div class="v">${fmt(tot,1)} units</div></div>
       <div><div class="k">Days with data</div><div class="v">${n}</div></div>
       <div><div class="k">Average per day</div><div class="v">${n ? fmt(tot/n,1) : 0} units</div></div>
       <div><div class="k">Best day</div><div class="v">${best ? fmt(best.v,1)+' <small style="font-size:.8rem;color:var(--muted)">'+dayLabel(best.d)+'</small>' : '–'}</div></div>`;
    if (!n) cap = 'No data in this range.';
  } else if (view==='years'){
    labels = yearKeys; datasets = single(yearKeys.map(k=>+byYear[k].toFixed(0)));
    cap = 'Units per year (current year is partial).';
  } else {
    type='line';
    const mk = (s,i,data) => ({label:s, data, borderColor:scol(i), backgroundColor:scol(i), borderWidth:2,
      tension:.3, pointRadius: sub==='sToday'?0:3, pointHoverRadius:5, pointBorderColor:card, pointBorderWidth:2, spanGaps:true});
    if (sub==='sBranch'){
      type='bar'; unit='amps';
      const bLast = B.days.length > 1 ? B.days[B.days.length-2] : (B.days[0]||{});
      labels = B.names.map(lab);
      datasets = [
        {label:'Yesterday (midday)', data:B.names.map(b=>bLast[b]||0), borderRadius:4, borderSkipped:false,
         backgroundColor:B.names.map(b => B.flags[b] ? css(STATUS[B.flags[b]].c) : scol(0))},
        {label:'Usual', data:B.names.map(bUsual), borderRadius:4, borderSkipped:false, backgroundColor:css('--muted')+'66'}
      ];
      cap = 'Midday current of each individual string, yesterday vs its usual. Strings with the same number of panels facing the same way should be about equal; a shorter bar marked in amber/red is the one to inspect.';
    } else if (sub==='sToday'){
      unit='kW'; labels = S.today.labels;
      datasets = S.names.map((s,i)=> mk(s,i,S.today.series[s]));
      cap = 'Each string’s power today. Strings facing the same way should track together; a string sitting lower all day deserves a look.';
    } else {
      const days = S.days;
      labels = days.map(d=>dayLabel(d.date));
      if (sub==='sShare'){
        unit='% of output';
        datasets = S.names.map((s,i)=> mk(s,i, days.map(d => { const t = S.names.reduce((a,n)=>a+((d[n]||{}).p||0),0); return t ? +(((d[s]||{}).p||0)/t*100).toFixed(1) : null; })));
        cap = 'Each string’s share of midday output. Clouds hit every string equally, so these lines stay flat when all is well — a step down means that string lost something.';
      } else {
        unit='volts';
        datasets = S.names.map((s,i)=> mk(s,i, days.map(d => (d[s] && d[s].v) || null)));
        const vs = datasets.flatMap(d=>d.data).filter(x=>x); yMin = vs.length ? Math.floor(Math.min(...vs)*0.85/10)*10 : undefined;
        cap = 'Average string voltage between 11am and 2pm. Each panel adds about the same voltage, so a sudden step down usually means one panel (or its diode/connector) has dropped out.';
      }
    }
  }
  document.getElementById('caption').textContent = cap;
  if (chart) chart.destroy();
  const multi = datasets.length > 1;
  chart = new Chart(document.getElementById('chart'), {
    type, data:{labels, datasets},
    options:{
      responsive:true, maintainAspectRatio:false, animation:{duration:400},
      interaction: multi ? {mode:'index', intersect:false} : {mode:'nearest', intersect:false},
      plugins:{
        legend:{display:multi, position:'top', align:'start', labels:{color:ink, usePointStyle:true, boxWidth:8}},
        tooltip:{callbacks:{label:c => (multi? c.dataset.label+': ' : '') + fmt(c.parsed.y,2)+' '+unit}}
      },
      scales:{
        x:{grid:{display:false}, ticks:{color:ink, maxRotation:0, autoSkip:true, maxTicksLimit: (view==='today'||sub==='sToday')?8:10}},
        y:{beginAtZero: yMin===undefined, min:yMin, grid:{color:line}, ticks:{color:ink}, title:{display:true, text:unit, color:ink}}
      }
    }
  });
}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on', x===b));
  view = b.dataset.v; draw();
});
document.querySelectorAll('.subtabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.subtabs button').forEach(x=>x.classList.toggle('on', x===b));
  sub = b.dataset.s; draw();
});
const sel = document.getElementById('themeSel');
try { const saved = localStorage.getItem('solarTheme'); if (saved && sel.querySelector(`[value="${saved}"]`)) document.documentElement.dataset.palette = saved; } catch(e) {}
sel.value = document.documentElement.dataset.palette;
sel.onchange = () => {
  document.documentElement.dataset.palette = sel.value;
  try { localStorage.setItem('solarTheme', sel.value); } catch(e) {}
  draw();
};
draw();
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ probe
def probe():
    """Print raw device responses (serial numbers shortened) for troubleshooting."""
    tz = get_tz()
    api = Deye()
    st = pick_station(api)
    print(f"Station {masked(st.get('id'))} {'(hidden)' if MASK_IDS else st.get('name')}")
    day = datetime.now(tz).date() - timedelta(days=1)
    for d in api.devices(int(st["id"])):
        sn, dtype = d["deviceSn"], d.get("deviceType")
        print(f"\nDevice ...{sn[-4:]}  type={dtype}  product={d.get('productId')}")
        try:
            pts = api.measure_points(sn, dtype)
            print(f"  measurePoints ({len(pts)}): {pts}")
        except DeyeError as e:
            print(f"  measurePoints failed: {e}")
            pts = []
        pv = [p for p in pts if PV_KEY.match(p)] or ["DCPowerPV1", "DCVoltagePV1", "DCCurrentPV1"]
        try:
            latest = api._post("device/latest", {"deviceList": [sn]})
            keys = [x.get("key") for dd in latest.get("deviceDataList") or [] for x in dd.get("dataList") or []]
            print(f"  device/latest keys with PV: {[k for k in keys if 'PV' in str(k).upper()]}")
        except DeyeError as e:
            print(f"  device/latest failed: {e}")
        for name, body in api._history_bodies(sn, day, pv, dtype):
            try:
                data = api._post("device/history", body)
                dl = data.get("dataList") or []
                print(f"  device/history [{name}]: OK, {len(dl)} rows; first row: "
                      f"{json.dumps(dl[:1])[:600]}")
                break
            except DeyeError as e:
                print(f"  device/history [{name}]: {e}")


# ------------------------------------------------------------------ resident update
WMO_RAIN = set(range(51, 68)) | set(range(80, 83))
WMO_STORM = {95, 96, 99}

LINES = {
    "sunny": [
        "☀️ Clear skies and bright sunshine today — a perfect day for our rooftop!",
        "☀️ A beautiful sunny day ahead — our panels are ready to shine.",
        "☀️ Sunshine all day — expect a strong day on the roof.",
    ],
    "mostly": [
        "🌤️ Mostly sunny with a few passing clouds — a good day for solar.",
        "🌤️ Plenty of sunshine with the odd cloud — the panels should do well.",
        "🌤️ A bright day with a few clouds drifting by — good solar weather.",
    ],
    "partly": [
        "⛅ A mix of sun and clouds today — the panels will make the most of every bright spell.",
        "⛅ Sun and clouds taking turns today — a fair day for solar.",
        "⛅ Partly cloudy — expect a steady, middling day on the roof.",
    ],
    "cloudy": [
        "☁️ A cloudy day — generation will be lighter, and that's perfectly normal.",
        "☁️ Grey skies today — the panels still work under clouds, just more gently.",
        "☁️ Clouds are in charge today — a quieter day for our rooftop.",
    ],
    "rain": [
        "🌧️ Rain expected today — the panels will take it easy, and they get a free wash too!",
        "🌧️ A rainy day ahead — lighter generation, but the rain will clean the panels nicely.",
        "🌧️ Showers on the way — a gentle day for solar and a refreshing one for the city.",
    ],
    "storm": [
        "⛈️ Thunderstorms are likely today — lighter generation expected. Stay safe!",
        "⛈️ Stormy weather ahead — the panels will rest a bit. Stay dry and safe!",
    ],
}
CLOSERS = [
    "Thank you for being part of a greener {name} 🌱",
    "Every unit from the sun is one less from the grid 🌍",
    "Clean energy, made right on our own roof 💚",
    "Small steps, bright future — together we make a difference 🌿",
    "Powered by sunshine, shared by all of us ☀️",
]


def fetch_forecast(lat, lng):
    """Today's weather for the rooftop from Open-Meteo (free, no key). None if unavailable."""
    import requests
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=20, params={
            "latitude": lat, "longitude": lng, "timezone": TIMEZONE, "forecast_days": 1,
            "daily": "weather_code,sunshine_duration,shortwave_radiation_sum,cloud_cover_mean,"
                     "precipitation_probability_max,precipitation_sum,temperature_2m_max,"
                     "temperature_2m_min,relative_humidity_2m_mean,wind_speed_10m_max,uv_index_max"})
        r.raise_for_status()
        d = r.json()["daily"]
        pick = lambda k: (d.get(k) or [None])[0]
        return {"date": pick("time"), "code": pick("weather_code"),
                "sun_h": round((pick("sunshine_duration") or 0) / 3600, 1),
                "rad": round((pick("shortwave_radiation_sum") or 0) / 3.6, 2),   # MJ/m² → kWh/m²
                "cloud": pick("cloud_cover_mean"), "rain": pick("precipitation_probability_max"),
                "tmax": pick("temperature_2m_max"), "tmin": pick("temperature_2m_min"),
                "rain_mm": pick("precipitation_sum"), "humidity": pick("relative_humidity_2m_mean"),
                "wind": pick("wind_speed_10m_max"), "uv": pick("uv_index_max")}
    except Exception as e:
        print(f"  weather forecast unavailable ({type(e).__name__}) — the message is sent without it")
        return None


def sky_mood(fc):
    code, rad = fc.get("code") or 0, fc.get("rad") or 0
    rain, cloud = fc.get("rain") or 0, fc.get("cloud") if fc.get("cloud") is not None else 50
    if code in WMO_STORM:
        return "storm"
    if (code in WMO_RAIN and rain >= 50) or rain >= 75:
        return "rain"
    if rad >= 5.5 and cloud < 40:
        return "sunny"
    if rad >= 4.5:
        return "mostly"
    if rad >= 3.3:
        return "partly"
    return "cloudy"


def expected_units(fc, daily, capacity, log):
    """Units expected today. Learns this rooftop's own units-per-radiation from past days."""
    if not fc or not fc.get("rad"):
        return None
    actual = dict(daily)
    ratios = sorted(actual[d] / r for d, r in log.items() if d in actual and r > 0.5 and actual[d] > 0)
    if len(ratios) >= 3:
        factor = ratios[len(ratios) // 2]
    elif capacity:
        factor = capacity * 0.78              # typical performance ratio until it has learned
    else:
        return None
    return fc["rad"] * factor


def round_range(x):
    lo, hi = x * 0.9, x * 1.1
    step = 10 if x >= 60 else 5 if x >= 20 else 1
    return int(lo // step * step), int(-(-hi // step) * step)


def compose_update(data, fc, log, today):
    name = COMMUNITY_NAME or (DISPLAY_NAME if DISPLAY_NAME else "") or \
        (data.get("name") if data.get("name") not in ("My Solar", None) else "Our rooftop")
    daily = [(d, float(v)) for d, v in data.get("daily", [])]
    by = dict(daily)
    yday = (today - timedelta(days=1)).isoformat()
    seed = today.toordinal()
    lines = [f"☀️ {name} — Solar update", f"{today.strftime('%A')}, {today.day} {today.strftime('%b %Y')}", ""]

    y = by.get(yday)
    prior = [v for d, v in daily if d < yday][-30:]
    avg = statistics.fmean(prior) if prior else 0
    if y is not None:
        if avg and y >= avg * 1.1:
            feel = f"a great day 🎉 ({(y / avg - 1):.0%} above our usual)"
        elif avg and y <= avg * 0.8:
            feel = "a lighter day — the clouds had their say ☁️"
        else:
            feel = "right on track 👍"
        lines.append(f"Yesterday our rooftop made *{y:,.0f} units* — {feel}.")

    if fc:
        if lines[-1] != "":
            lines.append("")
        mood_line = LINES[sky_mood(fc)][seed % len(LINES[sky_mood(fc)])]
        icon, mood_text = mood_line.split(" ", 1)
        exp = expected_units(fc, daily, data.get("capacity"), log)
        extra = []
        if fc.get("sun_h"):
            extra.append(f"about {fc['sun_h']:g} hours of sunshine")
        if exp:
            lo, hi = round_range(exp)
            tail = f" We expect around *{lo:,}–{hi:,} units*" + (", with " + " and ".join(extra) if extra else "") + "."
        elif extra:
            tail = " Expect " + " and ".join(extra) + "."
        else:
            tail = ""
        place = f" for {WEATHER_PLACE}" if WEATHER_PLACE else ""
        lines.append(f"{icon} *Today's forecast{place}:* {mood_text}{tail}")
        det = []
        if fc.get("tmin") is not None and fc.get("tmax") is not None:
            det.append(f"🌡️ {fc['tmin']:.0f}–{fc['tmax']:.0f}°C")
        elif fc.get("tmax") is not None:
            det.append(f"🌡️ high {fc['tmax']:.0f}°C")
        if fc.get("cloud") is not None:
            det.append(f"☁️ {fc['cloud']:.0f}% cloud")
        if fc.get("rain") is not None:
            r = f"🌧️ {fc['rain']:.0f}% chance of rain"
            if (fc.get("rain_mm") or 0) >= 1:
                r += f" (~{fc['rain_mm']:.0f} mm)"
            det.append(r)
        if fc.get("humidity") is not None:
            det.append(f"💧 {fc['humidity']:.0f}% humidity")
        if fc.get("wind") is not None:
            det.append(f"💨 up to {fc['wind']:.0f} km/h")
        if fc.get("uv") is not None and fc["uv"] >= 8:
            det.append(f"🔆 UV {fc['uv']:.0f} (very high)")
        if det:
            lines.append(" · ".join(det))

    month = today.strftime("%Y-%m")
    mtot = sum(v for d, v in daily if d.startswith(month) and d < today.isoformat())
    if lines[-1] != "":
        lines.append("")
    if mtot:
        lines.append(f"This month so far: {mtot:,.0f} units")
    last_m = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    ltot = sum(v for d, v in daily if d.startswith(last_m))
    if ltot:
        lines.append(f"Last month ({datetime.strptime(last_m, '%Y-%m').strftime('%B')}): {ltot:,.0f} units")

    h = (data.get("strings") or {}).get("health") or {}
    if SHOW_HEALTH_TO_RESIDENTS and h.get("status") in ("good", "warning", "critical"):
        lines.append({"good": "Panels: ✅ all working normally",
                      "warning": "Panels: 🔍 one section is being watched closely",
                      "critical": "Panels: 🛠️ one section needs a check — it's being looked into"}[h["status"]])
    if UPDATE_CLOSING_LINE:
        lines += ["", CLOSERS[seed % len(CLOSERS)].format(name=name)]
    if UPDATE_DASHBOARD_LINK and DASHBOARD_URL:
        lines.append(f"📊 {DASHBOARD_URL}")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def whatsapp_safe(text):
    """Text for a WhatsApp share link: drop the invisible emoji-style marker (U+FE0F)
    and zero-width joiners that some phones render as boxes or '?'."""
    return text.replace("\ufe0f", "").replace("\u200d", "")


def tg_send(chat, text, markdown=True):
    import requests
    body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if markdown:
        body["parse_mode"] = "Markdown"
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=body, timeout=20)
    if not r.ok and markdown:                      # odd characters: fall back to plain text
        return tg_send(chat, text.replace("*", ""), markdown=False)
    r.raise_for_status()


def resident_update(data, today, now, force=False, send=True):
    """Build today's message; send it once in the morning window. Returns the text."""
    lat = LATITUDE or data.get("lat") or 12.9716
    lng = LONGITUDE or data.get("lng") or 77.5946
    log = load_json(Path(FORECAST_JSON), {})
    fc = fetch_forecast(lat, lng)
    if fc and fc.get("rad"):
        log[today.isoformat()] = fc["rad"]
        cutoff = (today - timedelta(days=60)).isoformat()
        save_json(Path(FORECAST_JSON), {k: v for k, v in log.items() if k >= cutoff})
    text = compose_update(data, fc, log, today)
    Path(UPDATE_TXT).write_text(text, encoding="utf-8")
    data["update"] = {"text": text, "date": today.isoformat()}

    state = load_json(Path(ALERT_STATE), {})
    due = MORNING_WINDOW[0] <= now.hour < MORNING_WINDOW[1] and state.get("update_sent") != today.isoformat()
    if not send or not (due or force):
        return text
    if not TELEGRAM_BOT_TOKEN:
        print("  resident update written to", UPDATE_TXT, "(set TELEGRAM_BOT_TOKEN to send it automatically)")
        return text
    from urllib.parse import quote
    try:
        if RESIDENT_TELEGRAM_CHAT:
            tg_send(RESIDENT_TELEGRAM_CHAT, text)
            print("  resident update posted to", RESIDENT_TELEGRAM_CHAT)
        elif TELEGRAM_CHAT_ID:
            tg_send(TELEGRAM_CHAT_ID, text)
            share = "https://api.whatsapp.com/send?text=" + quote(whatsapp_safe(text), safe="")
            tg_send(TELEGRAM_CHAT_ID, f"📲 Share with residents on WhatsApp:\n{share}", markdown=False)
            print("  resident update sent to you on Telegram with a WhatsApp share link")
        state["update_sent"] = today.isoformat()
        save_json(Path(ALERT_STATE), state)
    except Exception as e:
        print(f"  resident update not sent ({e})")
    return text


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Deye Cloud solar units dashboard")
    ap.add_argument("--demo", action="store_true", help="use sample data (no API keys needed)")
    ap.add_argument("--stations", action="store_true", help="list stations and inverters, then exit")
    ap.add_argument("--test-alert", action="store_true", help="send a test alert and exit")
    ap.add_argument("--probe", action="store_true", help="print raw inverter API responses for troubleshooting")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard in a browser")
    ap.add_argument("--preview-update", action="store_true", help="print today's resident message (nothing is sent)")
    ap.add_argument("--send-update", action="store_true", help="send today's resident message now, even outside the morning")
    args = ap.parse_args()

    if args.test_alert:
        send_alerts(DISPLAY_NAME or "My", {"status": "good", "headline": "test alert — alerts are working.",
                                            "items": []}, force=True)
        return

    if args.probe:
        probe()
        return

    if args.stations:
        api = Deye()
        for s in api.stations():
            print(f"Station {masked(s.get('id'))}\t{'(hidden)' if MASK_IDS else s.get('name')}\t{s.get('installedCapacity')} kW")
            try:
                devs = api.devices(int(s["id"]))
            except DeyeError as e:
                print(f"   devices unavailable ({e})")
                continue
            for d in devs:
                print(f"   device ...{d.get('deviceSn', '')[-4:]}\t{d.get('deviceType')}")
                try:
                    pts = api.measure_points(d["deviceSn"], d.get("deviceType"))
                except DeyeError:
                    print("      (no readings — this is the Wi-Fi data logger, that's fine)")
                    continue
                pv = sorted({PV_KEY.match(p).group(2) for p in pts if PV_KEY.match(p)}, key=int)
                br = [p for p in pts if BRANCH_KEY.match(p)]
                print(f"      inputs PV{pv[0]}–PV{pv[-1]}" if pv else "      no PV inputs listed",
                      f"; per-string current for up to {len(br)} strings" if br else "")
        return

    data = collect_demo() if args.demo else collect_real()
    h = (data.get("strings") or {}).get("health") or (
        merge_alerts_into_health({"status": "good", "headline": "No problems reported by the inverter.",
                                  "items": []}, data.get("alerts")) if data.get("alerts") else None)
    if h:
        for s_, pinfo in ((data.get("strings") or {}).get("panels") or {}).items():
            if s_ == "_meta":
                continue
            ser = f"{'≈' if pinfo['est'] else ''}{pinfo['n']} in series"
            par = (f" × {'≈' if pinfo['est_p'] else ''}{pinfo['p']} strings = {pinfo['total']} panels"
                   if pinfo.get("p") else "")
            src = f"midday {pinfo['v']} V, peak {pinfo.get('ipeak', 0)} A" if pinfo.get("v") else "config"
            print(f"Panels on {s_}: {ser}{par}  ({src})")
        print(f"Panel health: {h['status'].upper()} — {h['headline']}")
        if not args.demo:
            send_alerts(data["name"], h)
    if RESIDENT_UPDATE or args.preview_update or args.send_update:
        now = datetime.now(get_tz())
        text = resident_update(data, now.date(), now, force=args.send_update,
                               send=not (args.demo or args.preview_update))
        if args.preview_update:
            print("\n----- resident message -----\n" + text + "\n----------------------------")
    out = Path(OUTPUT_HTML)
    out.write_text(build_html(data), encoding="utf-8")
    print(f"Dashboard written to {out.resolve()}")
    if OPEN_IN_BROWSER and not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()

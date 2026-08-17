#!/usr/bin/env python3
"""Mojave River flood monitor.

Watches the USGS real-time gages on the Mojave River (the reach that runs
through the Barstow / Victorville / Hodge area), compares each gage height
against the official NWS flood stages (fetched live from water.noaa.gov with
local fallbacks), watches the rate of rise for early warning, and publishes a
``mojave_river.json`` snapshot plus alert state.

Alerts fire on level *transitions* (or every few hours as a reminder while an
elevated level persists) through three channels:

- The site JSONs (always): ``mojave_river.json`` + ``mojave_flood_status.json``
- ntfy.sh push (optional): set ``USGS_NTFY_TOPIC``
- SMTP email (optional): set ``USGS_SMTP_*`` variables

Gages are listed in physical order, upstream first: the two Forks Reservoir
feeders (West Fork 10260950 + Deep Creek 10260500), then the main stem
Victorville lower narrows (10261500), Hodge (10262000), Barstow (10262500),
Daggett (10262700), and Afton (10263000) at the downstream end.

Official stages (water.noaa.gov, NRLDB):
- MBRC1 Mojave River at Barstow:      minor 5.0 ft / moderate 5.5 ft / major 6.0 ft
- MVVC1 Mojave River nr Victorville:  minor 16.0 ft / moderate 18.0 ft / major 19.0 ft
"""

from __future__ import annotations

import json
import math
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

BASE_DIR = Path(os.environ.get("SBCO_BASE_DIR", Path(__file__).resolve().parent))
BASE_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = BASE_DIR / ".state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_USER_AGENT = "sbco-calllog-scraper/1.0 (mojave flood monitor)"
WATER_NOAA_URL = "https://water.noaa.gov/gauges/"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
USGS_PERIOD = os.environ.get("USGS_FLOOD_PERIOD", "P3D")
TREND_WINDOW_SECONDS = int(os.environ.get("USGS_TREND_WINDOW_SECONDS", "10800"))  # 3h
RISE_WATCH_FT_PER_HOUR = float(os.environ.get("USGS_RISE_WATCH_FT_PER_HOUR", "0.5"))
RISE_WATCH_GAP_FT = float(os.environ.get("USGS_RISE_WATCH_GAP_FT", "2.0"))
ALERT_REMINDER_HOURS = float(os.environ.get("USGS_ALERT_REMINDER_HOURS", "6"))
NWS_ZONE_PREFIX = os.environ.get("USGS_NWS_ZONE_PREFIX", "CAZ06")
FETCH_TIMEOUT_SECONDS = int(os.environ.get("USGS_FETCH_TIMEOUT_SECONDS", "25"))

RIVER_FILE = BASE_DIR / "mojave_river.json"
STATUS_FILE = BASE_DIR / "mojave_flood_status.json"
STATE_FILE = STATE_DIR / "usgs_flood_state.json"

# site_no -> (label, nws gage id or None, lat, lon, default stages dict or None)
# Listed in physical order, upstream first: the two Forks Reservoir feeders (West
# Fork + Deep Creek), then the main stem Victorville -> Hodge -> Barstow ->
# Daggett -> Afton (the river flows north out of the mountains, then east past
# Barstow toward its terminus at the Mojave Sink). Only Barstow and Victorville
# have official NWS flood stages; the rest report as MONITOR.
SITES = [
    {
        "site_no": "10260950",
        "name": "W.F. Mojave River above Mojave River Forks Reservoir near Hesperia",
        "nws_id": None,
        "lat": 34.33889196,
        "lon": -117.2578213,
        "stages": None,
    },
    {
        "site_no": "10260500",
        "name": "Deep Creek near Hesperia",
        "nws_id": None,
        "lat": 34.34305858,
        "lon": -117.2264316,
        "stages": None,
    },
    {
        "site_no": "10261500",
        "name": "Mojave River at Lower Narrows near Victorville",
        "nws_id": "MVVC1",
        "lat": 34.57304916,
        "lon": -117.3206018,
        "stages": {"minor": 16.0, "moderate": 18.0, "major": 19.0},
    },
    {
        "site_no": "10262000",
        "name": "Mojave River near Hodge",
        "nws_id": None,
        "lat": 34.83551667,
        "lon": -117.1916778,
        "stages": None,
    },
    {
        "site_no": "10262500",
        "name": "Mojave River at Barstow",
        "nws_id": "MBRC1",
        "lat": 34.90693947,
        "lon": -117.0228129,
        "stages": {"minor": 5.0, "moderate": 5.5, "major": 6.0},
    },
    {
        "site_no": "10262700",
        "name": "Mojave River at Daggett",
        "nws_id": None,
        "lat": 34.8696611,
        "lon": -116.8876583,
        "stages": None,
    },
    {
        "site_no": "10263000",
        "name": "Mojave River at Afton",
        "nws_id": None,
        "lat": 35.03720565,
        "lon": -116.3841887,
        "stages": None,
    },
]

STAGE_PATTERN = re.compile(
    r'"(%s)":\{"label":"[^"]*","tooltip":"[^"]*","description":"[^"]*",'
    r'"stage":\{[^}]*?"value":(-?[0-9.]+)\},"flow":\{[^}]*?"value":(-?[0-9.]+)\}'
    % "major|moderate|minor|action",
    re.I,
)

LEVEL_ORDER = ["NORMAL", "WATCH", "ACTION", "MINOR", "MODERATE", "MAJOR"]
LEVEL_RANK = {"OFFLINE": -1, "MONITOR": 0, "NORMAL": 1, "WATCH": 2, "ACTION": 2, "MINOR": 3, "MODERATE": 4, "MAJOR": 5}
LEVEL_COLOR = {
    "NORMAL": "good",
    "WATCH": "warn",
    "ACTION": "warn",
    "MINOR": "warn",
    "MODERATE": "danger",
    "MAJOR": "danger",
}
NTFY_PRIORITY = {"ACTION": "min", "WATCH": "min", "MINOR": "default", "MODERATE": "high", "MAJOR": "urgent"}


def fetch_usgs(site_nos: list[str]) -> dict:
    response = requests.get(
        USGS_IV_URL,
        params={
            "format": "json",
            "sites": ",".join(site_nos),
            "parameterCd": "00065,00060",
            "period": USGS_PERIOD,
        },
        headers={"User-Agent": USGS_USER_AGENT},
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    by_site: dict[str, dict] = {}
    for series in payload.get("value", {}).get("timeSeries", []):
        info = series.get("sourceInfo", {})
        site_no = None
        for code in info.get("siteCode", []):
            if code.get("network") == "NWIS":
                site_no = code.get("value")
                break
        if not site_no:
            continue
        param = (series.get("variable", {}).get("variableCode") or [{}])[0].get("value")
        readings = (series.get("values") or [{}])[0].get("value", [])
        entry = by_site.setdefault(site_no, {"stage": None, "flow": None, "readings": []})
        parsed = []
        for reading in readings:
            try:
                parsed.append(
                    {
                        "t": datetime.fromisoformat(reading["dateTime"]).timestamp(),
                        "v": float(reading["value"]),
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
        parsed.sort(key=lambda item: item["t"])
        if param == "00065":
            entry["stage"] = parsed
        elif param == "00060":
            entry["flow"] = parsed
    return by_site


def fetch_nws_stages(nws_id: str) -> dict:
    stages: dict[str, float] = {}
    try:
        text = requests.get(
            WATER_NOAA_URL + nws_id,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=FETCH_TIMEOUT_SECONDS,
        ).text
        for match in STAGE_PATTERN.finditer(text):
            category = match.group(1).lower()
            value = float(match.group(2))
            if value > 0:
                stages[category] = value
    except Exception:  # noqa: BLE001 - fall back to configured defaults
        pass
    return stages


def fetch_nws_warnings() -> list[dict]:
    warnings = []
    try:
        response = requests.get(
            NWS_ALERTS_URL,
            params={"area": "CA"},
            headers={
                "User-Agent": USGS_USER_AGENT,
                "Accept": "application/geo+json",
            },
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for feature in response.json().get("features", []):
            props = feature.get("properties", {})
            zone = props.get("zoneId") or ""
            if not zone.startswith(NWS_ZONE_PREFIX):
                continue
            event = props.get("event") or ""
            if "flood" not in event.lower():
                continue
            warnings.append(
                {
                    "id": props.get("id", ""),
                    "event": event,
                    "severity": props.get("severity", ""),
                    "headline": props.get("headline", ""),
                    "sent": props.get("sent", ""),
                }
            )
    except Exception:  # noqa: BLE001 - warnings are a bonus signal, never fatal
        pass
    return warnings


def linear_slope(points: list[tuple[float, float]]) -> float:
    n = len(points)
    if n < 2:
        return 0.0
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    denom = sum((p[0] - mean_x) ** 2 for p in points)
    if denom == 0:
        return 0.0
    return sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / denom


def classify(stage: float | None, stages: dict, rise_rate: float) -> str:
    if stage is None or not stages:
        return "MONITOR"
    if stage >= stages.get("major", math.inf):
        return "MAJOR"
    if stage >= stages.get("moderate", math.inf):
        return "MODERATE"
    if stage >= stages.get("minor", math.inf):
        return "MINOR"
    action = stages.get("action")
    if action and stage >= action:
        return "ACTION"
    if stage >= stages.get("minor", math.inf) - RISE_WATCH_GAP_FT and rise_rate >= RISE_WATCH_FT_PER_HOUR:
        return "WATCH"
    return "NORMAL"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_ntfy(topic: str, title: str, body: str, priority: str, tags: str) -> bool:
    try:
        response = requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": NTFY_PRIORITY.get(priority, "default"),
                "Tags": tags,
            },
            timeout=20,
        )
        return response.status_code in (200, 201)
    except Exception:
        return False


def send_email(message_body: str) -> bool:
    host = os.environ.get("USGS_SMTP_HOST", "").strip()
    if not host:
        return False
    sender = os.environ.get("USGS_SMTP_FROM", "").strip()
    recipients = [r.strip() for r in os.environ.get("USGS_SMTP_TO", "").split(",") if r.strip()]
    if not sender or not recipients:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = "Mojave River flood alert"
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.set_content(message_body)
        port = int(os.environ.get("USGS_SMTP_PORT") or "587")
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if (os.environ.get("USGS_SMTP_STARTTLS") or "1") != "0":
                smtp.starttls()
            user = os.environ.get("USGS_SMTP_USER", "").strip()
            password = os.environ.get("USGS_SMTP_PASS", "").strip()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def fire_notifiers(title: str, body: str, level: str) -> list[str]:
    fired = []
    topic = os.environ.get("USGS_NTFY_TOPIC", "").strip()
    if topic and send_ntfy(topic, title, body, level, "droplet,flood"):
        fired.append("ntfy")
    if send_email(body):
        fired.append("email")
    return fired


def main() -> int:
    started = time.time()
    error = ""
    nws_warnings = fetch_nws_warnings()
    state = load_state()

    site_configs = {site["site_no"]: site for site in SITES}
    try:
        usgs = fetch_usgs(list(site_configs.keys()))
    except Exception as exc:  # noqa: BLE001
        usgs = {}
        error = f"{type(exc).__name__}: {exc}"

    gages = []
    overall_level = "NORMAL"
    alerts_fired = []

    for site in SITES:
        site_no = site["site_no"]
        stages = dict(site["stages"] or {})
        nws_id = site["nws_id"]
        if nws_id:
            live_stages = fetch_nws_stages(nws_id)
            for key, value in live_stages.items():
                stages[key] = value

        data = usgs.get(site_no, {})
        stage_readings = data.get("stage") or []
        flow_readings = data.get("flow") or []
        latest_stage = stage_readings[-1]["v"] if stage_readings else None
        latest_flow = flow_readings[-1]["v"] if flow_readings else None
        reading_time = (
            datetime.fromtimestamp(stage_readings[-1]["t"], tz=timezone.utc).isoformat()
            if stage_readings
            else ""
        )

        now = time.time()
        trend_points = [
            (r["t"], r["v"])
            for r in stage_readings
            if now - r["t"] <= TREND_WINDOW_SECONDS and r["v"] is not None
        ]
        # linear_slope works in epoch seconds -> multiply by 3600 for ft/hour.
        rise_rate = round(linear_slope(trend_points) * 3600, 3) if trend_points else 0.0

        level = classify(latest_stage, stages, rise_rate)
        if level == "MONITOR":
            level = "NORMAL" if latest_stage is not None else "OFFLINE"
        if LEVEL_RANK.get(level, 0) > LEVEL_RANK.get(overall_level, 0):
            overall_level = level

        gage = {
            "site_no": site_no,
            "name": site["name"],
            "lat": site["lat"],
            "lon": site["lon"],
            "nws_id": nws_id,
            "stage": latest_stage,
            "flow": latest_flow,
            "reading_time": reading_time,
            "rise_rate_ft_per_hr": rise_rate,
            "level": level,
            "stages": stages,
            "level_color": LEVEL_COLOR.get(level, "muted"),
        }

        # Alert transitions + reminders for flood-capable gages.
        if level in ("ACTION", "WATCH", "MINOR", "MODERATE", "MAJOR"):
            previous = state.get("gages", {}).get(site_no, {}).get("level", "NORMAL")
            last_fired = state.get("gages", {}).get(site_no, {}).get("last_fired_at", 0)
            escalated = LEVEL_ORDER.index(level) > LEVEL_ORDER.index(previous) if previous in LEVEL_ORDER else True
            reminder_due = (time.time() - last_fired) > ALERT_REMINDER_HOURS * 3600
            if escalated or reminder_due:
                detail = f"{site['name']} at {latest_stage} ft (minor {stages.get('minor')} ft)"
                if rise_rate >= RISE_WATCH_FT_PER_HOUR:
                    detail += f", rising {rise_rate} ft/hr"
                if latest_flow is not None:
                    detail += f", flow {latest_flow:g} cfs"
                if reading_time:
                    detail += f", observed {reading_time}"
                fired = fire_notifiers(
                    f"Mojave River {level} alert: {site['name']}",
                    detail,
                    level,
                )
                alerts_fired.append({"site_no": site_no, "level": level, "channels": fired, "detail": detail})
                state.setdefault("gages", {}).setdefault(site_no, {})["level"] = level
                state["gages"][site_no]["last_fired_at"] = time.time()
                state["gages"][site_no]["last_detail"] = detail
        elif level == "NORMAL":
            state.setdefault("gages", {}).setdefault(site_no, {})["level"] = "NORMAL"

        gages.append(gage)

    # NWS flood warnings boost the overall level to at least WATCH.
    if nws_warnings:
        if LEVEL_RANK.get(overall_level, 0) < LEVEL_RANK["WATCH"]:
            overall_level = "WATCH"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "USGS NWIS IV + NWS (water.noaa.gov flood stages)",
        "alert_level": overall_level,
        "alerting": overall_level in ("MINOR", "MODERATE", "MAJOR"),
        "watching": overall_level in ("ACTION", "WATCH", "MINOR", "MODERATE", "MAJOR"),
        "nws_warnings": nws_warnings,
        "alerts_fired": alerts_fired,
        "gages": gages,
    }
    RIVER_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[+] mojave_river.json: level={overall_level} gages={len(gages)} alerts_fired={len(alerts_fired)}")

    status = {
        "source": "usgs-flood-monitor",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "generated_at": payload["generated_at"],
        "alert_level": overall_level,
        "alerting": payload["alerting"],
        "gages": [
            {
                "site_no": g["site_no"],
                "name": g["name"],
                "stage": g["stage"],
                "level": g["level"],
            }
            for g in gages
        ],
        "nws_warnings": len(nws_warnings),
        "duration_seconds": round(time.time() - started, 2),
        "error": error,
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"[+] mojave_flood_status.json: level={overall_level} error={error or 'none'}")

    save_state(state)
    return 0 if not error else 1


if __name__ == "__main__":
    sys.exit(main())

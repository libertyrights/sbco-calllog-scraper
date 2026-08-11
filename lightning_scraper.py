#!/usr/bin/env python3
"""Blitzortung lightning strike scraper for the upnexx.xyz call log site.

Fetches live strikes from the Blitzortung / lightningmaps live feed, keeps a
rolling window around San Bernardino County, and writes a compact
``lightning_strikes.json`` for the site viewer plus a small status file.

The live feed (the same one the lightningmaps.org web client polls) is served
over HTTP(S) JSON at ``https://live.lightningmaps.org/l/``:

  1. GET ?v=<version>&l=0&i=<src_mask>   -> syncs the server stroke counter ``s``
  2. GET ?v=<version>&l=<s>&i=<src_mask> -> returns ``d[]`` of recent strikes and
     a fresh ``s`` (poll again to drain the backlog and stay current)

Each strike has ``lat``/``lon`` (decimal strings), ``time`` (milliseconds offset
relative to the response's ``t`` epoch-seconds field), ``src`` (detector source),
``id``, ``del`` and ``dev``.

Note: Blitzortung asks that data be used with attribution and not for commercial
purpose; the site viewer credits ``© Blitzortung.org contributors``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(os.environ.get("SBCO_BASE_DIR", Path(__file__).resolve().parent))
BASE_DIR.mkdir(parents=True, exist_ok=True)

LIGHTNING_FEED_VERSION = int(os.environ.get("SBCO_LIGHTNING_FEED_VERSION", "24"))
LIGHTNING_SRC_MASK = int(os.environ.get("SBCO_LIGHTNING_SRC_MASK", "4"))
LIGHTNING_HOSTS = [
    host.strip()
    for host in os.environ.get("SBCO_LIGHTNING_HOSTS", "live,live2").split(",")
    if host.strip()
]
LIGHTNING_TIMEOUT_SECONDS = int(os.environ.get("SBCO_LIGHTNING_TIMEOUT_SECONDS", "20"))
LIGHTNING_DRAIN_POLLS = int(os.environ.get("SBCO_LIGHTNING_DRAIN_POLLS", "10"))
LIGHTNING_POLL_DELAY_SECONDS = float(os.environ.get("SBCO_LIGHTNING_POLL_DELAY_SECONDS", "0.8"))

# south, west, north, east around San Bernardino County (+ a little cushion).
DEFAULT_BBOX = (33.5, -118.6, 36.0, -114.2)
RAW_BBOX = os.environ.get(
    "SBCO_LIGHTNING_BBOX",
    ",".join(str(value) for value in DEFAULT_BBOX),
).split(",")
try:
    BBOX = tuple(float(value.strip()) for value in RAW_BBOX[:4])
except Exception:
    BBOX = DEFAULT_BBOX
SOUTH, WEST, NORTH, EAST = BBOX

WINDOW_SECONDS = int(os.environ.get("SBCO_LIGHTNING_WINDOW_SECONDS", "3600"))
MAX_STRIKES = int(os.environ.get("SBCO_LIGHTNING_MAX_STRIKES", "3000"))
DEDUPE_ROUND = 4
DEDUPE_TIME_SECONDS = 5

STRIKES_FILE = BASE_DIR / "lightning_strikes.json"
STATUS_FILE = BASE_DIR / "lightning_status.json"
USER_AGENT = "Mozilla/5.0 (compatible; sbco-calllog-scraper/1.0)"
REFERER = "https://www.lightningmaps.org/"


def fetch_feed(host: str, length: int) -> dict | None:
    url = (
        f"https://{host}.lightningmaps.org/l/"
        f"?v={LIGHTNING_FEED_VERSION}&l={length}&i={LIGHTNING_SRC_MASK}"
    )
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Origin": "https://www.lightningmaps.org",
                "Referer": REFERER,
            },
            timeout=LIGHTNING_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def in_bbox(lat: float, lon: float) -> bool:
    return SOUTH <= lat <= NORTH and WEST <= lon <= EAST


def strike_time_epoch(data: dict, strike: dict) -> float:
    """Epoch seconds for a strike, based on the response's ``t`` time base."""
    base = float(data.get("t") or 0)
    offset = float(strike.get("time") or 0) / 1000.0
    return base + offset


def build_payload(strikes: list[dict], meta: dict) -> dict:
    strikes.sort(key=lambda item: item["t"], reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "blitzortung.org (live lightningmaps feed)",
        "attribution": "Lightning data © Blitzortung.org contributors",
        "bbox": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "window_seconds": WINDOW_SECONDS,
        "count": len(strikes),
        "meta": meta,
        "strikes": strikes,
    }


def main() -> int:
    started = time.time()
    error = ""
    seen = set()
    strikes: dict[int, dict] = {}
    meta = {"hosts": LIGHTNING_HOSTS, "polls": 0}

    # First: sync with l=0 on any available host.
    length = 0
    data = None
    for host in LIGHTNING_HOSTS:
        data = fetch_feed(host, length)
        if data is not None:
            meta["sync_host"] = host
            break
    if data is None:
        error = "Could not reach the Blitzortung live feed (sync failed)."
    else:
        server_count = data.get("s")
        if server_count is not None:
            length = int(server_count)

        polls = 0
        while polls < LIGHTNING_DRAIN_POLLS:
            for host in LIGHTNING_HOSTS:
                data = fetch_feed(host, length)
                if data is not None:
                    break
            if data is None:
                error = "Lost connection to the Blitzortung live feed during polling."
                break
            polls += 1

            server_count = data.get("s")
            if server_count is not None:
                length = int(server_count)
            now = time.time()
            for strike in data.get("d") or []:
                try:
                    lat = float(strike.get("lat"))
                    lon = float(strike.get("lon"))
                except (TypeError, ValueError):
                    continue
                if not in_bbox(lat, lon):
                    continue
                epoch = strike_time_epoch(data, strike)
                if now - epoch > WINDOW_SECONDS:
                    continue
                key = (
                    round(lat, DEDUPE_ROUND),
                    round(lon, DEDUPE_ROUND),
                    round(epoch / DEDUPE_TIME_SECONDS),
                )
                if key in seen:
                    continue
                seen.add(key)
                strikes[key] = {
                    "t": round(epoch, 1),
                    "lat": round(lat, DEDUPE_ROUND),
                    "lon": round(lon, DEDUPE_ROUND),
                    "src": int(strike.get("src") or 0),
                }
            # Stop early once the backlog has drained to recent strikes.
            if strikes and now - max(item["t"] for item in strikes.values()) < 30:
                break
            time.sleep(LIGHTNING_POLL_DELAY_SECONDS)

        meta["polls"] = polls

    ordered = list(strikes.values())
    ordered.sort(key=lambda item: item["t"], reverse=True)
    if len(ordered) > MAX_STRIKES:
        ordered = ordered[:MAX_STRIKES]

    payload = build_payload(ordered, meta)
    STRIKES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[+] lightning_strikes.json: {len(ordered)} strikes in window")

    status = {
        "source": "blitzortung",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "generated_at": payload["generated_at"],
        "count": len(ordered),
        "bbox": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "window_seconds": WINDOW_SECONDS,
        "duration_seconds": round(time.time() - started, 2),
        "error": error or "",
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"[+] lightning_status.json: count={len(ordered)} error={error or 'none'}")

    return 0 if not error else 1


if __name__ == "__main__":
    sys.exit(main())

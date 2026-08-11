#!/usr/bin/env python3
"""SCE (Southern California Edison) power outage scraper.

Queries the public ArcGIS feature service behind SCE's outage map
(``sce-outage-ags.esriemcs.com``), keeps the outages for the configured
counties, and writes ``sce_outages.json`` with local details for every outage
plus the link used to open it on Edison's own outage map.

The service layer 0 ("Outage") mirrors what the sce.com outage map shows: each
feature has an incident id, OAN, city, zip, district, affected-customer count,
start time, estimated restore time (ERT), cause and crew status strings, and a
point geometry (lon/lat with ``outSR=4326``).
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

OUTAGE_SERVICE_URL = os.environ.get(
    "SBCO_OUTAGE_SERVICE_URL",
    "https://sce-outage-ags.esriemcs.com/arcgis/rest/services/43/outage/MapServer/0/query",
)
OUTAGE_MAP_URL = os.environ.get(
    "SBCO_OUTAGE_MAP_URL",
    "https://www.sce.com/outages-safety/outage-center/check-outage-status",
)
OUTAGE_COUNTIES = [
    county.strip().upper()
    for county in os.environ.get("SBCO_OUTAGE_COUNTIES", "SAN BERNARDINO").split(",")
    if county.strip()
]
OUTAGE_STATUS = os.environ.get("SBCO_OUTAGE_STATUS", "ACTIVE").strip().upper()
OUTAGE_MAX_RECORDS = int(os.environ.get("SBCO_OUTAGE_MAX_RECORDS", "2000"))
OUTAGE_TIMEOUT_SECONDS = int(os.environ.get("SBCO_OUTAGE_TIMEOUT_SECONDS", "30"))
OUTAGE_USER_AGENT = "Mozilla/5.0 (compatible; sbco-calllog-scraper/1.0)"

OUTAGES_FILE = BASE_DIR / "sce_outages.json"
STATUS_FILE = BASE_DIR / "sce_outage_status.json"

# County names are stored uppercase in the service; values like "SAN BERNARDINO".
FIELD_MAP = {
    "IncidentId": "incident_id",
    "OanNo": "oan",
    "CityName": "city",
    "CountyName": "county",
    "ZipcodeName": "zip",
    "DistrictNo": "district",
    "NoOfAffectedCust_Inci": "customers",
    "OutageStartDateTime": "start_epoch_ms",
    "LastChngDateTime": "last_change",
    "ERT": "ert",
    "MemoCauseCdDesc": "cause",
    "CrewStatusCdDesc": "crew_status",
    "ResultCdDesc": "result",
    "IncidentType": "incident_type",
    "JobStatus": "job_status",
    "Status": "status",
    "OutageType": "outage_type",
}


def build_where() -> str:
    clauses = [f"CountyName IN ({','.join(f'{c!r}' for c in OUTAGE_COUNTIES)})"]
    if OUTAGE_STATUS:
        clauses.append(f"Status = {OUTAGE_STATUS!r}")
    return " AND ".join(clauses)


def fetch_outages() -> list[dict]:
    params = {
        "where": build_where(),
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
        "resultRecordCount": str(OUTAGE_MAX_RECORDS),
        "orderByFields": "NoOfAffectedCust_Inci DESC",
    }
    response = requests.get(
        OUTAGE_SERVICE_URL,
        params=params,
        headers={"User-Agent": OUTAGE_USER_AGENT},
        timeout=OUTAGE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    outages: list[dict] = []
    for feature in data.get("features") or []:
        attributes = feature.get("attributes") or {}
        geometry = feature.get("geometry") or {}
        lon = geometry.get("x")
        lat = geometry.get("y")
        if lon is None or lat is None:
            continue

        record = {"lat": round(float(lat), 5), "lon": round(float(lon), 5)}
        for field, key in FIELD_MAP.items():
            value = attributes.get(field)
            if value is None or value == "":
                continue
            if key == "customers":
                value = int(value)
            elif key == "start_epoch_ms":
                value = int(value)
            record[key] = value

        if record.get("start_epoch_ms") is not None:
            record["start_iso"] = datetime.fromtimestamp(
                record["start_epoch_ms"] / 1000.0, tz=timezone.utc
            ).isoformat()

        record["sce_url"] = OUTAGE_MAP_URL
        outages.append(record)

    return outages


def main() -> int:
    started = time.time()
    error = ""
    try:
        outages = fetch_outages()
    except Exception as exc:  # noqa: BLE001 - report any failure into the status file
        outages = []
        error = f"{type(exc).__name__}: {exc}"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "sce-outage-ags.esriemcs.com (SCE outage map ArcGIS service)",
        "counties": OUTAGE_COUNTIES,
        "status": OUTAGE_STATUS,
        "map_url": OUTAGE_MAP_URL,
        "count": len(outages),
        "outages": outages,
    }
    OUTAGES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[+] sce_outages.json: {len(outages)} outages")

    status = {
        "source": "sce-outage-ags.esriemcs.com",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "generated_at": payload["generated_at"],
        "count": len(outages),
        "counties": OUTAGE_COUNTIES,
        "status": OUTAGE_STATUS,
        "duration_seconds": round(time.time() - started, 2),
        "error": error,
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"[+] sce_outage_status.json: count={len(outages)} error={error or 'none'}")

    return 0 if not error else 1


if __name__ == "__main__":
    sys.exit(main())

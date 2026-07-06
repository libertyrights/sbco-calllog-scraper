#!/usr/bin/env python3
import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from ftplib import FTP
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from arrest_index_builder import build_arrest_index
except Exception:
    build_arrest_index = None

try:
    from chp_scraper import scrape_chp_incidents
except Exception:
    scrape_chp_incidents = None

try:
    from pulsepoint_scraper import scrape_pulsepoint_incidents
except Exception:
    scrape_pulsepoint_incidents = None

# ================= CONFIG =================

FRAME_URL = "https://mediasummary.shr.sbcounty.gov/"
DROPDOWN_EVENT_TARGET = "ctl00$MainContent$ddCity"
GRIDVIEW_ID = "GridView1"

BARSTOW_CODE = "BA"
PRIMARY_AGENCY_CODE = os.environ.get("SBCO_PRIMARY_AGENCY_CODE", "SBSO").strip() or "SBSO"
PULSEPOINT_AGENCY_CODE = (os.environ.get("SBCO_PULSEPOINT_AGENCY_CODE", "SBCFIRE").strip() or "SBCFIRE").upper()
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"

BASE_DIR = os.environ.get("SBCO_BASE_DIR", "/home/mark/python")
STATE_DIR = os.path.join(BASE_DIR, ".state")
LOCAL_HTML = os.path.join(BASE_DIR, "input.html")
LOCAL_CSV = os.path.join(BASE_DIR, "calllog.csv")
FORMATTED_CSV = os.path.join(BASE_DIR, "calllog_formatted.csv")
SERVER_COPY = os.path.join(BASE_DIR, "server_calllog.csv")
CALLLOG_JSON = os.path.join(BASE_DIR, "calllog.json")
CALLLOG_ARREST_INDEX_JSON = os.path.join(BASE_DIR, "calllog_arrest_index.json")
DEATH_INDEX_CSV = os.path.join(BASE_DIR, "death_index.csv")
ARREST_LOG_JSON = os.path.join(BASE_DIR, "all_records.json")
CALLLOG_UPLOAD_META_JSON = os.path.join(BASE_DIR, "calllog_upload_meta.json")
SECRET_CONFIG_PATH = os.environ.get("SBCO_SECRET_CONFIG", os.path.join(BASE_DIR, "secrets.local.json"))

BACKUP_DIR = os.path.join(BASE_DIR, "snapshots", "calllog")
MANIFEST_PATH = os.path.join(BACKUP_DIR, "manifest.json")

RELEASES_CSV = os.path.join(BASE_DIR, "releases.csv")
LEGACY_RELEASES_CSV = os.path.join(BASE_DIR, "daily_release_list.csv")
DAILY_RELEASE_LOG = os.path.join(BASE_DIR, "daily_release_list.log")
RELEASE_LOG_URL = "https://jimsnetil.shr.sbcounty.gov/bookingsearch.aspx/GetReleaseLog"
DEATH_INDEX_PAGE_URL = "https://xcore.sbcounty.gov/sheriff/SheriffCMS/DeathRegister"
DEATH_INDEX_GRID_URL = DEATH_INDEX_PAGE_URL + "/DataGrid"
DEATH_INDEX_PAGE_SIZE = int(os.environ.get("SBCO_DEATH_INDEX_PAGE_SIZE", "100"))
DEATH_INDEX_DELAY_SECONDS = float(os.environ.get("SBCO_DEATH_INDEX_DELAY_SECONDS", "0.4"))
DEATH_INDEX_REFRESH_TIMEOUT_SECONDS = int(os.environ.get("SBCO_DEATH_INDEX_REFRESH_TIMEOUT_SECONDS", "480"))
ARREST_LOG_SCRIPT = os.path.join(os.path.dirname(__file__), "scrape-sbco-arr-log.py")
ARREST_LOG_REFRESH_TIMEOUT_SECONDS = int(os.environ.get("SBCO_ARREST_LOG_REFRESH_TIMEOUT_SECONDS", "420"))
ARREST_LOG_REQUEST_DELAY_SECONDS = float(os.environ.get("SBCO_ARREST_LOG_REQUEST_DELAY_SECONDS", "2.0"))
ARREST_LOG_MAX_PAGES = int(os.environ.get("SBCO_ARREST_LOG_MAX_PAGES", "3"))
DAILY_REMOTE_FILE_FRESHNESS_HOURS = float(os.environ.get("SBCO_DAILY_REMOTE_FILE_FRESHNESS_HOURS", "20"))
PUBLIC_FILE_TIMEOUT_SECONDS = int(os.environ.get("SBCO_PUBLIC_FILE_TIMEOUT_SECONDS", "60"))
PHASE_WARNING_SECONDS = float(os.environ.get("SBCO_PHASE_WARNING_SECONDS", "60"))
GITHUB_JOB_SOFT_TIMEOUT_SECONDS = int(os.environ.get("SBCO_GITHUB_JOB_SOFT_TIMEOUT_SECONDS", "1200"))
GITHUB_DAILY_REFRESH_HEADROOM_SECONDS = int(os.environ.get("SBCO_GITHUB_DAILY_REFRESH_HEADROOM_SECONDS", "300"))

KEEP_RECENT_HOURS = 48
KEEP_DAILY_DAYS = 30
KEEP_WEEKLY_WEEKS = 26

LOG_FILE = os.path.join(BASE_DIR, "scraper_master.log")

FTP_REMOTE_DIR = os.environ.get("SBCO_FTP_REMOTE_DIR", "/domains/upnexx.xyz/public_html/osint")
FTP_TIMEOUT_SECONDS = int(os.environ.get("SBCO_FTP_TIMEOUT_SECONDS", "60"))
REMOTE_PUBLISH_LOCK_NAME = os.environ.get("SBCO_REMOTE_PUBLISH_LOCK_NAME", ".publish.lock.json")
REMOTE_PUBLISH_LOCK_STALE_SECONDS = int(os.environ.get("SBCO_REMOTE_PUBLISH_LOCK_STALE_SECONDS", "3600"))
REMOTE_DB_REBUILD_URL = "http://upnexx.xyz/osint/build_calllog_db.php"
REMOTE_DB_REBUILD_TIMEOUT_SECONDS = 180
SERVER_CALLLOG_URL = os.environ.get("SBCO_SERVER_CALLLOG_URL", "").strip()
HTTP_UPLOAD_URL = os.environ.get("SBCO_HTTP_UPLOAD_URL", "").strip()
HTTP_UPLOAD_TIMEOUT_SECONDS = int(os.environ.get("SBCO_HTTP_UPLOAD_TIMEOUT_SECONDS", "180"))
HTTP_UPLOAD_SOURCE = os.environ.get("SBCO_HTTP_UPLOAD_SOURCE", "scraper_run")
UPLOAD_TRACE_SOURCE = (
    os.environ.get("SBCO_UPLOAD_TRACE_SOURCE")
    or ("github" if (os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true") else "pc")
).strip() or "pc"

CSV_FIELDS = [
    "date/time",
    "agency",
    "station",
    "call number",
    "report number",
    "call type",
    "disposition",
    "location",
    "revision_scraped_at",
    "extra_json",
]

FORMATTED_FIELDS = [
    "date/time",
    "agency",
    "station",
    "division",
    "call number",
    "report number",
    "call type",
    "disposition",
    "streetAddr",
    "city",
]

RELEASE_FIELDS = ["Name", "Sex", "Age", "Height", "Weight", "Release Date"]

FIELDS_TO_COMPARE = ["call type", "disposition", "location", "extra_json"]
DISPLAY_DATE_FORMAT = "%m/%d/%Y %I:%M:%S %p"
DATE_FORMATS = [
    DISPLAY_DATE_FORMAT,
    "%m/%d/%y %I:%M:%S %p",
]


def load_secret_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


SECRET_CONFIG = load_secret_config(SECRET_CONFIG_PATH)


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def secret_value(env_name, config_key, default=""):
    value = os.environ.get(env_name)
    if value not in [None, ""]:
        return value
    value = SECRET_CONFIG.get(config_key, default)
    return value if value not in [None, ""] else default


FTP_HOST = secret_value("SBCO_FTP_HOST", "ftp_host", "s2.serv00.com")
FTP_USER = secret_value("SBCO_FTP_USER", "ftp_user", "")
FTP_PASS = secret_value("SBCO_FTP_PASS", "ftp_pass", "")
REMOTE_DB_REBUILD_TOKEN = secret_value("SBCO_REMOTE_DB_REBUILD_TOKEN", "remote_db_rebuild_token", "")
HTTP_UPLOAD_SECRET = secret_value("SBCO_HTTP_UPLOAD_SECRET", "http_upload_secret", "")
REMOTE_BACKED_DAILY_FILES = env_flag(
    "SBCO_REMOTE_BACKED_DAILY_FILES",
    default=(os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"),
)
ENABLE_DAILY_RELEASES = env_flag("SBCO_ENABLE_DAILY_RELEASES", default=True)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write("[SCRAPER] {} {}\n".format(ts, msg))


def log_daily_release(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DAILY_RELEASE_LOG, "a") as f:
        f.write("[RELEASES] {} {}\n".format(ts, msg))


def log_phase_duration(name, started_monotonic):
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    prefix = "WARNING: " if elapsed >= PHASE_WARNING_SECONDS else ""
    log("{}{} completed in {:.1f}s".format(prefix, name, elapsed))
    return elapsed


def github_job_seconds_remaining(run_started_monotonic):
    if not IS_GITHUB_ACTIONS or run_started_monotonic is None:
        return None
    elapsed = max(0.0, time.monotonic() - run_started_monotonic)
    return max(0.0, float(GITHUB_JOB_SOFT_TIMEOUT_SECONDS) - elapsed)


def should_skip_github_daily_refresh(run_started_monotonic):
    remaining = github_job_seconds_remaining(run_started_monotonic)
    if remaining is None:
        return False
    return remaining <= float(GITHUB_DAILY_REFRESH_HEADROOM_SECONDS)


def enforce_deadline(name, deadline_monotonic):
    if deadline_monotonic is None:
        return
    if time.monotonic() >= deadline_monotonic:
        raise TimeoutError("{} exceeded its time budget".format(name))


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_text():
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_text(text):
    value = (text or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def revision_scrape_timestamp():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def today_str():
    return datetime.now().date().isoformat()


def parse_generated_timestamp(text):
    value = (text or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def public_base_url():
    if not SERVER_CALLLOG_URL:
        return ""
    return SERVER_CALLLOG_URL.rsplit("/", 1)[0].rstrip("/")


def public_file_url(remote_name):
    base_url = public_base_url()
    if not base_url:
        return ""
    return "{}/{}".format(base_url, remote_name)


def parse_public_file_timestamp(remote_name, content, headers):
    if remote_name.lower().endswith(".json"):
        try:
            payload = json.loads(content.decode("utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            generated_at = parse_generated_timestamp(payload.get("generated_at"))
            if generated_at:
                return generated_at

    last_modified = headers.get("Last-Modified", "")
    if last_modified:
        try:
            parsed = parsedate_to_datetime(last_modified)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def public_file_usable(remote_name, content):
    text = content.decode("utf-8", errors="ignore")
    if remote_name == "death_index.csv":
        return "caseNumberDisplay" in text and "," in text
    if remote_name.lower().endswith(".json"):
        try:
            payload = json.loads(text)
        except Exception:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("records"), list)
    return bool(text.strip())


def write_bytes(path, payload):
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(payload)
    os.replace(tmp_path, path)


def fetch_public_file(remote_name):
    url = public_file_url(remote_name)
    if not url:
        return None
    response = requests.get(url, timeout=PUBLIC_FILE_TIMEOUT_SECONDS)
    response.raise_for_status()
    content = response.content
    return {
        "remote_name": remote_name,
        "url": url,
        "content": content,
        "usable": public_file_usable(remote_name, content),
        "timestamp": parse_public_file_timestamp(remote_name, content, response.headers),
    }


def remote_file_is_fresh(timestamp, max_age_hours):
    if not timestamp:
        return False
    age_seconds = (utc_now() - timestamp.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= max(1.0, float(max_age_hours)) * 3600.0


def ensure_dirs():
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)


def should_run_daily(name):
    path = os.path.join(STATE_DIR, "last_{}.txt".format(name))
    if not os.path.exists(path):
        return True
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip() != today_str()


def mark_daily_done(name):
    path = os.path.join(STATE_DIR, "last_{}.txt".format(name))
    with open(path, "w", encoding="utf-8") as f:
        f.write(today_str())


def clean_location_field(value):
    if value is None:
        return ""
    value = re.sub(r"(?<!\*)\*\*(?!\*)", "00", value)
    if re.fullmatch(r"\*,\*", value):
        value = "Not Provided, NA"
    return value.strip()


def looks_like_agency_code(value):
    text = (value or "").strip().upper()
    return text in {"SBSO", "CHP", "CALFIRE", "CAL FIRE", "SBCFIRE", "BPD", "SBPD", "BLM", "USFS", "BNSF"}


def normalize_agency_and_station(record):
    raw_agency = (record.get("agency", "") or "").strip()
    raw_station = (record.get("station", "") or "").strip()

    if raw_station:
        station = raw_station
        agency = raw_agency or PRIMARY_AGENCY_CODE
        if not raw_agency and looks_like_agency_code(station):
            agency = station
            station = ""
        return agency.upper(), station

    if looks_like_agency_code(raw_agency):
        return raw_agency.upper(), ""

    station = raw_agency
    agency = PRIMARY_AGENCY_CODE if station else raw_agency.upper()
    return agency.upper(), station


def normalize_display_station(agency, station):
    agency_upper = (agency or "").strip().upper()
    station_text = (station or "").strip()
    if not station_text:
        return ""
    if agency_upper == PRIMARY_AGENCY_CODE.upper() and station_text.upper() in {BARSTOW_CODE.upper(), "BARSTOW"}:
        return "Barstow"
    return station_text


def station_is_barstow(agency, station):
    agency_upper = (agency or "").strip().upper()
    station_upper = (station or "").strip().upper()
    if agency_upper != PRIMARY_AGENCY_CODE.upper():
        return False
    return station_upper in {BARSTOW_CODE.upper(), "BARSTOW"}


def normalize_display_datetime(value):
    text = (value or "").strip()
    if not text:
        return ""
    parsed = parse_datetime(text)
    if parsed is None:
        return text
    return parsed.strftime(DISPLAY_DATE_FORMAT)


def normalize_record(record):
    normalized = {}
    for field in CSV_FIELDS:
        normalized[field] = (record.get(field, "") or "").strip()
    normalized["agency"], normalized["station"] = normalize_agency_and_station(record)
    normalized["date/time"] = normalize_display_datetime(normalized.get("date/time", ""))
    normalized["station"] = normalize_display_station(normalized["agency"], normalized["station"])
    normalized["location"] = clean_location_field(normalized.get("location", ""))
    return normalized


def row_is_in_scope(record):
    normalized = normalize_record(record)
    agency = (normalized.get("agency", "") or "").strip().upper()
    if agency != PRIMARY_AGENCY_CODE.upper():
        return True
    if not (normalized.get("station", "") or "").strip():
        return True
    return station_is_barstow(normalized.get("agency", ""), normalized.get("station", ""))


def filter_scoped_rows(rows):
    return [normalize_record(row) for row in rows if row_is_in_scope(row)]


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        content = f.read().replace("\x00", "")
    rows = []
    for row in csv.DictReader(io.StringIO(content)):
        normalized = normalize_record(row)
        if any(normalized.values()):
            rows.append(normalized)
    return rows


def write_csv(path, rows):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_record(row))
    os.replace(tmp_path, path)


def union_merge(rows_a, rows_b):
    out = {}
    for row in rows_a + rows_b:
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if call_number:
            out[call_number] = normalized
    return filter_scoped_rows(out.values())


def file_size_or_zero(path):
    try:
        return max(0, int(os.path.getsize(path)))
    except Exception:
        return 0


def load_last_uploaded_calllog_size():
    payload = load_secret_config(CALLLOG_UPLOAD_META_JSON)
    try:
        return max(0, int(payload.get("calllog_bytes", 0) or 0))
    except Exception:
        return 0


def call_base(call_number):
    return call_number.split(".")[0] if "." in call_number else call_number


def revision_number(call_number):
    if "." not in call_number:
        return 0
    suffix = call_number.rsplit(".", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def parse_extra_json_value(value):
    if isinstance(value, dict):
        return dict(value)
    text = (value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def dump_extra_json_value(payload):
    if not isinstance(payload, dict) or not payload:
        return ""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def latest_rows_by_base(rows):
    latest = {}
    for row in rows:
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if not call_number:
            continue
        base = call_base(call_number)
        existing = latest.get(base)
        if existing is None or revision_number(call_number) > revision_number(existing.get("call number", "")):
            latest[base] = normalized
    return latest


def row_matches_feed(record, agency_code, extra_kind):
    normalized = normalize_record(record)
    if (normalized.get("agency", "") or "").strip().upper() != (agency_code or "").strip().upper():
        return False
    extra_payload = parse_extra_json_value(normalized.get("extra_json", ""))
    if extra_payload.get("kind") == extra_kind:
        return True
    call_number = (normalized.get("call number", "") or "").strip().upper()
    return bool(call_number) and call_number.startswith("{}-".format((agency_code or "").strip().upper()))


def build_feed_closure_candidate(latest_row, closed_disposition="CLS", reason="missing_from_source_feed"):
    closure_row = normalize_record(latest_row)
    closure_row["call number"] = call_base(closure_row.get("call number", ""))
    closure_row["disposition"] = closed_disposition
    closure_row["revision_scraped_at"] = ""

    extra_payload = parse_extra_json_value(closure_row.get("extra_json", ""))
    if extra_payload:
        extra_payload["is_active"] = False
        extra_payload["is_closed"] = True
        extra_payload["closure_reason"] = reason
        if not extra_payload.get("closed_at"):
            extra_payload["closed_at"] = datetime.now().astimezone().strftime("%m/%d/%Y %I:%M:%S %p")
        closure_row["extra_json"] = dump_extra_json_value(extra_payload)

    return closure_row


def close_missing_feed_rows(existing_rows, current_rows, agency_code, extra_kind, closed_disposition="CLS"):
    current_bases = set()
    for row in current_rows:
        call_number = normalize_record(row).get("call number", "")
        if call_number:
            current_bases.add(call_base(call_number))

    closure_candidates = []
    for base_call_number, latest_row in latest_rows_by_base(existing_rows).items():
        if not row_matches_feed(latest_row, agency_code, extra_kind):
            continue
        if (latest_row.get("disposition", "") or "").strip().upper() != "ACT":
            continue
        if base_call_number in current_bases:
            continue
        closure_candidates.append(
            build_feed_closure_candidate(latest_row, closed_disposition=closed_disposition)
        )

    if not closure_candidates:
        return filter_scoped_rows(existing_rows), 0

    return merge_revisions(existing_rows, closure_candidates), len(closure_candidates)


def parse_datetime(dt_str):
    if not dt_str:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_location(location):
    clean_loc = (location or "").strip()
    if clean_loc in ["*.*", "*,*", "* , *", "*", ""]:
        return "Location Not Provided", ""

    if "," in clean_loc:
        street_addr, city_part = clean_loc.rsplit(",", 1)
        city = city_part.strip()
        city = city[:3] if len(city) >= 3 else city
        if city == "*":
            city = ""
        return street_addr.strip(), city

    return clean_loc, ""


def extract_division(call_number):
    if call_number and len(call_number) >= 2:
        return call_number[:2]
    return ""


def write_formatted_csv(rows, output_path):
    formatted_rows = []

    for row in rows:
        dt_str = row.get("date/time", "")
        parsed_dt = parse_datetime(dt_str)
        if not parsed_dt:
            continue

        street_addr, city = parse_location(row.get("location", ""))
        formatted_rows.append(
            {
                "_dt": parsed_dt,
                "date/time": dt_str,
                "agency": row.get("agency", ""),
                "station": row.get("station", ""),
                "division": extract_division(row.get("call number", "")),
                "call number": row.get("call number", ""),
                "report number": row.get("report number", ""),
                "call type": row.get("call type", ""),
                "disposition": row.get("disposition", ""),
                "streetAddr": street_addr,
                "city": city,
            }
        )

    formatted_rows.sort(key=lambda row: row["_dt"], reverse=True)

    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FORMATTED_FIELDS)
        writer.writeheader()
        for row in formatted_rows:
            row.pop("_dt", None)
            writer.writerow(row)
    os.replace(tmp_path, output_path)
    return len(formatted_rows)


def write_text(path, text):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_path, path)


def refresh_death_index_csv(output_path):
    started_monotonic = time.monotonic()
    deadline_monotonic = started_monotonic + max(1, DEATH_INDEX_REFRESH_TIMEOUT_SECONDS)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://xcore.sbcounty.gov",
            "Referer": DEATH_INDEX_PAGE_URL,
        }
    )
    enforce_deadline("death index refresh", deadline_monotonic)
    session.get(DEATH_INDEX_PAGE_URL, timeout=60).raise_for_status()

    fieldnames = None
    total_rows = 0
    current_year = datetime.now().year
    page_count = 0
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = None
        for year in range(current_year, 2000, -1):
            enforce_deadline("death index refresh", deadline_monotonic)
            start = 0
            draw = 1
            while True:
                enforce_deadline("death index refresh", deadline_monotonic)
                response = session.post(
                    DEATH_INDEX_GRID_URL,
                    data={
                        "draw": str(draw),
                        "start": str(start),
                        "length": str(DEATH_INDEX_PAGE_SIZE),
                        "search[value]": "",
                        "search[regex]": "false",
                        "caseYear": str(year),
                    },
                    timeout=60,
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data") if isinstance(payload, dict) else []
                if not rows:
                    break
                page_count += 1

                if fieldnames is None:
                    fieldnames = list(rows[0].keys())
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()

                for row in rows:
                    writer.writerow(row)
                    total_rows += 1

                start += len(rows)
                draw += 1
                records_total = int(payload.get("recordsFiltered") or payload.get("recordsTotal") or 0)
                if start >= records_total:
                    break
                time.sleep(DEATH_INDEX_DELAY_SECONDS)

    if total_rows <= 0:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise RuntimeError("death index refresh returned no rows")

    os.replace(tmp_path, output_path)
    elapsed = log_phase_duration("death_index.csv refresh", started_monotonic)
    log("death_index.csv refreshed locally ({} rows across {} pages in {:.1f}s)".format(total_rows, page_count, elapsed))


def refresh_arrest_log_json(output_path):
    started_monotonic = time.monotonic()
    command = [
        sys.executable,
        ARREST_LOG_SCRIPT,
        "--output-json",
        output_path,
        "--request-delay",
        str(ARREST_LOG_REQUEST_DELAY_SECONDS),
        "--max-pages",
        str(ARREST_LOG_MAX_PAGES),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=ARREST_LOG_REFRESH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = log_phase_duration("all_records.json refresh", started_monotonic)
        raise TimeoutError(
            "arrest log refresh exceeded {}s after {:.1f}s".format(ARREST_LOG_REFRESH_TIMEOUT_SECONDS, elapsed)
        ) from exc
    stdout = " ".join((result.stdout or "").split())
    stderr = " ".join((result.stderr or "").split())
    if stdout:
        log("Arrest log refresh stdout: {}".format(stdout[:1000]))
    if stderr:
        log("Arrest log refresh stderr: {}".format(stderr[:1000]))
    if not os.path.exists(output_path):
        raise RuntimeError("arrest log refresh did not create {}".format(output_path))
    elapsed = log_phase_duration("all_records.json refresh", started_monotonic)
    log("all_records.json refreshed locally in {:.1f}s".format(elapsed))


def ensure_remote_backed_daily_file(remote_name, local_path, refresh_callback, run_started_monotonic=None):
    downloaded_fallback = False

    if REMOTE_BACKED_DAILY_FILES:
        try:
            remote_file = fetch_public_file(remote_name)
        except Exception as e:
            log("WARNING: public {} fetch failed: {}".format(remote_name, e))
            remote_file = None

        if remote_file and remote_file.get("usable"):
            write_bytes(local_path, remote_file["content"])
            downloaded_fallback = True
            if remote_file_is_fresh(remote_file.get("timestamp"), DAILY_REMOTE_FILE_FRESHNESS_HOURS):
                log(
                    "Reused fresh public {} from {}".format(
                        remote_name,
                        remote_file.get("url", ""),
                    )
                )
                return []
            log("Public {} is stale; refreshing locally".format(remote_name))
        elif remote_file:
            log("Public {} is present but unusable; refreshing locally".format(remote_name))

    if downloaded_fallback and should_skip_github_daily_refresh(run_started_monotonic):
        remaining = github_job_seconds_remaining(run_started_monotonic)
        log(
            "WARNING: skipping local refresh of {} on GitHub to preserve {:.0f}s of job headroom".format(
                remote_name,
                remaining or 0,
            )
        )
        return []

    try:
        phase_started = time.monotonic()
        refresh_callback(local_path)
        log_phase_duration("{} local refresh".format(remote_name), phase_started)
        return [(remote_name, local_path)]
    except Exception as e:
        if downloaded_fallback or os.path.exists(local_path):
            log("WARNING: {} refresh failed; keeping fallback copy: {}".format(remote_name, e))
            return []
        log("WARNING: {} refresh failed with no fallback: {}".format(remote_name, e))
        return []


def rebuild_calllog_arrest_index():
    if build_arrest_index is None:
        log("WARNING: arrest_index_builder unavailable; skipping calllog_arrest_index rebuild")
        return
    try:
        build_arrest_index(Path(LOCAL_CSV), Path(CALLLOG_ARREST_INDEX_JSON))
        log("Local calllog_arrest_index.json rebuilt")
    except Exception as e:
        if os.path.exists(CALLLOG_ARREST_INDEX_JSON):
            try:
                os.remove(CALLLOG_ARREST_INDEX_JSON)
            except Exception:
                pass
        log("WARNING: calllog_arrest_index rebuild failed: {}".format(e))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return []
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_manifest(entries):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def entry_timestamp(entry):
    return datetime.strptime(entry["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def cleanup_empty_dirs():
    if not os.path.exists(BACKUP_DIR):
        return
    for root, dirs, files in os.walk(BACKUP_DIR, topdown=False):
        if root == BACKUP_DIR:
            continue
        if not dirs and not files:
            os.rmdir(root)


def prune_backups(entries):
    now = utc_now()
    ordered = sorted(entries, key=entry_timestamp, reverse=True)

    keep_paths = set()
    daily_buckets = set()
    weekly_buckets = set()

    for entry in ordered:
        ts = entry_timestamp(entry)
        age = now - ts
        rel_path = entry["path"]

        if age <= timedelta(hours=KEEP_RECENT_HOURS):
            keep_paths.add(rel_path)
            continue

        if age <= timedelta(days=KEEP_DAILY_DAYS):
            bucket = ts.date().isoformat()
            if bucket not in daily_buckets:
                daily_buckets.add(bucket)
                keep_paths.add(rel_path)
            continue

        if age <= timedelta(weeks=KEEP_WEEKLY_WEEKS):
            iso = ts.isocalendar()
            bucket = "{}-W{:02d}".format(iso[0], iso[1])
            if bucket not in weekly_buckets:
                weekly_buckets.add(bucket)
                keep_paths.add(rel_path)

    if ordered:
        keep_paths.add(ordered[-1]["path"])

    kept_entries = []
    for entry in entries:
        file_path = os.path.join(BACKUP_DIR, *entry["path"].split("/"))
        if entry["path"] in keep_paths and os.path.exists(file_path):
            kept_entries.append(entry)
            continue
        if os.path.exists(file_path):
            os.unlink(file_path)

    cleanup_empty_dirs()
    return sorted(kept_entries, key=entry_timestamp)


def store_backup(data):
    digest = sha256_bytes(data)
    entries = load_manifest()

    if entries and entries[-1].get("sha256") == digest:
        return None

    timestamp = utc_now()
    rel_dir = os.path.join(timestamp.strftime("%Y"), timestamp.strftime("%m"))
    filename = "calllog-{}-{}.csv.gz".format(timestamp.strftime("%Y%m%dT%H%M%SZ"), digest[:8])
    backup_path = os.path.join(BACKUP_DIR, rel_dir, filename)
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)

    with gzip.open(backup_path, "wb") as f:
        f.write(data)

    entries.append(
        {
            "created_at": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "path": os.path.join(rel_dir, filename).replace("\\", "/"),
            "sha256": digest,
            "bytes": len(data),
        }
    )
    entries = prune_backups(entries)
    save_manifest(entries)
    return backup_path


def ftp_connect():
    if not FTP_HOST or not FTP_USER or not FTP_PASS:
        raise RuntimeError("FTP credentials are not configured")
    ftp = FTP()
    ftp.connect(FTP_HOST, timeout=FTP_TIMEOUT_SECONDS)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.cwd(FTP_REMOTE_DIR)
    return ftp


def ftp_download_bytes(ftp, remote_name):
    buffer = io.BytesIO()
    try:
        ftp.retrbinary("RETR {}".format(remote_name), buffer.write)
    except Exception as e:
        if "550" in str(e):
            return None
        raise
    return buffer.getvalue()


def ftp_upload(ftp, local_path, remote_name):
    with open(local_path, "rb") as f:
        ftp.storbinary("STOR {}".format(remote_name), f)


def ftp_upload_bytes(ftp, payload, remote_name):
    ftp.storbinary("STOR {}".format(remote_name), io.BytesIO(payload))


def ftp_delete_if_exists(ftp, remote_name):
    try:
        ftp.delete(remote_name)
        return True
    except Exception as e:
        if "550" in str(e):
            return False
        raise


def build_publish_run_id():
    explicit = (os.environ.get("SBCO_RUNNER_ID") or "").strip()
    if explicit:
        return explicit
    host = socket.gethostname() or "unknown-host"
    return "{}-pid{}-{}".format(host, os.getpid(), utc_now().strftime("%Y%m%dT%H%M%SZ"))


def build_publish_owner():
    github_repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    github_run_id = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    parts = [socket.gethostname() or "unknown-host", "pid{}".format(os.getpid())]
    if github_repo:
        parts.append(github_repo)
    if github_run_id:
        parts.append("run{}".format(github_run_id))
    return " ".join(part for part in parts if part)


def write_upload_trace_file(path, run_id, transport):
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": UPLOAD_TRACE_SOURCE,
        "transport": transport,
        "run_id": run_id,
        "publisher": build_publish_owner(),
        "host": socket.gethostname() or "unknown-host",
        "calllog_bytes": file_size_or_zero(LOCAL_CSV),
    }
    write_text(path, json.dumps(payload, indent=2, sort_keys=True))
    return payload


def build_lock_payload(run_id):
    return {
        "run_id": run_id,
        "owner": build_publish_owner(),
        "acquired_at": utc_now_text(),
        "stale_after_seconds": REMOTE_PUBLISH_LOCK_STALE_SECONDS,
    }


def lock_is_stale(payload):
    if not isinstance(payload, dict):
        return True
    acquired_at = parse_utc_text(payload.get("acquired_at"))
    if not acquired_at:
        return True
    stale_after_seconds = payload.get("stale_after_seconds", REMOTE_PUBLISH_LOCK_STALE_SECONDS)
    try:
        stale_after_seconds = int(stale_after_seconds)
    except Exception:
        stale_after_seconds = REMOTE_PUBLISH_LOCK_STALE_SECONDS
    age_seconds = (utc_now() - acquired_at).total_seconds()
    return age_seconds >= max(stale_after_seconds, 1)


def lock_owner_text(payload):
    if not isinstance(payload, dict):
        return "unknown publisher"
    return payload.get("owner") or payload.get("run_id") or "unknown publisher"


def ftp_read_json(ftp, remote_name):
    payload = ftp_download_bytes(ftp, remote_name)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def ftp_acquire_publish_lock(ftp):
    run_id = build_publish_run_id()
    lock_name = REMOTE_PUBLISH_LOCK_NAME
    temp_lock_name = "{}.{}.tmp".format(lock_name, re.sub(r"[^A-Za-z0-9._-]", "-", run_id))

    existing = ftp_read_json(ftp, lock_name)
    if existing and not lock_is_stale(existing):
        raise RuntimeError("Remote publish lock is active ({})".format(lock_owner_text(existing)))
    if existing and lock_is_stale(existing):
        log("Remote publish lock is stale; removing {}".format(lock_name))
        ftp_delete_if_exists(ftp, lock_name)

    payload = build_lock_payload(run_id)
    ftp_upload_bytes(ftp, json.dumps(payload, sort_keys=True).encode("utf-8"), temp_lock_name)
    try:
        ftp.rename(temp_lock_name, lock_name)
    except Exception as e:
        ftp_delete_if_exists(ftp, temp_lock_name)
        current = ftp_read_json(ftp, lock_name)
        if current and not lock_is_stale(current):
            raise RuntimeError("Remote publish lock was claimed by {}".format(lock_owner_text(current)))
        raise RuntimeError("Remote publish lock acquisition failed: {}".format(e))

    confirmed = ftp_read_json(ftp, lock_name)
    if not confirmed or confirmed.get("run_id") != run_id:
        raise RuntimeError("Remote publish lock verification failed")
    log("Acquired remote publish lock {}".format(run_id))
    return {
        "run_id": run_id,
        "lock_name": lock_name,
    }


def ftp_release_publish_lock(ftp, lock_state):
    if not lock_state:
        return
    current = ftp_read_json(ftp, lock_state.get("lock_name"))
    if current and current.get("run_id") != lock_state.get("run_id"):
        log("Remote publish lock ownership changed; leaving lock in place")
        return
    if ftp_delete_if_exists(ftp, lock_state.get("lock_name")):
        log("Released remote publish lock {}".format(lock_state.get("run_id")))


def remote_temp_name(remote_name, run_id):
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "-", run_id)
    return "{}.{}.{}.upload".format(remote_name, utc_now().strftime("%Y%m%dT%H%M%SZ"), safe_run_id)


def ftp_cleanup_temporary_uploads(ftp, remote_name, active_temp_name=None):
    prefix = remote_name + "."
    files = ftp.nlst()
    for name in files:
        if name == active_temp_name:
            continue
        if not name.startswith(prefix) or not name.endswith(".upload"):
            continue
        if ftp_delete_if_exists(ftp, name):
            log("Deleted stale remote temp upload {}".format(name))


def ftp_atomic_replace(ftp, local_path, remote_name, run_id):
    tmp_name = remote_temp_name(remote_name, run_id)
    bak_name = remote_name + ".bak"
    ftp_cleanup_temporary_uploads(ftp, remote_name)
    ftp_upload(ftp, local_path, tmp_name)
    log("Uploaded {}".format(tmp_name))

    try:
        ftp.delete(bak_name)
    except Exception:
        pass

    try:
        ftp.rename(remote_name, bak_name)
        log("Renamed {} -> {}".format(remote_name, bak_name))
    except Exception:
        log("No existing {} to back up".format(remote_name))

    try:
        ftp.rename(tmp_name, remote_name)
    except Exception:
        ftp_delete_if_exists(ftp, tmp_name)
        raise
    log("Promoted {} -> {}".format(tmp_name, remote_name))
    ftp_cleanup_temporary_uploads(ftp, remote_name)


def trigger_remote_db_rebuild():
    if not REMOTE_DB_REBUILD_TOKEN:
        log("Remote DB rebuild token is not configured; skipping rebuild request")
        return
    try:
        response = requests.get(
            REMOTE_DB_REBUILD_URL,
            params={"token": REMOTE_DB_REBUILD_TOKEN},
            timeout=REMOTE_DB_REBUILD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = " ".join(response.text.split())
        log("Remote DB rebuild response: {}".format(body[:500]))
    except Exception as e:
        log("WARNING: remote DB rebuild failed: {}".format(e))


def build_server_bootstrap_snapshot(remote_bytes, source_label):
    if remote_bytes is None:
        return {
            "rows": [],
            "size": 0,
            "source": source_label,
        }
    with open(SERVER_COPY, "wb") as f:
        f.write(remote_bytes)
    log("Downloaded server calllog.csv from {}".format(source_label))
    return {
        "rows": load_csv(SERVER_COPY),
        "size": len(remote_bytes),
        "source": source_label,
    }


def fetch_server_rows_from_url(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return build_server_bootstrap_snapshot(response.content, url)


def fetch_server_rows():
    if SERVER_CALLLOG_URL:
        try:
            return fetch_server_rows_from_url(SERVER_CALLLOG_URL)
        except Exception as e:
            log("WARNING: remote calllog bootstrap via URL failed: {}".format(e))

    try:
        ftp = ftp_connect()
    except Exception as e:
        log("WARNING: ftp bootstrap unavailable: {}".format(e))
        return []

    try:
        remote_bytes = ftp_download_bytes(ftp, "calllog.csv")
        if remote_bytes is None:
            return {"rows": [], "size": 0, "source": "ftp:calllog.csv"}
        return build_server_bootstrap_snapshot(remote_bytes, "ftp:calllog.csv")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def bootstrap_local_rows():
    local_rows = load_csv(LOCAL_CSV)
    local_size = file_size_or_zero(LOCAL_CSV)
    last_uploaded_size = load_last_uploaded_calllog_size()
    server_snapshot = fetch_server_rows()
    server_rows = server_snapshot.get("rows", [])
    server_size = max(0, int(server_snapshot.get("size", 0) or 0))
    server_source = server_snapshot.get("source", "server")

    if not server_rows or server_size <= 0:
        log("Bootstrap kept local calllog.csv because the server copy was unavailable")
        return filter_scoped_rows(local_rows)

    if local_size <= 0:
        write_csv(LOCAL_CSV, server_rows)
        log(
            "Bootstrap refreshed local calllog.csv from {} because the local file was missing (server_bytes={}, last_uploaded_bytes={})".format(
                server_source,
                server_size,
                last_uploaded_size,
            )
        )
        return server_rows

    if last_uploaded_size > 0 and server_size < last_uploaded_size:
        log(
            "Bootstrap kept local calllog.csv because the server copy from {} is smaller than the last uploaded file ({} < {})".format(
                server_source,
                server_size,
                last_uploaded_size,
            )
        )
        return filter_scoped_rows(local_rows)

    if server_size < local_size:
        log(
            "Bootstrap kept local calllog.csv because the server copy from {} is smaller than the local file ({} < {})".format(
                server_source,
                server_size,
                local_size,
            )
        )
        return filter_scoped_rows(local_rows)

    write_csv(LOCAL_CSV, server_rows)
    log(
        "Bootstrap refreshed local calllog.csv from {} (server_bytes={}, local_bytes={}, last_uploaded_bytes={})".format(
            server_source,
            server_size,
            local_size,
            last_uploaded_size,
        )
    )
    return server_rows


def build_publish_file_specs(extra_file_specs=None):
    specs = [
        ("calllog.csv", LOCAL_CSV),
        ("calllog.json", CALLLOG_JSON),
    ]
    if os.path.exists(CALLLOG_ARREST_INDEX_JSON):
        specs.append(("calllog_arrest_index.json", CALLLOG_ARREST_INDEX_JSON))

    seen_names = {remote_name for remote_name, _ in specs}
    for remote_name, local_path in extra_file_specs or []:
        if not remote_name or not local_path or not os.path.exists(local_path):
            continue
        if remote_name in seen_names:
            continue
        specs.append((remote_name, local_path))
        seen_names.add(remote_name)
    return specs


def build_http_upload_manifest(run_id, file_specs):
    files = []
    for index, (remote_name, local_path) in enumerate(file_specs):
        if not local_path or not os.path.exists(local_path):
            continue
        files.append(
            {
                "field_name": "upload_{}".format(index),
                "remote_name": remote_name,
                "filename": os.path.basename(local_path),
                "size": os.path.getsize(local_path),
                "sha256": sha256_file(local_path),
            }
        )
    return {
        "batch_timestamp": utc_now_text(),
        "run_id": run_id,
        "source": HTTP_UPLOAD_SOURCE,
        "publisher": build_publish_owner(),
        "files": files,
    }


def publish_outputs_via_http(extra_file_specs=None):
    if not HTTP_UPLOAD_URL or not HTTP_UPLOAD_SECRET:
        raise RuntimeError("HTTP upload endpoint or secret is not configured")

    run_id = build_publish_run_id()
    file_specs = build_publish_file_specs(extra_file_specs)

    manifest = build_http_upload_manifest(run_id, file_specs)
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(
        HTTP_UPLOAD_SECRET.encode("utf-8"),
        manifest_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    files = []
    opened = []
    try:
        for item in manifest["files"]:
            local_path = dict(file_specs).get(item["remote_name"])
            if not local_path:
                continue
            fp = open(local_path, "rb")
            opened.append(fp)
            files.append(
                (
                    item["field_name"],
                    (
                        item["filename"],
                        fp,
                        "application/octet-stream",
                    ),
                )
            )

        response = requests.post(
            HTTP_UPLOAD_URL,
            data={
                "manifest": manifest_json,
                "signature": signature,
            },
            files=files,
            timeout=HTTP_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}
        log("HTTP upload response: {}".format(json.dumps(payload, sort_keys=True)[:1200]))
    finally:
        for fp in opened:
            try:
                fp.close()
            except Exception:
                pass


def publish_outputs_via_ftp(extra_file_specs=None):
    with open(LOCAL_CSV, "rb") as f:
        local_bytes = f.read()
    needs_db_rebuild = False
    lock_state = None

    try:
        ftp = ftp_connect()
    except Exception as e:
        log("WARNING: ftp publish unavailable: {}".format(e))
        return

    try:
        lock_state = ftp_acquire_publish_lock(ftp)
        run_id = lock_state["run_id"]
        trace_meta = write_upload_trace_file(CALLLOG_UPLOAD_META_JSON, run_id, "ftp-direct")
        files = ftp.nlst()
        remote_bytes = ftp_download_bytes(ftp, "calllog.csv")
        if "calllog.sqlite" not in files:
            needs_db_rebuild = True

        if remote_bytes == local_bytes:
            log("Remote calllog.csv already matches local file; skipping CSV upload")
        else:
            if remote_bytes:
                backup_path = store_backup(remote_bytes)
                if backup_path:
                    log("Saved backup snapshot: {}".format(backup_path))
                else:
                    log("Remote file matched the latest snapshot; no new backup written")
            ftp_atomic_replace(ftp, LOCAL_CSV, "calllog.csv", run_id)
            needs_db_rebuild = True

        ftp_atomic_replace(ftp, CALLLOG_JSON, "calllog.json", run_id)
        log("calllog.json uploaded with remote lock")
        if os.path.exists(CALLLOG_ARREST_INDEX_JSON):
            ftp_atomic_replace(ftp, CALLLOG_ARREST_INDEX_JSON, "calllog_arrest_index.json", run_id)
            log("calllog_arrest_index.json uploaded with remote lock")
        else:
            log("calllog_arrest_index.json missing locally; skipping upload")
        ftp_atomic_replace(ftp, CALLLOG_UPLOAD_META_JSON, "calllog_upload_meta.json", run_id)
        log(
            "calllog_upload_meta.json uploaded with remote lock (source={}, run_id={})".format(
                trace_meta.get("source", ""),
                trace_meta.get("run_id", ""),
            )
        )
        for remote_name, local_path in extra_file_specs or []:
            if not remote_name or not local_path or not os.path.exists(local_path):
                continue
            ftp_atomic_replace(ftp, local_path, remote_name, run_id)
            log("{} uploaded with remote lock".format(remote_name))
    except Exception as e:
        log("WARNING: ftp publish failed: {}".format(e))
    finally:
        try:
            ftp_release_publish_lock(ftp, lock_state)
        except Exception as e:
            log("WARNING: failed to release remote publish lock: {}".format(e))
        try:
            ftp.quit()
        except Exception:
            pass

    if needs_db_rebuild:
        trigger_remote_db_rebuild()


def publish_outputs(extra_file_specs=None):
    if HTTP_UPLOAD_URL:
        try:
            publish_outputs_via_http(extra_file_specs)
            return
        except Exception as e:
            log("WARNING: http publish failed: {}".format(e))
            if not FTP_USER or not FTP_PASS:
                return
    publish_outputs_via_ftp(extra_file_specs)


def get_hidden_fields(soup):
    return {
        element["name"]: element.get("value", "")
        for element in soup.find_all("input", type="hidden")
        if element.get("name")
    }


def extract_agencies(soup):
    select = soup.find("select", {"name": DROPDOWN_EVENT_TARGET})
    if select is None:
        raise RuntimeError("Agency dropdown not found in mediasummary response")
    return [
        ((option.get("value") or "").strip(), option.get_text(strip=True))
        for option in select.find_all("option")
        if option.get("value") and option.get("value") != "-1"
    ]


def postback(session, base_html, target, argument, extra):
    soup = BeautifulSoup(base_html, "html.parser")
    data = get_hidden_fields(soup)
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = argument
    data.update(extra)
    response = session.post(FRAME_URL, data=data, timeout=60)
    response.raise_for_status()
    return response.text


def parse_grid(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=GRIDVIEW_ID)
    if table is None:
        raise ValueError("No table with id='{}' found in response".format(GRIDVIEW_ID))

    headers = [re.sub(r"\s+", " ", th.get_text(strip=True)).lower() for th in table.find_all("th")]
    header_map = {
        "date time": "date/time",
        "call no": "call number",
        "report no": "report number",
        "dispo": "disposition",
    }
    headers = [header_map.get(header, header) for header in headers]

    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue
        row = {headers[i]: tds[i].get_text(strip=True) for i in range(len(headers))}
        row["revision_scraped_at"] = ""
        rows.append(normalize_record(row))

    return rows, soup


def max_pages(soup):
    nums = [int(a.get_text()) for a in soup.find_all("a") if a.get_text().isdigit()]
    return max(nums) if nums else 1


def merge_revisions(existing, scraped):
    out = {}
    for row in filter_scoped_rows(existing):
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if call_number:
            out[call_number] = normalized

    for row in scraped:
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if not call_number or not row_is_in_scope(normalized):
            continue

        base = call_base(call_number)
        matches = [value for value in out.values() if call_base(value["call number"]) == base]

        if not matches:
            out[call_number] = normalized
            continue

        latest = max(matches, key=lambda value: revision_number(value["call number"]))
        if any(normalized.get(field, "") != latest.get(field, "") for field in FIELDS_TO_COMPARE):
            next_revision = max(revision_number(value["call number"]) for value in matches) + 1
            revision_row = dict(normalized)
            revision_row["call number"] = "{}.{}".format(base, next_revision)
            revision_row["revision_scraped_at"] = revision_scrape_timestamp()
            if revision_row["call number"] not in out:
                out[revision_row["call number"]] = revision_row

    return filter_scoped_rows(out.values())


def release_target_date():
    return (datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y")


def load_release_rows():
    source_path = RELEASES_CSV if os.path.exists(RELEASES_CSV) else LEGACY_RELEASES_CSV
    if not os.path.exists(source_path):
        return []
    with open(source_path, newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            normalized = {}
            for field in RELEASE_FIELDS:
                normalized[field] = (row.get(field, "") or "").strip()
            if any(normalized.values()):
                rows.append(normalized)
        return rows


def write_release_rows(rows):
    for path in [RELEASES_CSV, LEGACY_RELEASES_CSV]:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RELEASE_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in RELEASE_FIELDS})
        os.replace(tmp_path, path)


def fetch_daily_release_rows():
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Origin": "https://jimsnetil.shr.sbcounty.gov",
        "Referer": "https://jimsnetil.shr.sbcounty.gov/bookingsearch.aspx",
        "User-Agent": "Mozilla/5.0",
    }
    target_date = release_target_date()
    payload = {
        "mdl": {
            "__type": "InmateLocator.mdlReleaseLog",
            "ReleaseDate": target_date,
        }
    }

    response = requests.post(RELEASE_LOG_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    data = response.json()
    html_content = data.get("d", {}).get("SearchResults", "")
    if not html_content:
        return target_date, []

    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table", id="grdResults_grid")
    if table is None:
        return target_date, []

    rows = []
    for tr in table.find_all("tr"):
        cols = [col.get_text(strip=True) for col in tr.find_all("td")]
        if not cols:
            continue
        row = {
            "Name": cols[0] if len(cols) > 0 else "",
            "Sex": cols[1] if len(cols) > 1 else "",
            "Age": cols[2] if len(cols) > 2 else "",
            "Height": cols[3] if len(cols) > 3 else "",
            "Weight": cols[4] if len(cols) > 4 else "",
            "Release Date": target_date,
        }
        rows.append(row)

    return target_date, rows


def run_daily_release_if_due():
    if not ENABLE_DAILY_RELEASES:
        log("Daily release fetch is disabled")
        return
    if not should_run_daily("daily_release_list"):
        return

    target_date, new_rows = fetch_daily_release_rows()
    existing_rows = load_release_rows()
    seen_names = set()
    merged_rows = []

    for row in existing_rows:
        name = row.get("Name", "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        merged_rows.append(row)

    added_count = 0
    for row in new_rows:
        name = row.get("Name", "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        merged_rows.append(row)
        added_count += 1

    write_release_rows(merged_rows)
    mark_daily_done("daily_release_list")

    message = "Release list refreshed for {} ({} fetched, {} new, {} total)".format(
        target_date,
        len(new_rows),
        added_count,
        len(merged_rows),
    )
    log(message)
    log_daily_release(message)


def main():
    ensure_dirs()
    log("Run started")
    run_started_monotonic = time.monotonic()

    bootstrap_started = time.monotonic()
    merged = bootstrap_local_rows()
    log("Bootstrap selection complete ({} records)".format(len(merged)))
    log_phase_duration("bootstrap selection", bootstrap_started)

    sbso_started = time.monotonic()
    session = requests.Session()
    initial = session.get(FRAME_URL, timeout=60)
    initial.raise_for_status()

    agencies = [
        (code, name)
        for code, name in extract_agencies(BeautifulSoup(initial.text, "html.parser"))
        if code == BARSTOW_CODE
    ]
    if not agencies:
        raise RuntimeError("Could not find target agency {}".format(BARSTOW_CODE))

    for code, _ in agencies:
        html = postback(session, initial.text, DROPDOWN_EVENT_TARGET, "", {DROPDOWN_EVENT_TARGET: code})
        write_text(LOCAL_HTML, html)
        log("Saved input.html from the latest agency selection")

        rows, soup = parse_grid(html)
        pages = max_pages(soup)

        all_rows = rows[:]
        current_html = html
        for page_num in range(2, pages + 1):
            time.sleep(0.2)
            current_html = postback(
                session,
                current_html,
                GRIDVIEW_ID,
                "Page${}".format(page_num),
                {DROPDOWN_EVENT_TARGET: code},
            )
            page_rows, _ = parse_grid(current_html)
            all_rows.extend(page_rows)

        merged = merge_revisions(merged, all_rows)
    log_phase_duration("SBSO scrape", sbso_started)

    if scrape_chp_incidents is not None:
        chp_started = time.monotonic()
        try:
            chp_rows = scrape_chp_incidents()
            merged, chp_closed = close_missing_feed_rows(
                merged,
                chp_rows,
                agency_code="CHP",
                extra_kind="chp_incident",
            )
            if chp_rows:
                merged = merge_revisions(merged, chp_rows)
                log("Merged {} CHP incidents".format(len(chp_rows)))
            else:
                log("CHP scrape returned 0 incidents")
            if chp_closed:
                log("Closed {} stale CHP incidents".format(chp_closed))
        except Exception as e:
            log("WARNING: CHP scrape failed: {}".format(e))
        else:
            log_phase_duration("CHP scrape", chp_started)
    else:
        log("CHP scraper unavailable; skipping CHP merge")

    if scrape_pulsepoint_incidents is not None:
        pulsepoint_started = time.monotonic()
        try:
            pulsepoint_rows = scrape_pulsepoint_incidents()
            merged, pulsepoint_closed = close_missing_feed_rows(
                merged,
                pulsepoint_rows,
                agency_code=PULSEPOINT_AGENCY_CODE,
                extra_kind="pulsepoint_incident",
            )
            if pulsepoint_rows:
                merged = merge_revisions(merged, pulsepoint_rows)
                log("Merged {} PulsePoint incidents".format(len(pulsepoint_rows)))
            else:
                log("PulsePoint scrape returned 0 incidents")
            if pulsepoint_closed:
                log("Closed {} stale PulsePoint incidents".format(pulsepoint_closed))
        except Exception as e:
            log("WARNING: PulsePoint scrape failed: {}".format(e))
        else:
            log_phase_duration("PulsePoint scrape", pulsepoint_started)
    else:
        log("PulsePoint scraper unavailable; skipping PulsePoint merge")

    write_started = time.monotonic()
    write_csv(LOCAL_CSV, merged)
    log("Local calllog.csv written ({} records)".format(len(merged)))

    formatted_count = write_formatted_csv(merged, FORMATTED_CSV)
    log("Local calllog_formatted.csv written ({} rows)".format(formatted_count))

    barstow = [
        row
        for row in merged
        if station_is_barstow(row.get("agency", ""), row.get("station", ""))
    ]
    with open(CALLLOG_JSON, "w", encoding="utf-8") as f:
        json.dump(barstow, f, indent=2)
    log_phase_duration("local output write", write_started)

    daily_files_started = time.monotonic()
    extra_file_specs = []
    extra_file_specs.extend(
        ensure_remote_backed_daily_file(
            "death_index.csv",
            DEATH_INDEX_CSV,
            refresh_death_index_csv,
            run_started_monotonic=run_started_monotonic,
        )
    )
    extra_file_specs.extend(
        ensure_remote_backed_daily_file(
            "all_records.json",
            ARREST_LOG_JSON,
            refresh_arrest_log_json,
            run_started_monotonic=run_started_monotonic,
        )
    )
    log_phase_duration("daily supporting files", daily_files_started)

    arrest_index_started = time.monotonic()
    rebuild_calllog_arrest_index()
    log_phase_duration("calllog arrest index rebuild", arrest_index_started)

    publish_started = time.monotonic()
    publish_outputs(extra_file_specs)
    log_phase_duration("publish outputs", publish_started)

    release_started = time.monotonic()
    run_daily_release_if_due()
    log_phase_duration("daily release check", release_started)
    log_phase_duration("full run", run_started_monotonic)
    log("Run completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("ERROR: {}".format(e))
        raise

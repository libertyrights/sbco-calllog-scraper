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
import time
from datetime import datetime, timedelta, timezone
from ftplib import FTP
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from arrest_index_builder import build_arrest_index

# ================= CONFIG =================

FRAME_URL = "https://mediasummary.shr.sbcounty.gov/"
DROPDOWN_EVENT_TARGET = "ctl00$MainContent$ddCity"
GRIDVIEW_ID = "GridView1"

BARSTOW_CODE = "BA"

BASE_DIR = os.environ.get("SBCO_BASE_DIR", "/home/mark/python")
STATE_DIR = os.path.join(BASE_DIR, ".state")
LOCAL_HTML = os.path.join(BASE_DIR, "input.html")
LOCAL_CSV = os.path.join(BASE_DIR, "calllog.csv")
FORMATTED_CSV = os.path.join(BASE_DIR, "calllog_formatted.csv")
SERVER_COPY = os.path.join(BASE_DIR, "server_calllog.csv")
CALLLOG_JSON = os.path.join(BASE_DIR, "calllog.json")
CALLLOG_ARREST_INDEX_JSON = os.path.join(BASE_DIR, "calllog_arrest_index.json")
SECRET_CONFIG_PATH = os.environ.get("SBCO_SECRET_CONFIG", os.path.join(BASE_DIR, "secrets.local.json"))

BACKUP_DIR = os.path.join(BASE_DIR, "snapshots", "calllog")
MANIFEST_PATH = os.path.join(BACKUP_DIR, "manifest.json")

RELEASES_CSV = os.path.join(BASE_DIR, "releases.csv")
LEGACY_RELEASES_CSV = os.path.join(BASE_DIR, "daily_release_list.csv")
DAILY_RELEASE_LOG = os.path.join(BASE_DIR, "daily_release_list.log")
RELEASE_LOG_URL = "https://jimsnetil.shr.sbcounty.gov/bookingsearch.aspx/GetReleaseLog"

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

CSV_FIELDS = [
    "date/time",
    "agency",
    "call number",
    "report number",
    "call type",
    "disposition",
    "location",
    "revision_scraped_at",
]

FORMATTED_FIELDS = [
    "date/time",
    "agency",
    "division",
    "call number",
    "report number",
    "call type",
    "disposition",
    "streetAddr",
    "city",
]

RELEASE_FIELDS = ["Name", "Sex", "Age", "Height", "Weight", "Release Date"]

FIELDS_TO_COMPARE = ["call type", "disposition", "location"]
DATE_FORMATS = [
    "%m/%d/%Y %I:%M:%S %p",
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


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write("[SCRAPER] {} {}\n".format(ts, msg))


def log_daily_release(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DAILY_RELEASE_LOG, "a") as f:
        f.write("[RELEASES] {} {}\n".format(ts, msg))


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


def normalize_record(record):
    normalized = {}
    for field in CSV_FIELDS:
        normalized[field] = (record.get(field, "") or "").strip()
    normalized["location"] = clean_location_field(normalized.get("location", ""))
    return normalized


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
    return list(out.values())


def call_base(call_number):
    return call_number.split(".")[0] if "." in call_number else call_number


def revision_number(call_number):
    if "." not in call_number:
        return 0
    suffix = call_number.rsplit(".", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


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


def rebuild_calllog_arrest_index():
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


def fetch_server_rows_from_url(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with open(SERVER_COPY, "wb") as f:
        f.write(response.content)
    log("Downloaded server calllog.csv from {}".format(url))
    return load_csv(SERVER_COPY)


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
            return []
        with open(SERVER_COPY, "wb") as f:
            f.write(remote_bytes)
        log("Downloaded server calllog.csv")
        return load_csv(SERVER_COPY)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


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
        "files": files,
    }


def publish_outputs_via_http():
    if not HTTP_UPLOAD_URL or not HTTP_UPLOAD_SECRET:
        raise RuntimeError("HTTP upload endpoint or secret is not configured")

    run_id = build_publish_run_id()
    file_specs = [
        ("calllog.csv", LOCAL_CSV),
        ("calllog.json", CALLLOG_JSON),
    ]
    if os.path.exists(CALLLOG_ARREST_INDEX_JSON):
        file_specs.append(("calllog_arrest_index.json", CALLLOG_ARREST_INDEX_JSON))

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


def publish_outputs_via_ftp():
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


def publish_outputs():
    if HTTP_UPLOAD_URL:
        try:
            publish_outputs_via_http()
            return
        except Exception as e:
            log("WARNING: http publish failed: {}".format(e))
            if not FTP_USER or not FTP_PASS:
                return
    publish_outputs_via_ftp()


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
    for row in existing:
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if call_number:
            out[call_number] = normalized

    for row in scraped:
        normalized = normalize_record(row)
        call_number = normalized.get("call number", "")
        if not call_number:
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

    return list(out.values())


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

    local_rows = load_csv(LOCAL_CSV)
    server_rows = fetch_server_rows()
    merged = union_merge(local_rows, server_rows)
    write_csv(LOCAL_CSV, merged)
    log("Bootstrap union complete ({} records)".format(len(merged)))

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

    write_csv(LOCAL_CSV, merged)
    log("Local calllog.csv written ({} records)".format(len(merged)))

    formatted_count = write_formatted_csv(merged, FORMATTED_CSV)
    log("Local calllog_formatted.csv written ({} rows)".format(formatted_count))

    barstow = [row for row in merged if row.get("agency") == BARSTOW_CODE]
    with open(CALLLOG_JSON, "w", encoding="utf-8") as f:
        json.dump(barstow, f, indent=2)

    rebuild_calllog_arrest_index()
    publish_outputs()
    run_daily_release_if_due()
    log("Run completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("ERROR: {}".format(e))
        raise

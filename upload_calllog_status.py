from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from ftplib import FTP
from pathlib import Path

BASE_DIR = Path(os.environ.get("SBCO_BASE_DIR", Path(__file__).resolve().parent))
REMOTE_DIR = "/domains/upnexx.xyz/public_html/status"
REMOTE_STATUS_FILE = "calllog_status.json"

FTP_HOST = os.environ.get("SERV00_FTP_HOST", os.environ.get("FTP_HOST", ""))
FTP_USER = os.environ.get("SERV00_FTP_USER", os.environ.get("FTP_USER", ""))
FTP_PASS = os.environ.get("SERV00_FTP_PASS", os.environ.get("FTP_PASS", ""))

GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def build_status() -> dict:
    status = {
        "source": "sbco-calllog-scraper",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "github_run_id": GITHUB_RUN_ID,
        "github_repo": GITHUB_REPOSITORY,
    }

    for name in ("calllog.csv", "calllog.json", "calllog_formatted.csv",
                  "all_records.json", "death_index.csv", "calllog_arrest_index.json"):
        path = BASE_DIR / name
        size = file_size(path)
        if size:
            status[name] = size

    return status


def upload_status() -> None:
    if not FTP_HOST:
        print("[i] No FTP_HOST set; skipping status upload.")
        return

    status = build_status()
    data = json.dumps(status, indent=2).encode("utf-8")
    print(f"[+] Status:\n{status}")

    GITHUB_STEP_SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if GITHUB_STEP_SUMMARY:
        Path(GITHUB_STEP_SUMMARY).write_text(f"```json\n{data.decode()}\n```")

    ftp = FTP(FTP_HOST)
    try:
        ftp.login(user=FTP_USER, passwd=FTP_PASS)
        parts = [p for p in REMOTE_DIR.split("/") if p]
        ftp.cwd("/")
        for part in parts:
            try:
                ftp.cwd(part)
            except Exception:
                ftp.mkd(part)
                ftp.cwd(part)
        ftp.storbinary(f"STOR {REMOTE_STATUS_FILE}", io.BytesIO(data))
        print(f"[+] Uploaded {REMOTE_STATUS_FILE} ({len(data)} bytes)")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


if __name__ == "__main__":
    upload_status()

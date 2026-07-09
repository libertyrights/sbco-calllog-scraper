#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

import scraper_run


ARTIFACT_PREFIX = "sbco-calllog-runtime-"


def parse_date(value: str) -> date:
    return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()


def iso_today() -> date:
    return datetime.now(UTC).date()


def iter_days(start_day: date, end_day: date):
    current = start_day
    while current <= end_day:
        yield current
        current += timedelta(days=1)


def github_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer {}".format(token),
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    return session


def list_runtime_artifacts(session: requests.Session, repo: str):
    url = "https://api.github.com/repos/{}/actions/artifacts?per_page=100".format(repo)
    artifacts = []
    while url:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        payload = response.json()
        artifacts.extend(payload.get("artifacts", []) or [])
        url = response.links.get("next", {}).get("url")
    return [artifact for artifact in artifacts if (artifact.get("name") or "").startswith(ARTIFACT_PREFIX)]


def group_artifacts_by_day(artifacts):
    grouped = defaultdict(list)
    for artifact in artifacts:
        created_at = (artifact.get("created_at") or "").strip()
        if len(created_at) < 10:
            continue
        day = created_at[:10]
        grouped[day].append(artifact)
    for day in grouped:
        grouped[day].sort(
            key=lambda artifact: (
                int(artifact.get("size_in_bytes") or 0),
                artifact.get("created_at") or "",
            ),
            reverse=True,
        )
    return grouped


def download_artifact_zip(session: requests.Session, artifact: dict) -> bytes:
    download_url = (artifact.get("archive_download_url") or "").strip()
    if not download_url:
        raise RuntimeError("Artifact is missing archive download URL")
    response = session.get(download_url, timeout=300, allow_redirects=True)
    response.raise_for_status()
    return response.content


def extract_calllog_csv_bytes(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        candidates = sorted(
            [name for name in archive.namelist() if name.endswith("/calllog.csv") or name == "calllog.csv"],
            key=len,
        )
        if not candidates:
            raise RuntimeError("Artifact zip does not contain calllog.csv")
        with archive.open(candidates[0]) as handle:
            return handle.read()


def count_csv_rows(csv_bytes: bytes) -> int:
    count = 0
    wrapper = io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="utf-8", errors="replace", newline="")
    try:
        reader = csv.DictReader(wrapper)
        for _ in reader:
            count += 1
    finally:
        wrapper.close()
    return count


def archive_remote_name_for_day(day: date) -> str:
    return scraper_run.archive_remote_name_for_date(day.isoformat())


def ensure_archive_runtime_dir() -> Path:
    path = Path(scraper_run.ARCHIVE_RUNTIME_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_archive_file(day: date, csv_bytes: bytes) -> tuple[str, str, int]:
    runtime_dir = ensure_archive_runtime_dir()
    remote_name = archive_remote_name_for_day(day)
    archive_path = runtime_dir / remote_name
    tmp_path = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with gzip.open(tmp_path, "wb") as handle:
        handle.write(csv_bytes)
    os.replace(tmp_path, archive_path)
    row_count = count_csv_rows(csv_bytes)
    return remote_name, str(archive_path), row_count


def normalize_existing_archives(index_payload: dict) -> dict[str, dict]:
    archives = {}
    for entry in index_payload.get("archives", []) if isinstance(index_payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        remote_name = (entry.get("remote_name") or "").strip()
        if not remote_name:
            continue
        archives[remote_name] = dict(entry)
    return archives


def build_archive_entry(day: date, remote_name: str, archive_path: str, row_count: int, csv_bytes: bytes, artifact: dict) -> dict:
    entry = {
        "date": day.isoformat(),
        "remote_name": remote_name,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "row_count": int(row_count),
        "calllog_bytes": len(csv_bytes),
        "archive_bytes": os.path.getsize(archive_path),
        "archive_sha256": scraper_run.sha256_file(archive_path),
        "source_artifact": artifact.get("name") or "",
        "artifact_created_at": artifact.get("created_at") or "",
        "workflow_run_id": (artifact.get("workflow_run") or {}).get("id"),
    }
    archive_url = scraper_run.public_file_url(remote_name)
    if archive_url:
        entry["url"] = archive_url
    return entry


def write_archive_index(entries: list[dict]) -> str:
    runtime_dir = ensure_archive_runtime_dir()
    index_path = runtime_dir / scraper_run.CALLLOG_ARCHIVE_INDEX_REMOTE_NAME
    payload = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "github-archive-backfill",
        "archives": sorted(entries, key=lambda entry: ((entry.get("date") or ""), (entry.get("remote_name") or ""))),
    }
    scraper_run.write_text(str(index_path), json.dumps(payload, indent=2, sort_keys=True))
    return str(index_path)


def upload_file_specs(file_specs):
    run_id = "archive-backfill-{}".format(scraper_run.build_publish_run_id())
    manifest = scraper_run.build_http_upload_manifest(run_id, file_specs)
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    signature = scraper_run.sign_http_upload_manifest(manifest_json)

    files = []
    opened = []
    try:
        for index, (remote_name, local_path) in enumerate(file_specs):
            field_name = "upload_{}".format(index)
            handle = open(local_path, "rb")
            opened.append(handle)
            files.append((field_name, (os.path.basename(local_path), handle, "application/octet-stream")))
        response = requests.post(
            scraper_run.HTTP_UPLOAD_URL,
            data={
                "manifest": manifest_json,
                "signature": signature,
            },
            files=files,
            timeout=scraper_run.HTTP_UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError("Archive upload rejected: {}".format(json.dumps(payload, sort_keys=True)[:1200]))
        print("Uploaded:", ", ".join(remote_name for remote_name, _ in file_specs))
    finally:
        for handle in opened:
            try:
                handle.close()
            except Exception:
                pass


def choose_artifact_for_day(session: requests.Session, artifacts_for_day: list[dict]):
    last_error = None
    for artifact in artifacts_for_day:
        try:
            zip_bytes = download_artifact_zip(session, artifact)
            csv_bytes = extract_calllog_csv_bytes(zip_bytes)
            if not csv_bytes.strip():
                raise RuntimeError("calllog.csv is empty")
            return artifact, csv_bytes
        except Exception as exc:
            last_error = exc
            print("Skipping artifact {}: {}".format(artifact.get("name") or "unknown", exc))
    if last_error is not None:
        raise last_error
    raise RuntimeError("No usable artifact candidates were provided")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill durable daily calllog archives from retained GitHub runtime artifacts.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "libertyrights/sbco-calllog-scraper"))
    parser.add_argument("--start-date", default="2026-06-26")
    parser.add_argument("--end-date", default=(iso_today() - timedelta(days=1)).isoformat())
    parser.add_argument("--overwrite-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    github_token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is required")

    start_day = parse_date(args.start_date)
    end_day = parse_date(args.end_date)
    if end_day < start_day:
        raise RuntimeError("end-date must be on or after start-date")

    session = github_session(github_token)
    artifacts = list_runtime_artifacts(session, args.repo)
    grouped = group_artifacts_by_day(artifacts)
    existing_index = scraper_run.fetch_public_json(scraper_run.CALLLOG_ARCHIVE_INDEX_REMOTE_NAME) or {}
    existing_archives = normalize_existing_archives(existing_index)
    updated_archives = dict(existing_archives)

    file_specs_to_upload = []
    selected_days = []
    for day in iter_days(start_day, end_day):
        day_text = day.isoformat()
        remote_name = archive_remote_name_for_day(day)
        if remote_name in existing_archives and not args.overwrite_existing:
            print("Skipping existing archive for {}".format(day_text))
            continue
        candidates = grouped.get(day_text, [])
        if not candidates:
            print("No retained artifact found for {}".format(day_text))
            continue
        artifact, csv_bytes = choose_artifact_for_day(session, candidates)
        archive_remote_name, archive_path, row_count = build_archive_file(day, csv_bytes)
        updated_archives[archive_remote_name] = build_archive_entry(day, archive_remote_name, archive_path, row_count, csv_bytes, artifact)
        file_specs_to_upload.append((archive_remote_name, archive_path))
        selected_days.append((day_text, artifact.get("name") or "", row_count))

    if not selected_days:
        print("No retained archive days matched the requested range")
        return 0

    index_entries = sorted(updated_archives.values(), key=lambda entry: ((entry.get("date") or ""), (entry.get("remote_name") or "")))
    index_path = write_archive_index(index_entries)

    if args.dry_run:
        print("Dry run selected {} archive day(s)".format(len(selected_days)))
        for day_text, artifact_name, row_count in selected_days:
            print("{} <- {} ({} rows)".format(day_text, artifact_name, row_count))
        print("Index path:", index_path)
        return 0

    if not scraper_run.HTTP_UPLOAD_URL:
        raise RuntimeError("SBCO_HTTP_UPLOAD_URL is not configured")
    if not scraper_run.load_http_upload_signing_key_pem():
        raise RuntimeError("SBCO_UPLOAD_SIGNING_PRIVATE_KEY is not configured")

    for file_spec in file_specs_to_upload:
        upload_file_specs([file_spec])

    upload_file_specs([(scraper_run.CALLLOG_ARCHIVE_INDEX_REMOTE_NAME, index_path)])
    print("Backfilled {} archive day(s)".format(len(selected_days)))
    for day_text, artifact_name, row_count in selected_days:
        print("{} <- {} ({} rows)".format(day_text, artifact_name, row_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

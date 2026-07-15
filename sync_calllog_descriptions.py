from __future__ import annotations

import argparse
import csv
import io
import json
import os
import posixpath
import re
import time
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Iterable


REMOTE_CANDIDATES: tuple[tuple[str, str | None], ...] = (
    ("calllog_descriptions.csv", None),
    ("calllog_desc.csv", None),
    ("calllog_descriptions.local.csv", None),
    ("descriptions.csv", None),
    ("descriptions/calllog.csv", None),
    ("call_type_descriptions.csv", "call_types"),
    ("call_type_desc.csv", "call_types"),
    ("call_types.csv", "call_types"),
    ("disposition_descriptions.csv", "dispositions"),
    ("disposition_desc.csv", "dispositions"),
    ("dispo_descriptions.csv", "dispositions"),
    ("dispo_desc.csv", "dispositions"),
    ("dispositions.csv", "dispositions"),
    ("prefix_descriptions.csv", "prefixes"),
    ("prefix_desc.csv", "prefixes"),
    ("call_prefix_descriptions.csv", "prefixes"),
    ("call_prefix_desc.csv", "prefixes"),
    ("prefixes.csv", "prefixes"),
)

CSV_NAME_SKIP_RE = re.compile(
    r"^(?:calllog|calllog_formatted|server_calllog|releases?|daily_release_list|death_index|all_records)\.csv$",
    re.I,
)
CSV_NAME_HINT_RE = re.compile(r"(desc|description|dispo|disposition|prefix|call[_-]?type|types?|codes?)", re.I)


def ftp_connect() -> tuple[FTP, str]:
    host = (
        os.environ.get("SBCO_FTP_HOST")
        or os.environ.get("SERV00_FTP_HOST")
        or os.environ.get("FTP_HOST")
        or ""
    ).strip()
    user = (
        os.environ.get("SBCO_FTP_USER")
        or os.environ.get("SERV00_FTP_USER")
        or os.environ.get("FTP_USER")
        or ""
    ).strip()
    password = (
        os.environ.get("SBCO_FTP_PASS")
        or os.environ.get("SERV00_FTP_PASS")
        or os.environ.get("FTP_PASS")
        or ""
    ).strip()
    remote_dir = (os.environ.get("SBCO_FTP_REMOTE_DIR") or "/domains/upnexx.xyz/public_html/osint").strip()
    timeout = int(os.environ.get("SBCO_FTP_TIMEOUT_SECONDS", "60"))

    if not host or not user or not password:
        raise RuntimeError("FTP credentials are not configured")

    ftp = FTP()
    ftp.connect(host, timeout=timeout)
    ftp.login(user, password)
    ftp.cwd(remote_dir)
    return ftp, remote_dir


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def section_name(value: str, default: str | None = None) -> str:
    key = normalize_header(value or default or "")
    if key in {"prefix", "prefixes", "callprefix", "callprefixes"}:
        return "prefixes"
    if key in {"dispo", "disposition", "dispositions", "dispositioncode"}:
        return "dispositions"
    if key in {"calltype", "calltypes", "type", "types", "call", "calls"}:
        return "call_types"
    return ""


def first_value(row: list[str], headers: dict[str, int], names: Iterable[str]) -> str:
    for name in names:
        index = headers.get(normalize_header(name))
        if index is not None and index < len(row):
            value = row[index].strip()
            if value:
                return value
    return ""


def parse_description_csv(text: str, default_section: str | None) -> dict[str, dict[str, str]]:
    payload: dict[str, dict[str, str]] = {"prefixes": {}, "dispositions": {}, "call_types": {}}
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return payload

    normalized_headers = {normalize_header(name): index for index, name in enumerate(header)}
    named_columns = {
        "category",
        "section",
        "kind",
        "map",
        "list",
        "group",
        "code",
        "key",
        "calltype",
        "type",
        "dispo",
        "disposition",
        "prefix",
        "description",
        "desc",
        "label",
        "meaning",
        "text",
    }
    has_header = bool(set(normalized_headers).intersection(named_columns))

    if has_header:
        headers = normalized_headers
        rows = list(reader)
    else:
        headers = {"code": 0, "description": 1}
        rows = [header, *list(reader)]

    for row in rows:
        if not row or not any(cell.strip() for cell in row):
            continue
        section = section_name(
            first_value(row, headers, ["category", "section", "kind", "map", "list", "group"]),
            default_section,
        )
        if not section:
            continue

        code = first_value(
            row,
            headers,
            ["code", "key", "call type", "call_type", "type", "dispo", "disposition", "prefix"],
        )
        description = first_value(row, headers, ["description", "desc", "label", "meaning", "text"])
        if not description and len(row) >= 2:
            description = row[-1].strip()
        if code and description:
            payload[section][code.strip().upper()] = description.strip()

    return payload


def merge_descriptions(target: dict[str, dict[str, str]], update: dict[str, dict[str, str]]) -> None:
    for section in ("prefixes", "dispositions", "call_types"):
        target.setdefault(section, {})
        target[section].update(update.get(section, {}))


def retr_text(ftp: FTP, remote_name: str) -> str | None:
    chunks: list[bytes] = []
    try:
        ftp.retrbinary(f"RETR {remote_name}", chunks.append)
    except error_perm:
        return None
    return b"".join(chunks).decode("utf-8-sig", errors="replace")


def section_from_filename(remote_name: str) -> str | None:
    name = posixpath.basename(remote_name).lower()
    if "prefix" in name:
        return "prefixes"
    if "dispo" in name or "disposition" in name:
        return "dispositions"
    if "call_type" in name or "call-type" in name or "calltype" in name or "type" in name:
        return "call_types"
    return None


def discover_remote_csv_specs(ftp: FTP) -> list[tuple[str, str | None]]:
    discovered: list[tuple[str, str | None]] = []
    for directory in ("", "descriptions"):
        try:
            names = ftp.nlst(directory or ".")
        except Exception:
            continue
        for name in names:
            remote_name = name.replace("\\", "/")
            base = posixpath.basename(remote_name)
            if not base.lower().endswith(".csv"):
                continue
            if CSV_NAME_SKIP_RE.search(base):
                continue
            if not CSV_NAME_HINT_RE.search(base):
                continue
            if directory and "/" not in remote_name:
                remote_name = posixpath.join(directory, remote_name)
            discovered.append((remote_name, section_from_filename(base)))
    return discovered


def atomic_upload(ftp: FTP, local_path: Path, remote_name: str) -> None:
    temp_name = f"{remote_name}.deploy.{int(time.time())}.tmp"
    backup_name = f"{remote_name}.bak"
    with local_path.open("rb") as handle:
        ftp.storbinary(f"STOR {temp_name}", handle)
    try:
        ftp.delete(backup_name)
    except Exception:
        pass
    try:
        ftp.rename(remote_name, backup_name)
    except Exception:
        pass
    ftp.rename(temp_name, remote_name)


def sync_from_ftp(output_path: Path, upload: bool) -> int:
    ftp, remote_dir = ftp_connect()
    merged: dict[str, dict[str, str]] = {"prefixes": {}, "dispositions": {}, "call_types": {}}
    sources: list[str] = []
    try:
        candidates = list(REMOTE_CANDIDATES)
        for spec in discover_remote_csv_specs(ftp):
            if spec not in candidates:
                candidates.append(spec)

        for remote_name, default_section in candidates:
            text = retr_text(ftp, remote_name)
            if text is None:
                continue
            parsed = parse_description_csv(text, default_section)
            if any(parsed[section] for section in parsed):
                merge_descriptions(merged, parsed)
                sources.append(remote_name)

        if not sources:
            print(f"No description CSV found in {remote_dir}")
            return 0

        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": sources,
            "prefixes": dict(sorted(merged["prefixes"].items())),
            "dispositions": dict(sorted(merged["dispositions"].items())),
            "call_types": dict(sorted(merged["call_types"].items())),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            "Imported {} CSV source(s): {} prefixes, {} dispositions, {} call types".format(
                len(sources),
                len(payload["prefixes"]),
                len(payload["dispositions"]),
                len(payload["call_types"]),
            )
        )
        if upload:
            atomic_upload(ftp, output_path, posixpath.basename(str(output_path)))
            print(f"Uploaded {output_path} -> {remote_dir}/{output_path.name}")
        return 0
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Import server CSV call-log descriptions into JSON.")
    parser.add_argument("--output", default="site/calllog_descriptions.json")
    parser.add_argument("--no-upload", action="store_true", help="Only write the local JSON file.")
    args = parser.parse_args()
    raise SystemExit(sync_from_ftp(Path(args.output), upload=not args.no_upload))


if __name__ == "__main__":
    main()

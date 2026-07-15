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

REMOTE_LIVE_JSON = "calllog_descriptions.json"
REMOTE_BACKUP_JSON = "calllog_descriptions.github_backup.json"
REMOTE_STATUS_JSON = "calllog_descriptions.status.json"
DEFAULT_STATUS_MODE = "authoritative_maintain_backup"
DEPLOY_DECISION_FILE = Path("runtime/calllog_descriptions_deploy.env")
STATUS_MODE_ALIASES = {
    "1": "authoritative_override_reverse",
    "override_reverse": "authoritative_override_reverse",
    "authoritative_override_reverse": "authoritative_override_reverse",
    "server_to_backup": "authoritative_override_reverse",
    "server_authoritative": "authoritative_override_reverse",
    "2": "authoritative_maintain_backup",
    "maintain_backup": "authoritative_maintain_backup",
    "authoritative_maintain_backup": "authoritative_maintain_backup",
    "backup_only": "authoritative_maintain_backup",
    "3": "restore_backup",
    "restore": "restore_backup",
    "restore_backup": "restore_backup",
    "backup_to_server": "restore_backup",
}
STATUS_MODE_LABELS = {
    "authoritative_override_reverse": "authoritative (override reverse)",
    "authoritative_maintain_backup": "authoritative (maintain backup)",
    "restore_backup": "restore backup",
}

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


def is_useful_description(value: str) -> bool:
    return value.strip().lower() not in {"", "unknown", "unk", "n/a", "na", "none", "null", "?"}


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
        if code and is_useful_description(description):
            payload[section][code.strip().upper()] = description.strip()

    return payload


def merge_descriptions(target: dict[str, dict[str, str]], update: dict[str, dict[str, str]]) -> None:
    for section in ("prefixes", "dispositions", "call_types"):
        target.setdefault(section, {})
        target[section].update(update.get(section, {}))


def normalize_status_mode(value: object) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return STATUS_MODE_ALIASES.get(key, DEFAULT_STATUS_MODE)


def retr_bytes(ftp: FTP, remote_name: str) -> bytes | None:
    chunks: list[bytes] = []
    try:
        ftp.retrbinary(f"RETR {remote_name}", chunks.append)
    except error_perm:
        return None
    return b"".join(chunks)


def retr_text(ftp: FTP, remote_name: str) -> str | None:
    data = retr_bytes(ftp, remote_name)
    if data is None:
        return None
    return data.decode("utf-8-sig", errors="replace")


def load_remote_json(ftp: FTP, remote_name: str) -> dict | None:
    text = retr_text(ftp, remote_name)
    if text is None or not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Warning: {remote_name} is not valid JSON: {exc}")
        return None
    return payload if isinstance(payload, dict) else None


def write_deploy_decision(allow_upload: bool, mode: str, reason: str) -> None:
    DEPLOY_DECISION_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEPLOY_DECISION_FILE.write_text(
        "\n".join(
            [
                f"CALLLOG_DESCRIPTIONS_UPLOAD={'1' if allow_upload else '0'}",
                f"CALLLOG_DESCRIPTIONS_MODE={mode}",
                f"CALLLOG_DESCRIPTIONS_REASON={reason}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def status_payload(mode: str, note: str = "") -> dict:
    return {
        "mode": mode,
        "mode_label": STATUS_MODE_LABELS.get(mode, mode),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
        "modes": {
            "authoritative_override_reverse": "Server live copy is authoritative and replaces the GitHub backup, then this status resets to authoritative_maintain_backup.",
            "authoritative_maintain_backup": "Server live copy is authoritative; GitHub maintains its backup but does not overwrite the server copy.",
            "restore_backup": "GitHub backup is restored to the server live copy, then this status resets to authoritative_maintain_backup.",
        },
    }


def upload_json(ftp: FTP, remote_name: str, payload: dict) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    remote_size = remote_file_size(ftp, remote_name)
    if remote_size is not None and remote_size > len(data):
        copy_remote_file(
            ftp,
            remote_name,
            f"{remote_name}.larger-before-override.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak",
        )
    ftp.storbinary(f"STOR {remote_name}", io.BytesIO(data))


def description_counts(payload: dict | None) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {"prefixes": 0, "dispositions": 0, "call_types": 0}
    counts: dict[str, int] = {}
    for section in ("prefixes", "dispositions", "call_types"):
        values = payload.get(section)
        counts[section] = len(values) if isinstance(values, dict) else 0
    return counts


def has_description_data(payload: dict | None) -> bool:
    counts = description_counts(payload)
    return sum(counts.values()) > 0


def write_payload(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_generated_payload(ftp: FTP, remote_dir: str) -> tuple[dict | None, list[str]]:
    merged: dict[str, dict[str, str]] = {"prefixes": {}, "dispositions": {}, "call_types": {}}
    sources: list[str] = []
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
        return None, sources

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": sources,
        "prefixes": dict(sorted(merged["prefixes"].items())),
        "dispositions": dict(sorted(merged["dispositions"].items())),
        "call_types": dict(sorted(merged["call_types"].items())),
    }, sources


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
    local_size = local_path.stat().st_size
    remote_size = remote_file_size(ftp, remote_name)
    if remote_size is not None and remote_size > local_size:
        copy_remote_file(
            ftp,
            remote_name,
            f"{remote_name}.larger-before-override.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak",
        )
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


def remote_file_size(ftp: FTP, remote_name: str) -> int | None:
    try:
        size = ftp.size(remote_name)
    except Exception:
        return None
    return int(size) if size is not None else None


def copy_remote_file(ftp: FTP, source_name: str, backup_name: str) -> bool:
    data = retr_bytes(ftp, source_name)
    if data is None:
        return False
    ftp.storbinary(f"STOR {backup_name}", io.BytesIO(data))
    print(f"Backed up larger target before override: {source_name} -> {backup_name} ({len(data)} bytes)")
    return True


def sync_from_ftp(output_path: Path, upload: bool) -> int:
    ftp, remote_dir = ftp_connect()
    try:
        status = load_remote_json(ftp, REMOTE_STATUS_JSON) or {}
        mode = normalize_status_mode(status.get("mode"))
        print(f"Description sync mode: {STATUS_MODE_LABELS.get(mode, mode)}")

        if mode == "restore_backup":
            backup_payload = load_remote_json(ftp, REMOTE_BACKUP_JSON)
            if not has_description_data(backup_payload):
                print(f"Restore skipped: {REMOTE_BACKUP_JSON} is missing or has no description data.")
                write_deploy_decision(False, mode, "restore_backup_missing_or_empty")
                return 0
            backup_payload["restored_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            backup_payload["restored_from"] = REMOTE_BACKUP_JSON
            write_payload(output_path, backup_payload)
            if upload:
                atomic_upload(ftp, output_path, REMOTE_LIVE_JSON)
                upload_json(
                    ftp,
                    REMOTE_STATUS_JSON,
                    status_payload(
                        DEFAULT_STATUS_MODE,
                        f"Restored {REMOTE_BACKUP_JSON} to {REMOTE_LIVE_JSON}; reset to maintain backup.",
                    ),
                )
                print(f"Restored {REMOTE_BACKUP_JSON} -> {remote_dir}/{REMOTE_LIVE_JSON}")
            write_deploy_decision(False, DEFAULT_STATUS_MODE, "restored_backup")
            return 0

        if mode == "authoritative_override_reverse":
            live_payload = load_remote_json(ftp, REMOTE_LIVE_JSON)
            if not has_description_data(live_payload):
                print(f"Reverse override skipped: {REMOTE_LIVE_JSON} is missing or has no description data.")
                write_deploy_decision(False, mode, "server_live_missing_or_empty")
                return 0
            live_payload["backed_up_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            live_payload["backup_source"] = REMOTE_LIVE_JSON
            write_payload(output_path, live_payload)
            if upload:
                atomic_upload(ftp, output_path, REMOTE_BACKUP_JSON)
                upload_json(
                    ftp,
                    REMOTE_STATUS_JSON,
                    status_payload(
                        DEFAULT_STATUS_MODE,
                        f"Copied {REMOTE_LIVE_JSON} to {REMOTE_BACKUP_JSON}; reset to maintain backup.",
                    ),
                )
                print(f"Backed up authoritative server copy -> {remote_dir}/{REMOTE_BACKUP_JSON}")
            write_deploy_decision(False, DEFAULT_STATUS_MODE, "server_live_backed_up")
            return 0

        payload, sources = build_generated_payload(ftp, remote_dir)
        if payload is None:
            write_deploy_decision(False, mode, "no_generated_descriptions")
            return 0

        write_payload(output_path, payload)
        print(
            "Imported {} CSV source(s): {} prefixes, {} dispositions, {} call types".format(
                len(sources),
                len(payload["prefixes"]),
                len(payload["dispositions"]),
                len(payload["call_types"]),
            )
        )
        if upload and has_description_data(payload):
            atomic_upload(ftp, output_path, REMOTE_BACKUP_JSON)
            if not status:
                upload_json(
                    ftp,
                    REMOTE_STATUS_JSON,
                    status_payload(DEFAULT_STATUS_MODE, "Initialized description sync status."),
                )
            print(f"Updated generated backup -> {remote_dir}/{REMOTE_BACKUP_JSON}")
        write_deploy_decision(False, mode, "maintained_backup_without_overwriting_server")
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

from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import time
from dataclasses import dataclass, asdict
from ftplib import FTP, error_perm
from pathlib import Path


DEFAULT_REMOTE_DIR = "/domains/upnexx.xyz/public_html/osint"
RUNTIME_DIR = Path(os.environ.get("SBCO_BASE_DIR", "runtime"))


@dataclass
class RemoteEntry:
    path: str
    type: str
    size: int | None = None
    modified: str | None = None


def env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def ftp_connect() -> FTP:
    host = env_value("SBCO_FTP_HOST", "SERV00_FTP_HOST", "FTP_HOST")
    user = env_value("SBCO_FTP_USER", "SERV00_FTP_USER", "FTP_USER")
    password = env_value("SBCO_FTP_PASS", "SERV00_FTP_PASS", "FTP_PASS")
    if not host or not user or not password:
        raise RuntimeError("FTP credentials are not configured")
    ftp = FTP()
    ftp.connect(host, timeout=int(os.environ.get("SBCO_FTP_TIMEOUT_SECONDS", "60")))
    ftp.login(user, password)
    ftp.cwd(env_value("SBCO_FTP_REMOTE_DIR") or DEFAULT_REMOTE_DIR)
    return ftp


def is_probably_file(ftp: FTP, path: str) -> bool:
    try:
        ftp.size(path)
        return True
    except Exception:
        return False


def list_dir(ftp: FTP, path: str = ".") -> list[RemoteEntry]:
    entries: list[RemoteEntry] = []
    try:
        facts = list(ftp.mlsd(path))
    except Exception:
        facts = []
        lines: list[str] = []
        ftp.dir(path, lines.append)
        for line in lines:
            name = line.split()[-1]
            if name in (".", ".."):
                continue
            child = posixpath.join(path, name) if path != "." else name
            entry_type = "file" if is_probably_file(ftp, child) else "dir"
            facts.append((name, {"type": entry_type}))

    for name, info in facts:
        if name in (".", ".."):
            continue
        child = posixpath.join(path, name) if path != "." else name
        entry_type = info.get("type") or ("file" if is_probably_file(ftp, child) else "dir")
        if entry_type == "dir":
            entries.append(RemoteEntry(path=child, type="dir"))
            entries.extend(list_dir(ftp, child))
        else:
            size = None
            try:
                size = int(info.get("size") or ftp.size(child) or 0)
            except Exception:
                pass
            entries.append(
                RemoteEntry(
                    path=child,
                    type="file",
                    size=size,
                    modified=info.get("modify"),
                )
            )
    return entries


def safe_local_path(root: Path, remote_path: str) -> Path:
    clean_parts = [part for part in remote_path.replace("\\", "/").split("/") if part and part not in (".", "..")]
    return root.joinpath(*clean_parts)


def download_file(ftp: FTP, remote_path: str, backup_root: Path) -> Path:
    local_path = safe_local_path(backup_root, remote_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("wb") as fh:
        ftp.retrbinary(f"RETR {remote_path}", fh.write)
    return local_path


def read_manifest(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files") if isinstance(payload, dict) else payload
    if not isinstance(files, list):
        raise ValueError("manifest must be a list or an object with a files list")
    clean = []
    for item in files:
        value = str(item).strip().replace("\\", "/")
        if not value or value.startswith("/") or ".." in value.split("/"):
            raise ValueError(f"unsafe remote path in manifest: {item!r}")
        clean.append(value)
    return clean


def write_inventory(entries: list[RemoteEntry]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RUNTIME_DIR / "osint_ftp_inventory.json"
    txt_path = RUNTIME_DIR / "osint_ftp_inventory.txt"
    json_path.write_text(json.dumps([asdict(entry) for entry in entries], indent=2) + "\n", encoding="utf-8")
    with txt_path.open("w", encoding="utf-8") as fh:
        for entry in sorted(entries, key=lambda item: item.path):
            size = "" if entry.size is None else str(entry.size)
            fh.write(f"{entry.type}\t{size}\t{entry.modified or ''}\t{entry.path}\n")
    print(f"Wrote {json_path} and {txt_path}")


def backup_files(ftp: FTP, files: list[str], label: str) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_root = RUNTIME_DIR / "osint_server_backups" / f"{timestamp}-{label}"
    for remote_path in files:
        print(f"Backing up {remote_path}")
        download_file(ftp, remote_path, backup_root)
    archive_base = backup_root.with_suffix("")
    shutil.make_archive(str(archive_base), "zip", backup_root)
    print(f"Backup archive: {archive_base}.zip")
    return backup_root


def delete_files(ftp: FTP, files: list[str]) -> list[dict[str, str]]:
    report: list[dict[str, str]] = []
    for remote_path in files:
        try:
            ftp.delete(remote_path)
            status = "deleted"
        except error_perm as exc:
            status = f"error_perm: {exc}"
        except Exception as exc:  # noqa: BLE001
            status = f"error: {exc}"
        print(f"{remote_path}: {status}")
        report.append({"path": remote_path, "status": status})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory, back up, or delete files in the OSINT FTP directory.")
    parser.add_argument("--mode", choices=["list", "backup", "delete"], default="list")
    parser.add_argument("--manifest", default="ops/osint_cleanup_manifest.json")
    parser.add_argument("--label", default="manual")
    args = parser.parse_args()

    ftp = ftp_connect()
    try:
        if args.mode == "list":
            write_inventory(list_dir(ftp))
            return

        manifest = Path(args.manifest)
        files = read_manifest(manifest)
        backup_root = backup_files(ftp, files, args.label)
        if args.mode == "delete":
            report = delete_files(ftp, files)
            report_path = backup_root / "delete_report.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            shutil.make_archive(str(backup_root.with_suffix("")), "zip", backup_root)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()

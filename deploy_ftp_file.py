from __future__ import annotations

import argparse
import io
import os
import time
from ftplib import FTP
from pathlib import Path


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


def remote_exists(ftp: FTP, remote_name: str) -> bool:
    try:
        ftp.size(remote_name)
        return True
    except Exception:
        return False


def remote_size(ftp: FTP, remote_name: str) -> int | None:
    try:
        size = ftp.size(remote_name)
    except Exception:
        return None
    return int(size) if size is not None else None


def backup_larger_remote_file(ftp: FTP, remote_name: str, local_size: int) -> None:
    size = remote_size(ftp, remote_name)
    if size is None or size <= local_size:
        return

    chunks: list[bytes] = []
    ftp.retrbinary(f"RETR {remote_name}", chunks.append)
    backup_name = f"{remote_name}.larger-before-override.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak"
    data = b"".join(chunks)
    ftp.storbinary(f"STOR {backup_name}", io.BytesIO(data))
    print(f"Backed up larger target before override: {remote_name} -> {backup_name} ({len(data)} bytes)")


def delete_if_exists(ftp: FTP, remote_name: str) -> None:
    try:
        ftp.delete(remote_name)
    except Exception:
        pass


def atomic_upload(local_path: Path, remote_name: str) -> None:
    ftp, remote_dir = ftp_connect()
    temp_name = f"{remote_name}.deploy.{int(time.time())}.tmp"
    backup_name = f"{remote_name}.bak"

    try:
        backup_larger_remote_file(ftp, remote_name, local_path.stat().st_size)
        with local_path.open("rb") as fh:
            ftp.storbinary(f"STOR {temp_name}", fh)

        delete_if_exists(ftp, backup_name)
        if remote_exists(ftp, remote_name):
            ftp.rename(remote_name, backup_name)
        ftp.rename(temp_name, remote_name)
        print(f"Uploaded {local_path} -> {remote_dir}/{remote_name}")
    finally:
        try:
            delete_if_exists(ftp, temp_name)
        except Exception:
            pass
        try:
            ftp.quit()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a local file to the configured FTP remote dir.")
    parser.add_argument("local_path", help="Path to the local file to upload")
    parser.add_argument("remote_name", help="Remote filename inside the FTP remote dir")
    args = parser.parse_args()

    local_path = Path(args.local_path).resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    atomic_upload(local_path, args.remote_name)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Refresh local arrest, release, and death support files without publishing.

This is the local companion to the GitHub scraper run. It keeps the data files
that power `calllog_arrest_index.json` current on this Windows box, while
leaving the live call-log publisher to Serv00/GitHub unless explicitly enabled
elsewhere.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


DEFAULT_BASE_DIR = Path.home() / "Documents" / "python"
DEFAULT_SERVER_CALLLOG_URL = "https://upnexx.xyz/osint/calllog.csv"


def configure_environment(base_dir: Path, force_refresh: bool) -> None:
    os.environ.setdefault("SBCO_BASE_DIR", str(base_dir))
    os.environ.setdefault("SBCO_SERVER_CALLLOG_URL", DEFAULT_SERVER_CALLLOG_URL)
    os.environ.setdefault("SBCO_REMOTE_BACKED_DAILY_FILES", "1")
    os.environ.setdefault("SBCO_ENABLE_DAILY_RELEASES", "1")
    os.environ.setdefault("SBCO_RUN_HARD_TIMEOUT_SECONDS", "1200")
    os.environ.setdefault("SBCO_DEATH_INDEX_REFRESH_TIMEOUT_SECONDS", "600")
    os.environ.setdefault("SBCO_ARREST_LOG_REFRESH_TIMEOUT_SECONDS", "600")
    os.environ.setdefault("SBCO_ARREST_LOG_REQUEST_DELAY_SECONDS", "2.0")
    os.environ.setdefault("SBCO_ARREST_LOG_MAX_PAGES", "3")
    if force_refresh:
        os.environ["SBCO_DAILY_REMOTE_FILE_FRESHNESS_HOURS"] = "0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        default=str(DEFAULT_BASE_DIR),
        help="Local runtime/data directory. Default: %(default)s",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh support files even when the public fallback copy is fresh.",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).expanduser().resolve()
    configure_environment(base_dir, args.force)

    import scraper_run  # noqa: PLC0415 - env must be set before importing scraper_run.

    scraper_run.ensure_dirs()
    started = time.monotonic()
    scraper_run.log("Local support refresh started")

    extra_specs = []
    extra_specs.extend(
        scraper_run.ensure_remote_backed_daily_file(
            "death_index.csv",
            scraper_run.DEATH_INDEX_CSV,
            scraper_run.refresh_death_index_csv,
            run_started_monotonic=started,
        )
    )
    extra_specs.extend(
        scraper_run.ensure_remote_backed_daily_file(
            "all_records.json",
            scraper_run.ARREST_LOG_JSON,
            scraper_run.refresh_arrest_log_json,
            run_started_monotonic=started,
        )
    )
    extra_specs.extend(
        scraper_run.ensure_remote_backed_daily_file(
            "releases.csv",
            scraper_run.RELEASES_CSV,
            scraper_run.refresh_releases_csv,
            run_started_monotonic=started,
        )
    )

    scraper_run.rebuild_calllog_arrest_index()
    scraper_run.log(
        "Local support refresh completed ({} refreshed files)".format(len(extra_specs))
    )
    scraper_run.log_phase_duration("local support refresh", started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


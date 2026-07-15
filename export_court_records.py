from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path(r"C:\Users\mark\Documents\python\sb_court_scraper\state\court_calendar.db")


def row_value(row: sqlite3.Row, key: str) -> str:
    value = row[key] if key in row.keys() else ""
    return str(value or "").strip()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def export_records(db_path: Path, output_path: Path, limit: int = 0) -> dict:
    query = """
        SELECT
            c.cap_case_id,
            c.case_number,
            c.case_type,
            c.style,
            c.file_date,
            c.status,
            c.court_location,
            c.assigned_judicial_officer_text,
            c.next_hearing,
            c.citation_number,
            c.is_criminal,
            c.first_seen_at,
            c.latest_seen_at,
            c.detail_scraped_at,
            (
                SELECT GROUP_CONCAT(DISTINCT cp.full_name)
                FROM case_parties cp
                WHERE cp.cap_case_id = c.cap_case_id
                  AND COALESCE(cp.full_name, '') <> ''
                  AND (cp.is_defendant = 1 OR UPPER(COALESCE(cp.party_type, '')) LIKE '%DEF%')
            ) AS defendants,
            (
                SELECT GROUP_CONCAT(DISTINCT ca.full_name)
                FROM case_aliases ca
                WHERE ca.cap_case_id = c.cap_case_id
                  AND COALESCE(ca.full_name, '') <> ''
            ) AS aliases,
            (
                SELECT GROUP_CONCAT(DISTINCT TRIM(COALESCE(cc.statute_raw, '') || ' ' || COALESCE(cc.offense_description, '')))
                FROM case_charges cc
                WHERE cc.cap_case_id = c.cap_case_id
                  AND TRIM(COALESCE(cc.statute_raw, '') || COALESCE(cc.offense_description, '')) <> ''
            ) AS charges,
            (
                SELECT MAX(COALESCE(ch.hearing_date, ''))
                FROM case_hearings ch
                WHERE ch.cap_case_id = c.cap_case_id
            ) AS latest_hearing_date,
            (
                SELECT COUNT(*)
                FROM case_events ce
                WHERE ce.cap_case_id = c.cap_case_id
            ) AS event_count
        FROM cases c
        ORDER BY COALESCE(c.file_date, '') DESC, c.case_number DESC
    """
    if limit > 0:
        query += f"\nLIMIT {int(limit)}"

    conn = connect(db_path)
    try:
        rows = []
        for row in conn.execute(query):
            rows.append(
                {
                    "cap_case_id": row_value(row, "cap_case_id"),
                    "caseNumber": row_value(row, "case_number"),
                    "caseType": row_value(row, "case_type"),
                    "caseName": row_value(row, "style"),
                    "name": row_value(row, "defendants") or row_value(row, "style"),
                    "aliases": row_value(row, "aliases"),
                    "fileDate": row_value(row, "file_date"),
                    "date": row_value(row, "file_date"),
                    "status": row_value(row, "status"),
                    "court": row_value(row, "court_location"),
                    "judge": row_value(row, "assigned_judicial_officer_text"),
                    "nextHearing": row_value(row, "next_hearing"),
                    "latestHearingDate": row_value(row, "latest_hearing_date"),
                    "citationNumber": row_value(row, "citation_number"),
                    "charge": row_value(row, "charges"),
                    "eventCount": int(row["event_count"] or 0),
                    "isCriminal": bool(row["is_criminal"]),
                    "firstSeenAt": row_value(row, "first_seen_at"),
                    "latestSeenAt": row_value(row, "latest_seen_at"),
                    "detailScrapedAt": row_value(row, "detail_scraped_at"),
                }
            )
    finally:
        conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(db_path),
        "count": len(rows),
        "records": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export public court-record summaries from the local court SQLite DB.")
    parser.add_argument("--db", default=os.environ.get("SBCO_COURT_DB", str(DEFAULT_DB)))
    parser.add_argument("--output", default="runtime/court_records.json")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    payload = export_records(Path(args.db), Path(args.output), limit=args.limit)
    print(f"Exported {payload['count']:,} court records -> {args.output}")


if __name__ == "__main__":
    main()

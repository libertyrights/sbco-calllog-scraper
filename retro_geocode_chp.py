"""Retroactively geocode existing CHP calls in calllog_check2.csv.
Only calls with GPS coords in extra_json.map will be geocoded.
Nominatim rate limit: 1 req/sec."""
import csv
import json
import sys
import time

sys.path.insert(0, r"C:\Users\mark\Documents\python\misc")
from chp_scraper import reverse_geocode, parse_highway_info

CSV_PATH = r"C:\Users\mark\Documents\python\calllog\calllog_check2.csv"

with open(CSV_PATH, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

updated = 0
skipped = 0
for r in rows:
    if r.get("agency") != "CHP":
        continue
    ej = r.get("extra_json", "")
    if not ej:
        skipped += 1
        continue
    try:
        extra = json.loads(ej)
    except Exception:
        skipped += 1
        continue
    if "map" not in extra:
        skipped += 1
        continue
    if extra.get("address_resolved"):
        print("Already geocoded, skipping:", r.get("call number", ""))
        continue

    lat = extra["map"].get("lat")
    lon = extra["map"].get("lon")
    if not lat or not lon:
        skipped += 1
        continue

    result = reverse_geocode(lat, lon)
    if result:
        extra["address_resolved"] = result["address"]
        extra["road_name"] = result.get("road", "")
        hw = parse_highway_info(r.get("location", ""))
        if hw:
            extra["highway_info"] = hw
        r["extra_json"] = json.dumps(extra)
        updated += 1
        loc = r.get("location", "")
        addr = result["address"]
        print("Geocoded: %s -> %s" % (loc[:50], addr[:80]))
        time.sleep(1.1)
    else:
        print("Failed:", r.get("location", ""))
        time.sleep(1.1)

with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print("Done. Updated %d, skipped %d CHP calls." % (updated, skipped))

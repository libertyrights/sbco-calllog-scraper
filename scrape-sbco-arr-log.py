#!/usr/bin/env python3
import argparse
import calendar
import json
import os
import re
import sys
import time
from typing import List, Dict, Optional, Iterable

import requests
from bs4 import BeautifulSoup
from requests import HTTPError


PAGE_URL = "https://sb-american.com/arrest-records-for-san-bernardino-county-updated-daily/"
EMBEDDED_FEED_URLS = [
    "https://vms.unitedreporting.com/index.php/reports/showArrests/30690/1",
    "https://vms.unitedreporting.com/index.php/reports/showArrests/30690/2",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
DEFAULT_OUTPUT_JSON = "/var/log/scrapers/sbco-arr-log/latest.json"


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def load_proxy_list(path: Optional[str]) -> List[str]:
    if not path or not os.path.exists(path):
        return []
    proxies = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            proxies.append(line)
    return proxies


def normalize_proxy(proxy: str) -> Dict[str, str]:
    proxy = proxy.strip()
    if "://" not in proxy:
        proxy = "http://" + proxy
    return {"http": proxy, "https": proxy}


def fetch_via_proxy(url: str, referer: Optional[str], proxy: str) -> str:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    response = requests.get(url, headers=headers, proxies=normalize_proxy(proxy), timeout=20)
    response.raise_for_status()
    return response.text


def fetch_html(
    session: requests.Session,
    url: str,
    referer: str = None,
    proxy_candidates: Optional[Iterable[str]] = None,
    retries: int = 3,
    retry_delay: float = 2.0,
    request_delay: float = 0.0,
) -> str:
    headers = {}
    if referer:
        headers["Referer"] = referer
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            if request_delay > 0:
                time.sleep(request_delay)
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            return response.text
        except HTTPError as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else None
            last_error = exc
            if status in (403, 429) and proxy_candidates:
                break
            if attempt + 1 >= max(1, retries):
                raise
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 >= max(1, retries) and not proxy_candidates:
                raise
        if attempt + 1 < max(1, retries):
            time.sleep(retry_delay * (attempt + 1))

    for proxy in proxy_candidates or []:
        try:
            if request_delay > 0:
                time.sleep(request_delay)
            return fetch_via_proxy(url, referer, proxy)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed and no usable proxy succeeded")


def extract_embedded_urls(page_html: str) -> List[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    urls = []
    for obj in soup.find_all("object"):
        data = (obj.get("data") or "").strip()
        if "vms.unitedreporting.com" in data and data not in urls:
            urls.append(data)
    return urls


def expand_paginated_feed_urls(feed_urls: List[str], max_pages: int) -> List[str]:
    expanded: List[str] = []
    seen = set()
    for url in feed_urls:
        match = re.match(r"^(https://vms\.unitedreporting\.com/index\.php/reports/showArrests/\d+)/(\d+)$", url)
        if not match:
            if url not in seen:
                expanded.append(url)
                seen.add(url)
            continue
        base = match.group(1)
        for page_num in range(1, max_pages + 1):
            paged_url = f"{base}/{page_num}"
            if paged_url not in seen:
                expanded.append(paged_url)
                seen.add(paged_url)
    return expanded


def parse_age_from_name(name_text: str):
    match = re.search(r"\s*-\s*(\d+)\s*$", name_text)
    if not match:
        return name_text.strip(), None
    return name_text[: match.start()].strip(), int(match.group(1))


def parse_arrest_date_value(value: Optional[str]) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return int(calendar.timegm(time.strptime(text, fmt)))
        except ValueError:
            continue
    return 0


def detail_id_from_url(url: Optional[str]) -> int:
    if not url:
        return 0
    match = re.search(r"/detail/(\d+)/", url)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def record_sort_key(record: Dict):
    details = record.get("details") or {}
    arrest_date = (
        details.get("arrest_date_full")
        or record.get("arrest_date")
        or ""
    )
    epoch = parse_arrest_date_value(arrest_date)
    detail_id = detail_id_from_url(record.get("detail_url"))
    name = (record.get("name") or "").lower()
    charge = (record.get("charge") or "").lower()
    return (-epoch, -detail_id, name, charge)


def sort_records(records: List[Dict]) -> List[Dict]:
    return sorted(records, key=record_sort_key)


def parse_record_triplets(feed_html: str, source_url: str) -> List[Dict]:
    soup = BeautifulSoup(feed_html, "html.parser")
    elements = soup.find_all(["span", "a"])
    records = []
    i = 0
    while i < len(elements):
        el = elements[i]
        if el.name != "span":
            i += 1
            continue

        name_line = " ".join(el.get_text(" ", strip=True).split())
        if " - " not in name_line:
            i += 1
            continue

        if i + 2 >= len(elements):
            break

        date_el = elements[i + 1]
        charge_el = elements[i + 2]
        if date_el.name != "span" or charge_el.name != "span":
            i += 1
            continue

        date_text = " ".join(date_el.get_text(" ", strip=True).split())
        charge_text = " ".join(charge_el.get_text(" ", strip=True).split())
        detail_url = None
        for candidate in elements[i + 1 : min(i + 8, len(elements))]:
            if candidate.name != "a":
                continue
            href = clean_text(candidate.get("href"))
            if not href:
                continue
            href_lower = href.lower()
            anchor_text = clean_text(candidate.get_text(" ", strip=True)).lower()
            if "localcrimenews.com" in href_lower or "/welcome/detail/" in href_lower or "view details" in anchor_text:
                detail_url = href
                break

        full_name, age = parse_age_from_name(name_line)
        arrest_date = date_text.replace("Arrested on ", "", 1).strip()
        records.append(
            {
                "name": full_name,
                "age": age,
                "arrest_date": arrest_date,
                "charge": charge_text,
                "detail_url": detail_url,
                "source_url": source_url,
            }
        )
        i += 4
    return records


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def first_nonempty(mapping: Dict[str, str], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        value = clean_text(mapping.get(key))
        if value:
            return value
    return None


def extract_label_value_rows(soup: BeautifulSoup, heading_text: str) -> Dict[str, str]:
    heading = soup.find(
        lambda tag: tag.name in ("td", "th", "h1", "h2", "h3", "h4")
        and clean_text(tag.get_text(" ", strip=True)).lower() == heading_text.lower()
    )
    if not heading:
        return {}
    table = heading.find_parent("table")
    if not table:
        return {}

    rows: Dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        vals = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        vals = [v for v in vals if v]
        if len(vals) == 2:
            key, value = vals
            if key.lower() != heading_text.lower():
                rows[key] = value
    return rows


def extract_prior_history(soup: BeautifulSoup) -> List[Dict[str, str]]:
    header_row = soup.find(
        lambda tag: tag.name == "tr"
        and ["Arrested For:", "By:", "Date:"] == [
            clean_text(cell.get_text(" ", strip=True))
            for cell in tag.find_all(["th", "td"])
        ]
    )
    if not header_row:
        return []

    table = header_row.find_parent("table")
    if not table:
        return []

    history = []
    started = False
    for tr in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if cells == ["Arrested For:", "By:", "Date:"]:
            started = True
            continue
        if not started:
            continue
        if len(cells) != 3:
            continue
        history.append(
            {
                "charge": cells[0],
                "agency": cells[1],
                "date": cells[2],
            }
        )
    return history


def fetch_detail(
    session: requests.Session,
    detail_url: str,
    referer: str,
    proxy_candidates: Optional[Iterable[str]] = None,
    retries: int = 3,
    retry_delay: float = 2.0,
    request_delay: float = 0.0,
) -> Dict:
    html = fetch_html(
        session,
        detail_url,
        referer=referer,
        proxy_candidates=proxy_candidates,
        retries=retries,
        retry_delay=retry_delay,
        request_delay=request_delay,
    )
    soup = BeautifulSoup(html, "html.parser")

    citizen = extract_label_value_rows(soup, "Citizen Details")
    arrest = extract_label_value_rows(soup, "Arrest Details")
    priors = extract_prior_history(soup)

    return {
        "detail_url": detail_url,
        "address": citizen.get("Address"),
        "city_state": citizen.get("City, State"),
        "age_gender": citizen.get("Age / Gender"),
        "race": citizen.get("Race"),
        "hair_eyes": citizen.get("Hair / Eyes"),
        "height_weight": citizen.get("Height / Weight"),
        "arrested_for": arrest.get("Arrested For"),
        "arrest_date_full": arrest.get("Arrest Date"),
        "arrest_time": arrest.get("Arrest Time") or arrest.get("Time"),
        "arrest_date_time": arrest.get("Arrest Date / Time") or arrest.get("Arrest Date/Time"),
        "release_date": arrest.get("Release Date"),
        "bail_amount": arrest.get("Bail Amount"),
        "arrest_location": arrest.get("Arrest Location"),
        "county_of_arrest": arrest.get("County of Arrest"),
        "booking_time": arrest.get("Booking Time"),
        "booking_date_time": arrest.get("Booking Date / Time") or arrest.get("Booking Date/Time"),
        "cad_number": first_nonempty(
            arrest,
            [
                "CAD Number",
                "Cad Number",
                "CAD #",
                "Cad #",
                "CAD",
            ],
        ),
        "linked_cad_number": first_nonempty(
            arrest,
            [
                "Linked CAD Number",
                "Linked Cad Number",
                "Linked CAD #",
                "Linked Cad #",
                "Linked CAD",
            ],
        ),
        "call_number": first_nonempty(
            arrest,
            [
                "Call Number",
                "Linked Call Number",
                "Event Number",
            ],
        ),
        "report_number": first_nonempty(
            arrest,
            [
                "Report Number",
                "Report #",
                "DR Number",
                "DR #",
                "Case Number",
            ],
        ),
        "linked_report_number": first_nonempty(
            arrest,
            [
                "Linked Report Number",
                "Linked Report #",
                "Incident Number",
                "Incident #",
            ],
        ),
        "source_agency": arrest.get("Source"),
        "prior_arrests": priors,
    }


def fetch_records(
    session: requests.Session,
    page_url: str,
    enrich: bool = True,
    proxy_candidates: Optional[Iterable[str]] = None,
    retries: int = 3,
    retry_delay: float = 2.0,
    request_delay: float = 0.25,
    max_pages: int = 12,
    max_empty_pages: int = 3,
) -> List[Dict]:
    try:
        page_html = fetch_html(
            session,
            page_url,
            proxy_candidates=proxy_candidates,
            retries=retries,
            retry_delay=retry_delay,
            request_delay=request_delay,
        )
        embedded_urls = extract_embedded_urls(page_html)
    except HTTPError as exc:
        response = getattr(exc, "response", None)
        if response is not None and response.status_code == 429:
            embedded_urls = list(EMBEDDED_FEED_URLS)
        else:
            raise

    if not embedded_urls:
        embedded_urls = list(EMBEDDED_FEED_URLS)

    all_records = []
    seen_detail_urls = set()
    empty_pages = 0
    for url in expand_paginated_feed_urls(embedded_urls, max_pages=max_pages):
        feed_html = fetch_html(
            session,
            url,
            referer=page_url,
            proxy_candidates=proxy_candidates,
            retries=retries,
            retry_delay=retry_delay,
            request_delay=request_delay,
        )
        if "Unauthorized Feed" in feed_html:
            raise RuntimeError(
                "Embedded arrest feed returned 'Unauthorized Feed'. "
                "Session/referer handling may have changed."
            )
        records = parse_record_triplets(feed_html, url)
        unique_records = []
        for rec in records:
            detail_key = rec.get("detail_url") or f"{rec.get('name')}|{rec.get('arrest_date')}|{rec.get('charge')}"
            if detail_key in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_key)
            unique_records.append(rec)

        if not unique_records:
            empty_pages += 1
            if empty_pages >= max_empty_pages:
                break
            continue

        empty_pages = 0
        if enrich:
            for rec in unique_records:
                if rec.get("detail_url"):
                    try:
                        rec["details"] = fetch_detail(
                            session,
                            rec["detail_url"],
                            referer=url,
                            proxy_candidates=proxy_candidates,
                            retries=retries,
                            retry_delay=retry_delay,
                            request_delay=request_delay,
                        )
                    except Exception as exc:
                        rec["details_error"] = str(exc)
        all_records.extend(unique_records)
    return sort_records(all_records)


def print_records(records: List[Dict], as_json: bool):
    if as_json:
        json.dump(records, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    print("Found %d entries" % len(records))
    for idx, rec in enumerate(records, 1):
        parts = [f"{idx}. {rec['name']}"]
        if rec.get("age") is not None:
            parts.append("age %s" % rec["age"])
        parts.append("arrested %s" % rec["arrest_date"])
        print(" | ".join(parts))
        print("   %s" % rec["charge"])
        if rec.get("detail_url"):
            print("   %s" % rec["detail_url"])
        details = rec.get("details") or {}
        if details:
            for key in (
                "address",
                "city_state",
                "age_gender",
                "race",
                "hair_eyes",
                "height_weight",
                "arrested_for",
                "arrest_date_full",
                "release_date",
                "bail_amount",
                "arrest_location",
                "county_of_arrest",
                "source_agency",
            ):
                value = details.get(key)
                if value:
                    print("   %s: %s" % (key, value))
            priors = details.get("prior_arrests") or []
            if priors:
                print("   prior_arrests: %d" % len(priors))
                for prior in priors[:5]:
                    print("     - %(date)s | %(agency)s | %(charge)s" % prior)
                if len(priors) > 5:
                    print("     ... %d more" % (len(priors) - 5))


def write_json_output(path: str, records: List[Dict], source_url: str):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_url": source_url,
        "count": len(records),
        "records": records,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="Scrape SBCO arrest log entries.")
    parser.add_argument("--url", default=PAGE_URL)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--list-only", action="store_true", help="skip detail-page enrichment")
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON, help="save JSON payload to this path")
    parser.add_argument("--proxy-list-file", default=None, help="optional file of one proxy per line for 403/429 fallback")
    parser.add_argument("--retries", type=int, default=3, help="direct-fetch retry count before failing over")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="base delay between retries in seconds")
    parser.add_argument("--request-delay", type=float, default=0.25, help="delay between HTTP requests in seconds")
    parser.add_argument("--max-pages", type=int, default=12, help="maximum numbered feed pages to probe")
    parser.add_argument("--max-empty-pages", type=int, default=3, help="stop after this many empty pages in a row")
    args = parser.parse_args()

    session = build_session()
    proxy_candidates = load_proxy_list(args.proxy_list_file)
    records = fetch_records(
        session,
        args.url,
        enrich=not args.list_only,
        proxy_candidates=proxy_candidates,
        retries=args.retries,
        retry_delay=args.retry_delay,
        request_delay=args.request_delay,
        max_pages=args.max_pages,
        max_empty_pages=args.max_empty_pages,
    )
    if args.output_json:
        write_json_output(args.output_json, records, args.url)
    print_records(records, args.json)


if __name__ == "__main__":
    main()

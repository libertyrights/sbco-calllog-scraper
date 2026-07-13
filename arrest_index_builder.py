from __future__ import annotations

import csv
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CALLLOG_CSV = BASE_DIR / "calllog.csv"
OUTPUT_JSON = BASE_DIR / "calllog_arrest_index.json"
CACHE_DIR = BASE_DIR / ".cache" / "localcrimenews"
CITY_MAP_PATH = BASE_DIR / "city_map.json"
DOWNLOADED_ARREST_LOG_NAME = "all_records.json"
DOWNLOADED_DEATH_INDEX_NAME = "death_index.csv"
DOWNLOADED_RELEASE_NAMES = ("releases.csv", "daily_release_list.csv")

LCN_BASE_URL = "https://www.localcrimenews.com"
LCN_MAP_URL = f"{LCN_BASE_URL}/welcome/ArrestMap"
LCN_MAP_ENDPOINT = f"{LCN_BASE_URL}/index.php/welcome/getarrestslocation"
LCN_DETAIL_URL = f"{LCN_BASE_URL}/welcome/detail/{{arrest_id}}"
LCN_SBSD_AGENCY_URL = f"{LCN_BASE_URL}/welcome/agencyarrests/418/San_Bernardino_County_Sheriff"
DEATH_REGISTER_PAGE_URL = "https://xcore.sbcounty.gov/sheriff/SheriffCMS/DeathRegister"
DEATH_REGISTER_GRID_URL = f"{DEATH_REGISTER_PAGE_URL}/DataGrid"

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_BACKOFF_SECONDS = 1.5

LOOKBACK_DAYS = 14
MAP_CACHE_TTL_SECONDS = 20 * 60
DETAIL_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
AGENCY_CACHE_TTL_SECONDS = 20 * 60
AGENCY_PAGES_PER_RUN = 3
DEATH_REGISTER_CACHE_TTL_SECONDS = 6 * 60 * 60
CORONER_ASSOCIATION_MAX_MINUTES = 3 * 60
ARRAIGNMENT_CUSTODY_HOURS = 72
RELEASE_MATCH_MAX_DAYS = 10

MAX_DETAIL_CANDIDATES_PER_CALL = 8
MAX_MATCHES_PER_CALL = 1
MIN_ROUGH_SCORE = 3
MIN_FINAL_SCORE = 7

ARREST_DISPOSITIONS = {"ARR", "WAR", "ABA", "CIA"}
CORONER_SOURCE_CALL_TYPES = {"DB"}
SMALL_TOWN_DATE_MATCH_TOWNS = {"daggett", "newberry springs", "yermo"}
CONDITIONAL_DATE_MATCH_TOWNS = {"barstow"}

DEFAULT_CITY_MAP = {
    "BAK": "Baker",
    "BAR": "Barstow",
    "CMA": "Cima",
    "DAG": "Daggett",
    "FTI": "Fort Irwin",
    "HEL": "Helendale",
    "HNK": "Hinkley",
    "HRV": "Newberry Springs",
    "KEL": "Kelso",
    "KRJ": "Kramer Junction",
    "LEN": "Lenwood",
    "LUD": "Ludlow",
    "MTP": "Mountain Pass",
    "NA": "Not Available",
    "NBD": "Newberry Springs",
    "YER": "Yermo",
}

TOWN_CENTERS = {
    "barstow": {"lat": "34.8958", "long": "-117.0173"},
    "lenwood": {"lat": "34.8843", "long": "-117.1058"},
    "yermo": {"lat": "34.9055", "long": "-116.8380"},
    "newberry springs": {"lat": "34.8272", "long": "-116.6883"},
    "daggett": {"lat": "34.8623", "long": "-116.8881"},
    "hinkley": {"lat": "34.9397", "long": "-117.1884"},
    "ludlow": {"lat": "34.7189", "long": "-116.1631"},
    "baker": {"lat": "35.2650", "long": "-116.0740"},
    "mountain pass": {"lat": "35.4661", "long": "-115.5397"},
    "cima": {"lat": "35.2360", "long": "-115.5098"},
    "kramer junction": {"lat": "34.9922", "long": "-117.5608"},
    "helendale": {"lat": "34.7411", "long": "-117.3239"},
}

DEFAULT_BARSTOW_REGION_CENTERS = {
    "barstow",
    "lenwood",
    "yermo",
    "newberry springs",
    "baker",
}

STOPWORDS = {
    "AND",
    "AT",
    "AVE",
    "BLVD",
    "CT",
    "DR",
    "E",
    "FWY",
    "HIGHWAY",
    "HWY",
    "I",
    "N",
    "NA",
    "NOT",
    "OFRP",
    "ONRP",
    "PROVIDED",
    "RD",
    "S",
    "ST",
    "STATE",
    "W",
}

SBSD_SOURCE_HINTS = {
    "san bernardino county sheriff",
    "san bernardino county sd",
}

DEATH_REGISTER_COLUMNS = [
    ("caseNumberDisplay", "CaseNumberDisplay"),
    ("decedentName", "DecedentName"),
    ("decedentCity", "DecedentCity"),
    ("dateOfDeath", "DateOfDeath"),
    ("placeOfDeath", "PlaceOfDeath"),
    ("injuryCity", "InjuryCity"),
    ("podCity", "PodCity"),
    ("ageDisplay", "AgeDisplay"),
    ("sex", "Sex"),
    ("caseYear", "CaseYear"),
    ("currentMode", "CurrentMode"),
]


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_tag(value: Any) -> str:
    text = normalize_space(value).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = normalize_space(text)
    aliases = {
        "harvard": "newberry springs",
        "hrv": "newberry springs",
        "not available": "",
        "na": "",
    }
    return aliases.get(text, text)


def canonical_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_code_variants(value: Any) -> set[str]:
    text = normalize_space(value).upper()
    if not text:
        return set()
    matches = re.findall(r"\b(?:PC|VC|HS)?\s*\d{2,5}(?:\.\d+)?(?:\s*\([A-Z0-9]+\))*", text)
    if not matches:
        matches = [text]
    out: set[str] = set()
    for match in matches:
        base = canonical_code(match)
        if not base or not any(ch.isdigit() for ch in base):
            continue
        out.add(base)
        for prefix in ("PC", "VC", "HS"):
            if base.startswith(prefix) and len(base) > len(prefix):
                out.add(base[len(prefix):])
    return out


def extract_code_tokens(value: Any) -> set[str]:
    return extract_code_variants(value)


def score_code_match(left_codes: set[str], right_codes: set[str]) -> tuple[int, str | None]:
    left = {canonical_code(code) for code in left_codes if canonical_code(code)}
    right = {canonical_code(code) for code in right_codes if canonical_code(code)}
    if left & right:
        return 4, "charge_code_match"
    for left_code in left:
        for right_code in right:
            shorter, longer = sorted((left_code, right_code), key=len)
            if len(shorter) < 3 or not longer.startswith(shorter):
                continue
            if shorter[-1].isdigit() and len(longer) > len(shorter):
                return 2, "charge_code_root_match"
    return 0, None


def parse_call_datetime(value: Any) -> datetime | None:
    text = normalize_space(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return None


def parse_arrest_date(value: Any) -> datetime | None:
    text = normalize_space(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_coroner_report_number(value: Any) -> tuple[str, int] | None:
    text = normalize_space(value).upper()
    match = re.match(r"^COR(\d{2})(\d{4,5})$", text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def derive_death_register_case_display(case_year_2: str, case_number: int) -> str:
    return f"70{case_year_2}{int(case_number):05d}"


def date_key(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def canonical_person_fragment(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_space(value).upper())


def normalize_person_name_key(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "," in text:
        last_name, remainder = text.split(",", 1)
        first_name = normalize_space(remainder).split(" ")[0] if normalize_space(remainder) else ""
    else:
        parts = [part for part in normalize_space(text).split(" ") if part]
        if len(parts) < 2:
            return canonical_person_fragment(text)
        first_name = parts[0]
        last_name = parts[-1]
    last_key = canonical_person_fragment(last_name)
    first_key = canonical_person_fragment(first_name)
    return "{}|{}".format(last_key, first_key) if last_key and first_key else last_key or first_key


def parse_gender_code(value: Any) -> str:
    text = normalize_space(value).upper()
    if "/" in text:
        text = normalize_space(text.split("/")[-1]).upper()
    if text.startswith("M"):
        return "M"
    if text.startswith("F"):
        return "F"
    return ""


def nth_weekday_of_month(year: int, month: int, weekday: int, ordinal: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    current += timedelta(days=7 * max(0, ordinal - 1))
    return current


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def observed_judicial_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def judicial_holiday_dates(year: int) -> tuple[date, ...]:
    thanksgiving = nth_weekday_of_month(year, 11, 3, 4)
    holidays = {
        observed_judicial_holiday(date(year, 1, 1)),
        nth_weekday_of_month(year, 1, 0, 3),
        observed_judicial_holiday(date(year, 2, 12)),
        nth_weekday_of_month(year, 2, 0, 3),
        observed_judicial_holiday(date(year, 3, 31)),
        last_weekday_of_month(year, 5, 0),
        observed_judicial_holiday(date(year, 6, 19)),
        observed_judicial_holiday(date(year, 7, 4)),
        nth_weekday_of_month(year, 9, 0, 1),
        nth_weekday_of_month(year, 9, 4, 4),
        observed_judicial_holiday(date(year, 11, 11)),
        thanksgiving,
        thanksgiving + timedelta(days=1),
        observed_judicial_holiday(date(year, 12, 25)),
    }
    return tuple(sorted(holidays))


def is_judicial_holiday(day: date) -> bool:
    if day.weekday() in {5, 6}:
        return True
    for year in (day.year - 1, day.year, day.year + 1):
        if day in judicial_holiday_dates(year):
            return True
    return False


def add_court_business_hours(start_dt: datetime, hours: int = ARRAIGNMENT_CUSTODY_HOURS) -> tuple[datetime, list[str]]:
    current = start_dt
    counted = 0
    skipped_dates: set[str] = set()
    while counted < max(0, hours):
        current += timedelta(hours=1)
        if is_judicial_holiday(current.date()):
            skipped_dates.add(current.date().isoformat())
            continue
        counted += 1
    return current, sorted(skipped_dates)


def base_call_number(call_number: Any) -> str:
    text = normalize_space(call_number)
    if "." not in text:
        return text
    stem, suffix = text.rsplit(".", 1)
    return stem if suffix.isdigit() else text


def revision_number(call_number: Any) -> int:
    text = normalize_space(call_number)
    if "." not in text:
        return 0
    suffix = text.rsplit(".", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def call_base_from_linked_value(value: Any) -> str:
    text = normalize_space(value).upper()
    if not text:
        return ""
    match = re.search(r"\b([A-Z]{2}\d{9})(?:\.\d+)?\b", text)
    if match:
        return base_call_number(match.group(1))
    return ""


def normalize_report_number(value: Any) -> str:
    return normalize_space(value).upper()


def location_suffix(location: Any) -> str:
    text = normalize_space(location)
    if "," not in text:
        return ""
    return normalize_space(text.rsplit(",", 1)[-1]).upper()


def extract_location_tokens(location: Any) -> list[str]:
    left = normalize_space(location).split(",", 1)[0].upper()
    tokens = []
    for token in re.findall(r"[A-Z0-9]+", left):
        if token in STOPWORDS:
            continue
        if token.count("X") >= 3:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        tokens.append(token)
    return sorted(set(tokens))


def has_specific_location(location: Any) -> bool:
    text = normalize_space(location)
    if text in {"", "Not Available", "* ,*", "*,*", "*", "Not Provided, NA"}:
        return False
    return bool(extract_location_tokens(text))


def is_arrest_like(row: dict[str, Any]) -> bool:
    dispo = normalize_space(row.get("disposition")).upper()
    call_type = normalize_space(row.get("call type")).upper()
    if dispo in ARREST_DISPOSITIONS:
        return True
    if call_type == "ARR" or call_type.endswith("ARR"):
        return True
    return False


def load_city_map() -> dict[str, str]:
    mapping = dict(DEFAULT_CITY_MAP)
    if CITY_MAP_PATH.exists():
        try:
            raw = json.loads(CITY_MAP_PATH.read_text(encoding="utf-8"))
            for key, value in raw.items():
                mapping[str(key).upper()] = normalize_space(value)
        except Exception:
            pass
    return mapping


def resolve_call_town(location: Any, city_map: dict[str, str]) -> tuple[str, str]:
    suffix = location_suffix(location)
    town = normalize_tag(city_map.get(suffix, ""))
    return town, suffix


def normalize_call_row_source(row: dict[str, Any]) -> tuple[str, str]:
    agency = normalize_space(row.get("agency"))
    station = normalize_space(row.get("station"))
    if station:
        return agency, station
    if agency in SBSD_SOURCE_HINTS or agency in {"SBSO", "CHP", "CALFIRE", "CAL FIRE", "BLM"}:
        return agency, ""
    return "SBSO" if agency else "", agency


def load_recent_arrest_calls(calllog_path: Path) -> list[dict[str, Any]]:
    city_map = load_city_map()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with calllog_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dt = parse_call_datetime(row.get("date/time"))
            if not dt:
                continue
            agency, station = normalize_call_row_source(row)
            entry = {
                "date/time": normalize_space(row.get("date/time")),
                "agency": agency,
                "station": station,
                "call number": normalize_space(row.get("call number")),
                "report number": normalize_space(row.get("report number")),
                "call type": normalize_space(row.get("call type")),
                "disposition": normalize_space(row.get("disposition")),
                "location": normalize_space(row.get("location")),
                "revision_scraped_at": normalize_space(row.get("revision_scraped_at")),
                "_dt": dt,
                "_rev": revision_number(row.get("call number")),
            }
            grouped[base_call_number(entry["call number"])].append(entry)

    cutoff = now_local().replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)
    calls: list[dict[str, Any]] = []
    for base, chain in grouped.items():
        chain.sort(key=lambda item: (item["_rev"], item["_dt"]))
        latest = chain[-1]
        if latest["_dt"] < cutoff:
            continue
        if not any(is_arrest_like(item) for item in chain):
            continue
        town, suffix = resolve_call_town(latest["location"], city_map)
        calls.append(
            {
                "base_call_number": base,
                "call_number": latest["call number"],
                "agency": latest["agency"],
                "station": latest["station"],
                "date_time": latest["date/time"],
                "report_number": latest["report number"],
                "call_type": latest["call type"],
                "disposition": latest["disposition"],
                "location": latest["location"],
                "call_dt": latest["_dt"],
                "call_date_key": date_key(latest["_dt"]),
                "call_town": town,
                "call_suffix": suffix,
                "call_prefix": base[:2].upper(),
                "location_tokens": extract_location_tokens(latest["location"]),
                "has_specific_location": has_specific_location(latest["location"]),
                "call_code_variants": sorted(extract_code_variants(latest["call type"])),
            }
        )
    calls.sort(key=lambda item: (item["call_dt"], item["base_call_number"]), reverse=True)
    return calls


def load_recent_coroner_calls(calllog_path: Path) -> list[dict[str, Any]]:
    city_map = load_city_map()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with calllog_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dt = parse_call_datetime(row.get("date/time"))
            if not dt:
                continue
            agency, station = normalize_call_row_source(row)
            entry = {
                "date/time": normalize_space(row.get("date/time")),
                "agency": agency,
                "station": station,
                "call number": normalize_space(row.get("call number")),
                "report number": normalize_space(row.get("report number")),
                "call type": normalize_space(row.get("call type")),
                "disposition": normalize_space(row.get("disposition")),
                "location": normalize_space(row.get("location")),
                "revision_scraped_at": normalize_space(row.get("revision_scraped_at")),
                "_dt": dt,
                "_rev": revision_number(row.get("call number")),
            }
            if not (
                entry["call number"].upper().startswith("CO")
                or entry["report number"].upper().startswith("COR")
            ):
                continue
            grouped[base_call_number(entry["call number"])].append(entry)

    cutoff = now_local().replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)
    calls: list[dict[str, Any]] = []
    for base, chain in grouped.items():
        chain.sort(key=lambda item: (item["_rev"], item["_dt"]))
        latest = chain[-1]
        if latest["_dt"] < cutoff:
            continue
        best_report = next(
            (item["report number"] for item in reversed(chain) if normalize_space(item["report number"])),
            "",
        )
        report_parts = parse_coroner_report_number(best_report)
        town, suffix = resolve_call_town(latest["location"], city_map)
        calls.append(
            {
                "base_call_number": base,
                "call_number": latest["call number"],
                "agency": latest["agency"],
                "station": latest["station"],
                "date_time": latest["date/time"],
                "report_number": best_report,
                "call_type": latest["call type"],
                "disposition": latest["disposition"],
                "location": latest["location"],
                "call_dt": latest["_dt"],
                "call_date_key": date_key(latest["_dt"]),
                "call_town": town,
                "call_suffix": suffix,
                "call_prefix": base[:2].upper(),
                "location_tokens": extract_location_tokens(latest["location"]),
                "has_specific_location": has_specific_location(latest["location"]),
                "report_case_year_2": report_parts[0] if report_parts else "",
                "report_case_number": report_parts[1] if report_parts else None,
            }
        )

    calls.sort(key=lambda item: (item["call_dt"], item["base_call_number"]), reverse=True)
    return calls


def load_recent_call_chains(calllog_path: Path) -> list[dict[str, Any]]:
    city_map = load_city_map()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with calllog_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dt = parse_call_datetime(row.get("date/time"))
            if not dt:
                continue
            agency, station = normalize_call_row_source(row)
            entry = {
                "date/time": normalize_space(row.get("date/time")),
                "agency": agency,
                "station": station,
                "call number": normalize_space(row.get("call number")),
                "report number": normalize_space(row.get("report number")),
                "call type": normalize_space(row.get("call type")),
                "disposition": normalize_space(row.get("disposition")),
                "location": normalize_space(row.get("location")),
                "revision_scraped_at": normalize_space(row.get("revision_scraped_at")),
                "_dt": dt,
                "_rev": revision_number(row.get("call number")),
            }
            grouped[base_call_number(entry["call number"])].append(entry)

    cutoff = now_local().replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)
    calls: list[dict[str, Any]] = []
    for base, chain in grouped.items():
        chain.sort(key=lambda item: (item["_rev"], item["_dt"]))
        latest = chain[-1]
        if latest["_dt"] < cutoff:
            continue

        town, suffix = resolve_call_town(latest["location"], city_map)
        call_types = sorted(
            {
                normalize_space(item.get("call type")).upper()
                for item in chain
                if normalize_space(item.get("call type"))
            }
        )
        dispositions = sorted(
            {
                normalize_space(item.get("disposition")).upper()
                for item in chain
                if normalize_space(item.get("disposition"))
            }
        )
        calls.append(
            {
                "base_call_number": base,
                "call_number": latest["call number"],
                "agency": latest["agency"],
                "station": latest["station"],
                "date_time": latest["date/time"],
                "report_number": latest["report number"],
                "call_type": latest["call type"],
                "disposition": latest["disposition"],
                "location": latest["location"],
                "call_dt": latest["_dt"],
                "call_date_key": date_key(latest["_dt"]),
                "call_town": town,
                "call_suffix": suffix,
                "call_prefix": base[:2].upper(),
                "location_tokens": extract_location_tokens(latest["location"]),
                "has_specific_location": has_specific_location(latest["location"]),
                "call_types": call_types,
                "dispositions": dispositions,
            }
        )

    calls.sort(key=lambda item: (item["call_dt"], item["base_call_number"]), reverse=True)
    return calls


def calllog_data_dir(calllog_path: Path) -> Path:
    return calllog_path.resolve().parent


def downloaded_arrest_log_path(calllog_path: Path) -> Path:
    return calllog_data_dir(calllog_path) / DOWNLOADED_ARREST_LOG_NAME


def downloaded_death_index_path(calllog_path: Path) -> Path:
    return calllog_data_dir(calllog_path) / DOWNLOADED_DEATH_INDEX_NAME


def downloaded_release_paths(calllog_path: Path) -> list[Path]:
    data_dir = calllog_data_dir(calllog_path)
    return [data_dir / name for name in DOWNLOADED_RELEASE_NAMES]


def detail_id_from_url(detail_url: Any) -> str:
    text = normalize_space(detail_url)
    match = re.search(r"/detail/(\d+)/", text)
    return match.group(1) if match else ""


def looks_like_sbsd_source(source_agency: Any, county_of_arrest: Any = "") -> bool:
    source_text = normalize_space(source_agency).lower()
    county_text = normalize_space(county_of_arrest).lower()
    if source_text in SBSD_SOURCE_HINTS:
        return True
    if "san bernardino" in source_text and ("sheriff" in source_text or source_text.endswith(" sd")):
        return True
    if "san bernardino" in county_text and ("sheriff" in source_text or source_text.endswith(" sd")):
        return True
    return False


def load_downloaded_death_lookup(calllog_path: Path) -> dict[str, dict[str, Any]]:
    path = downloaded_death_index_path(calllog_path)
    if not path.exists():
        return {}

    lookup: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            case_display = normalize_space(row.get("caseNumberDisplay") or row.get("CaseNumberDisplay"))
            case_year = normalize_space(row.get("caseYear") or row.get("CaseYear"))
            case_number_value = normalize_space(row.get("caseNumber") or row.get("CaseNumber"))
            if not case_display or not case_year or not case_number_value.isdigit():
                continue
            case_number_int = int(case_number_value)
            report_number = f"COR{case_year[-2:]}{case_number_int:05d}"
            lookup[report_number] = {
                "caseNumberDisplay": case_display,
                "caseYear": case_year,
                "caseNumber": case_number_int,
                "decedentName": normalize_space(row.get("decedentName") or row.get("DecedentName")),
                "dateOfDeath": normalize_space(row.get("dateOfDeath") or row.get("DateOfDeath")),
                "placeOfDeath": normalize_space(row.get("placeOfDeath") or row.get("PlaceOfDeath")),
                "podCity": normalize_space(row.get("podCity") or row.get("PodCity")),
                "ageDisplay": normalize_space(row.get("ageDisplay") or row.get("AgeDisplay")),
                "sex": normalize_space(row.get("sex") or row.get("Sex")),
                "currentMode": normalize_space(row.get("currentMode") or row.get("CurrentMode")),
                "decedentCity": normalize_space(row.get("decedentCity") or row.get("DecedentCity")),
                "injuryCity": normalize_space(row.get("injuryCity") or row.get("InjuryCity")),
            }
    return lookup


def load_downloaded_release_rows(calllog_path: Path) -> list[dict[str, Any]]:
    for path in downloaded_release_paths(calllog_path):
        if not path.exists():
            continue
        rows: list[dict[str, Any]] = []
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = normalize_space(row.get("Name"))
                release_dt = parse_arrest_date(row.get("Release Date"))
                if not name or not release_dt:
                    continue
                rows.append(
                    {
                        "name": name,
                        "name_key": normalize_person_name_key(name),
                        "sex": normalize_space(row.get("Sex")),
                        "sex_code": parse_gender_code(row.get("Sex")),
                        "age": normalize_space(row.get("Age")),
                        "height": normalize_space(row.get("Height")),
                        "weight": normalize_space(row.get("Weight")),
                        "release_date": release_dt.strftime("%Y-%m-%d"),
                        "release_date_dt": release_dt,
                        "source_file": path.name,
                    }
                )
        return rows
    return []


def build_release_lookup(release_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in release_rows:
        key = normalize_person_name_key(row.get("name"))
        if not key:
            continue
        lookup[key].append(row)
    for key in lookup:
        lookup[key].sort(key=lambda item: (item.get("release_date_dt") or datetime.min, item.get("source_file") or ""))
    return dict(lookup)


def build_release_evidence(candidate: dict[str, Any], release_lookup: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    arrest_dt = candidate.get("arrest_date_dt")
    arrest_name = normalize_space(candidate.get("arrest_name"))
    candidate_gender = parse_gender_code((candidate.get("details", {}) or {}).get("age_gender"))
    evidence: dict[str, Any] = {"sources": [], "confidence_signals": []}

    detail_release_dt = parse_arrest_date((candidate.get("details", {}) or {}).get("release_date"))
    if detail_release_dt:
        evidence["sources"].append(
            {
                "source": "arrest_detail",
                "release_date": detail_release_dt.strftime("%Y-%m-%d"),
            }
        )
        evidence["confidence_signals"].append("detail_release_date_present")

    release_rows = release_lookup.get(normalize_person_name_key(arrest_name), [])
    if arrest_dt is not None and release_rows:
        best_row = None
        best_day_delta = None
        for row in release_rows:
            release_dt = row.get("release_date_dt")
            if not isinstance(release_dt, datetime):
                continue
            day_delta = (release_dt.date() - arrest_dt.date()).days
            if day_delta < 0 or day_delta > RELEASE_MATCH_MAX_DAYS:
                continue
            row_gender = row.get("sex_code", "")
            if candidate_gender and row_gender and candidate_gender != row_gender:
                continue
            if best_day_delta is None or day_delta < best_day_delta:
                best_row = row
                best_day_delta = day_delta
        if best_row is not None:
            evidence["sources"].append(
                {
                    "source": "release_list_name_match",
                    "release_date": best_row["release_date"],
                    "name": best_row["name"],
                    "sex": best_row.get("sex", ""),
                    "age": best_row.get("age", ""),
                    "source_file": best_row.get("source_file", ""),
                }
            )
            evidence["confidence_signals"].append("release_list_name_match")
            if best_row.get("sex_code") and candidate_gender and best_row["sex_code"] == candidate_gender:
                evidence["confidence_signals"].append("release_list_sex_match")

    if not evidence["sources"]:
        return {}

    release_dates = []
    for source in evidence["sources"]:
        release_dt = parse_arrest_date(source.get("release_date"))
        if release_dt:
            release_dates.append(release_dt)
    if release_dates:
        earliest_release_dt = min(release_dates)
        evidence["earliest_release_date"] = earliest_release_dt.strftime("%Y-%m-%d")
    evidence["confidence_signals"] = sorted(set(evidence["confidence_signals"]))
    return evidence


def build_custody_signals(call: dict[str, Any], candidate: dict[str, Any], release_lookup: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    start_dt = call.get("call_dt") or candidate.get("arrest_date_dt")
    if not isinstance(start_dt, datetime):
        return {}

    deadline_dt, skipped_dates = add_court_business_hours(start_dt, ARRAIGNMENT_CUSTODY_HOURS)
    release_evidence = build_release_evidence(candidate, release_lookup)
    now_dt = now_local().replace(tzinfo=None)

    status = "within_window"
    needs_court_check = False
    if now_dt > deadline_dt:
        if release_evidence.get("earliest_release_date"):
            earliest_release_dt = parse_arrest_date(release_evidence["earliest_release_date"])
            if earliest_release_dt and earliest_release_dt.date() < deadline_dt.date():
                status = "released_before_deadline"
                release_evidence["released_before_deadline"] = True
            elif earliest_release_dt and earliest_release_dt.date() == deadline_dt.date():
                status = "released_on_deadline_date_unknown_time"
                needs_court_check = True
            else:
                status = "released_after_deadline_or_unknown_time"
                needs_court_check = True
        else:
            status = "past_deadline_needs_court_check"
            needs_court_check = True
    elif release_evidence.get("earliest_release_date"):
        earliest_release_dt = parse_arrest_date(release_evidence["earliest_release_date"])
        if earliest_release_dt and earliest_release_dt.date() < deadline_dt.date():
            release_evidence["released_before_deadline"] = True

    signals = {
        "custody_start": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "custody_start_source": "call_datetime" if call.get("call_dt") else "arrest_date",
        "arraignment_deadline_local": deadline_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "excluded_court_dates": skipped_dates,
        "status": status,
        "needs_court_check": needs_court_check,
    }
    if release_evidence:
        signals["release_evidence"] = release_evidence
        if release_evidence.get("confidence_signals"):
            signals["confidence_signals"] = release_evidence["confidence_signals"]
    return signals


def map_downloaded_record_to_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    details = record.get("details")
    if not isinstance(details, dict):
        details = {}

    source_agency = normalize_space(details.get("source_agency") or record.get("source_agency"))
    county_of_arrest = normalize_space(details.get("county_of_arrest") or record.get("county_of_arrest"))
    if not looks_like_sbsd_source(source_agency, county_of_arrest):
        return None

    arrest_id = normalize_space(record.get("arrest_id")) or detail_id_from_url(record.get("detail_url"))
    if not arrest_id:
        return None

    arrest_name = normalize_space(record.get("arrest_name") or record.get("name"))
    arrest_location = normalize_space(details.get("arrest_location") or record.get("arrest_location"))
    arrest_date_text = normalize_space(details.get("arrest_date_full") or record.get("arrest_date"))
    arrest_date_dt = parse_arrest_date(arrest_date_text)
    resident_city_state = normalize_space(record.get("resident_city_state") or details.get("city_state"))

    resident_tags = [normalize_tag(item) for item in (record.get("resident_tags") or [])]
    resident_tags = sorted({item for item in resident_tags if item})
    if not resident_tags and resident_city_state:
        resident_tags = sorted({normalize_tag(resident_city_state.split(",", 1)[0])} - {""})

    area_tags = [normalize_tag(item) for item in (record.get("area_tags") or [])]
    area_tags = sorted({item for item in area_tags if item})

    charge = normalize_space(details.get("arrested_for") or record.get("charge"))
    linked_call_bases = sorted(
        {
            call_base_from_linked_value(value)
            for value in (
                details.get("cad_number"),
                details.get("linked_cad_number"),
                details.get("call_number"),
            )
            if call_base_from_linked_value(value)
        }
    )
    linked_report_numbers = sorted(
        {
            normalize_report_number(value)
            for value in (
                details.get("report_number"),
                details.get("linked_report_number"),
            )
            if normalize_report_number(value)
        }
    )

    return {
        "arrest_id": arrest_id,
        "arrest_name": arrest_name,
        "detail_url": normalize_space(record.get("detail_url") or details.get("detail_url")),
        "arrest_date": arrest_date_text,
        "arrest_date_dt": arrest_date_dt,
        "arrest_date_key": date_key(arrest_date_dt),
        "charge": charge,
        "map_charge_codes": sorted(extract_code_variants(charge)),
        "detail_charge_codes": sorted(extract_code_variants(charge)),
        "resident_city_state": resident_city_state,
        "resident_tags": resident_tags,
        "area_tags": area_tags,
        "is_local_resident": bool(record.get("is_local_resident") or resident_tags),
        "arrest_location": arrest_location,
        "has_explicit_location": bool(record.get("has_explicit_location")) or has_specific_location(arrest_location),
        "reasons": [],
        "score": 0,
        "map_county": county_of_arrest,
        "map_source_agency": source_agency,
        "map_record": record,
        "details": details,
        "overlap_tokens": [],
        "source_kinds": ["downloaded_daily"],
        "linked_call_bases": linked_call_bases,
        "linked_report_numbers": linked_report_numbers,
    }


def load_downloaded_arrest_log_candidates(calllog_path: Path) -> dict[str, dict[str, Any]]:
    path = downloaded_arrest_log_path(calllog_path)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return {}

    candidates: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        candidate = map_downloaded_record_to_candidate(record)
        if not candidate:
            continue
        arrest_id = candidate["arrest_id"]
        if arrest_id in candidates:
            candidates[arrest_id] = merge_candidate(candidates[arrest_id], candidate)
        else:
            candidates[arrest_id] = candidate
    return candidates


def cache_path(kind: str, key: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-") or "default"
    return CACHE_DIR / kind / f"{slug}.json"


def make_json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    return value


def restore_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {"__datetime__"}:
            try:
                return datetime.fromisoformat(str(value["__datetime__"]))
            except ValueError:
                return value["__datetime__"]
        return {key: restore_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [restore_json_safe(item) for item in value]
    return value


def read_json_cache(path: Path, max_age_seconds: int) -> tuple[Any | None, bool]:
    if not path.exists():
        return None, False
    try:
        data = restore_json_safe(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None, False
    age_seconds = max(0.0, time.time() - path.stat().st_mtime)
    return data, age_seconds <= max_age_seconds


def write_json_cache(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    safe_payload = make_json_safe(payload)
    path.write_text(json.dumps(safe_payload, indent=2, sort_keys=True), encoding="utf-8")


class LocalCrimeNewsClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/137.0.0.0 Safari/537.36"
                )
            }
        )
        self._map_primed = False

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                response = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:  # pragma: no cover - network failure path
                last_error = exc
                if attempt == REQUEST_RETRIES:
                    raise
                time.sleep(REQUEST_BACKOFF_SECONDS * attempt)
        if last_error:
            raise last_error
        raise RuntimeError(f"Unexpected empty response for {method} {url}")

    def prime_map_session(self) -> None:
        if self._map_primed:
            return
        self.request("GET", LCN_MAP_URL)
        self._map_primed = True

    def fetch_map_records(self, town: str, lat: str, long_value: str) -> list[dict[str, Any]]:
        cache_file = cache_path("map", town)
        cached, is_fresh = read_json_cache(cache_file, MAP_CACHE_TTL_SECONDS)
        if is_fresh and isinstance(cached, list):
            return cached

        try:
            self.prime_map_session()
            response = self.request(
                "POST",
                LCN_MAP_ENDPOINT,
                data={"lat": lat, "long": long_value},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": LCN_BASE_URL,
                    "Referer": LCN_MAP_URL,
                },
            )
            records = response.json()
            if not isinstance(records, list):
                raise ValueError(f"Unexpected map payload for {town}")
            write_json_cache(cache_file, records)
            return records
        except Exception:
            if isinstance(cached, list):
                return cached
            raise

    def fetch_detail_payload(self, arrest_id: str) -> dict[str, Any]:
        cache_file = cache_path("detail", arrest_id)
        cached, is_fresh = read_json_cache(cache_file, DETAIL_CACHE_TTL_SECONDS)
        if is_fresh and isinstance(cached, dict):
            return cached

        try:
            response = self.request("GET", LCN_DETAIL_URL.format(arrest_id=arrest_id))
            payload = parse_detail_page(response.text, arrest_id)
            write_json_cache(cache_file, payload)
            return payload
        except Exception:
            if isinstance(cached, dict):
                return cached
            raise

    def fetch_agency_page_records(self, page_number: int) -> list[dict[str, Any]]:
        cache_file = cache_path("agency", f"sbsd-page-{page_number}")
        cached, is_fresh = read_json_cache(cache_file, AGENCY_CACHE_TTL_SECONDS)
        if is_fresh and isinstance(cached, list):
            return cached

        url = f"{LCN_SBSD_AGENCY_URL}?pg={int(page_number)}"
        try:
            response = self.request("GET", url)
            records = parse_agency_page(response.text, url)
            write_json_cache(cache_file, records)
            return records
        except Exception:
            if isinstance(cached, list):
                return cached
            raise


class DeathRegisterClient:
    def __init__(self, local_lookup: dict[str, dict[str, Any]] | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/137.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self._page_primed = False
        self.local_lookup = {normalize_report_number(key): value for key, value in (local_lookup or {}).items()}

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                response = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:  # pragma: no cover - network failure path
                last_error = exc
                if attempt == REQUEST_RETRIES:
                    raise
                time.sleep(REQUEST_BACKOFF_SECONDS * attempt)
        if last_error:
            raise last_error
        raise RuntimeError(f"Unexpected empty response for {method} {url}")

    def prime_page_session(self) -> None:
        if self._page_primed:
            return
        self.request("GET", DEATH_REGISTER_PAGE_URL)
        self._page_primed = True

    def fetch_match_for_report(self, report_number: str) -> dict[str, Any] | None:
        normalized_report = normalize_report_number(report_number)
        if normalized_report and normalized_report in self.local_lookup:
            return parse_death_register_match_row(self.local_lookup[normalized_report], normalized_report)

        parsed = parse_coroner_report_number(normalized_report)
        if not parsed:
            return None

        case_year_2, case_number = parsed
        cache_file = cache_path("death", normalized_report)
        cached, is_fresh = read_json_cache(cache_file, DEATH_REGISTER_CACHE_TTL_SECONDS)
        if is_fresh and isinstance(cached, dict) and cached:
            return cached or None

        case_display = derive_death_register_case_display(case_year_2, case_number)
        data = {
            "draw": "1",
            "start": "0",
            "length": "5",
            "search[value]": case_display,
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "caseYear": f"20{case_year_2}",
        }
        for index, (column_data, column_name) in enumerate(DEATH_REGISTER_COLUMNS):
            data[f"columns[{index}][data]"] = column_data
            data[f"columns[{index}][name]"] = column_name
            data[f"columns[{index}][searchable]"] = "true"
            data[f"columns[{index}][orderable]"] = "true"
            data[f"columns[{index}][search][value]"] = ""
            data[f"columns[{index}][search][regex]"] = "false"

        try:
            self.prime_page_session()
            response = self.request(
                "POST",
                DEATH_REGISTER_GRID_URL,
                data=data,
                headers={
                    "Origin": "https://xcore.sbcounty.gov",
                    "Referer": DEATH_REGISTER_PAGE_URL,
                },
            )
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else []
            match_row = None
            for row in rows or []:
                row_case_number = int(row.get("caseNumber") or 0)
                row_display = normalize_space(row.get("caseNumberDisplay"))
                if row_case_number == case_number or row_display == case_display:
                    match_row = row
                    break
            result = parse_death_register_match_row(match_row, normalized_report) if match_row else {}
            write_json_cache(cache_file, result)
            return result or None
        except Exception:
            if isinstance(cached, dict):
                return cached or None
            raise


def format_name(last_name: Any, first_name: Any, middle_name: Any = "") -> str:
    last_part = normalize_space(last_name)
    first_part = normalize_space(first_name)
    middle_part = normalize_space(middle_name)
    if middle_part:
        return normalize_space(f"{last_part}, {first_part} {middle_part}")
    return normalize_space(f"{last_part}, {first_part}")


def parse_death_register_match_row(row: dict[str, Any] | None, report_number: str) -> dict[str, Any]:
    if not row:
        return {}

    case_display = normalize_space(row.get("caseNumberDisplay"))
    case_year = normalize_space(row.get("caseYear"))
    case_number_value = row.get("caseNumber")
    try:
        case_number_int = int(case_number_value)
    except Exception:
        case_number_int = 0

    return {
        "case_number": case_display,
        "case_year": case_year,
        "case_number_numeric": case_number_int,
        "source_url": DEATH_REGISTER_PAGE_URL,
        "decedent_name": normalize_space(row.get("decedentName")),
        "decedent_city": normalize_space(row.get("decedentCity")),
        "date_of_death": normalize_space(row.get("dateOfDeath")),
        "place_of_death": normalize_space(row.get("placeOfDeath")),
        "injury_city": normalize_space(row.get("injuryCity")),
        "pod_city": normalize_space(row.get("podCity")),
        "resolved_location": normalize_space(row.get("placeOfDeath") or row.get("podCity") or row.get("decedentCity")),
        "age": normalize_space(row.get("ageDisplay")),
        "sex": normalize_space(row.get("sex")),
        "manner": normalize_space(row.get("currentMode")),
        "coroner_report_number": normalize_space(report_number).upper(),
        "derived_coroner_report_number": case_display,
        "reasons": ["coroner_report_number_case_match"],
        "score": 100,
    }


def parse_agency_page(html: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = normalize_space(link.get("href"))
        match = re.search(r"/welcome/detail/(\d+)/", href)
        if not match:
            continue

        arrest_id = match.group(1)
        if arrest_id in seen_ids:
            continue
        seen_ids.add(arrest_id)

        detail_url = href if href.startswith("http") else f"{LCN_BASE_URL}{href}"
        card_text = ""
        card_detail_text = ""
        node = link
        for _ in range(10):
            node = node.parent
            if node is None or not hasattr(node, "get_text"):
                break
            text = normalize_space(node.get_text(" ", strip=True))
            if not card_detail_text and "Age:" in text and "County:" in text and "Reported On:" in text:
                card_detail_text = text
            if "Reported On:" in text and "Arrested For:" in text:
                card_text = text
                if card_detail_text:
                    break

        reported_on = ""
        resident_city_state = ""
        charge = ""
        arrest_name = ""

        m = re.search(r"Reported On:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", card_text, flags=re.I)
        if m:
            reported_on = normalize_space(m.group(1))

        m = re.search(r"^(.*?)\s+Age:", card_detail_text, flags=re.I)
        if m:
            arrest_name = normalize_space(m.group(1))

        m = re.search(r"Age:\s*[^-–]+[-–]\s*(.*?)\s+County:", card_detail_text, flags=re.I)
        if m:
            resident_city_state = normalize_space(m.group(1))

        m = re.search(r"Arrested For:\s*(.*?)(?:View Arrest Details|$)", card_text, flags=re.I)
        if m:
            charge = normalize_space(m.group(1))

        candidate = {
            "arrest_id": arrest_id,
            "arrest_name": arrest_name,
            "detail_url": detail_url,
            "arrest_date": reported_on,
            "arrest_date_dt": parse_arrest_date(reported_on),
            "arrest_date_key": date_key(parse_arrest_date(reported_on)),
            "charge": charge,
            "map_charge_codes": sorted(extract_code_variants(charge)),
            "resident_city_state": resident_city_state,
            "resident_tags": sorted({normalize_tag(resident_city_state.split(",", 1)[0])} - {""}),
            "area_tags": [],
            "is_local_resident": bool(normalize_tag(resident_city_state.split(",", 1)[0])),
            "has_explicit_location": False,
            "reasons": [],
            "score": 0,
            "map_county": "San Bernardino",
            "map_source_agency": "San Bernardino County Sheriff",
            "map_record": {
                "page_url": page_url,
                "page_arrest_date": reported_on,
                "summary_text": card_text,
            },
            "details": {},
            "overlap_tokens": [],
            "source_kinds": ["agency_page"],
        }
        records.append(candidate)

    return records


def map_record_to_candidate(record: dict[str, Any], town: str) -> dict[str, Any] | None:
    arrest_id = normalize_space(record.get("ArrestId"))
    if not arrest_id:
        return None

    resident_city = normalize_space(record.get("PerpCity"))
    resident_state = normalize_space(record.get("PerpState"))
    resident_city_state = normalize_space(", ".join(part for part in (resident_city, resident_state) if part))
    crime_code = normalize_space(record.get("crime_code"))
    crime_desc = normalize_space(record.get("crime_desc"))
    county = normalize_space(record.get("County"))
    agency_name = normalize_space(record.get("AgencyName"))

    return {
        "arrest_id": arrest_id,
        "arrest_name": format_name(record.get("LastName"), record.get("FirstName"), record.get("MiddleName")),
        "detail_url": LCN_DETAIL_URL.format(arrest_id=arrest_id),
        "arrest_date": normalize_space(record.get("ArrestDate")),
        "arrest_date_dt": parse_arrest_date(record.get("ArrestDate")),
        "arrest_date_key": date_key(parse_arrest_date(record.get("ArrestDate"))),
        "charge": crime_code or crime_desc,
        "map_charge_codes": sorted(extract_code_variants(crime_code)),
        "resident_city_state": resident_city_state,
        "resident_tags": sorted({normalize_tag(resident_city)} - {""}),
        "area_tags": [town],
        "is_local_resident": bool(normalize_tag(resident_city)),
        "has_explicit_location": False,
        "reasons": [],
        "score": 0,
        "map_county": county,
        "map_source_agency": agency_name,
        "map_record": record,
        "details": {},
        "overlap_tokens": [],
        "source_kinds": ["map"],
    }


def merge_candidate(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "arrest_name",
        "detail_url",
        "arrest_date",
        "charge",
        "resident_city_state",
        "map_county",
        "map_source_agency",
        "arrest_location",
    ):
        if not base.get(key) and incoming.get(key):
            base[key] = incoming[key]
    if not base.get("arrest_date_dt") and incoming.get("arrest_date_dt"):
        base["arrest_date_dt"] = incoming["arrest_date_dt"]
    if not base.get("arrest_date_key") and incoming.get("arrest_date_key"):
        base["arrest_date_key"] = incoming["arrest_date_key"]
    base["resident_tags"] = sorted(set(base.get("resident_tags", [])) | set(incoming.get("resident_tags", [])))
    base["area_tags"] = sorted(set(base.get("area_tags", [])) | set(incoming.get("area_tags", [])))
    base["map_charge_codes"] = sorted(set(base.get("map_charge_codes", [])) | set(incoming.get("map_charge_codes", [])))
    base["detail_charge_codes"] = sorted(
        set(base.get("detail_charge_codes", [])) | set(incoming.get("detail_charge_codes", []))
    )
    base["source_kinds"] = sorted(set(base.get("source_kinds", [])) | set(incoming.get("source_kinds", [])))
    base["linked_call_bases"] = sorted(
        set(base.get("linked_call_bases", [])) | set(incoming.get("linked_call_bases", []))
    )
    base["linked_report_numbers"] = sorted(
        set(base.get("linked_report_numbers", [])) | set(incoming.get("linked_report_numbers", []))
    )
    if not base.get("details") and incoming.get("details"):
        base["details"] = incoming["details"]
    base["has_explicit_location"] = bool(base.get("has_explicit_location") or incoming.get("has_explicit_location"))
    base["is_local_resident"] = bool(base.get("resident_tags"))
    return base


def choose_centers(calls: list[dict[str, Any]]) -> list[str]:
    centers = {call["call_town"] for call in calls if call["call_town"] in TOWN_CENTERS}
    if any(call["call_prefix"] in {"BA", "SE", "SP"} for call in calls):
        centers.update(DEFAULT_BARSTOW_REGION_CENTERS)
    return sorted(center for center in centers if center in TOWN_CENTERS)


def collect_map_candidates(client: LocalCrimeNewsClient, calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    centers = choose_centers(calls)
    candidates: dict[str, dict[str, Any]] = {}
    for town in centers:
        coords = TOWN_CENTERS[town]
        try:
            records = client.fetch_map_records(town, coords["lat"], coords["long"])
        except Exception as exc:
            print(f"[!] Failed to fetch LCN map records for {town}: {exc}")
            continue
        for record in records:
            candidate = map_record_to_candidate(record, town)
            if not candidate:
                continue
            arrest_id = candidate["arrest_id"]
            if arrest_id in candidates:
                candidates[arrest_id] = merge_candidate(candidates[arrest_id], candidate)
            else:
                candidates[arrest_id] = candidate
    return candidates


def collect_agency_candidates(
    client: LocalCrimeNewsClient,
    pages_per_run: int = AGENCY_PAGES_PER_RUN,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    for page_number in range(1, max(1, pages_per_run) + 1):
        try:
            records = client.fetch_agency_page_records(page_number)
        except Exception as exc:
            print(f"[!] Failed to fetch LCN SBSD agency page {page_number}: {exc}")
            continue

        for candidate in records:
            arrest_id = normalize_space(candidate.get("arrest_id"))
            if not arrest_id:
                continue
            if arrest_id in candidates:
                candidates[arrest_id] = merge_candidate(candidates[arrest_id], candidate)
            else:
                candidates[arrest_id] = candidate

    return candidates


def extract_label_value_rows(table: BeautifulSoup) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        label = normalize_space(cells[0].get_text(" ", strip=True)).rstrip(":")
        if not label:
            continue
        separator = "; " if label == "Arrested For" else " "
        value = normalize_space(cells[1].get_text(separator, strip=True))
        output[label] = value
    return output


def parse_prior_arrests(soup: BeautifulSoup) -> list[dict[str, str]]:
    prior: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        headers = [normalize_space(th.get_text(" ", strip=True)).lower() for th in table.find_all("th")]
        if {"arrested for", "by", "date"} - {header.rstrip(":") for header in headers}:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            prior.append(
                {
                    "charge": normalize_space(cells[0].get_text(" ", strip=True)),
                    "agency": normalize_space(cells[1].get_text(" ", strip=True)),
                    "date": normalize_space(cells[2].get_text(" ", strip=True)),
                }
            )
        if prior:
            break
    return prior


def parse_detail_page(html: str, arrest_id: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    main_table = None
    for table in soup.find_all("table"):
        text = normalize_space(table.get_text(" ", strip=True))
        if "Citizen Details" in text and "Arrest Details" in text:
            main_table = table
            break

    fields = extract_label_value_rows(main_table) if main_table else {}
    arrest_name = fields.get("Arrest Name", "")
    prior_arrests = parse_prior_arrests(soup)

    detail_url = LCN_DETAIL_URL.format(arrest_id=arrest_id)
    city_state = fields.get("City, State", "")
    arrested_for = fields.get("Arrested For", "")
    arrest_date_full = fields.get("Arrest Date", "")

    payload = {
        "arrest_id": arrest_id,
        "arrest_name": arrest_name,
        "arrest_date": normalize_space(arrest_date_full),
        "arrest_date_dt": parse_arrest_date(arrest_date_full),
        "charge": arrested_for,
        "resident_city_state": city_state,
        "resident_tags": sorted({normalize_tag(city_state.split(",", 1)[0])} - {""}),
        "arrest_location": fields.get("Arrest Location", ""),
        "has_explicit_location": has_specific_location(fields.get("Arrest Location")),
        "details": {
            "address": fields.get("Address", ""),
            "city_state": city_state,
            "age_gender": fields.get("Age / Gender", ""),
            "race": fields.get("Race", ""),
            "hair_eyes": fields.get("Hair / Eyes", ""),
            "height_weight": fields.get("Height / Weight", ""),
            "arrested_for": arrested_for,
            "arrest_date_full": arrest_date_full,
            "release_date": fields.get("Release Date", ""),
            "bail_amount": fields.get("Bail Amount", ""),
            "arrest_location": fields.get("Arrest Location", ""),
            "county_of_arrest": fields.get("County of Arrest", ""),
            "source_agency": fields.get("Source", ""),
            "detail_url": detail_url,
            "prior_arrests": prior_arrests,
        },
        "detail_url": detail_url,
        "detail_charge_codes": sorted(extract_code_variants(arrested_for)),
        "arrest_date_key": date_key(parse_arrest_date(arrest_date_full)),
    }
    return payload


def score_date_delta(call_dt: datetime, arrest_dt: datetime | None) -> tuple[int, str | None]:
    if not arrest_dt:
        return 0, None
    delta_days = abs((call_dt.date() - arrest_dt.date()).days)
    if delta_days == 0:
        return 4, "same_day"
    if delta_days == 1:
        return 3, "within_1_day"
    if delta_days == 2:
        return 2, "within_2_days"
    if delta_days <= 4:
        return 1, "within_4_days"
    return -99, None


def resident_region_match(call_town: str, resident_tags: list[str], area_tags: list[str]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    if not call_town:
        return 0, reasons
    resident_set = set(resident_tags or [])
    area_set = set(area_tags or [])
    score = 0
    if call_town in resident_set:
        score += 1
        reasons.append("resident_same_town")
    if call_town in area_set:
        score += 1
        reasons.append("same_map_area")
    return score, reasons


def candidate_context_towns(candidate: dict[str, Any]) -> set[str]:
    towns = set(candidate.get("resident_tags") or []) | set(candidate.get("area_tags") or [])
    arrest_location = normalize_space(candidate.get("arrest_location"))
    if arrest_location and "," in arrest_location:
        towns.add(normalize_tag(arrest_location.rsplit(",", 1)[-1]))
    return {town for town in towns if town}


def score_map_candidate(call: dict[str, Any], candidate: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if call["base_call_number"] in set(candidate.get("linked_call_bases", [])):
        score += 20
        reasons.append("linked_call_number_match")

    call_report_number = normalize_report_number(call.get("report_number"))
    if call_report_number and call_report_number in set(candidate.get("linked_report_numbers", [])):
        score += 18
        reasons.append("linked_report_number_match")

    date_score, date_reason = score_date_delta(call["call_dt"], candidate.get("arrest_date_dt"))
    if date_score < 0:
        return date_score, reasons
    score += date_score
    if date_reason:
        reasons.append(date_reason)

    call_codes = set(call.get("call_code_variants", []))
    candidate_codes = set(candidate.get("map_charge_codes", [])) or set(candidate.get("detail_charge_codes", []))
    charge_score, charge_reason = score_code_match(call_codes, candidate_codes)
    if charge_reason:
        score += charge_score
        reasons.append(charge_reason)

    region_score, region_reasons = resident_region_match(
        call.get("call_town", ""),
        candidate.get("resident_tags", []),
        candidate.get("area_tags", []),
    )
    score += region_score
    reasons.extend(region_reasons)

    county_hint = normalize_space(candidate.get("map_county")).lower()
    source_hint = normalize_space(candidate.get("map_source_agency")).lower()
    if "san bernardino" in county_hint or "san bernardino" in source_hint:
        score += 2
        reasons.append("san_bernardino_county")
    else:
        score -= 2
        reasons.append("out_of_county_hint")

    return score, reasons


def enrich_candidates_for_calls(
    client: LocalCrimeNewsClient,
    calls: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    detail_needed: set[str] = set()
    for call in calls:
        ranked: list[tuple[int, str]] = []
        for arrest_id, candidate in candidates.items():
            rough_score, _ = score_map_candidate(call, candidate)
            if rough_score < MIN_ROUGH_SCORE:
                continue
            ranked.append((rough_score, arrest_id))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, arrest_id in ranked[:MAX_DETAIL_CANDIDATES_PER_CALL]:
            detail_needed.add(arrest_id)

    for arrest_id in sorted(detail_needed):
        try:
            detail = client.fetch_detail_payload(arrest_id)
        except Exception as exc:
            print(f"[!] Failed to fetch LCN detail for arrest {arrest_id}: {exc}")
            continue
        candidate = candidates.get(arrest_id)
        if not candidate:
            continue
        candidate["arrest_name"] = detail.get("arrest_name") or candidate.get("arrest_name")
        candidate["arrest_date"] = detail.get("arrest_date") or candidate.get("arrest_date")
        candidate["arrest_date_dt"] = detail.get("arrest_date_dt") or candidate.get("arrest_date_dt")
        candidate["charge"] = detail.get("charge") or candidate.get("charge")
        candidate["resident_city_state"] = detail.get("resident_city_state") or candidate.get("resident_city_state")
        candidate["resident_tags"] = sorted(
            set(candidate.get("resident_tags", [])) | set(detail.get("resident_tags", []))
        )
        candidate["arrest_location"] = detail.get("arrest_location", "")
        candidate["has_explicit_location"] = bool(detail.get("has_explicit_location"))
        candidate["details"] = detail.get("details", {})
        candidate["detail_charge_codes"] = detail.get("detail_charge_codes", [])


def enrich_candidates_with_details_all(
    client: LocalCrimeNewsClient,
    candidates: dict[str, dict[str, Any]],
) -> None:
    for arrest_id in sorted(candidates):
        try:
            detail = client.fetch_detail_payload(arrest_id)
        except Exception as exc:
            print(f"[!] Failed to fetch LCN detail for arrest {arrest_id}: {exc}")
            continue

        candidate = candidates.get(arrest_id)
        if not candidate:
            continue

        candidate["arrest_name"] = detail.get("arrest_name") or candidate.get("arrest_name")
        candidate["arrest_date"] = detail.get("arrest_date") or candidate.get("arrest_date")
        candidate["arrest_date_dt"] = detail.get("arrest_date_dt") or candidate.get("arrest_date_dt")
        candidate["arrest_date_key"] = date_key(candidate.get("arrest_date_dt"))
        candidate["charge"] = detail.get("charge") or candidate.get("charge")
        candidate["resident_city_state"] = detail.get("resident_city_state") or candidate.get("resident_city_state")
        candidate["resident_tags"] = sorted(
            set(candidate.get("resident_tags", [])) | set(detail.get("resident_tags", []))
        )
        candidate["is_local_resident"] = bool(candidate.get("resident_tags"))
        candidate["arrest_location"] = detail.get("arrest_location", "")
        candidate["has_explicit_location"] = bool(detail.get("has_explicit_location"))
        candidate["details"] = detail.get("details", {})
        candidate["detail_charge_codes"] = detail.get("detail_charge_codes", [])
        if not candidate.get("map_charge_codes"):
            candidate["map_charge_codes"] = list(detail.get("detail_charge_codes", []))


def score_location_overlap(call_tokens: list[str], arrest_location: Any) -> tuple[int, list[str], list[str]]:
    arrest_tokens = extract_location_tokens(arrest_location)
    overlap = sorted(set(call_tokens) & set(arrest_tokens))
    if not overlap:
        return 0, [], []
    if any(token.isdigit() for token in overlap) or len(overlap) >= 2:
        return 4, ["strong_location_overlap"], overlap
    return 1, ["weak_location_overlap"], overlap


def has_location_conflict(call: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if not call.get("has_specific_location"):
        return False
    arrest_location = candidate.get("arrest_location", "")
    if not candidate.get("has_explicit_location") or not has_specific_location(arrest_location):
        return False
    call_tokens = set(call.get("location_tokens", []))
    arrest_tokens = set(extract_location_tokens(arrest_location))
    if not call_tokens or not arrest_tokens:
        return False
    return not bool(call_tokens & arrest_tokens)


def score_final_candidate(call: dict[str, Any], candidate: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    score, reasons = score_map_candidate(call, candidate)
    if score < 0:
        return score, reasons, []

    if has_location_conflict(call, candidate):
        return -99, sorted(set(reasons + ["location_conflict"])), []

    detail = candidate.get("details", {}) or {}
    source_agency = normalize_space(detail.get("source_agency")).lower()
    county_of_arrest = normalize_space(detail.get("county_of_arrest")).lower()
    if "san bernardino county sheriff" in source_agency:
        score += 3
        reasons.append("sbsd_source")
    elif "san bernardino" in county_of_arrest:
        score += 1
        reasons.append("san_bernardino_detail")
    else:
        score -= 3
        reasons.append("detail_out_of_county")

    call_codes = set(call.get("call_code_variants", []))
    detail_codes = set(candidate.get("detail_charge_codes", []))
    charge_score, charge_reason = score_code_match(call_codes, detail_codes)
    if charge_reason:
        score += min(2, charge_score)
        reasons.append("detail_charge_code_match" if charge_reason == "charge_code_match" else "detail_charge_code_root_match")

    location_score, location_reasons, overlap = score_location_overlap(
        call.get("location_tokens", []),
        candidate.get("arrest_location", ""),
    )
    score += location_score
    reasons.extend(location_reasons)

    return score, sorted(set(reasons)), overlap


def build_daily_arrest_record(candidate: dict[str, Any]) -> dict[str, Any] | None:
    arrest_dt = candidate.get("arrest_date_dt")
    arrest_date_key = date_key(arrest_dt)
    if not arrest_date_key:
        return None
    details = candidate.get("details", {}) or {}
    source_agency = details.get("source_agency") or candidate.get("map_source_agency", "")
    county_of_arrest = details.get("county_of_arrest") or candidate.get("map_county", "")
    arrest_location = candidate.get("arrest_location", "")
    if not arrest_location:
        arrest_location = details.get("arrest_location", "")
    return {
        "arrest_id": candidate.get("arrest_id"),
        "arrest_name": candidate.get("arrest_name"),
        "arrest_date": candidate.get("arrest_date"),
        "arrest_date_key": arrest_date_key,
        "release_date": normalize_space((details or {}).get("release_date")),
        "arrest_location": arrest_location,
        "charge": candidate.get("charge"),
        "detail_url": candidate.get("detail_url"),
        "resident_city_state": candidate.get("resident_city_state", ""),
        "resident_tags": candidate.get("resident_tags", []),
        "area_tags": candidate.get("area_tags", []),
        "is_local_resident": candidate.get("is_local_resident", False),
        "has_explicit_location": candidate.get("has_explicit_location", False),
        "source_agency": source_agency,
        "county_of_arrest": county_of_arrest,
        "linked_call_bases": candidate.get("linked_call_bases", []),
        "linked_report_numbers": candidate.get("linked_report_numbers", []),
    }


def build_daily_arrest_index(candidates: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates.values():
        record = build_daily_arrest_record(candidate)
        if not record:
            continue
        by_date[record["arrest_date_key"]].append(record)

    out: dict[str, list[dict[str, Any]]] = {}
    for arrest_date_key, records in by_date.items():
        seen_ids: set[str] = set()
        unique_records: list[dict[str, Any]] = []
        for record in sorted(
            records,
            key=lambda item: (
                item.get("resident_city_state", ""),
                item.get("arrest_name", ""),
                item.get("charge", ""),
                item.get("arrest_id", ""),
            ),
        ):
            arrest_id = str(record.get("arrest_id", ""))
            if not arrest_id or arrest_id in seen_ids:
                continue
            seen_ids.add(arrest_id)
            unique_records.append(record)
        out[arrest_date_key] = unique_records
    return dict(sorted(out.items()))


def build_call_payload_entry(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_call_number": call["base_call_number"],
        "call_number": call["call_number"],
        "date_time": call["date_time"],
        "call_type": call["call_type"],
        "disposition": call["disposition"],
        "report_number": call["report_number"],
        "location": call["location"],
        "call_date_key": call["call_date_key"],
        "call_town": call["call_town"],
    }


def build_death_matches(
    coroner_calls: list[dict[str, Any]],
    death_client: DeathRegisterClient,
) -> dict[str, list[dict[str, Any]]]:
    matches_by_call: dict[str, list[dict[str, Any]]] = {}

    for call in coroner_calls:
        report_number = normalize_space(call.get("report_number")).upper()
        if not report_number:
            continue

        try:
            match = death_client.fetch_match_for_report(report_number)
        except Exception as exc:
            print(f"[!] Failed to fetch death register match for {report_number}: {exc}")
            continue

        if not match:
            continue

        matches_by_call[call["base_call_number"]] = [match]

    return matches_by_call


def build_coroner_associations(
    coroner_calls: list[dict[str, Any]],
    recent_calls: list[dict[str, Any]],
    death_matches_by_call: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    calls_by_base = {call["base_call_number"]: call for call in recent_calls}
    associations: dict[str, dict[str, Any]] = {}

    for coroner_call in coroner_calls:
        coroner_base = coroner_call["base_call_number"]
        death_match = (death_matches_by_call.get(coroner_base) or [None])[0]
        death_tokens = extract_location_tokens((death_match or {}).get("resolved_location", ""))
        death_towns = {
            normalize_tag((death_match or {}).get("decedent_city")),
            normalize_tag((death_match or {}).get("injury_city")),
            normalize_tag((death_match or {}).get("pod_city")),
        }
        death_towns.discard("")

        ranked: list[dict[str, Any]] = []
        for candidate in recent_calls:
            candidate_base = candidate["base_call_number"]
            if candidate_base == coroner_base:
                continue
            if candidate["call_dt"] > coroner_call["call_dt"]:
                continue

            minutes_delta = (coroner_call["call_dt"] - candidate["call_dt"]).total_seconds() / 60.0
            if minutes_delta < 0 or minutes_delta > CORONER_ASSOCIATION_MAX_MINUTES:
                continue

            candidate_types = set(candidate.get("call_types") or [])
            if not candidate_types.intersection(CORONER_SOURCE_CALL_TYPES):
                continue

            score = 0
            reasons: list[str] = []
            if "DB" in candidate_types:
                score += 8
                reasons.append("prior_db_call")
            if candidate.get("has_specific_location"):
                score += 2
                reasons.append("specific_location")
            if minutes_delta <= 20:
                score += 4
                reasons.append("very_close_in_time")
            elif minutes_delta <= 45:
                score += 3
                reasons.append("close_in_time")
            elif minutes_delta <= 90:
                score += 2
                reasons.append("same_hour_window")
            else:
                score += 1
                reasons.append("same_shift_window")

            overlap_tokens = sorted(set(candidate.get("location_tokens") or []).intersection(death_tokens))
            if overlap_tokens:
                score += 5 + len(overlap_tokens)
                reasons.append("death_location_overlap")

            if candidate.get("call_town") and candidate["call_town"] in death_towns:
                score += 3
                reasons.append("death_town_match")

            ranked.append(
                {
                    "base_call_number": candidate_base,
                    "call_number": candidate["call_number"],
                    "call_type": candidate["call_type"],
                    "report_number": candidate["report_number"],
                    "location": candidate["location"],
                    "date_time": candidate["date_time"],
                    "call_date_key": candidate["call_date_key"],
                    "call_town": candidate["call_town"],
                    "minutes_before_coroner": round(minutes_delta, 1),
                    "overlap_tokens": overlap_tokens,
                    "score": score,
                    "reasons": reasons,
                }
            )

        if not ranked:
            continue

        ranked.sort(
            key=lambda item: (
                item["score"],
                len(item["overlap_tokens"]),
                -item["minutes_before_coroner"],
            ),
            reverse=True,
        )
        best = ranked[0]
        second_best_score = ranked[1]["score"] if len(ranked) > 1 else -999
        confident = (
            "death_location_overlap" in best["reasons"]
            or "prior_db_call" in best["reasons"]
            or (best["score"] >= 8 and best["score"] >= second_best_score + 2)
        )
        if not confident:
            continue

        related_call = calls_by_base.get(best["base_call_number"])
        if not related_call:
            continue

        associations[coroner_base] = {
            **best,
            "related_call_number": related_call["call_number"],
            "related_report_number": related_call["report_number"],
            "related_call_type": related_call["call_type"],
            "related_disposition": related_call["disposition"],
            "related_location": related_call["location"],
        }

    return associations


def build_matches(
    calls: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    release_lookup: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    release_lookup = release_lookup or {}
    date_town_counts: dict[tuple[str, str], int] = defaultdict(int)
    for candidate in candidates.values():
        arrest_date_key = candidate.get("arrest_date_key") or date_key(candidate.get("arrest_date_dt"))
        if not arrest_date_key:
            continue
        for town in candidate_context_towns(candidate):
            date_town_counts[(arrest_date_key, town)] += 1

    def is_unique_date_town_candidate(call: dict[str, Any], candidate: dict[str, Any]) -> bool:
        call_town = normalize_tag(call.get("call_town"))
        arrest_date_key = candidate.get("arrest_date_key") or date_key(candidate.get("arrest_date_dt"))
        if not call_town or not arrest_date_key or arrest_date_key != call.get("call_date_key"):
            return False
        if call_town not in candidate_context_towns(candidate):
            return False
        return date_town_counts.get((arrest_date_key, call_town), 0) == 1

    def is_ambiguous_date_town_candidate(call: dict[str, Any], candidate: dict[str, Any]) -> bool:
        call_town = normalize_tag(call.get("call_town"))
        arrest_date_key = candidate.get("arrest_date_key") or date_key(candidate.get("arrest_date_dt"))
        if not call_town or not arrest_date_key or arrest_date_key != call.get("call_date_key"):
            return False
        if call_town not in candidate_context_towns(candidate):
            return False
        return date_town_counts.get((arrest_date_key, call_town), 0) > 1

    def is_confident(edge: dict[str, Any], second_best_score: int, call: dict[str, Any]) -> bool:
        reasons = set(edge["reasons"])
        if "linked_call_number_match" in reasons or "linked_report_number_match" in reasons:
            return True
        if "charge_code_match" in reasons or "detail_charge_code_match" in reasons:
            return True
        if "strong_location_overlap" in reasons:
            return True
        unresolved_location = normalize_space(call.get("location")) in {"* ,*", "*,*", "*", ""}
        if unresolved_location:
            return False
        call_town = normalize_tag(call.get("call_town"))
        candidate = edge["raw_candidate"]
        if call_town in SMALL_TOWN_DATE_MATCH_TOWNS and is_unique_date_town_candidate(call, candidate):
            edge["candidate"]["reasons"] = sorted(set(edge["candidate"]["reasons"] + ["unique_small_town_same_day"]))
            return True
        if call_town in CONDITIONAL_DATE_MATCH_TOWNS and is_unique_date_town_candidate(call, candidate):
            edge["candidate"]["reasons"] = sorted(set(edge["candidate"]["reasons"] + ["unique_town_same_day"]))
            return True
        if call_town in SMALL_TOWN_DATE_MATCH_TOWNS | CONDITIONAL_DATE_MATCH_TOWNS:
            if is_ambiguous_date_town_candidate(call, candidate):
                return False
        if {
            "resident_same_town",
            "same_day",
            "sbsd_source",
        }.issubset(reasons) and edge["score"] >= second_best_score + 2:
            return True
        return False

    def build_candidate_view(call: dict[str, Any], arrest_id: str, candidate: dict[str, Any], score: int, reasons: list[str], overlap: list[str]) -> dict[str, Any]:
        view = {
            "arrest_id": arrest_id,
            "arrest_name": candidate.get("arrest_name"),
            "arrest_date": candidate.get("arrest_date"),
            "arrest_date_key": candidate.get("arrest_date_key") or date_key(candidate.get("arrest_date_dt")),
            "arrest_location": candidate.get("arrest_location", ""),
            "charge": candidate.get("charge"),
            "detail_url": candidate.get("detail_url"),
            "details": candidate.get("details", {}),
            "area_tags": candidate.get("area_tags", []),
            "resident_tags": candidate.get("resident_tags", []),
            "resident_city_state": candidate.get("resident_city_state", ""),
            "is_local_resident": candidate.get("is_local_resident", False),
            "has_explicit_location": candidate.get("has_explicit_location", False),
            "source_agency": (candidate.get("details", {}) or {}).get("source_agency", "") or candidate.get("map_source_agency", ""),
            "county_of_arrest": (candidate.get("details", {}) or {}).get("county_of_arrest", "") or candidate.get("map_county", ""),
            "linked_call_bases": candidate.get("linked_call_bases", []),
            "linked_report_numbers": candidate.get("linked_report_numbers", []),
            "overlap_tokens": overlap,
            "reasons": reasons,
            "score": score,
        }
        custody_signals = build_custody_signals(call, candidate, release_lookup)
        if custody_signals:
            view["custody_signals"] = custody_signals
        return view

    calls_by_base = {call["base_call_number"]: call for call in calls}
    proposals_by_call: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for arrest_id, candidate in candidates.items():
        ranked_edges: list[dict[str, Any]] = []

        for call in calls:
            final_score, reasons, overlap = score_final_candidate(call, candidate)
            if final_score < MIN_FINAL_SCORE:
                continue

            ranked_edges.append(
                {
                    "call_base": call["base_call_number"],
                    "call_number": call["call_number"],
                    "arrest_id": arrest_id,
                    "score": final_score,
                    "reasons": reasons,
                    "overlap_count": len(overlap),
                    "candidate": build_candidate_view(call, arrest_id, candidate, final_score, reasons, overlap),
                    "raw_candidate": candidate,
                    "call_dt": call["call_dt"],
                    "arrest_dt": candidate.get("arrest_date_dt") or datetime.min,
                }
            )

        if not ranked_edges:
            continue

        ranked_edges.sort(key=lambda item: (item["score"], item["overlap_count"]), reverse=True)
        top_edge = ranked_edges[0]
        second_best_score = ranked_edges[1]["score"] if len(ranked_edges) > 1 else -999
        if not is_confident(top_edge, second_best_score, calls_by_base[top_edge["call_base"]]):
            continue
        proposals_by_call[top_edge["call_base"]].append(top_edge)

    selected_by_call: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call_base, edges in proposals_by_call.items():
        edges.sort(key=lambda item: (item["score"], item["overlap_count"]), reverse=True)
        best_edges = edges[:MAX_MATCHES_PER_CALL]
        selected_by_call[call_base].extend(edge["candidate"] for edge in best_edges)

    return {call_base: matches for call_base, matches in selected_by_call.items() if matches}


def build_payload(calllog_path: Path = CALLLOG_CSV) -> dict[str, Any]:
    arrest_calls = load_recent_arrest_calls(calllog_path)
    coroner_calls = load_recent_coroner_calls(calllog_path)
    recent_calls = load_recent_call_chains(calllog_path)
    candidates = load_downloaded_arrest_log_candidates(calllog_path)
    release_rows = load_downloaded_release_rows(calllog_path)
    release_lookup = build_release_lookup(release_rows)
    death_lookup = load_downloaded_death_lookup(calllog_path)
    candidate_source = "downloaded_daily"
    if not candidates:
        client = LocalCrimeNewsClient()
        candidates = collect_agency_candidates(client, pages_per_run=AGENCY_PAGES_PER_RUN)
        if not candidates:
            candidates = collect_map_candidates(client, arrest_calls)
            enrich_candidates_for_calls(client, arrest_calls, candidates)
            candidate_source = "live_map"
        else:
            enrich_candidates_with_details_all(client, candidates)
            candidate_source = "live_agency_pages"
    matches_by_call = build_matches(arrest_calls, candidates, release_lookup=release_lookup)
    death_client = DeathRegisterClient(death_lookup)
    death_matches_by_call = build_death_matches(coroner_calls, death_client)
    coroner_associations = build_coroner_associations(coroner_calls, recent_calls, death_matches_by_call)
    daily_arrests = build_daily_arrest_index(candidates)
    recent_calls_by_base = {call["base_call_number"]: call for call in recent_calls}
    coroner_call_bases = {call["base_call_number"] for call in coroner_calls}
    related_death_source_bases = {
        association["base_call_number"]
        for association in coroner_associations.values()
        if association.get("base_call_number")
    }

    payload_calls: dict[str, Any] = {}
    for call in arrest_calls:
        matches = matches_by_call.get(call["base_call_number"], [])
        entry = payload_calls.get(call["base_call_number"]) or build_call_payload_entry(call)
        entry["arrest_matches"] = matches
        payload_calls[call["base_call_number"]] = entry

    for coroner_call in coroner_calls:
        coroner_base = coroner_call["base_call_number"]
        death_matches = death_matches_by_call.get(coroner_base)
        association = coroner_associations.get(coroner_base)

        coroner_entry = payload_calls.get(coroner_base) or build_call_payload_entry(coroner_call)
        coroner_entry["coroner_call"] = {
            "base_call_number": coroner_base,
            "call_number": coroner_call["call_number"],
            "date_time": coroner_call["date_time"],
            "call_type": coroner_call["call_type"],
            "disposition": coroner_call["disposition"],
            "report_number": coroner_call["report_number"],
            "location": coroner_call["location"],
            "call_town": coroner_call["call_town"],
        }
        if death_matches:
            coroner_entry["death_matches"] = death_matches
        if association:
            coroner_entry["related_base_call"] = association
        payload_calls[coroner_base] = coroner_entry

        if association:
            related_base = association["base_call_number"]
            related_call = recent_calls_by_base.get(related_base)
            if related_call:
                related_entry = payload_calls.get(related_base) or build_call_payload_entry(related_call)
                related_entry["related_coroner_call"] = {
                    "base_call_number": coroner_base,
                    "call_number": coroner_call["call_number"],
                    "date_time": coroner_call["date_time"],
                    "call_type": coroner_call["call_type"],
                    "report_number": coroner_call["report_number"],
                    "minutes_after_related_call": association["minutes_before_coroner"],
                    "score": association["score"],
                    "reasons": association["reasons"],
                }
                payload_calls[related_base] = related_entry

    death_source_call_count = 0
    for call in recent_calls:
        base = call["base_call_number"]
        if base in coroner_call_bases or base in related_death_source_bases:
            continue
        if "DB" not in set(call.get("call_types") or []):
            continue
        entry = payload_calls.get(base) or build_call_payload_entry(call)
        entry["death_source_call"] = {
            "base_call_number": base,
            "call_number": call["call_number"],
            "date_time": call["date_time"],
            "call_type": call["call_type"],
            "disposition": call["disposition"],
            "report_number": call["report_number"],
            "location": call["location"],
            "call_town": call["call_town"],
            "reasons": ["db_call_without_related_coroner_call"],
        }
        payload_calls[base] = entry
        death_source_call_count += 1

    return {
        "generated_at": iso_now(),
        "lookback_days": LOOKBACK_DAYS,
        "agency_pages_per_run": AGENCY_PAGES_PER_RUN,
        "candidate_source": candidate_source,
        "entry_count": len(payload_calls),
        "arr_row_count": len(arrest_calls),
        "coroner_row_count": len(coroner_calls),
        "matched_call_count": sum(
            1
            for entry in payload_calls.values()
            if entry.get("arrest_matches") or entry.get("death_matches") or entry.get("related_coroner_call")
            or entry.get("death_source_call")
        ),
        "arrest_annotated_call_count": len(arrest_calls),
        "arrest_matched_call_count": sum(1 for entry in payload_calls.values() if entry.get("arrest_matches")),
        "unmatched_arrest_call_count": sum(
            1 for call in arrest_calls if not matches_by_call.get(call["base_call_number"])
        ),
        "death_matched_coroner_count": len(death_matches_by_call),
        "death_annotated_call_count": sum(1 for entry in payload_calls.values() if entry.get("death_matches")),
        "death_source_call_count": death_source_call_count,
        "coroner_related_call_count": len(coroner_associations),
        "candidate_count": len(candidates),
        "death_register_count": len(death_lookup),
        "release_row_count": len(release_rows),
        "release_match_count": sum(
            1
            for entry in payload_calls.values()
            for match in entry.get("arrest_matches", [])
            if (match.get("custody_signals", {}) or {}).get("release_evidence")
        ),
        "daily_arrests": daily_arrests,
        "calls": payload_calls,
    }


def build_arrest_index(
    calllog_path: Path = CALLLOG_CSV,
    output_path: Path = OUTPUT_JSON,
) -> Path:
    payload = build_payload(calllog_path)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    path = build_arrest_index()
    print(f"Wrote {path}")

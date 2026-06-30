#!/usr/bin/env python3
"""CHP incident scraper for the Barstow dispatch center."""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"
CHP_MOBILE_BASE_URL = "http://m.chp.ca.gov/incident.aspx"
CHP_DISPATCH_BARSTOW = "BSCC"
CHP_AGENCY_CODE = "CHP"
CHP_STATION_BARSTOW = "Barstow"
CHP_ACTIVE_DISPOSITION = "ACT"
CHP_IGNORED_AREA_SUFFIXES = {"MOR", "VVC"}

AREA_SUFFIX_MAP = {
    "Barstow": "BAR",
    "Victorville": "VVC",
    "Morongo Basin": "MOR",
    "Needles": "NED",
    "BS": "BAR",
}

LOG_BLOCK_RE = re.compile(r'<Log ID = "([^"]+)">(.*?)</Log>', re.S)
DETAIL_BLOCK_RE = re.compile(
    r"<details>\s*<DetailTime>\"?(.*?)\"?</DetailTime>\s*<IncidentDetail>\"?(.*?)\"?</IncidentDetail>\s*</details>",
    re.S,
)
UNIT_BLOCK_RE = re.compile(
    r"<units>\s*<UnitTime>\"?(.*?)\"?</UnitTime>\s*<UnitDetail>\"?(.*?)\"?</UnitDetail>\s*</units>",
    re.S,
)


def clean_chp_text(value: Any) -> str:
    text = (value or "").strip().strip('"')
    return re.sub(r"\s+", " ", text).strip()


def parse_chp_timestamp(timestamp_str: str) -> Optional[datetime]:
    cleaned = clean_chp_text(timestamp_str)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%b %d %Y %I:%M%p")
    except ValueError:
        return None


def area_suffix(area: str) -> str:
    cleaned = clean_chp_text(area)
    if not cleaned:
        return ""
    return AREA_SUFFIX_MAP.get(cleaned, cleaned[:3].upper())


def build_location(location: str, area: str, location_desc: str = "") -> str:
    base = clean_chp_text(location)
    desc = clean_chp_text(location_desc)
    if desc and desc not in {"AT", "AT ", "N/A"}:
        base_lower = base.lower()
        desc_lower = desc.lower()
        if base_lower and base_lower in desc_lower and len(desc) > len(base):
            base = desc
        elif desc_lower not in base_lower:
            base = "{} {}".format(base, desc).strip()
    suffix = area_suffix(area)
    if suffix:
        return "{} ,{}".format(base, suffix).strip()
    return base


def build_call_number(log_id: str) -> str:
    return "CHP-{}".format(clean_chp_text(log_id))


def location_mentions_ignored_area(*values: str) -> bool:
    for value in values:
        cleaned = clean_chp_text(value).upper()
        if re.search(r"(^|[^A-Z])(MOR|VVC)([^A-Z]|$)", cleaned):
            return True
    return False


def split_chp_call_type(value: str) -> Tuple[str, str]:
    cleaned = clean_chp_text(value)
    if not cleaned:
        return "", ""
    patterns = [
        r"^([A-Z0-9./]+)\s*-\s*([A-Za-z].+)$",
        r"^([A-Z0-9./]+)\s+([A-Za-z].+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if match:
            return clean_chp_text(match.group(1)), clean_chp_text(match.group(2))
    return cleaned, ""


def extract_dispatch_chunk(content: str, dispatch_id: str) -> str:
    pattern = r'<Dispatch ID = "{}">(.*?)(?:</Dispatch>|<Dispatch ID = |</Center>|</State>|$)'.format(
        re.escape(dispatch_id)
    )
    match = re.search(pattern, content, re.S)
    return match.group(1) if match else ""


def extract_log_tag(body: str, tag_name: str) -> str:
    match = re.search(r"<{0}>\"?(.*?)\"?</{0}>".format(re.escape(tag_name)), body, re.S)
    return clean_chp_text(match.group(1)) if match else ""


def parse_log_lines(body: str, block_re: re.Pattern, time_tag: str, text_tag: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for time_text, detail_text in block_re.findall(body):
        cleaned_text = clean_chp_text(detail_text)
        cleaned_time = clean_chp_text(time_text)
        if not cleaned_text:
            continue
        items.append({"time": cleaned_time, "text": cleaned_text})
    return items


def parse_chp_latlon(latlon_raw: str) -> Dict[str, Any]:
    cleaned = clean_chp_text(latlon_raw)
    if not cleaned:
        return {}
    payload: Dict[str, Any] = {"raw": cleaned}
    if ":" not in cleaned:
        return payload
    lat_raw, lon_raw = cleaned.split(":", 1)
    try:
        lat = int(lat_raw) / 1000000.0
        lon = -abs(int(lon_raw) / 1000000.0)
    except ValueError:
        return payload
    payload["lat"] = round(lat, 6)
    payload["lon"] = round(lon, 6)
    return payload


def build_extra_payload(
    *,
    log_id: str,
    call_type_code: str,
    call_type_description: str,
    area: str,
    location_desc: str,
    latlon_raw: str,
    thomas_brothers: str,
    detail_lines: List[Dict[str, str]],
    unit_activity: List[Dict[str, str]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": "chp_incident",
        "provider": "chp",
        "dispatch_id": CHP_DISPATCH_BARSTOW,
        "log_id": clean_chp_text(log_id),
        "call_type_code": clean_chp_text(call_type_code),
        "call_type_description": clean_chp_text(call_type_description),
        "area": clean_chp_text(area),
        "location_detail": clean_chp_text(location_desc),
        "detail_lines": detail_lines,
        "unit_activity": unit_activity,
        "is_active": True,
        "is_closed": False,
    }
    map_info = parse_chp_latlon(latlon_raw)
    if map_info:
        payload["map"] = map_info
    thomas = clean_chp_text(thomas_brothers)
    if thomas:
        payload["thomas_brothers"] = thomas
    return payload


def normalize_incident(
    *,
    log_id: str,
    log_time: str,
    log_type: str,
    location: str,
    area: str,
    location_desc: str = "",
    latlon_raw: str = "",
    thomas_brothers: str = "",
    detail_lines: Optional[List[Dict[str, str]]] = None,
    unit_activity: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, str]]:
    parsed_time = parse_chp_timestamp(log_time)
    cleaned_log_id = clean_chp_text(log_id)
    cleaned_type = clean_chp_text(log_type)
    call_type_code, call_type_description = split_chp_call_type(cleaned_type)
    if not parsed_time or not cleaned_log_id or not call_type_code:
        return None
    if area_suffix(area) in CHP_IGNORED_AREA_SUFFIXES:
        return None
    if location_mentions_ignored_area(location, location_desc):
        return None
    extra_payload = build_extra_payload(
        log_id=cleaned_log_id,
        call_type_code=call_type_code,
        call_type_description=call_type_description,
        area=area,
        location_desc=location_desc,
        latlon_raw=latlon_raw,
        thomas_brothers=thomas_brothers,
        detail_lines=detail_lines or [],
        unit_activity=unit_activity or [],
    )
    return {
        "date/time": parsed_time.strftime("%m/%d/%Y %I:%M:%S %p"),
        "agency": CHP_AGENCY_CODE,
        "station": CHP_STATION_BARSTOW,
        "call number": build_call_number(cleaned_log_id),
        "report number": cleaned_log_id,
        "call type": call_type_code,
        "disposition": CHP_ACTIVE_DISPOSITION,
        "location": build_location(location, area, location_desc),
        "revision_scraped_at": "",
        "extra_json": json.dumps(extra_payload, separators=(",", ":"), ensure_ascii=True),
    }


def parse_chp_xml_feed(content: str) -> List[Dict[str, str]]:
    incidents: List[Dict[str, str]] = []
    dispatch_chunk = extract_dispatch_chunk(content, CHP_DISPATCH_BARSTOW)
    if not dispatch_chunk:
        return incidents
    for log_id, log_body in LOG_BLOCK_RE.findall(dispatch_chunk):
        incident = normalize_incident(
            log_id=log_id,
            log_time=extract_log_tag(log_body, "LogTime"),
            log_type=extract_log_tag(log_body, "LogType"),
            location=extract_log_tag(log_body, "Location"),
            location_desc=extract_log_tag(log_body, "LocationDesc"),
            area=extract_log_tag(log_body, "Area"),
            latlon_raw=extract_log_tag(log_body, "LATLON"),
            thomas_brothers=extract_log_tag(log_body, "ThomasBrothers"),
            detail_lines=parse_log_lines(log_body, DETAIL_BLOCK_RE, "DetailTime", "IncidentDetail"),
            unit_activity=parse_log_lines(log_body, UNIT_BLOCK_RE, "UnitTime", "UnitDetail"),
        )
        if incident:
            incidents.append(incident)
    return incidents


def parse_mobile_log_id(href: str) -> str:
    parsed = urlparse(href or "")
    return clean_chp_text(parse_qs(parsed.query).get("id", [""])[0])


def fetch_chp_xml_feed(session: Optional[requests.Session] = None) -> str:
    sess = session or requests.Session()
    response = sess.get(CHP_XML_FEED_URL, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_chp_mobile_barstow(session: Optional[requests.Session] = None) -> List[Dict[str, str]]:
    sess = session or requests.Session()
    url = "{}?DispatchId={}".format(CHP_MOBILE_BASE_URL, CHP_DISPATCH_BARSTOW)
    response = sess.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    incidents: List[Dict[str, str]] = []
    for item in soup.select("ul.details > li"):
        link = item.find("a")
        if not link:
            continue
        log_id = parse_mobile_log_id(link.get("href", ""))
        lines = [clean_chp_text(line) for line in link.get_text("\n", strip=True).splitlines() if clean_chp_text(line)]
        if len(lines) < 4:
            continue
        incident = normalize_incident(
            log_id=log_id,
            log_time=lines[3],
            log_type=lines[0],
            area=lines[1],
            location=lines[2],
        )
        if incident:
            incidents.append(incident)
    return incidents


def dedupe_incidents(incidents: List[Dict[str, str]]) -> List[Dict[str, str]]:
    unique: Dict[str, Dict[str, str]] = {}
    for incident in incidents:
        call_number = incident.get("call number", "")
        if not call_number:
            continue
        existing = unique.get(call_number)
        if existing is None or incident_richness(incident) > incident_richness(existing):
            unique[call_number] = incident
    return list(unique.values())


def incident_richness(incident: Dict[str, str]) -> int:
    score = 0
    extra_json = incident.get("extra_json", "")
    if extra_json:
        try:
            payload = json.loads(extra_json)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            score += len(payload.get("detail_lines", []) or []) * 10
            score += len(payload.get("unit_activity", []) or []) * 10
            if payload.get("location_detail"):
                score += 2
            if payload.get("map"):
                score += 2
    if incident.get("location"):
        score += 1
    return score


def scrape_chp_incidents() -> List[Dict[str, str]]:
    session = requests.Session()
    incidents: List[Dict[str, str]] = []
    errors: List[str] = []
    fetch_succeeded = False
    try:
        incidents.extend(parse_chp_xml_feed(fetch_chp_xml_feed(session)))
        fetch_succeeded = True
    except Exception as exc:
        errors.append("xml={}".format(exc))
    try:
        incidents.extend(fetch_chp_mobile_barstow(session))
        fetch_succeeded = True
    except Exception as exc:
        errors.append("mobile={}".format(exc))
    if not fetch_succeeded:
        raise RuntimeError("CHP feed fetch failed ({})".format("; ".join(errors) or "unknown error"))
    deduped = dedupe_incidents(incidents)
    deduped.sort(key=lambda row: row.get("date/time", ""), reverse=True)
    return deduped


if __name__ == "__main__":
    rows = scrape_chp_incidents()
    print("Total CHP incidents:", len(rows))
    for row in rows[:10]:
        print(row)

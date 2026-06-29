#!/usr/bin/env python3
"""CHP incident scraper for the Barstow dispatch center."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"
CHP_MOBILE_BASE_URL = "http://m.chp.ca.gov/incident.aspx"
CHP_DISPATCH_BARSTOW = "BSCC"
CHP_AGENCY_CODE = "CHP"
CHP_STATION_BARSTOW = "Barstow"
CHP_ACTIVE_DISPOSITION = "ACT"

AREA_SUFFIX_MAP = {
    "Barstow": "BAR",
    "Victorville": "VVC",
    "Morongo Basin": "MOR",
    "Needles": "NED",
}


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


def normalize_incident(
    *,
    log_id: str,
    log_time: str,
    log_type: str,
    location: str,
    area: str,
    location_desc: str = "",
) -> Optional[Dict[str, str]]:
    parsed_time = parse_chp_timestamp(log_time)
    cleaned_log_id = clean_chp_text(log_id)
    cleaned_type = clean_chp_text(log_type)
    if not parsed_time or not cleaned_log_id or not cleaned_type:
        return None
    return {
        "date/time": parsed_time.strftime("%m/%d/%Y %I:%M:%S %p"),
        "agency": CHP_AGENCY_CODE,
        "station": CHP_STATION_BARSTOW,
        "call number": build_call_number(cleaned_log_id),
        "report number": cleaned_log_id,
        "call type": cleaned_type,
        "disposition": CHP_ACTIVE_DISPOSITION,
        "location": build_location(location, area, location_desc),
        "revision_scraped_at": "",
    }


def parse_chp_xml_feed(content: str) -> List[Dict[str, str]]:
    root = ET.fromstring(content)
    incidents: List[Dict[str, str]] = []
    for dispatch in root.iterfind(".//Dispatch"):
        if clean_chp_text(dispatch.get("ID", "")) != CHP_DISPATCH_BARSTOW:
            continue
        for log in dispatch.findall("Log"):
            incident = normalize_incident(
                log_id=log.get("ID", ""),
                log_time=log.findtext("LogTime", ""),
                log_type=log.findtext("LogType", ""),
                location=log.findtext("Location", ""),
                location_desc=log.findtext("LocationDesc", ""),
                area=log.findtext("Area", ""),
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
        if call_number and call_number not in unique:
            unique[call_number] = incident
    return list(unique.values())


def scrape_chp_incidents() -> List[Dict[str, str]]:
    session = requests.Session()
    incidents: List[Dict[str, str]] = []
    try:
        incidents.extend(parse_chp_xml_feed(fetch_chp_xml_feed(session)))
    except Exception:
        pass
    try:
        incidents.extend(fetch_chp_mobile_barstow(session))
    except Exception:
        pass
    deduped = dedupe_incidents(incidents)
    deduped.sort(key=lambda row: row.get("date/time", ""), reverse=True)
    return deduped


if __name__ == "__main__":
    rows = scrape_chp_incidents()
    print("Total CHP incidents:", len(rows))
    for row in rows[:10]:
        print(row)

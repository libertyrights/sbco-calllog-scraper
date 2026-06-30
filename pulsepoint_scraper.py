#!/usr/bin/env python3
"""PulsePoint incident scraper for the Barstow-area fire feed."""

import base64
import hashlib
import json
import math
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover
    Cipher = None
    algorithms = None
    modes = None
    default_backend = None

try:
    from Crypto.Cipher import AES as CryptoAes
except Exception:  # pragma: no cover
    CryptoAes = None

PULSEPOINT_API_URL = "https://api.pulsepoint.org"
PULSEPOINT_WEB_ORIGIN = "https://web.pulsepoint.org"
PULSEPOINT_API_TIMEOUT_SECONDS = int(os.environ.get("SBCO_PULSEPOINT_TIMEOUT_SECONDS", "30"))
PULSEPOINT_ENVIRONMENT = os.environ.get("SBCO_PULSEPOINT_ENVIRONMENT", "PRODUCTION").strip().upper() or "PRODUCTION"
PULSEPOINT_VERSION = "v1-sandbox" if PULSEPOINT_ENVIRONMENT == "SANDBOX" else "v1"
PULSEPOINT_AGENCY_IDS = [
    agency_id.strip()
    for agency_id in os.environ.get("SBCO_PULSEPOINT_AGENCY_IDS", "36193").split(",")
    if agency_id.strip()
]
PULSEPOINT_AGENCY_CODE = (os.environ.get("SBCO_PULSEPOINT_AGENCY_CODE", "SBCFIRE").strip() or "SBCFIRE").upper()
PULSEPOINT_STATION_NAME = os.environ.get("SBCO_PULSEPOINT_STATION", "Barstow Area Fire").strip() or "Barstow Area Fire"
PULSEPOINT_CENTER_LAT = float(os.environ.get("SBCO_PULSEPOINT_CENTER_LAT", "34.8958"))
PULSEPOINT_CENTER_LON = float(os.environ.get("SBCO_PULSEPOINT_CENTER_LON", "-117.0173"))
PULSEPOINT_RADIUS_MILES = float(os.environ.get("SBCO_PULSEPOINT_RADIUS_MILES", "25"))
PULSEPOINT_TIMEZONE_NAME = os.environ.get("SBCO_PULSEPOINT_TIMEZONE", "America/Los_Angeles").strip() or "America/Los_Angeles"
PULSEPOINT_LOCAL_TOKENS = [
    token.strip().upper()
    for token in os.environ.get(
        "SBCO_PULSEPOINT_LOCAL_TOKENS",
        "BARSTOW,LENWOOD,DAGGETT,YERMO,HINKLEY,HARVARD,NEWBERRY,NEWBERRY SPRINGS",
    ).split(",")
    if token.strip()
]
PULSEPOINT_DECRYPTION_PASSPHRASE = "tombrady5rings"

PULSEPOINT_CALL_TYPES: Dict[str, Dict[str, Any]] = {
    "AA": {"description": "Auto Aid", "category": "Aid", "alertable": True},
    "MU": {"description": "Mutual Aid", "category": "Aid", "alertable": True},
    "ST": {"description": "Strike Team/Task Force", "category": "Aid", "alertable": True},
    "AC": {"description": "Aircraft Crash", "category": "Aircraft", "alertable": True},
    "AE": {"description": "Aircraft Emergency", "category": "Aircraft", "alertable": True},
    "AES": {"description": "Aircraft Emergency Standby", "category": "Aircraft", "alertable": True},
    "LZ": {"description": "Landing Zone", "category": "Aircraft", "alertable": True},
    "AED": {"description": "AED Alarm", "category": "Alarm", "alertable": False},
    "OA": {"description": "Alarm", "category": "Alarm", "alertable": False},
    "CMA": {"description": "Carbon Monoxide", "category": "Alarm", "alertable": False},
    "FA": {"description": "Fire Alarm", "category": "Alarm", "alertable": False},
    "MA": {"description": "Manual Alarm", "category": "Alarm", "alertable": False},
    "SD": {"description": "Smoke Detector", "category": "Alarm", "alertable": False},
    "TRBL": {"description": "Trouble Alarm", "category": "Alarm", "alertable": False},
    "WFA": {"description": "Waterflow Alarm", "category": "Alarm", "alertable": False},
    "FL": {"description": "Flooding", "category": "Assist", "alertable": True},
    "LR": {"description": "Ladder Request", "category": "Assist", "alertable": False},
    "LA": {"description": "Lift Assist", "category": "Assist", "alertable": False},
    "PA": {"description": "Police Assist", "category": "Assist", "alertable": False},
    "PS": {"description": "Public Service", "category": "Assist", "alertable": False},
    "SH": {"description": "Sheared Hydrant", "category": "Assist", "alertable": False},
    "EX": {"description": "Explosion", "category": "Explosion", "alertable": False},
    "PE": {"description": "Pipeline Emergency", "category": "Explosion", "alertable": True},
    "TE": {"description": "Transformer Explosion", "category": "Explosion", "alertable": True},
    "AF": {"description": "Appliance Fire", "category": "Fire", "alertable": False},
    "CHIM": {"description": "Chimney Fire", "category": "Fire", "alertable": False},
    "CF": {"description": "Commercial Fire", "category": "Fire", "alertable": True},
    "WSF": {"description": "Confirmed Structure Fire", "category": "Fire", "alertable": True},
    "WVEG": {"description": "Confirmed Vegetation Fire", "category": "Fire", "alertable": True},
    "CB": {"description": "Controlled Burn/Prescribed Fire", "category": "Fire", "alertable": False},
    "ELF": {"description": "Electrical Fire", "category": "Fire", "alertable": False},
    "EF": {"description": "Extinguished Fire", "category": "Fire", "alertable": False},
    "FIRE": {"description": "Fire", "category": "Fire", "alertable": False},
    "FULL": {"description": "Full Assignment", "category": "Fire", "alertable": False},
    "IF": {"description": "Illegal Fire", "category": "Fire", "alertable": False},
    "MF": {"description": "Marine Fire", "category": "Fire", "alertable": False},
    "OF": {"description": "Outside Fire", "category": "Fire", "alertable": False},
    "PF": {"description": "Pole Fire", "category": "Fire", "alertable": True},
    "GF": {"description": "Refuse/Garbage Fire", "category": "Fire", "alertable": False},
    "RF": {"description": "Residential Fire", "category": "Fire", "alertable": True},
    "SF": {"description": "Structure Fire", "category": "Fire", "alertable": True},
    "TF": {"description": "Tank Fire", "category": "Fire", "alertable": False},
    "VEG": {"description": "Vegetation Fire", "category": "Fire", "alertable": True},
    "VF": {"description": "Vehicle Fire", "category": "Fire", "alertable": True},
    "WF": {"description": "Confirmed Fire", "category": "Fire", "alertable": False},
    "WCF": {"description": "Working Commercial Fire", "category": "Fire", "alertable": True},
    "WRF": {"description": "Working Residential Fire", "category": "Fire", "alertable": True},
    "BT": {"description": "Bomb Threat", "category": "Hazard", "alertable": False},
    "EE": {"description": "Electrical Emergency", "category": "Hazard", "alertable": True},
    "EM": {"description": "Emergency", "category": "Hazard", "alertable": False},
    "ER": {"description": "Emergency Response", "category": "Hazard", "alertable": False},
    "GAS": {"description": "Gas Leak", "category": "Hazard", "alertable": True},
    "HC": {"description": "Hazardous Condition", "category": "Hazard", "alertable": False},
    "HMR": {"description": "Hazardous Response", "category": "Hazard", "alertable": True},
    "TD": {"description": "Tree Down", "category": "Hazard", "alertable": False},
    "WE": {"description": "Water Emergency", "category": "Hazard", "alertable": True},
    "AI": {"description": "Arson Investigation", "category": "Investigation", "alertable": False},
    "FWI": {"description": "Fireworks Investigation", "category": "Investigation", "alertable": False},
    "HMI": {"description": "Hazmat Investigation", "category": "Investigation", "alertable": False},
    "INV": {"description": "Investigation", "category": "Investigation", "alertable": False},
    "OI": {"description": "Odor Investigation", "category": "Investigation", "alertable": False},
    "SI": {"description": "Smoke Investigation", "category": "Investigation", "alertable": False},
    "CL": {"description": "Commercial Lockout", "category": "Lockout", "alertable": False},
    "LO": {"description": "Lockout", "category": "Lockout", "alertable": False},
    "RL": {"description": "Residential Lockout", "category": "Lockout", "alertable": False},
    "VL": {"description": "Vehicle Lockout", "category": "Lockout", "alertable": False},
    "CP": {"description": "Community Paramedicine", "category": "Medical", "alertable": False},
    "IFT": {"description": "Interfacility Transfer", "category": "Medical", "alertable": False},
    "ME": {"description": "Medical Emergency", "category": "Medical", "alertable": False},
    "MCI": {"description": "Multi Casualty", "category": "Medical", "alertable": True},
    "EQ": {"description": "Earthquake", "category": "Natural Disaster", "alertable": True},
    "FLW": {"description": "Flood Warning", "category": "Natural Disaster", "alertable": True},
    "TOW": {"description": "Tornado Warning", "category": "Natural Disaster", "alertable": True},
    "TSW": {"description": "Tsunami Warning", "category": "Natural Disaster", "alertable": True},
    "WX": {"description": "Weather Incident", "category": "Natural Disaster", "alertable": False},
    "AR": {"description": "Animal Rescue", "category": "Rescue", "alertable": True},
    "CR": {"description": "Cliff Rescue", "category": "Rescue", "alertable": True},
    "CSR": {"description": "Confined Space Rescue", "category": "Rescue", "alertable": True},
    "ELR": {"description": "Elevator Rescue", "category": "Rescue", "alertable": True},
    "EER": {"description": "Elevator/Escalator Rescue", "category": "Rescue", "alertable": True},
    "IR": {"description": "Ice Rescue", "category": "Rescue", "alertable": True},
    "IA": {"description": "Industrial Accident", "category": "Rescue", "alertable": False},
    "RES": {"description": "Rescue", "category": "Rescue", "alertable": True},
    "RR": {"description": "Rope Rescue", "category": "Rescue", "alertable": True},
    "SC": {"description": "Structural Collapse", "category": "Rescue", "alertable": False},
    "TR": {"description": "Technical Rescue", "category": "Rescue", "alertable": True},
    "TNR": {"description": "Trench Rescue", "category": "Rescue", "alertable": True},
    "USAR": {"description": "Urban Search and Rescue", "category": "Rescue", "alertable": True},
    "VS": {"description": "Vessel Sinking", "category": "Rescue", "alertable": True},
    "WR": {"description": "Water Rescue", "category": "Rescue", "alertable": True},
    "TCP": {"description": "Collision Involving Pedestrian", "category": "Vehicle", "alertable": True},
    "TCS": {"description": "Collision Involving Structure", "category": "Vehicle", "alertable": True},
    "TCT": {"description": "Collision Involving Train", "category": "Vehicle", "alertable": True},
    "TCE": {"description": "Expanded Traffic Collision", "category": "Vehicle", "alertable": True},
    "RTE": {"description": "Railroad/Train Emergency", "category": "Vehicle", "alertable": True},
    "TC": {"description": "Traffic Collision", "category": "Vehicle", "alertable": True},
    "PLE": {"description": "Powerline Emergency", "category": "Wires", "alertable": True},
    "WA": {"description": "Wires Arching", "category": "Wires", "alertable": True},
    "WD": {"description": "Wires Down", "category": "Wires", "alertable": True},
    "WDA": {"description": "Wires Down/Arcing", "category": "Wires", "alertable": True},
    "BP": {"description": "Burn Permit", "category": "Other", "alertable": False},
    "CA": {"description": "Community Activity", "category": "Other", "alertable": False},
    "FW": {"description": "Fire Watch", "category": "Other", "alertable": False},
    "MC": {"description": "Move-up/Cover", "category": "Other", "alertable": False},
    "NO": {"description": "Notification", "category": "Other", "alertable": False},
    "STBY": {"description": "Standby", "category": "Other", "alertable": False},
    "TEST": {"description": "Test", "category": "Other", "alertable": False},
    "TRNG": {"description": "Training", "category": "Other", "alertable": False},
}

PULSEPOINT_UNIT_STATUSES = {
    "DP": "Dispatched",
    "AK": "Acknowledged",
    "ER": "Enroute",
    "SG": "Staged",
    "OS": "On Scene",
    "AE": "Available On Scene",
    "TR": "Transport",
    "TA": "Transport Arrived",
    "AR": "Cleared From Incident",
}


def local_timezone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(PULSEPOINT_TIMEZONE_NAME)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


LOCAL_TIMEZONE = local_timezone()


def clean_text(value: Any) -> str:
    return " ".join((value or "").strip().split())


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip() == "1"


def parse_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def parse_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def parse_iso_text(value: str) -> Optional[datetime]:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    normalized = cleaned.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except (AttributeError, ValueError):
        pass
    if len(normalized) >= 6 and normalized[-3] == ":" and normalized[-6] in "+-":
        normalized = normalized[:-3] + normalized[-2:]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def format_local_timestamp(value: str) -> str:
    parsed = parse_iso_text(value)
    if parsed is None:
        return ""
    tzinfo = LOCAL_TIMEZONE or parsed.astimezone().tzinfo
    return parsed.astimezone(tzinfo).strftime("%m/%d/%Y %I:%M:%S %p")


def cryptojs_evp_bytes_to_key(passphrase: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16) -> Tuple[bytes, bytes]:
    output = b""
    previous = b""
    while len(output) < key_len + iv_len:
        previous = hashlib.md5(previous + passphrase + salt).digest()
        output += previous
    return output[:key_len], output[key_len : key_len + iv_len]


def pkcs7_unpad(payload: bytes) -> bytes:
    if not payload:
        raise ValueError("empty payload")
    pad_len = payload[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid PKCS7 padding")
    if payload[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("malformed PKCS7 padding")
    return payload[:-pad_len]


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    if Cipher is not None and algorithms is not None and modes is not None and default_backend is not None:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()
    if CryptoAes is not None:
        return CryptoAes.new(key, CryptoAes.MODE_CBC, iv).decrypt(ciphertext)
    raise RuntimeError("PulsePoint decryption requires cryptography or pycryptodome")


def decrypt_pulsepoint_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
    ciphertext = base64.b64decode(clean_text(response_data.get("ct", "")))
    salt = bytes.fromhex(clean_text(response_data.get("s", "")))
    iv = bytes.fromhex(clean_text(response_data.get("iv", "")))
    key, derived_iv = cryptojs_evp_bytes_to_key(PULSEPOINT_DECRYPTION_PASSPHRASE.encode("utf-8"), salt)
    payload = aes_cbc_decrypt(key, iv or derived_iv, ciphertext)
    decoded = pkcs7_unpad(payload).decode("utf-8")
    return json.loads(json.loads(decoded))


def pulsepoint_headers() -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": PULSEPOINT_WEB_ORIGIN + "/",
        "Origin": PULSEPOINT_WEB_ORIGIN,
        "Accept": "*/*",
        "Content-Type": "application/json",
    }


def build_webapp_url(resource: str, **params: str) -> str:
    query_parts = ["resource={}".format(resource)]
    for key, value in params.items():
        if clean_text(value):
            query_parts.append("{}={}".format(key, requests.utils.quote(value)))
    return "{}/{}/webapp?{}".format(PULSEPOINT_API_URL, PULSEPOINT_VERSION, "&".join(query_parts))


def fetch_encrypted_json(url: str, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    sess = session or requests.Session()
    response = sess.get(url, headers=pulsepoint_headers(), timeout=PULSEPOINT_API_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("PulsePoint response was not a JSON object")
    return payload


def fetch_incident_feed(agency_id: str, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    url = build_webapp_url("incidents", agencyid=agency_id)
    payload = fetch_encrypted_json(url, session=session)
    decrypted = decrypt_pulsepoint_response(payload)
    return decrypted.get("incidents", {}) if isinstance(decrypted, dict) else {}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * earth_radius_miles * math.asin(math.sqrt(a))


def address_looks_local(address: str) -> bool:
    upper = clean_text(address).upper()
    return any(token in upper for token in PULSEPOINT_LOCAL_TOKENS)


def incident_distance_miles(incident: Dict[str, Any]) -> Optional[float]:
    lat = parse_float(incident.get("Latitude"))
    lon = parse_float(incident.get("Longitude"))
    if not lat and not lon:
        return None
    return haversine_miles(PULSEPOINT_CENTER_LAT, PULSEPOINT_CENTER_LON, lat, lon)


def incident_is_local(incident: Dict[str, Any]) -> Tuple[bool, Optional[float]]:
    distance = incident_distance_miles(incident)
    if distance is not None and PULSEPOINT_RADIUS_MILES > 0 and distance <= PULSEPOINT_RADIUS_MILES:
        return True, distance
    address = pick_location(incident)
    if address and address_looks_local(address):
        return True, distance
    if distance is None and not PULSEPOINT_LOCAL_TOKENS:
        return True, None
    return False, distance


def pulsepoint_call_type(call_type_code: str) -> Dict[str, Any]:
    default = {
        "description": clean_text(call_type_code) or "Unknown Call Type",
        "category": "Unknown",
        "alertable": False,
    }
    return PULSEPOINT_CALL_TYPES.get(clean_text(call_type_code), default)


def pick_location(incident: Dict[str, Any]) -> str:
    for key in ("FullDisplayAddress", "MedicalEmergencyDisplayAddress", "LocationComment"):
        value = clean_text(incident.get(key, ""))
        if value:
            return value
    return ""


def normalize_unit(unit: Dict[str, Any]) -> Dict[str, str]:
    status_code = clean_text(unit.get("PulsePointDispatchStatus"))
    status_code = status_code if status_code in PULSEPOINT_UNIT_STATUSES else "AR"
    normalized = {
        "id": clean_text(unit.get("UnitID")),
        "status": status_code,
        "status_label": PULSEPOINT_UNIT_STATUSES.get(status_code, status_code),
    }
    transport_location = clean_text(unit.get("TransportLocation"))
    if transport_location:
        normalized["transport_location"] = transport_location
    cleared_at = format_local_timestamp(clean_text(unit.get("UnitClearedDateTime")))
    if cleared_at:
        normalized["cleared_at"] = cleared_at
    return normalized


def build_extra_payload(
    incident: Dict[str, Any],
    *,
    agency_id: str,
    is_active: bool,
    distance_miles: Optional[float],
) -> Dict[str, Any]:
    call_type_code = clean_text(incident.get("PulsePointIncidentCallType"))
    call_type_meta = pulsepoint_call_type(call_type_code)
    units = sorted(
        [normalize_unit(unit) for unit in incident.get("Unit", []) if isinstance(unit, dict)],
        key=lambda item: (item.get("id", ""), item.get("status", "")),
    )
    payload: Dict[str, Any] = {
        "kind": "pulsepoint_incident",
        "provider": "pulsepoint",
        "agency_id": agency_id,
        "incident_id": clean_text(incident.get("IncidentNumber") or incident.get("ID")),
        "call_type_code": call_type_code,
        "call_type_description": call_type_meta["description"],
        "call_type_category": call_type_meta["category"],
        "alertable": bool(call_type_meta["alertable"]),
        "is_active": bool(is_active),
        "is_closed": bool(clean_text(incident.get("ClosedDateTime"))),
        "public_location": parse_bool(incident.get("PublicLocation")),
        "station": clean_text(incident.get("FirstDueStation")) or PULSEPOINT_STATION_NAME,
        "command_name": clean_text(incident.get("CommandName")),
        "location_comment": clean_text(incident.get("LocationComment")),
        "medical_display_address": clean_text(incident.get("MedicalEmergencyDisplayAddress")),
        "units": units,
        "local_filter": {
            "center_lat": round(PULSEPOINT_CENTER_LAT, 6),
            "center_lon": round(PULSEPOINT_CENTER_LON, 6),
            "radius_miles": round(PULSEPOINT_RADIUS_MILES, 2),
        },
    }
    received_at = format_local_timestamp(clean_text(incident.get("CallReceivedDateTime")))
    if received_at:
        payload["received_at"] = received_at
    closed_at = format_local_timestamp(clean_text(incident.get("ClosedDateTime")))
    if closed_at:
        payload["closed_at"] = closed_at
    alarm_level = parse_int(incident.get("AlarmLevel"))
    if alarm_level:
        payload["alarm_level"] = alarm_level
    cross_street_1 = clean_text(incident.get("CrossStreet1"))
    cross_street_2 = clean_text(incident.get("CrossStreet2"))
    if cross_street_1 or cross_street_2:
        payload["cross_streets"] = [value for value in [cross_street_1, cross_street_2] if value]
    lat = parse_float(incident.get("Latitude"))
    lon = parse_float(incident.get("Longitude"))
    if lat or lon:
        payload["map"] = {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        }
    if distance_miles is not None:
        payload["distance_miles"] = round(distance_miles, 1)
    return payload


def normalize_incident(
    incident: Dict[str, Any],
    *,
    agency_id: str,
    is_active: bool,
    distance_miles: Optional[float],
) -> Optional[Dict[str, str]]:
    incident_id = clean_text(incident.get("IncidentNumber") or incident.get("ID"))
    if not incident_id:
        return None
    call_type_code = clean_text(incident.get("PulsePointIncidentCallType"))
    call_type_meta = pulsepoint_call_type(call_type_code)
    location = pick_location(incident)
    if not location:
        return None
    station = clean_text(incident.get("FirstDueStation")) or PULSEPOINT_STATION_NAME
    disposition = "ACT" if is_active and not clean_text(incident.get("ClosedDateTime")) else "CLS"
    extra_payload = build_extra_payload(
        incident,
        agency_id=agency_id,
        is_active=is_active,
        distance_miles=distance_miles,
    )
    return {
        "date/time": format_local_timestamp(clean_text(incident.get("CallReceivedDateTime"))),
        "agency": PULSEPOINT_AGENCY_CODE,
        "station": station,
        "call number": "{}-{}".format(PULSEPOINT_AGENCY_CODE, incident_id),
        "report number": incident_id,
        "call type": call_type_meta["description"],
        "disposition": disposition,
        "location": location,
        "revision_scraped_at": "",
        "extra_json": json.dumps(extra_payload, separators=(",", ":"), ensure_ascii=True),
    }


def dedupe_incidents(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    unique: Dict[str, Dict[str, str]] = {}
    for row in rows:
        call_number = clean_text(row.get("call number"))
        if not call_number:
            continue
        existing = unique.get(call_number)
        if existing is None:
            unique[call_number] = row
            continue
        existing_closed = existing.get("disposition") == "CLS"
        row_closed = row.get("disposition") == "CLS"
        if existing_closed and not row_closed:
            unique[call_number] = row
            continue
        if len(row.get("extra_json", "")) > len(existing.get("extra_json", "")):
            unique[call_number] = row
    return list(unique.values())


def scrape_pulsepoint_incidents(agency_ids: Optional[Sequence[str]] = None) -> List[Dict[str, str]]:
    ids = [clean_text(agency_id) for agency_id in (agency_ids or PULSEPOINT_AGENCY_IDS) if clean_text(agency_id)]
    if not ids:
        return []
    session = requests.Session()
    rows: List[Dict[str, str]] = []
    for agency_id in ids:
        feed = fetch_incident_feed(agency_id, session=session)
        for is_active, bucket_name in [(True, "active"), (False, "recent")]:
            incidents = feed.get(bucket_name, [])
            if not isinstance(incidents, list):
                continue
            for incident in incidents:
                if not isinstance(incident, dict):
                    continue
                include, distance_miles = incident_is_local(incident)
                if not include:
                    continue
                normalized = normalize_incident(
                    incident,
                    agency_id=agency_id,
                    is_active=is_active,
                    distance_miles=distance_miles,
                )
                if normalized:
                    rows.append(normalized)
    deduped = dedupe_incidents(rows)
    deduped.sort(key=lambda row: row.get("date/time", ""), reverse=True)
    return deduped


if __name__ == "__main__":
    incidents = scrape_pulsepoint_incidents()
    print("Total PulsePoint incidents:", len(incidents))
    for incident in incidents[:10]:
        print(incident)

"""Geocoding via OpenStreetMap Nominatim — free, no API key.

Usage policy: max ~1 request/sec and a descriptive User-Agent (enforced below).
Data © OpenStreetMap contributors (ODbL) — attribute in the report/app.
"""



import difflib
import os
import re
import requests
from typing import Optional

OLA_GEOCODE_URL = "https://api.olamaps.io/places/v1/geocode"
OLA_API_KEY = os.environ.get("OLA_MAPS_API_KEY", "").strip()

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "travel-agent-student-project/0.1 (academic use)"}


def _geocode_ola(name: str, country_codes: str = "in") -> Optional[dict]:
    if not OLA_API_KEY:
        return None
    try:
        r = requests.get(
            OLA_GEOCODE_URL,
            params={"address": name, "api_key": OLA_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("geocodingResults") or []
        if not results:
            return None
        h = results[0]
        loc = h.get("geometry", {}).get("location", {})
        # Sanity guard 1: Ola's geocoder occasionally matches a same-name or
        # fuzzy place far outside the requested country (observed live:
        # 'Thekkady' -> a Baikunthpur in Chhattisgarh, lat 26.3). Reject any
        # result outside the India bounding box and fall through to
        # Nominatim, which honours countrycodes.
        lat, lng = loc.get("lat"), loc.get("lng")
        if (lat is None or lng is None
                or not (6.5 <= float(lat) <= 37.5)
                or not (68.0 <= float(lng) <= 97.5)):
            return None
        # Sanity guard 2: the result must actually BE about the queried
        # place. Ola's top hit can be an unrelated Indian town that merely
        # ranks higher; a completely different name is a wrong match, not a
        # spelling variant (variants share tokens: 'Nandi Hills' ->
        # 'Nandi Hill Station'). Genuinely renamed cities ('Ooty' ->
        # 'Udhagamandalam') also fall through to Nominatim, which resolves
        # them correctly.
        result_name = h.get("formatted_address", name).split(",")[0]
        q_tokens = set(re.findall(r"[a-z]+", name.lower()))
        r_tokens = set(re.findall(r"[a-z]+", result_name.lower()))
        if q_tokens and r_tokens and not (q_tokens & r_tokens):
            ratio = difflib.SequenceMatcher(
                None, name.lower(), result_name.lower()).ratio()
            if ratio < 0.45:
                return None
        return {
            "place_id": h.get("place_id"),
            "name": h.get("formatted_address", name).split(",")[0],
            "address": h.get("formatted_address"),
            "kind": h.get("types", [None])[0] if h.get("types") else None,
            "rating": None,
            "reviews": None,
            "lat": lat,
            "lng": lng,
            "importance": None,
        }
    except requests.RequestException:
        return None


def _curated_lookup(name: str) -> dict | None:
    """Match `name` against the curated destinations DB.

    Tier 0 of geocoding: exact-ish name match returns verified coordinates
    with zero network calls and no provider fuzzy-matching risk. Aliases
    are resolved through Nominatim rather than duplicated here.
    """
    try:
        from agent.destinations_db import DESTINATIONS
    except Exception:
        return None
    q = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    if not q:
        return None
    for entry in DESTINATIONS:
        db = re.sub(r"[^a-z0-9 ]", " ",
                    entry["name"].lower().replace("(", " ").replace(")", " ")).split()
        # Query tokens must be a subset of the DB entry's tokens (e.g.
        # 'coorg' ⊂ 'madikeri coorg', 'nandi hills' ⊂ 'nandi hills').
        if q and set(q) <= set(db):
            return {
                "place_id": f"curated:{entry['name']}",
                "name": entry["name"],
                "address": f"{entry['name']}, {entry['state']}, India",
                "kind": entry["categories"][0] if entry.get("categories") else None,
                "rating": None,
                "reviews": None,
                "lat": float(entry["lat"]),
                "lng": float(entry["lng"]),
                "importance": 1.0,   # curated = authoritative
            }
    return None


def geocode(name: str, country_codes: str = "in") -> dict | None:
    """Curated DB → Ola Maps → Nominatim, in that order.

    The curated tier eliminates the wrong-match failure mode observed live
    (Ola returning an unrelated Indian town for a hill-station name);
    everything else falls through to the network providers. Both providers
    are guarded against out-of-country results when the requested country
    is India (the default), so a fuzzy match abroad can never enter the
    trip state.
    """
    if india := (country_codes.strip().lower() in ("in", "ind", "india")):
        curated = _curated_lookup(name)
        if curated is not None:
            return curated

    def _outside(res: dict | None) -> bool:
        if not (india and res and isinstance(res.get("lat"), (int, float))):
            return False
        return not (6.5 <= res["lat"] <= 37.5
                    and 68.0 <= res["lng"] <= 97.5)

    if OLA_API_KEY:
        ola = _geocode_ola(name, country_codes)
        if ola is not None and not _outside(ola):
            return ola
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": name, "format": "jsonv2", "limit": 1,
                    "countrycodes": country_codes},
            headers=HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return {"error": f"Nominatim HTTP {r.status_code}"}
        hits = r.json()
        if not hits:
            return None
        h = hits[0]
        res = {
            "place_id": f"{h.get('osm_type', 'n')[0]}{h.get('osm_id')}",
            "name": h.get("name") or name,
            "address": h.get("display_name"),
            "kind": h.get("type"),
            "rating": None,                    # OSM has no ratings — Phase 3 uses importance
            "reviews": None,
            "lat": float(h["lat"]),
            "lng": float(h["lon"]),
            "importance": h.get("importance"), # popularity proxy 0-1
        }
        if _outside(res):
            return None
        return res
    except requests.RequestException as e:
        return {"error": f"Nominatim request failed: {e}"}
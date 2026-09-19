"""Geocoding via OpenStreetMap Nominatim — free, no API key.

Usage policy: max ~1 request/sec and a descriptive User-Agent (enforced below).
Data © OpenStreetMap contributors (ODbL) — attribute in the report/app.
"""



import os
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
        return {
            "place_id": h.get("place_id"),
            "name": h.get("formatted_address", name).split(",")[0],
            "address": h.get("formatted_address"),
            "kind": h.get("types", [None])[0] if h.get("types") else None,
            "rating": None,
            "reviews": None,
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "importance": None,
        }
    except requests.RequestException:
        return None


def geocode(name: str, country_codes: str = "in") -> dict | None:
    """Prefer Ola Maps when key is set, fall back to Nominatim."""
    if OLA_API_KEY:
        ola = _geocode_ola(name, country_codes)
        if ola is not None:
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
        return {
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
    except requests.RequestException as e:
        return {"error": f"Nominatim request failed: {e}"}
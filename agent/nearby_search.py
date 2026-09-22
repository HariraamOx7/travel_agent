"""Generic proximity + category search over the curated destinations DB.

Replaces the hill-specific hill_stations_near() with one function that
filters DESTINATIONS by haversine distance and category overlap.
"""
import math

from agent.destinations_db import DESTINATIONS


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    p = math.pi / 180
    h = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * (1 - math.cos((lng2 - lng1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))


def find_nearby_destinations(
    lat: float,
    lng: float,
    category: str | None = None,
    radius_km: float = 600,
    limit: int = 40,
) -> list[dict]:
    """Destinations within radius_km, closest first.

    category: optional category filter (hill_station, beach, temple,
    wildlife, ...). None returns every destination regardless of category.
    """
    cat = (category or "").strip().lower()
    out = []
    for entry in DESTINATIONS:
        if cat and cat not in entry.get("categories", []):
            continue
        d = _haversine_km(lat, lng, entry["lat"], entry["lng"])
        if d > radius_km:
            continue
        out.append({
            "name": entry["name"],
            "state": entry["state"],
            "lat": entry["lat"],
            "lng": entry["lng"],
            "elevation": entry.get("elevation_m"),
            "categories": entry.get("categories", []),
            "distance_km": round(d, 1),
            "fcode": "hill_station" if cat == "hill_station" else (cat or "destination"),
            "source": "curated_destinations_db",
            "population": 0,
            "admin": entry["state"],
            "country": "India",
        })
    out.sort(key=lambda c: c["distance_km"])
    return out[:limit]

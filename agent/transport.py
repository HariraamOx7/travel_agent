"""Transport mode recommendation between origin and destination.

Cost and time are estimates from a documented model — no live prices.

Road distance is resolved in three tiers, in order:
  1. Ola Maps Distance Matrix API (real road distance + duration)
     — used when OLA_MAPS_API_KEY is set in .env
  2. ROUTE_OVERRIDES lookup table (hand-tuned for common Indian pairs)
  3. Haversine great-circle distance × 1.3 road factor (final fallback)

Every tier is optional. The module works with zero configuration, gets
better as tiers are added, and never crashes if Ola is unreachable.
"""
from __future__ import annotations
import math
import os
import time
from typing import Optional

import requests

# --------------------------------------------------------------------------- #
# Ola Maps configuration
# --------------------------------------------------------------------------- #

from dotenv import load_dotenv
load_dotenv()

# --------------------------------------------------------------------------- #
# Ola Maps configuration
# --------------------------------------------------------------------------- #


OLA_DISTANCE_URL = "https://api.olamaps.io/routing/v1/distanceMatrix"
OLA_API_KEY = os.environ.get("OLA_MAPS_API_KEY", "").strip()

# Cache road distances for the session so repeated queries don't burn quota.
# Key is a rounded (origin, dest) tuple; value is (distance_km, timestamp).
_DISTANCE_CACHE: dict[tuple, tuple[float, float]] = {}
_DISTANCE_CACHE_TTL = 3600 * 6   # 6 hours


# --------------------------------------------------------------------------- #
# Transport cost / time model
# --------------------------------------------------------------------------- #

# Per-km and fixed components for each mode (one-way, per person).
# cost = base + per_km * distance_km ; hours = fixed_hours + distance / speed
MODEL = {
    "flight": {"base": 1500, "per_km": 4.0,  "fixed_h": 2.0, "speed": 600},
    "train":  {"base": 150,  "per_km": 0.7,  "fixed_h": 0.5, "speed": 55},
    "bus":    {"base": 200,  "per_km": 0.9,  "fixed_h": 0.3, "speed": 45},
    "car":    {"base": 0,    "per_km": 0.0,  "fixed_h": 0.0, "speed": 45,
               "mileage": 15, "fuel_per_l": 100},   # petrol, mid-SUV
    "bike":   {"base": 0,    "per_km": 0.0,  "fixed_h": 0.0, "speed": 40,
               "mileage": 40, "fuel_per_l": 100},   # 150cc commuter
}

# Hand-tuned overrides for common pairs. Distance in km is road-distance,
# not straight-line. Used when Ola is unavailable.
ROUTE_OVERRIDES = {
    ("chennai", "munnar"):     {"distance_km": 590, "flight_via": "Kochi"},
    ("chennai", "ooty"):       {"distance_km": 550, "flight_via": "Coimbatore"},
    ("chennai", "kodaikanal"): {"distance_km": 520, "flight_via": "Madurai"},
    ("chennai", "yercaud"):    {"distance_km": 360, "flight_via": None},
    ("chennai", "coorg"):      {"distance_km": 640, "flight_via": "Mangaluru"},
    ("chennai", "wayanad"):    {"distance_km": 610, "flight_via": "Kozhikode"},
    ("chennai", "yelagiri"):   {"distance_km": 230, "flight_via": None},
    ("chennai", "chikkamagaluru"): {"distance_km": 610, "flight_via": "Mangaluru"},
    ("chennai", "madikeri"):   {"distance_km": 640, "flight_via": "Mangaluru"},
    ("chennai", "horsley hills"): {"distance_km": 280, "flight_via": None},
    ("bangalore", "coorg"):    {"distance_km": 265, "flight_via": None},
    ("bangalore", "ooty"):     {"distance_km": 270, "flight_via": None},
    ("bangalore", "munnar"):   {"distance_km": 475, "flight_via": "Kochi"},
    ("mumbai", "mahabaleshwar"): {"distance_km": 265, "flight_via": None},
    ("mumbai", "lonavala"):    {"distance_km": 95,  "flight_via": None},
    ("delhi", "nainital"):     {"distance_km": 300, "flight_via": None},
    ("delhi", "shimla"):       {"distance_km": 350, "flight_via": None},
    ("kolkata", "darjeeling"): {"distance_km": 620, "flight_via": "Bagdogra"},
}


# --------------------------------------------------------------------------- #
# Ola Maps Distance Matrix
# --------------------------------------------------------------------------- #

def _ola_cache_key(origin_coords, dest_coords) -> tuple:
    """Round coords to ~100m so slightly different inputs share cache."""
    return (
        round(origin_coords[0], 3), round(origin_coords[1], 3),
        round(dest_coords[0], 3), round(dest_coords[1], 3),
    )


def _ola_road_distance_km(
    origin_coords: tuple[float, float],
    dest_coords: tuple[float, float],
) -> Optional[float]:
    """Query Ola Maps Distance Matrix for the real road distance.

    Returns distance in km on success, None on any failure (no key,
    network error, non-200, malformed response, rate limit).

    The Ola Distance Matrix API accepts origins and destinations as
    pipe-separated lat,lng pairs and returns distance in metres.
    """
    if not OLA_API_KEY:
        return None
    if not origin_coords or not dest_coords:
        return None

    cache_key = _ola_cache_key(origin_coords, dest_coords)
    cached = _DISTANCE_CACHE.get(cache_key)
    if cached is not None:
        distance, ts = cached
        if time.time() - ts < _DISTANCE_CACHE_TTL:
            return distance

    try:
        origin_str = f"{origin_coords[0]},{origin_coords[1]}"
        dest_str = f"{dest_coords[0]},{dest_coords[1]}"

        r = requests.get(
            OLA_DISTANCE_URL,
            params={
                "origins": origin_str,
                "destinations": dest_str,
                "api_key": OLA_API_KEY,
            },
            timeout=12,
        )

        if r.status_code == 429:
            print("  [ola] rate limited on Distance Matrix", flush=True)
            return None
        if r.status_code != 200:
            print(f"  [ola] Distance Matrix HTTP {r.status_code}", flush=True)
            return None

        data = r.json()
        # Expected shape:
        # { "rows": [ { "elements": [ { "distance": 12345, "duration": 900 } ] } ] }
        meters = (
            data.get("rows", [{}])[0]
                .get("elements", [{}])[0]
                .get("distance")
        )
        if meters is None:
            return None

        km = round(float(meters) / 1000.0, 1)
        _DISTANCE_CACHE[cache_key] = (km, time.time())
        print(f"  [ola] road distance {km} km", flush=True)
        return km

    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
        print(f"  [ola] Distance Matrix failed: {type(e).__name__}", flush=True)
        return None


# --------------------------------------------------------------------------- #
# Fallback road distance
# --------------------------------------------------------------------------- #

def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km."""
    lat1, lng1 = a
    lat2, lng2 = b
    p = math.pi / 180
    h = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * (1 - math.cos((lng2 - lng1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))


def _road_distance_km(
    origin_coords,
    dest_coords,
    origin_name: str,
    dest_name: str,
) -> float:
    """Resolve road distance through the three tiers.

    1. Ola Maps (real roads, real traffic ignores — the API returns
       distance, not duration-weighted distance)
    2. ROUTE_OVERRIDES for known pairs
    3. Haversine × 1.3 road factor
    """
    # Tier 1 — Ola.
    if OLA_API_KEY and origin_coords and dest_coords:
        ola_km = _ola_road_distance_km(origin_coords, dest_coords)
        if ola_km is not None and ola_km > 0:
            return ola_km

    # Tier 2 — override table.
    key = (origin_name.lower().strip(), dest_name.lower().strip())
    if key in ROUTE_OVERRIDES:
        return float(ROUTE_OVERRIDES[key]["distance_km"])

    # Tier 3 — haversine fallback.
    if not origin_coords or not dest_coords:
        return 0.0
    straight = _haversine_km(origin_coords, dest_coords)
    return round(straight * 1.3, 0)


# --------------------------------------------------------------------------- #
# Option ranking
# --------------------------------------------------------------------------- #

def recommend(
    origin: str,
    dest: str,
    origin_coords,
    dest_coords,
    travellers: int = 1,
    pace: str = "balanced",
) -> dict:
    """Return ranked transport options with cost + time + a fit label.

    Cost and time are documented estimates, not live prices. Road distance
    is real when Ola is configured, otherwise a close approximation.
    """
    distance_km = _road_distance_km(
        origin_coords, dest_coords, origin, dest,
    )
    if distance_km <= 0:
        return {
            "error": "could not compute distance — origin or dest coords missing"
        }

    key = (origin.lower().strip(), dest.lower().strip())
    override = ROUTE_OVERRIDES.get(key, {})
    flight_via = override.get("flight_via")

    options = []

    for mode, spec in MODEL.items():
        if "mileage" in spec:
            # Fuel cost for the whole vehicle, round trip.
            fuel_l = (distance_km * 2) / spec["mileage"]
            total = fuel_l * spec["fuel_per_l"]
            hours = distance_km / spec["speed"]
            notes = [f"~{fuel_l:.0f} L fuel (round trip)"]
            if mode == "car" and travellers > 4:
                notes.append("may need 2 vehicles")
            cost_range = (int(total * 0.9), int(total * 1.15))
        else:
            per_person = spec["base"] + spec["per_km"] * distance_km
            cost = per_person * travellers
            hours = spec["fixed_h"] + distance_km / spec["speed"]
            cost_range = (int(cost * 0.85), int(cost * 1.2))
            notes = []
            if mode == "flight" and flight_via:
                notes.append(f"via {flight_via} + road transfer")
            if mode == "train":
                notes.append("sleeper class reference")
            if mode == "bus":
                notes.append("AC semi-sleeper reference")

        options.append({
            "mode": mode,
            "distance_km": round(distance_km, 0),
            "hours": round(hours, 1),
            "cost_inr_low": cost_range[0],
            "cost_inr_high": cost_range[1],
            "cost_label": f"₹{cost_range[0]:,} – ₹{cost_range[1]:,}",
            "notes": " · ".join(notes) if notes else None,
        })

    # "Best fit" labels — cheap heuristics that the LLM can quote.
    for o in options:
        if o["mode"] in ("car", "bike") and pace == "packed":
            o["fit"] = "flexible"
        elif o["mode"] == "flight" and distance_km > 400:
            o["fit"] = "fastest"
        elif o["mode"] == "train" and 200 < distance_km < 700:
            o["fit"] = "good value"
        elif o["mode"] == "bus":
            o["fit"] = "budget"
        else:
            o["fit"] = None

    return {
        "distance_km": distance_km,
        "distance_source": (
            "ola_maps" if OLA_API_KEY and origin_coords and dest_coords
                        and _DISTANCE_CACHE.get(_ola_cache_key(origin_coords, dest_coords))
            else "override_table" if key in ROUTE_OVERRIDES
            else "haversine_approximation"
        ),
        "options": options,
    }
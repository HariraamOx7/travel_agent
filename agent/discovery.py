"""Destination discovery via a two-source pipeline.

Stage 1 — Overpass (primary): queries OSM for every populated place and
          natural feature within `radius_km` of the origin. No auth, no
          radius cap, cached to disk for 24h. This is the wide net.

Stage 1b — GeoNames (fallback): only invoked if Overpass fails. Tiles
           the region into 300 km circles (free-tier cap), merges and
           dedupes. Slower and rate-limited, but a solid backup.

Stage 2 — Merge, dedupe, distance-sort. Overpass and GeoNames results
          are combined by name and proximity.

Stage 3 — GeoNames enrichment (optional): for the top N candidates,
          attach population and admin data to improve ranking. Skips
          gracefully if GeoNames is unavailable. Overpass-only
          candidates still pass through.

Stage 4 — Score and trim. Composite score = distance + feature type +
          population + name hints. Returns top `max_results`.

The returned dict always includes a `sources` field listing which
backends contributed, for the trace and for debugging.
"""
import math
import os
import time
from typing import Optional

import requests

from agent.state import DestinationCandidate

# --------------------------------------------------------------------------- #
# Overpass configuration (shared mirror list with pois.py — same reliability
# ordering, same caching discipline)
# --------------------------------------------------------------------------- #

_OVERPASS_MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

_OVERPASS_HEADERS = {
    "User-Agent": "travel-agent-student-project/0.1 "
                  "(academic; github.com/HariraamOx7/travel-agent)"
}

_CACHE_DIR = "cache"


# A "nearby" destination from a major city is never a 20 km suburb.
# 80 km is far enough to exclude the origin's own metro area, close
# enough that day-trip destinations still qualify.
_MIN_DESTINATION_DISTANCE_KM = 80

# OSM place types worth treating as destination candidates.
_PLACE_TYPES = "city|town|village"

# Natural features that often correspond to scenic destinations.
_NATURAL_FEATURES = "peak|spring|waterfall"

# Names containing these words are strong hill-station signals.
_HILL_NAME_HINTS = [
    "giri", "malai", "hills", "hill",
    # Tamil Nadu / Kerala
    "ooty", "udhagamandalam", "kodaikanal", "yercaud", "coonoor",
    "kotagiri", "kothagiri", "yelagiri", "valparai", "gudalur",
    "manjolai", "kolli", "sirumalai", "munnar", "thekkady",
    "peermade", "idukki", "vagamon", "ponmudi", "mudumalai",
    "bandipur", "willingdon",
    # Karnataka
    "chikmagalur", "chikkamagaluru", "coorg", "madikeri",
    "mudigere", "kemmangundi", "kudremukh", "nandi",
    # Andhra / others
    "horsley", "araku", "lambasingi", "paderu", "wayanad",
    "vythiri", "kakkadampoyil",
  "Aru Valley","Bhaderwah","Gulmarg","Gurez Valley","Kargil","Leh","Pahalgam","Patnitop",
  "Sonamarg","Srinagar","Yusmarg","Barot", "Chail", "Chamba", "Dalhousie", "Dharamshala & McLeod Ganj", "Jibhi",
  "Kalpa & Sangla Valley",
  "Kasauli","Kaza","Keylong","Khajjiar","Kufri","Kullu Valley","Manali","Palampur","Shimla","Solan","Almora","Auli","Bhimtal & Sattal","Chakrata",
  "Chopta", "Dehradun","Dhanaulti", "Joshimath", "Kausani", "Lansdowne", "Mukteshwar", "Munsiyari", "Mussoorie", "Nainital" "Ranikhet","Coonoor",
  "Gudalur", "Kodaikanal", "Kolli Hills", "Kotagiri", "Kurangani", "Manjolai",
  "Megamalai",
  "Mudumalai",
  "Ooty / Udhagamandalam",
  "Sirumalai",
  "Valparai",
  "Yelagiri",
  "Yercaud",
  "Athirappilly Hills",
  "Idukki",
  "Kakkadampoyil",
  "Malakkappara",
  "Munnar",
  "Peermade",
  "Ponmudi",
  "Thekkady",
  "Vagamon",
  "Vythiri & Lakkidi",
  "Wayanad",
  "Agumbe",
  "Baba Budangiri",
  "Bandipur",
  "Biligirirangana Hills / BR Hills",
  "Chikmagalur / Chikkamagaluru",
  "Coorg / Kodagu",
  "Kemmangundi",
  "Kodachadri",
  "Kudremukh",
  "Madikeri",
  "Mudigere",
  "Nandi Hills",
  "Ananthagiri Hills",
  "Araku Valley",
  "Horsley Hills",
  "Lambasingi",
  "Maredumilli",
  "Paderu",
  "Amboli",
  "Chikhaldara",
  "Chorla Ghat",
  "Igatpuri",
  "Karjat",
  "Lavasa",
  "Lonavala & Khandala",
  "Mahabaleshwar",
  "Matheran",
  "Mhaismal",
  "Panchgani",
  "Panhala",
  "Girnar",
  "Mount Abu",
  "Saputara",
  "Wilson Hills",
  "Amarkantak",
  "Chirmiri",
  "Mainpat",
  "Pachmarhi",
  "Darjeeling",
  "Daringbadi",
  "Kalimpong",
  "Kurseong",
  "Mirik",
  "Netarhat",
  "Ranchi",
  "Cherrapunjee / Sohra",
  "Haflong",
  "Jowai",
  "Mawsynram",
  "Shillong",
  "Bomdila & Dirang",
  "Gangtok",
  "Lachen & Lachung",
  "Pelling",
  "Tawang",
  "Ziro Valley",
  "Aizawl",
  "Kohima",
  "Lunglei",
  "Mokokchung",
  "Pfütsero",
  "Tamenglong",
  "Ukhrul"

]

# Stopwords that shouldn't be treated as candidate names even if OSM tags
# them as a populated place.
_NAME_STOPWORDS = {
    "the", "a", "an", "here", "there", "home", "unknown",
    "hill", "hills", "town", "city", "village",
}


# --------------------------------------------------------------------------- #
# GeoNames configuration
# --------------------------------------------------------------------------- #

_GEONAMES_URL = "https://secure.geonames.org/findNearbyPlaceNameJSON"
_GEONAMES_FREE_RADIUS_KM = 300     # hard cap on the free tier
_GEONAMES_USERNAME = os.environ.get("GEONAMES_USERNAME", "").strip()


# --------------------------------------------------------------------------- #
# Shared geometry
# --------------------------------------------------------------------------- #

def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    p = math.pi / 180
    h = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * (1 - math.cos((lng2 - lng1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- #
# Stage 1 — Overpass discovery
# --------------------------------------------------------------------------- #

def _overpass_query(lat: float, lng: float, radius_km: float) -> str:
    r = int(radius_km * 1000)
    return f"""
[out:json][timeout:60];
(
  node["place"~"^({_PLACE_TYPES})$"](around:{r},{lat},{lng});
  node["natural"~"^({_NATURAL_FEATURES})$"](around:{r},{lat},{lng});
);
out center 800;
"""


def _overpass_cache_path(lat: float, lng: float, radius_km: float) -> str:
    return os.path.join(
        _CACHE_DIR,
        f"discovery_{lat:.2f}_{lng:.2f}_{int(radius_km)}.json",
    )


def _parse_overpass(data: dict) -> list[dict]:
    """Turn raw Overpass elements into a uniform candidate list."""
    out: list[dict] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        if not name or name.lower() in _NAME_STOPWORDS:
            continue

        # Determine feature type.
        place = tags.get("place")
        natural = tags.get("natural")
        tourism = tags.get("tourism")

        if place:
            fcode = f"place={place}"
        elif natural:
            fcode = f"natural={natural}"
        elif tourism:
            fcode = f"tourism={tourism}"
        else:
            continue

        lat_v = el.get("lat")
        lng_v = el.get("lon")
        if lat_v is None or lng_v is None:
            continue

        ele_raw = tags.get("ele")
        elevation = None
        if ele_raw:
            try:
                elevation = float(
                    str(ele_raw).replace("m", "").strip().split()[0]
                )
            except (ValueError, IndexError):
                elevation = None

        out.append({
            "name": name,
            "lat": float(lat_v),
            "lng": float(lng_v),
            "fcode": fcode,
            "source": "overpass",
            "population": 0,
            "admin": tags.get("addr:state") or tags.get("is_in:state"),
            "country": tags.get("addr:country"),
            "elevation": elevation,
        })
    return out

def _overpass_query_bbox(south: float, west: float,
                         north: float, east: float) -> str:
    """Query a bounding box instead of a circle — cheaper for Overpass."""
    return f"""
[out:json][timeout:45];
(
  node["place"~"^({_PLACE_TYPES})$"]({south},{west},{north},{east});
  node["natural"~"^({_NATURAL_FEATURES})$"]({south},{west},{north},{east});
);
out center 800;
"""


def _tile_bboxes(origin_lat: float, origin_lng: float,
                 radius_km: float, tile_km: float = 150) -> list[tuple]:
    """Split the search area into a grid of bounding boxes."""
    km_per_deg_lat = 111.0
    km_per_deg_lng = 111.0 * math.cos(math.radians(origin_lat)) or 1.0

    d_lat = radius_km / km_per_deg_lat
    d_lng = radius_km / km_per_deg_lng

    south = origin_lat - d_lat
    north = origin_lat + d_lat
    west  = origin_lng - d_lng
    east  = origin_lng + d_lng

    step_lat = tile_km / km_per_deg_lat
    step_lng = tile_km / km_per_deg_lng

    boxes = []
    lat = south
    while lat < north:
        lng = west
        while lng < east:
            boxes.append((
                max(lat, south),
                max(lng, west),
                min(lat + step_lat, north),
                min(lng + step_lng, east),
            ))
            lng += step_lng
        lat += step_lat
    return boxes


def _discover_via_overpass(lat: float, lng: float, radius_km: float) -> dict:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = _overpass_cache_path(lat, lng, radius_km)

    if os.path.exists(cache_path) and \
            time.time() - os.path.getmtime(cache_path) < 86400:
        try:
            import json
            with open(cache_path, encoding="utf-8") as f:
                print("  [discovery/overpass] cache hit", flush=True)
                return json.load(f)
        except Exception:
            pass

    boxes = _tile_bboxes(lat, lng, radius_km, tile_km=150)
    print(f"  [discovery/overpass] {len(boxes)} tiles to query", flush=True)

    all_candidates: list[dict] = []
    failures = 0

    for i, (s, w, n, e) in enumerate(boxes):
        q = _overpass_query_bbox(s, w, n, e)
        got_one = False
        for url in _OVERPASS_MIRRORS:
            host = url.split("/")[2]
            try:
                resp = requests.post(
                    url, data={"data": q}, timeout=(5, 30),
                    headers=_OVERPASS_HEADERS,
                )
                if resp.status_code == 429:
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                all_candidates.extend(_parse_overpass(resp.json()))
                print(f"  [discovery/overpass] tile {i+1}/{len(boxes)} "
                      f"OK ({host})", flush=True)
                got_one = True
                break
            except (requests.RequestException, ValueError) as err:
                continue
        if not got_one:
            failures += 1
            print(f"  [discovery/overpass] tile {i+1}/{len(boxes)} "
                  f"failed on all mirrors", flush=True)
        time.sleep(1.5)  # polite to mirrors

    if not all_candidates:
        return {"error": f"all {len(boxes)} tiles failed"}

    # Dedupe within Overpass results (adjacent tiles may overlap).
    result = {"candidates": _merge_and_dedupe(all_candidates)}
    print(f"  [discovery/overpass] {len(all_candidates)} raw -> "
          f"{len(result['candidates'])} unique "
          f"({failures} tile(s) failed)", flush=True)

    import json
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


def _geonames_request(lat: float, lng: float, radius_km: int,
                      max_rows: int = 100) -> dict:
    """One GeoNames findNearbyPlaceName call. Returns {"places": [...]}
    or {"error": ...}."""
    if not _GEONAMES_USERNAME:
        return {"error": "GEONAMES_USERNAME not set"}

    radius = min(int(radius_km), _GEONAMES_FREE_RADIUS_KM)

    try:
        r = requests.get(
            _GEONAMES_URL,
            params={
                "lat": lat, "lng": lng,
                "radius": radius,
                "maxRows": max_rows,
                "featureClass": "P",
                "username": _GEONAMES_USERNAME,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return {"error": f"GeoNames request failed: {e}"}

    if "status" in data:
        return {"error": data["status"].get("message", "GeoNames error")}

    places = []
    for g in data.get("geonames", []):
        name = g.get("name") or g.get("toponymName")
        if not name or name.lower() in _NAME_STOPWORDS:
            continue
        g_lat, g_lng = g.get("lat"), g.get("lng")
        if g_lat is None or g_lng is None:
            continue
        places.append({
            "name": name,
            "lat": float(g_lat),
            "lng": float(g_lng),
            "fcode": g.get("fcode", ""),
            "source": "geonames",
            "population": int(g.get("population") or 0),
            "admin": g.get("adminName1"),
            "country": g.get("countryName"),
        })
    return {"places": places}


def _geonames_grid(origin_lat: float, origin_lng: float,
                   target_radius_km: float, step_km: float = 250) -> list[tuple[float, float]]:
    """Return a small grid of centres whose 300 km circles cover the target
    radius around the origin."""
    import math as _m
    # Always include the origin.
    centres = [(origin_lat, origin_lng)]

    if target_radius_km <= _GEONAMES_FREE_RADIUS_KM:
        return centres

    # Approximate km-per-degree at this latitude.
    km_per_deg_lat = 111.0
    km_per_deg_lng = 111.0 * _m.cos(_m.radians(origin_lat)) or 1.0

    rings = int(_m.ceil((target_radius_km - _GEONAMES_FREE_RADIUS_KM) / step_km))
    for ring in range(1, rings + 1):
        r = ring * step_km
        n_points = 6 * ring  # 6, 12, 18...
        for k in range(n_points):
            angle = 2 * _m.pi * k / n_points
            d_lat = (r * _m.cos(angle)) / km_per_deg_lat
            d_lng = (r * _m.sin(angle)) / km_per_deg_lng
            centres.append((origin_lat + d_lat, origin_lng + d_lng))

    return centres


def _discover_via_geonames_tiled(origin_lat: float, origin_lng: float,
                                 radius_km: float) -> dict:
    if not _GEONAMES_USERNAME:
        return {"error": "GEONAMES_USERNAME not set"}

    centres = _geonames_grid(origin_lat, origin_lng, radius_km)
    print(f"  [discovery/geonames] querying {len(centres)} centres",
          flush=True)

    merged: dict[str, dict] = {}
    errors = []
    for c_lat, c_lng in centres:
        res = _geonames_request(c_lat, c_lng, _GEONAMES_FREE_RADIUS_KM)
        if "error" in res:
            errors.append(res["error"])
            continue
        for p in res.get("places", []):
            key = p["name"].lower()
            # Keep the record with the larger population.
            if key not in merged or p["population"] > merged[key]["population"]:
                merged[key] = p
        time.sleep(1.0)  # be polite to GeoNames

    if not merged:
        return {"error": f"GeoNames tiled discovery returned nothing. "
                         f"errors={errors[:3]}"}

    return {"candidates": list(merged.values())}


# --------------------------------------------------------------------------- #
# Stage 2 — merge and dedupe
# --------------------------------------------------------------------------- #

def _merge_and_dedupe(candidates: list[dict],
                      min_km: float = 3.0) -> list[dict]:
    """Combine results from both sources. Prefer the record with more
    metadata; drop near-duplicates by proximity."""
    # Sort so Overpass records come first (they carry richer feature type),
    # then larger-population GeoNames records.
    ranked = sorted(
        candidates,
        key=lambda c: (c.get("source") != "overpass", -c.get("population", 0)),
    )

    kept: list[dict] = []
    for c in ranked:
        dup = False
        for k in kept:
            if _haversine_km(c["lat"], c["lng"], k["lat"], k["lng"]) < min_km:
                dup = True
                break
            if c["name"].lower() == k["name"].lower():
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept


# --------------------------------------------------------------------------- #
# Stage 3 — GeoNames enrichment on top candidates
# --------------------------------------------------------------------------- #

def _enrich_with_geonames(candidates: list[dict], origin_lat: float,
                          origin_lng: float) -> dict:
    """For each candidate, ask GeoNames for a nearby match to pull
    population and admin data. Best-effort — failures are silent."""
    if not _GEONAMES_USERNAME:
        return {"success": False, "reason": "no username"}

    enriched = []
    for c in candidates:
        # If the candidate already came from GeoNames, no enrichment needed.
        if c.get("source") == "geonames":
            enriched.append(c)
            continue

        res = _geonames_request(c["lat"], c["lng"], radius_km=10, max_rows=1)
        if "error" in res or not res.get("places"):
            enriched.append(c)
            continue

        g = res["places"][0]
        # Only merge if the GeoNames name is close enough to be the same place.
        if g["name"].lower() == c["name"].lower():
            c = {**c}
            c["population"] = max(c.get("population", 0), g.get("population", 0))
            c["admin"] = c.get("admin") or g.get("admin")
            c["country"] = c.get("country") or g.get("country")
        enriched.append(c)
        time.sleep(0.2)

    return {"success": True, "candidates": enriched}

def _is_hill_candidate(c: dict) -> bool:
    fcode = c.get("fcode", "")
    if fcode.startswith("natural="):
        return True

    ele = c.get("elevation")
    if ele is not None and ele >= 500:
        return True

    name = c["name"].lower()
    if any(h in name for h in _HILL_NAME_HINTS):
        return True

    # Far towns are almost always destinations, not day-trip suburbs.
    # 200 km is well past any urban commuter belt.
    if c.get("distance_km", 0) >= 200 and fcode.startswith("place="):
        kind = fcode.split("=", 1)[1]
        if kind in ("town", "city"):
            return True

    return False
# --------------------------------------------------------------------------- #
# Stage 4 — scoring
# --------------------------------------------------------------------------- #
def _score_candidate(name, fcode, distance_km, population, query_hints,
                     elevation=None):
    score = 0.0

    wants_hills = any(
        h in " ".join(query_hints) for h in
        ("hill", "mountain", "giri", "malai", "cool", "climate")
    )

    if wants_hills:
        # Closer stations score higher (especially for nearby hill station queries)
        score += max(0.0, (500 - distance_km) / 50.0)
    else:
        if distance_km <= 600:
            score += (600 - distance_km) / 100.0
        else:
            score -= (distance_km - 600) / 200.0

    # Name hints.
    low = name.lower()
    for hint in _HILL_NAME_HINTS:
        if hint in low:
            score += 3.0
            break

    for hint in query_hints:
        if hint.lower() in low:
            score += 2.0

    # Feature type.
    if fcode.startswith("place="):
        kind = fcode.split("=", 1)[1]
        if kind == "city":
            score += 2.0
        elif kind == "town":
            score += 1.5
        elif kind == "village":
            score += 0.8
    elif fcode.startswith("natural="):
        score += 1.5         # peaks and waterfalls are attractions
    elif fcode.startswith("tourism="):
        score += 0.8

    # Elevation: only rewarded for hill queries.
    if wants_hills and elevation is not None:
        if elevation >= 1500:
            score += 3.0
        elif elevation >= 1000:
            score += 2.0
        elif elevation >= 500:
            score += 1.0

    # Population: log-scaled.
    if population > 0:
        score += min(math.log10(population) * 0.8, 4.0)

    return round(score, 3)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def discover_nearby(
    lat: float,
    lng: float,
    radius_km: float = 500,
    max_results: int = 40,
    query_hints: Optional[list[str]] = None,
) -> dict:
    """Return ranked real places within radius_km of (lat, lng).

    Two paths:
      - Hill queries  -> curated list of verified Indian hill stations.
                         Fast, accurate, no external calls. Sources: state
                         tourism boards, Wikipedia. Enriched with GeoNames
                         population data when available.
      - Other queries -> live Overpass discovery with a GeoNames fallback.
                         Works for beaches, temple towns, generic "somewhere
                         near X" requests, etc.
    """
    query_hints = [h.lower() for h in (query_hints or [])]
    hint_blob = " ".join(query_hints)

    # ---------------------------------------------------------------- #
    # Category detection — the curated fast path covers any recognized
    # category (hill, beach, temple, wildlife, ...); Overpass remains the
    # fallback for uncategorized or rare queries.
    # ---------------------------------------------------------------- #
    from agent.destinations_db import CATEGORY_ALIASES, DESTINATIONS

    _known_categories = {c for e in DESTINATIONS
                         for c in e.get("categories", [])}
    category = None
    for word in hint_blob.replace("-", " ").replace("_", " ").split():
        canonical = CATEGORY_ALIASES.get(word) or (
            word if word in _known_categories else None
        )
        if canonical:
            category = canonical
            break
    wants_hills = category == "hill_station"

    # ---------------------------------------------------------------- #
    # FAST PATH: curated destinations DB (category-aware)
    # ---------------------------------------------------------------- #
    if category:
        from agent import nearby_search as ns

        candidates = ns.find_nearby_destinations(
            lat, lng, category=category, radius_km=radius_km,
        )
        print(
            f"  [discovery] curated '{category}' path: "
            f"{len(candidates)} destinations within {int(radius_km)} km",
            flush=True,
        )

        if not candidates:
            return {
                "error": (
                    f"no {category.replace('_', ' ')} destinations found "
                    f"within {int(radius_km)} km of ({lat:.2f}, {lng:.2f})"
                ),
                "sources": ["curated_destinations_db"],
                "search_radius_km": radius_km,
            }

        # Optional GeoNames enrichment for population ranking signal.
        # Failures are silent — candidates still pass through.
        try:
            head = candidates[:20]
            tail = candidates[20:]
            enriched = _enrich_with_geonames(head, lat, lng)
            if enriched.get("success"):
                candidates = enriched["candidates"] + tail
        except Exception as e:
            print(f"  [discovery] enrichment skipped: {e}", flush=True)

        # For hill stations, sort by proximity (closest stations first, e.g. Manjolai near Tirunelveli)
        candidates.sort(key=lambda c: c["distance_km"])

        # Enrich top candidates with real Ola Maps road distance if available
        try:
            from agent.transport import _ola_road_distance_km
            for c in candidates[:8]:
                road_km = _ola_road_distance_km((lat, lng), (c["lat"], c["lng"]))
                if road_km is not None and road_km > 0:
                    c["road_distance_km"] = road_km
                    c["distance_km"] = road_km  # Use real road distance
        except Exception as e:
            print(f"  [discovery] Ola road distance query skipped: {e}", flush=True)

        trimmed = candidates[:max_results]
        return {
            "candidates": trimmed,
            "count": len(trimmed),
            "sources": ["curated_destinations_db"],
            "category": category,
            "search_radius_km": radius_km,
        }

    # ---------------------------------------------------------------- #
    # DISCOVERY PATH: Overpass primary, GeoNames fallback
    # ---------------------------------------------------------------- #
    sources: list[str] = []

    # --- Stage 1: Overpass ------------------------------------------
    op = _discover_via_overpass(lat, lng, radius_km)
    if "error" not in op and op.get("candidates"):
        candidates = op["candidates"]
        sources.append("overpass")
    else:
        candidates = []
        print(
            f"  [discovery] overpass failed: {op.get('error')}",
            flush=True,
        )

    # --- Stage 1b: GeoNames fallback --------------------------------
    if not candidates:
        gn = _discover_via_geonames_tiled(lat, lng, radius_km)
        if "error" not in gn and gn.get("candidates"):
            candidates = gn["candidates"]
            sources.append("geonames")
        else:
            return {
                "error": "both Overpass and GeoNames discovery failed",
                "overpass_error": op.get("error"),
                "geonames_error": gn.get("error"),
            }

    # --- Stage 2: merge, dedupe, distance-sort ----------------------
    candidates = _merge_and_dedupe(candidates)
    for c in candidates:
        c["distance_km"] = round(
            _haversine_km(lat, lng, c["lat"], c["lng"]), 1,
        )
    candidates.sort(key=lambda c: c["distance_km"])

    # Drop anything inside the origin's own metro area.
    top = [
        c for c in candidates
        if c["distance_km"] >= _MIN_DESTINATION_DISTANCE_KM
    ]

    # --- Stage 3: enrichment on top 30 -------------------------------
    top = top[:30]
    enriched = _enrich_with_geonames(top, lat, lng)
    if enriched.get("success"):
        top = enriched["candidates"]
        sources.append("geonames_enrichment")

    # --- Stage 4: score, trim ----------------------------------------
    for c in top:
        c["score"] = _score_candidate(
            c["name"],
            c.get("fcode", ""),
            c["distance_km"],
            c.get("population", 0),
            query_hints,
            elevation=c.get("elevation"),
        )
    top.sort(key=lambda c: c["score"], reverse=True)

    trimmed = top[:max_results]
    return {
        "candidates": trimmed,
        "count": len(trimmed),
        "sources": sources,
        "search_radius_km": radius_km,
    }


# --------------------------------------------------------------------------- #
# Adapter for tools.py
# --------------------------------------------------------------------------- #

def to_destination_candidates(raw: list[dict],
                              state=None) -> list[DestinationCandidate]:
    """Convert discovery output to TripState-compatible candidates."""
    return [
        DestinationCandidate(
            name=c["name"],
            lat=c["lat"],
            lng=c["lng"],
            kind=c.get("fcode"),
            distance_km=c.get("distance_km"),
            road_distance_km=c.get("road_distance_km"),
            address=", ".join(
                p for p in (c.get("admin"), c.get("country")) if p
            ) or None,
        )
        for c in raw
    ]
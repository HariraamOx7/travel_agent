"""POI search via OpenStreetMap Overpass — keyless.

Mirrors ordered by observed reliability (Sept 2026):
  1. maps.mail.ru          — least loaded, no 504s seen
  2. overpass.kumi.systems — Europe, moderate load
  3. overpass.private.coffee — big HW but 504s under peak load

Reliability notes:
  - Server timeout is 40s; client read timeout is 45s so the client
    does not abort before the server finishes.
  - `out center 800` caps the response size. Larger limits cause the
    free mirrors to throttle, especially for dense areas like hill
    stations with many resorts and viewpoints.
  - All three mirrors are tried twice with a 10s gap, so transient
    outages usually resolve without surfacing an error.

Data © OpenStreetMap contributors (ODbL).
"""
import os
import json
import time
import requests

MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

HEADERS = {
    "User-Agent": "travel-agent-student-project/0.1 "
                  "(academic; github.com/HariraamOx7/travel-agent)"
}

_CACHE_DIR = "cache"

# Server budget is 40s; client read timeout must be longer so we don't
# abort before the server's own timeout fires.
_SERVER_TIMEOUT_S = 40
_CLIENT_READ_TIMEOUT_S = 45
_CLIENT_CONNECT_TIMEOUT_S = 5

# Two full passes over the mirror list before giving up.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_S = 10

# Response cap. 800 is enough for a 25 km radius; the ranker keeps the
# top slice of each category anyway.
_OUT_LIMIT = 800

_QUERY = """
[out:json][timeout:{t}];
(
  node["tourism"~"^(attraction|viewpoint|museum|zoo|park|artwork|gallery)$"](around:{r},{lat},{lng});
  way["tourism"~"^(attraction|viewpoint|museum|zoo|park|artwork|gallery)$"](around:{r},{lat},{lng});
  node["historic"~"^(monument|memorial|ruins|archaeological_site|castle|fort)$"](around:{r},{lat},{lng});
  way["historic"~"^(monument|memorial|ruins|archaeological_site|castle|fort)$"](around:{r},{lat},{lng});
  node["leisure"~"^(park|garden|nature_reserve)$"](around:{r},{lat},{lng});
  way["leisure"~"^(park|garden|nature_reserve)$"](around:{r},{lat},{lng});
  node["natural"~"^(peak|waterfall|spring|wood)$"](around:{r},{lat},{lng});
  node["amenity"~"^(restaurant|cafe)$"](around:{r},{lat},{lng});
  way["amenity"~"^(restaurant|cafe)$"](around:{r},{lat},{lng});
  node["tourism"~"^(hotel|guest_house|resort|hostel|apartment)$"](around:{r},{lat},{lng});
  way["tourism"~"^(hotel|guest_house|resort|hostel|apartment)$"](around:{r},{lat},{lng});
);
out center {limit};
"""

_KIND = {
    "attraction": "attraction", "viewpoint": "attraction", "museum": "attraction",
    "zoo": "attraction", "park": "attraction",
    "artwork": "attraction", "gallery": "attraction",
    "monument": "attraction", "memorial": "attraction", "ruins": "attraction",
    "archaeological_site": "attraction", "castle": "attraction", "fort": "attraction",
    "garden": "attraction", "nature_reserve": "attraction",
    "peak": "attraction", "waterfall": "attraction", "spring": "attraction", "wood": "attraction",
    "restaurant": "food", "cafe": "food",
    "hotel": "stay", "guest_house": "stay", "resort": "stay",
    "hostel": "stay", "apartment": "stay",
}


def _parse(data: dict) -> dict:
    out = {"attraction": [], "food": [], "stay": []}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        # Prefer tourism > historic > leisure > natural > amenity for `kind`.
        kind = (tags.get("tourism") or tags.get("historic")
                or tags.get("leisure") or tags.get("natural")
                or tags.get("amenity"))
        cat = _KIND.get(kind)
        if not cat:
            continue
        pos = el.get("center") or {}
        plat, plng = el.get("lat", pos.get("lat")), el.get("lon", pos.get("lon"))
        if plat is None:
            continue
        out[cat].append({
            "name": name,
            "kind": kind,
            "cat": cat,
            "lat": plat, "lng": plng,
            "cuisine": tags.get("cuisine"),
            "stars": tags.get("stars"),
            "rooms": tags.get("rooms"),
            "breakfast": tags.get("breakfast"),
            "board_type": tags.get("board_type"),
            "internet_access": tags.get("internet_access"),
            "wikidata": tags.get("wikidata"),
            "wikipedia": tags.get("wikipedia"),
        })
    return out


def search_pois(lat: float, lng: float, radius_km: float = 25) -> dict:
    """Search POIs around (lat, lng). Returns the parsed category dict on
    success, or {"error": ...} after all mirrors and retries fail."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(
        _CACHE_DIR, f"pois_{lat:.3f}_{lng:.3f}_{int(radius_km)}.json"
    )

    # Cache hit — return without a server call.
    if os.path.exists(cache_path) and \
            time.time() - os.path.getmtime(cache_path) < 86400:
        print("  [overpass] cache hit (no server call needed)", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    q = _QUERY.format(
        t=_SERVER_TIMEOUT_S,
        r=int(radius_km * 1000),
        lat=lat,
        lng=lng,
        limit=_OUT_LIMIT,
    )

    last_err = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        for url in MIRRORS:
            host = url.split("/")[2]
            t0 = time.time()
            try:
                print(
                    f"  [overpass] asking {host} "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS})...",
                    flush=True,
                )
                resp = requests.post(
                    url,
                    data={"data": q},
                    timeout=(_CLIENT_CONNECT_TIMEOUT_S,
                             _CLIENT_READ_TIMEOUT_S),
                    headers=HEADERS,
                )

                if resp.status_code == 429:
                    print(
                        f"  [overpass] {host} -> busy (429), trying next",
                        flush=True,
                    )
                    last_err = "Overpass busy (429)"
                    time.sleep(5)
                    continue

                resp.raise_for_status()
                result = _parse(resp.json())

                # Only cache a non-empty result — an empty parse usually
                # means the mirror returned malformed data, and caching
                # that would poison subsequent runs.
                total = sum(len(v) for v in result.values())
                if total == 0:
                    print(
                        f"  [overpass] {host} -> empty result, not caching",
                        flush=True,
                    )
                    last_err = "empty result from mirror"
                    continue

                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(result, f)
                print(
                    f"  [overpass] {host} -> OK in {time.time() - t0:.1f}s "
                    f"({total} POIs), saved to cache",
                    flush=True,
                )
                return result

            except requests.RequestException as e:
                print(
                    f"  [overpass] {host} -> {type(e).__name__} "
                    f"after {time.time() - t0:.1f}s: {str(e)[:100]}",
                    flush=True,
                )
                last_err = f"{type(e).__name__}: {e}"
                continue
            except ValueError as e:
                print(f"  [overpass] {host} -> bad JSON: {e}", flush=True)
                last_err = f"bad JSON: {e}"
                continue

        # End of one pass. Wait before retrying, unless this was the last.
        if attempt < _MAX_ATTEMPTS:
            print(
                f"  [overpass] all mirrors failed on attempt {attempt}, "
                f"waiting {_RETRY_DELAY_S}s before retry...",
                flush=True,
            )
            time.sleep(_RETRY_DELAY_S)

    return {"error": f"Overpass failed on all mirrors after "
                     f"{_MAX_ATTEMPTS} attempts: {last_err}"}
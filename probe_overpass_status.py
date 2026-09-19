"""POI search via OpenStreetMap Overpass — keyless.

Mirrors are ordered by current reliability (as of Sept 2026):
  - overpass.private.coffee : biggest headroom, primary
  - overpass.kumi.systems   : Europe, low latency
  - maps.mail.ru            : last-resort fallback
  - overpass-api.de         : deliberately omitted — IPs commonly banned
  - overpass.osm.jp         : deliberately omitted — invalid TLS cert

Results are cached to disk so each place is fetched only once per day.
Data © OpenStreetMap contributors (ODbL).
"""
import os
import json
import time
import requests

MIRRORS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

HEADERS = {
    "User-Agent": "travel-agent-student-project/0.1 "
                  "(academic; github.com/HariraamOx7/travel-agent)"
}

_CACHE_DIR = "cache"

_QUERY = """
[out:json][timeout:25];
(
  node["tourism"~"^(attraction|viewpoint|museum|zoo|park)$"](around:{r},{lat},{lng});
  way["tourism"~"^(attraction|viewpoint|museum|zoo|park)$"](around:{r},{lat},{lng});
  node["amenity"~"^(restaurant|cafe)$"](around:{r},{lat},{lng});
  way["amenity"~"^(restaurant|cafe)$"](around:{r},{lat},{lng});
  node["tourism"~"^(hotel|guest_house|resort)$"](around:{r},{lat},{lng});
  way["tourism"~"^(hotel|guest_house|resort)$"](around:{r},{lat},{lng});
);
out center 1500;
"""

_KIND = {"attraction": "attraction", "viewpoint": "attraction", "museum": "attraction",
         "zoo": "attraction", "park": "attraction",
         "restaurant": "food", "cafe": "food",
         "hotel": "stay", "guest_house": "stay", "resort": "stay"}


def search_pois(lat: float, lng: float, radius_km: float = 25) -> dict:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_CACHE_DIR, f"pois_{lat:.3f}_{lng:.3f}_{int(radius_km)}.json")
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 86400:
        print("  [overpass] cache hit (no server call needed)", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    q = _QUERY.format(r=int(radius_km * 1000), lat=lat, lng=lng)
    last_err = None
    for url in MIRRORS:
        host = url.split("/")[2]
        try:
            print(f"  [overpass] asking {host}...", flush=True)
            resp = requests.post(url, data={"data": q}, timeout=60, headers=HEADERS)
            if resp.status_code == 429:
                print(f"  [overpass] {host} -> busy (429)", flush=True)
                last_err = "Overpass busy (429)"
                time.sleep(30)
                continue
            resp.raise_for_status()
            result = _parse(resp.json())
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f)
            print(f"  [overpass] {host} -> OK, saved to cache", flush=True)
            return result
        except (requests.RequestException, ValueError) as e:
            print(f"  [overpass] {host} -> {type(e).__name__}: {str(e)[:100]}", flush=True)
            last_err = f"{type(e).__name__}: {e}"
    return {"error": f"Overpass failed on all mirrors: {last_err}"}


def _parse(data: dict) -> dict:
    out = {"attraction": [], "food": [], "stay": []}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        kind = tags.get("tourism") or tags.get("amenity")
        cat = _KIND.get(kind)
        if not cat:
            continue
        pos = el.get("center") or {}
        plat, plng = el.get("lat", pos.get("lat")), el.get("lon", pos.get("lon"))
        if plat is None:
            continue
        out[cat].append({"name": name, "kind": kind, "cat": cat,
                         "lat": plat, "lng": plng,
                         "cuisine": tags.get("cuisine"), "stars": tags.get("stars")})
    return out
"""Curated list of Indian hill stations with verified coordinates.

Sources cross-referenced: India Tourism, state tourism boards, Wikipedia.
This is the authoritative destination set for hill queries. Discovery via
Overpass is kept as a fallback for non-hill queries.
"""

# (name, state, lat, lng, elevation_m)
HILL_STATIONS = [
    # Tamil Nadu
    ("Udhagamandalam (Ooty)", "Tamil Nadu", 11.4064, 76.6932, 2240),
    ("Coonoor",               "Tamil Nadu", 11.3530, 76.7950, 1850),
    ("Kotagiri",              "Tamil Nadu", 11.4200, 76.8600, 1793),
    ("Kodaikanal",            "Tamil Nadu", 10.2381, 77.4892, 2133),
    ("Yercaud",               "Tamil Nadu", 11.7753, 78.2097, 1515),
    ("Yelagiri",              "Tamil Nadu", 12.5810, 78.6395, 1410),
    ("Kolli Hills",           "Tamil Nadu", 11.2500, 78.3333, 1300),
    ("Valparai",              "Tamil Nadu", 10.3230, 76.9518, 1193),
    ("Sirumalai",             "Tamil Nadu", 10.2830, 77.9500, 1600),
    ("Gudalur",               "Tamil Nadu", 11.4976, 76.4935, 1072),
    ("Manjolai",              "Tamil Nadu", 8.6333, 77.4167, 1200),
    # Kerala
    ("Munnar",                "Kerala",     10.0889, 77.0595, 1600),
    ("Thekkady",              "Kerala",      9.6000, 77.1600, 900),
    ("Vagamon",               "Kerala",      9.6833, 76.9000, 1100),
    ("Ponmudi",               "Kerala",      8.7600, 77.1167, 1100),
    ("Peermade",              "Kerala",      9.5667, 76.9833, 915),
    ("Idukki",                "Kerala",      9.8500, 76.9667, 750),
    ("Wayanad",               "Kerala",     11.6854, 76.1320, 975),
    ("Vythiri",               "Kerala",     11.5333, 76.0333, 900),
    # Karnataka
    ("Chikkamagaluru",        "Karnataka",  13.3161, 75.7720, 1090),
    ("Kemmangundi",           "Karnataka",  13.5333, 75.7500, 1434),
    ("Kudremukh",             "Karnataka",  13.2500, 75.2500, 1894),
    ("Madikeri (Coorg)",      "Karnataka",  12.4200, 75.7400, 1150),
    ("Mudigere",              "Karnataka",  13.1333, 75.6333, 900),
    ("Nandi Hills",           "Karnataka",  13.3702, 77.6835, 1478),
    ("BR Hills",              "Karnataka",  11.9167, 77.1167, 1000),
    # Andhra Pradesh / Telangana
    ("Horsley Hills",         "Andhra",     13.6579, 78.4078, 1265),
    ("Araku Valley",          "Andhra",     18.3273, 82.8753, 911),
    ("Lambasingi",            "Andhra",     17.8000, 82.4833, 1000),
]


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    import math
    p = math.pi / 180
    h = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * (1 - math.cos((lng2 - lng1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))


def hill_stations_near(lat: float, lng: float,
                       radius_km: float = 600) -> list[dict]:
    """Return hill stations within radius_km, ranked by distance."""
    out = []
    for name, state, s_lat, s_lng, ele in HILL_STATIONS:
        d = _haversine_km(lat, lng, s_lat, s_lng)
        if d <= radius_km:
            out.append({
                "name": name,
                "state": state,
                "lat": s_lat,
                "lng": s_lng,
                "elevation": ele,
                "distance_km": round(d, 1),
                "fcode": "hill_station",
                "source": "curated",
                "population": 0,
                "admin": state,
                "country": "India",
            })
    out.sort(key=lambda c: c["distance_km"])
    return out
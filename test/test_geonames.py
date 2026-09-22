import os, requests
from dotenv import load_dotenv
load_dotenv()

r = requests.get(
    "https://secure.geonames.org/findNearbyPlaceNameJSON",
    params={"lat": 13.08, "lng": 80.27, "radius": 500,
            "maxRows": 5, "featureClass": "P",
            "username": os.environ["GEONAMES_USERNAME"]},
    timeout=10,
)
print(r.status_code, r.json().get("status"))
print(r.json().get("geonames", [])[:3])
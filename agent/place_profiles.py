"""India attraction facts with explicit provenance and conservative defaults."""
from __future__ import annotations

import json
import re
from pathlib import Path

_CURATED = Path(__file__).resolve().parent.parent / "data" / "india_place_profiles.json"
_TAGS = {
    "museum": ("heritage", "culture"), "monument": ("heritage",),
    "fort": ("heritage",), "castle": ("heritage",),
    "viewpoint": ("nature", "photography"), "peak": ("nature", "adventure"),
    "waterfall": ("nature", "photography"),
    "garden": ("nature", "relaxation"), "park": ("nature", "relaxation"),
    "nature_reserve": ("nature", "wildlife"),
    "attraction": ("sightseeing",),
}
_INDOOR = {"museum", "gallery", "aquarium"}
_MIXED = {"temple", "church", "fort", "castle", "monument", "market"}
_DURATION = {"museum": 90, "monument": 75, "fort": 120, "castle": 120,
             "viewpoint": 45, "peak": 180, "waterfall": 90, "garden": 60,
             "park": 60, "nature_reserve": 180}
_INTENSITY = {"museum": 1, "monument": 2, "fort": 3, "castle": 3,
              "viewpoint": 1, "peak": 4, "waterfall": 3, "garden": 1,
              "park": 1, "nature_reserve": 3}
_CATEGORIES = {"temple": ("spirituality", "heritage"),
               "beach": ("nature", "relaxation"),
               "trek": ("nature", "adventure"),
               "market": ("shopping", "culture")}


def _curated() -> dict:
    try:
        rows = json.loads(_CURATED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        key = (f"osm:{row['osm_id']}" if row.get("osm_id") else
               str(row["name"]).strip().casefold())
        indexed[key] = row
    return indexed


def _parse_charge(value):
    if not value:
        return None
    # OSM charge values may contain conditions or currencies other than INR.
    match = re.fullmatch(r"\s*(?:INR|Rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(?:INR|Rs\.?)?\s*", str(value), re.I)
    return float(match.group(1)) if match else None


def enrich(poi: dict, curated: dict | None = None) -> dict:
    """Merge factual fields. Missing information remains unknown."""
    out = dict(poi)
    profiles = curated if curated is not None else _curated()
    row = (profiles.get(f"osm:{poi.get('osm_id')}") or
           profiles.get(str(poi.get("name", "")).strip().casefold(), {}))
    kind = str(out.get("kind") or "attraction").lower()
    sources = dict(out.get("data_sources") or {})
    out["activity_tags"] = list(_CATEGORIES.get(kind, _TAGS.get(kind, ("sightseeing",))))
    out["indoor_outdoor"] = ("indoor" if kind in _INDOOR else
                              "mixed" if kind in _MIXED else "outdoor")
    out["duration_minutes"] = _DURATION.get(kind, 75)
    out["intensity"] = _INTENSITY.get(kind, 2)
    sources.update({k: "category_rule" for k in
                    ("activity_tags", "indoor_outdoor", "duration_minutes", "intensity")})
    if out.get("opening_hours"):
        sources["opening_hours"] = "openstreetmap"
    fee = str(out.get("fee") or "").lower()
    charge = _parse_charge(out.get("charge"))
    out["entry_fee_inr"] = 0.0 if fee == "no" else charge
    if out["entry_fee_inr"] is not None:
        sources["entry_fee_inr"] = "openstreetmap"
    out["accessibility"] = out.get("wheelchair") or "unknown"
    if out["accessibility"] != "unknown":
        sources["accessibility"] = "openstreetmap"
    out["best_months"] = []
    for field in ("activity_tags", "indoor_outdoor", "duration_minutes",
                  "intensity", "opening_hours", "entry_fee_inr", "best_months",
                  "accessibility", "booking_required", "restrictions"):
        if field in row and row[field] is not None:
            value = row[field]
            if field == "intensity" and (not isinstance(value, int) or not 1 <= value <= 5):
                continue
            if field == "duration_minutes" and (not isinstance(value, int) or not 15 <= value <= 480):
                continue
            if field == "entry_fee_inr" and (not isinstance(value, (int, float)) or value < 0):
                continue
            if field == "indoor_outdoor" and value not in {"indoor", "outdoor", "mixed"}:
                continue
            if field == "best_months" and (not isinstance(value, list) or
                any(not isinstance(m, int) or not 1 <= m <= 12 for m in value)):
                continue
            if field == "activity_tags" and (not isinstance(value, list) or
                any(not isinstance(t, str) for t in value)):
                continue
            if field == "accessibility" and value not in {"yes", "limited", "no", "unknown"}:
                continue
            out[field] = value
            sources[field] = "official_site"
    if row.get("source_url"):
        out["source_url"] = row["source_url"]
        out["last_verified"] = row.get("last_verified")
    out["data_sources"] = sources
    return out


def enrich_many(items: list[dict]) -> list[dict]:
    curated = _curated()
    return [enrich(item, curated) for item in items]

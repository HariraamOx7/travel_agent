"""Accommodation enrichment from OSM tags + a documented price model.

OSM carries no ratings and almost never publishes prices for Indian
stays. This module exposes an explicit assumption model in the same
spirit as budget.py — every number is a named constant the report can cite.
"""
TYPE_LABEL = {
    "hotel": "Hotel",
    "guest_house": "Guest house",
    "resort": "Resort",
    "hostel": "Hostel",
    "apartment": "Apartment",
}

# Base nightly rate (INR) by type — midrange Indian hill-station market.
BASE_NIGHTLY_INR = {
    "hostel": 800,
    "guest_house": 1500,
    "hotel": 2500,
    "resort": 4500,
    "apartment": 3000,
}

# Multiplier when OSM has a `stars` tag; absent → 1.0 (assume midrange).
STAR_MULT = {"1": 0.7, "2": 1.0, "3": 1.5, "4": 2.5, "5": 4.0}

MEAL_PLAN = {
    "breakfast": "Breakfast included",
    "half_board": "Half board (breakfast + dinner)",
    "full_board": "Full board (all meals)",
}


def describe(stay: dict) -> dict:
    """Return an enriched stay record from an OSM POI dict."""
    kind = (stay.get("kind") or "").lower()
    stars = (stay.get("stars") or "").strip() or None

    base = BASE_NIGHTLY_INR.get(kind, 2000)
    mult = STAR_MULT.get(stars, 1.0)
    price = int(round(base * mult / 100.0) * 100)   # round to nearest ₹100

    if stay.get("breakfast") == "yes":
        meal = "Breakfast included"
    else:
        meal = MEAL_PLAN.get(stay.get("board_type"), "Meal plan not specified")

    rating_label = f"{stars}★ (OSM listed)" if stars else "Not rated"
    nightly_label = f"~₹{price:,}/night (est.)"

    return {
        "name": stay["name"],
        "kind": stay.get("kind"),
        "type": TYPE_LABEL.get(kind, kind.title() or "Stay"),
        "nightly_inr": price,
        "nightly_label": nightly_label,
        "rating_label": rating_label,
        "meal_plan": meal,
        "internet": stay.get("internet_access"),
        "lat": stay.get("lat"),
        "lng": stay.get("lng"),
    }
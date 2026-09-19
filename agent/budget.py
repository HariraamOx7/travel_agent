"""Trip cost estimation from documented per-unit assumptions.

OSM carries no prices — this is an explicit assumption model, not live
market data. Every unit cost is a named constant the report can cite
(and tweak: midrange vs budget is one edit here).
"""
import math

UNIT_INR = {
    "stay_per_night": 3500,          # midrange double room, hill station
    "food_per_meal": 400,            # casual restaurant, per person
    "local_transport_per_day": 1500, # hired cab / scooter per day
    "activity_per_stop": 100,        # entry fees (many viewpoints are free)
}


def estimate(travellers: int, n_days: int, n_stops: int,
             travel_mode: str | None = None,
             distance_km: float | None = None) -> dict:
    """Estimate trip cost. If travel_mode is set and distance_km is known,
    add a transport line; otherwise omit it (traveller arranges their own)."""
    rooms = math.ceil(travellers / 2)
    nights = max(n_days - 1, 0)
    stay = nights * UNIT_INR["stay_per_night"] * rooms
    meals = n_days * travellers * 2
    food = meals * UNIT_INR["food_per_meal"]
    local = n_days * UNIT_INR["local_transport_per_day"]
    activities = n_stops * UNIT_INR["activity_per_stop"] * travellers

    intercity = 0
    intercity_note = None
    if travel_mode and distance_km:
        from agent import transport as transport_mod
        spec = transport_mod.MODEL.get(travel_mode)
        if spec:
            if "mileage" in spec:
                # Driving: round-trip fuel.
                litres = (distance_km * 2) / spec["mileage"]
                intercity = int(litres * spec["fuel_per_l"])
                intercity_note = (
                    f"{travel_mode} fuel estimate: {litres:.0f} L round trip"
                )
            else:
                # Per-person modes.
                per_person = spec["base"] + spec["per_km"] * distance_km
                intercity = int(per_person * travellers * 2)  # return trip
                intercity_note = (
                    f"{travel_mode} return fare, {travellers} traveller(s)"
                )

    line_items = {
        "stay": stay, "food": food,
        "local_transport": local, "activities": activities,
    }
    if intercity:
        line_items["intercity_transport"] = intercity

    total = sum(line_items.values())

    return {
        "line_items": line_items,
        "total_inr": total,
        "assumptions": {
            **UNIT_INR,
            "rooms": rooms,
            "nights": nights,
            "travellers": travellers,
            "n_days": n_days,
            "transport_days": n_days,
            "meals_per_person_per_day": 2,
            "total_meals": meals,
            "activity_stops": n_stops,
            "travel_mode": travel_mode or "not chosen",
            "intercity_note": intercity_note,
        },
    }
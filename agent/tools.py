"""Tool implementations for the travel agent.

Design invariants (from the design plan):
  - Tools never raise — errors are returned as {"error": ...} dicts and reach
    the LLM as observations, so the ReAct loop can recover.
  - LLM proposes, tools dispose: hallucinated candidate names are silently
    dropped by Nominatim grounding. Picks enter `destinations` ONLY via
    confirm_destination.
  - Zero-argument tools where possible (get_weather, get_recommendations
    read their inputs from TripState).
"""
import json
import math
import time
from datetime import date, timedelta

from agent.state import Destination, DestinationCandidate, TripState
from agent import geo, scheduler as scheduler_mod
from agent import pois as pois_mod
from agent import weather


# --------------------------------------------------------------------------- #
# Slot extraction
# --------------------------------------------------------------------------- #

def _parse_date(value) -> date | None:
    """Best-effort ISO date parse. Returns None on anything unexpected."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None

def _already_set(state: TripState, key: str, value) -> bool:
    """True if state.<key> already holds `value` (or an equivalent form).

    Used to make extract_trip_slots idempotent: the LLM re-sends slots
    it sees in CURRENT TRIP STATE, and we don't want those counted as
    fresh applications in the trace.
    """
    cur = getattr(state, key, None)

    if cur is None and value is None:
        return True
    if cur is None or value is None:
        return False

    # Dates: compare parsed values, not raw strings.
    if key in ("start_date", "end_date"):
        parsed = _parse_date(value)
        return parsed is not None and parsed == cur

    # Numbers: numeric equality, tolerant of int/float and str/int.
    if key in ("budget_total", "travellers"):
        try:
            return float(cur) == float(value)
        except (TypeError, ValueError):
            return False

    # Strings (origin, destination_raw, budget_currency, travel_mode, pace):
    # case-insensitive and whitespace-normalised.
    if isinstance(cur, str) and isinstance(value, str):
        return cur.strip().lower() == value.strip().lower()

    # Lists (interests): same elements, order-insensitive.
    if isinstance(cur, list) and isinstance(value, list):
        return sorted(map(str, cur)) == sorted(map(str, value))

    # Fallback: strict equality.
    return cur == value

def extract_trip_slots(args: dict, state: TripState) -> dict:
    """Apply LLM-extracted slot values to state. Only keys present in args
    are touched. Values that fail validation are reported via `rejected`.

    Idempotent: if a slot already equals the value the LLM is sending,
    it is reported as `skipped` rather than `applied`.
    """
    applied: list[str] = []
    skipped: list[str] = []
    rejected: list[str] = []
    flags: list[str] = list(args.get("confidence_flags") or [])

    # --- origin --------------------------------------------------------- #
    if "origin" in args and args["origin"]:
        val = str(args["origin"]).strip()
        if _already_set(state, "origin", val):
            skipped.append("origin")
        else:
            state.origin = val
            applied.append("origin")

    # --- destination ---------------------------------------------------- #
    if "destination_raw" in args and args["destination_raw"]:
        raw = str(args["destination_raw"]).strip()
        is_vague = bool(args.get("destination_is_vague"))
        if is_vague:
            if _already_set(state, "pending_destination_query", raw):
                skipped.append("pending_destination_query")
            else:
                state.pending_destination_query = raw
                state.destination_candidates = []
                applied.append("pending_destination_query")
        else:
            # Specific name: only re-write the candidate stub if it
            # differs from what's already there.
            current = state.destination_candidates
            if (len(current) == 1
                    and current[0].name.lower() == raw.lower()):
                skipped.append(f"destination_candidates(name={raw})")
            else:
                state.destination_candidates = [
                    DestinationCandidate(name=raw)
                ]
                applied.append(f"destination_candidates(name={raw})")

    # --- dates ---------------------------------------------------------- #
    for key in ("start_date", "end_date"):
        if key in args and args[key]:
            d = _parse_date(args[key])
            if d is None:
                rejected.append(f"{key}={args[key]!r}")
                continue
            if _already_set(state, key, d):
                skipped.append(key)
            else:
                setattr(state, key, d)
                applied.append(key)

    if state.start_date and state.end_date and state.end_date < state.start_date:
        state.start_date, state.end_date = state.end_date, state.start_date
        flags.append("dates_swapped")

    # --- budget --------------------------------------------------------- #
    if "budget_total" in args and args["budget_total"] is not None:
        try:
            val = float(args["budget_total"])
        except (ValueError, TypeError):
            rejected.append(f"budget_total={args['budget_total']!r}")
        else:
            if _already_set(state, "budget_total", val):
                skipped.append("budget_total")
            else:
                state.budget_total = val
                applied.append("budget_total")

    if "budget_currency" in args and args["budget_currency"]:
        val = str(args["budget_currency"]).upper()
        if _already_set(state, "budget_currency", val):
            skipped.append("budget_currency")
        else:
            state.budget_currency = val
            applied.append("budget_currency")

    # --- travellers ----------------------------------------------------- #
    if "travellers" in args and args["travellers"] is not None:
        try:
            n = int(args["travellers"])
        except (ValueError, TypeError):
            rejected.append(f"travellers={args['travellers']!r}")
        else:
            if n < 1:
                rejected.append(f"travellers={n} (must be >=1)")
            elif _already_set(state, "travellers", n):
                skipped.append("travellers")
            else:
                state.travellers = n
                applied.append("travellers")

    # --- interests ------------------------------------------------------ #
    if "interests" in args and args["interests"]:
        if isinstance(args["interests"], list):
            val = [str(i).strip() for i in args["interests"] if i]
            if _already_set(state, "interests", val):
                skipped.append("interests")
            else:
                state.interests = val
                applied.append("interests")
        else:
            rejected.append("interests (not a list)")

    # --- pace (optional) ------------------------------------------------ #
    if "pace" in args and args["pace"]:
        p = str(args["pace"]).lower()
        if p not in {"relaxed", "balanced", "packed"}:
            rejected.append(f"pace={args['pace']!r}")
        elif _already_set(state, "pace", p):
            skipped.append("pace")
        else:
            state.pace = p
            applied.append("pace")

    # --- travel mode ---------------------------------------------------- #
    if "travel_mode" in args and args["travel_mode"]:
        m = str(args["travel_mode"]).lower()
        if m not in {"flight", "train", "bus", "car", "bike"}:
            rejected.append(f"travel_mode={args['travel_mode']!r}")
        elif _already_set(state, "travel_mode", m):
            skipped.append("travel_mode")
        else:
            state.travel_mode = m
            applied.append("travel_mode")

    return {
        "applied": applied,
        "skipped": skipped,
        "rejected": rejected,
        "flags": flags,
        "missing_required": state.missing_required(),
    }

# --------------------------------------------------------------------------- #
# Vague-destination resolution
# --------------------------------------------------------------------------- #

from agent import discovery as discovery_mod

def search_destination_candidates(args: dict, state: TripState) -> dict:
    """Discover real places near the origin via Overpass (with a GeoNames
    fallback), then expose them as state.destination_candidates.

    The LLM's `candidate_names` argument is ignored — the tool discovers
    names from a database rather than trusting the LLM's geography.
    """
    if not state.origin:
        return {"error": "origin required before searching for destinations"}

    # --- Geocode origin if we don't already have coords ----------------
    if not state.origin_coords:
        rec = geo.geocode(state.origin)
        if not rec or (isinstance(rec, dict) and "error" in rec) \
                or rec.get("lat") is None:
            return {"error": f"could not geocode origin '{state.origin}'"}
        state.origin_coords = {"lat": rec["lat"], "lng": rec["lng"]}

    origin_lat = state.origin_coords["lat"]
    origin_lng = state.origin_coords["lng"]

    # --- Build query hints from state ----------------------------------
    hints: list[str] = []
    if state.pending_destination_query:
        hints.extend(state.pending_destination_query.lower().split())
    if state.interests:
        hints.extend(i.lower() for i in state.interests)

    # --- Radius (clamped) ----------------------------------------------
    radius = 500
    if args.get("radius_km"):
        try:
            radius = max(100, min(int(args["radius_km"]), 1000))
        except (ValueError, TypeError):
            pass

    # --- Discovery (defensive) -----------------------------------------
    try:
        disc = discovery_mod.discover_nearby(
            origin_lat, origin_lng,
            radius_km=radius,
            max_results=50,
            query_hints=hints,
        )
    except Exception as e:
        return {
            "error": f"discovery failed: {type(e).__name__}: {e}",
            "hint": "check agent/discovery.py and the cache/ directory",
        }

    if not isinstance(disc, dict):
        return {"error": f"discovery returned {type(disc).__name__}, expected dict"}

    if "error" in disc:
        return disc

    raw = disc.get("candidates") or []
    if not raw:
        return {"error": "no places found near origin",
                "sources": disc.get("sources", [])}

    # --- Convert to DestinationCandidate objects -----------------------
    verified = discovery_mod.to_destination_candidates(raw)
    state.destination_candidates = verified

    return {
        "verified": [
            {
                "name": v.name,
                "address": v.address,
                "lat": v.lat,
                "lng": v.lng,
                "kind": v.kind,
                "distance_km": next(
                    (c["distance_km"] for c in raw if c["name"] == v.name),
                    None,
                ),
            }
            for v in verified
        ],
        "count": len(verified),
        "sources": disc.get("sources", []),
        "search_radius_km": disc.get("search_radius_km"),
        "instruction": (
            "Present as a numbered list. Include the distance from the "
            "origin for each. Ask the user to pick one by number."
        ),
    }

def confirm_destination(args: dict, state: TripState) -> dict:
    """Lock in the user's pick from state.destination_candidates.

    Accepts either a 1-based index ('2') or a name substring ('Ooty').
    """
    if not state.destination_candidates:
        return {"error": "no candidates pending — call search_destination_candidates first"}

    choice = str(args.get("choice", "")).strip()
    if not choice:
        return {"error": "choice is required"}

    picked: DestinationCandidate | None = None

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(state.destination_candidates):
            picked = state.destination_candidates[idx]
    else:
        needle = choice.lower()
        for c in state.destination_candidates:
            if needle in c.name.lower():
                picked = c
                break

    if picked is None:
        return {
            "error": f"could not match choice {choice!r}",
            "available": [c.name for c in state.destination_candidates],
        }

    if picked.lat is None or picked.lng is None:
        return {"error": f"candidate {picked.name!r} has no coordinates"}

    state.destinations = [Destination(
        name=picked.name,
        place_id=picked.place_id,
        lat=picked.lat,
        lng=picked.lng,
    )]
    state.destination_candidates = []           # clear the pending list
    state.pending_destination_query = None

    return {
        "confirmed": {
            "name": picked.name,
            "lat": picked.lat,
            "lng": picked.lng,
            "place_id": picked.place_id,
        },
        "missing_required": state.missing_required(),
        "instruction": (
            "Confirm to the user in one short sentence, then ask the next "
            "missing required slot (if any)."
        ),
    }


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #

def get_weather(args: dict, state: TripState) -> dict:
    d = state.destinations[0] if state.destinations else None
    if not d or d.lat is None:
        return {"error": "no confirmed destination with coordinates"}
    if not (state.start_date and state.end_date):
        return {"error": "trip dates unknown"}

    res = weather.trip_weather(d.lat, d.lng, state.start_date, state.end_date)
    if "error" not in res:
        state.recommendations = state.recommendations or {}
        state.recommendations["weather"] = res
    return res


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #

def _haversine_km(a_lat, a_lng, b_lat, b_lng):
    p = math.pi / 180
    h = (0.5 - math.cos((b_lat - a_lat) * p) / 2
         + math.cos(a_lat * p) * math.cos(b_lat * p)
         * (1 - math.cos((b_lng - a_lng) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))

_RARE_KINDS = {
    "artwork", "gallery", "ruins", "archaeological_site", "castle", "fort",
    "waterfall", "peak", "nature_reserve", "wood", "memorial", "monument",
}


def _hidden_score(poi: dict, clat: float, clng: float) -> float:
    """Reward less-documented, off-center, rarer-kind POIs — the opposite
    signals from _score, so the two lists diverge."""
    s = 0.0
    if not poi.get("wikidata") and not poi.get("wikipedia"):
        s += 2.0                          # undocumented in knowledge bases
    if (poi.get("kind") or "").lower() in _RARE_KINDS:
        s += 1.5                          # rare category
    d = _haversine_km(clat, clng, poi["lat"], poi["lng"])
    if 3.0 <= d <= 15.0:
        s += 1.0                          # off-center but reachable
    return round(s, 3)

def _score(poi: dict, clat, clng) -> float:
    """Explainable ranking: category weight + kind bonus + proximity decay."""
    w = {"attraction": 3.0, "food": 1.0, "stay": 1.0}[poi["cat"]]
    bonus = {"viewpoint": 0.5, "museum": 0.4, "zoo": 0.3}.get(poi["kind"], 0.0)
    d = _haversine_km(clat, clng, poi["lat"], poi["lng"])
    return round(w + bonus + 1.0 / (1.0 + d / 5.0), 3)

def _name_is_variant(a: str, b: str) -> bool:
    """True if two OSM names likely refer to the same venue."""
    import difflib
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 8 and longer.startswith(shorter):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.7


def _dedupe(pois: list[dict], min_km: float = 0.3) -> list[dict]:
    """Drop POIs that are either very close (same node, two names) or
    close AND similarly named (parent/subsidiary variants in OSM)."""
    kept: list[dict] = []
    for p in pois:
        drop = False
        for k in kept:
            dist = _haversine_km(p["lat"], p["lng"], k["lat"], k["lng"])
            if dist < min_km:
                drop = True
                break
            if _name_is_variant(p["name"], k["name"]) and dist < 1.0:
                drop = True
                break
        if not drop:
            kept.append(p)
    return kept

def _cluster_attractions(attrs: list[dict], n_days: int | None) -> list[dict]:
    """Return day-group clusters, or a single cluster if the attractions
    are too tightly packed to be worth splitting.

    Guards against the degenerate case where k-means returns mostly
    singletons (Munnar's viewpoints are all within ~1 km of each other).
    """
    if not attrs:
        return []

    # Spread = max distance from the first attraction to any other.
    spread = max(
        _haversine_km(attrs[0]["lat"], attrs[0]["lng"], a["lat"], a["lng"])
        for a in attrs
    )

    # Everything within 2 km = one day, don't cluster.
    if spread < 2.0:
        return [{"cluster": 0, "n_pois": len(attrs),
                 "center_lat": round(attrs[0]["lat"], 3),
                 "center_lng": round(attrs[0]["lng"], 3)}]

    from sklearn.cluster import KMeans

    # Want ~2-3 attractions per cluster, cap by days and by a ceiling of 5.
    by_size = max(len(attrs) // 3, 2)
    by_days = max(n_days or 3, 2)
    k = min(by_size, by_days, 5, len(attrs))

    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    labels = km.fit_predict([[a["lat"], a["lng"]] for a in attrs])
    for a, lab in zip(attrs, labels):
        a["day_cluster"] = int(lab)

    return [
        {"cluster": i,
         "n_pois": int((labels == i).sum()),
         "center_lat": round(c[0], 3),
         "center_lng": round(c[1], 3)}
        for i, c in enumerate(km.cluster_centers_)
    ]

def get_recommendations(args: dict, state: TripState) -> dict:
    d = state.destinations[0] if state.destinations else None
    if not d or d.lat is None:
        return {"error": "no confirmed destination — confirm one first"}

    pois = pois_mod.search_pois(d.lat, d.lng)
    if "error" in pois:
        return pois

    # Rank every category by the explainable score, then dedupe.
        # Rank every category by the visibility score, then dedupe.
    for cat, items in pois.items():
        items.sort(key=lambda p: _score(p, d.lat, d.lng), reverse=True)
        pois[cat] = _dedupe(items)

    attrs_all = pois["attraction"]
    top_picks = attrs_all[:6]
    top_names = {p["name"] for p in top_picks}

    remainder = [p for p in attrs_all if p["name"] not in top_names]
    remainder.sort(key=lambda p: _hidden_score(p, d.lat, d.lng), reverse=True)
    hidden_gems = remainder[:5]

    attrs = top_picks + hidden_gems        # scheduler input
    food = pois["food"][:12]
    stay_raw = pois["stay"][:8]

    # Enrich stay entries with price / meal / rating labels.
    from agent import stay as stay_mod
    stay = [stay_mod.describe(s) for s in stay_raw]

    food_with_cuisine = [f for f in food if f.get("cuisine")]
    food_without = [f for f in food if not f.get("cuisine")]

    clusters = _cluster_attractions(attrs, state.n_days)

    state.recommendations = {
        "weather": (state.recommendations or {}).get("weather"),
        "attractions": attrs,
        "top_picks": top_picks,
        "hidden_gems": hidden_gems,
        "food": food,
        "stay": stay,
        "clusters": clusters,
        "counts": {c: len(pois[c]) for c in pois},
    }
    state.stage = "recommending"

    def _attr_payload(a: dict) -> dict:
        return {
            "name": a["name"],
            "kind": a.get("kind"),
            "km_from_center": round(_haversine_km(d.lat, d.lng, a["lat"], a["lng"]), 1),
        }

    return {
        "weather": state.recommendations["weather"],
        "top_picks": [_attr_payload(a) for a in top_picks],
        "hidden_gems": [_attr_payload(a) for a in hidden_gems],
        "top_food": (
            [{"name": f["name"], "cuisine": f["cuisine"]}
             for f in food_with_cuisine[:3]]
            + [{"name": f["name"], "cuisine": None,
                "note": "name only — no cuisine data in OSM"}
               for f in food_without[:2]]
        ),
        "top_stay": [
            {"name": s["name"], "type": s["type"],
             "nightly_inr": s["nightly_inr"], "nightly_label": s["nightly_label"],
             "rating_label": s["rating_label"], "meal_plan": s["meal_plan"]}
            for s in stay[:5]
        ],
        "geographic_clusters": clusters,
        "counts": state.recommendations["counts"],
        "instruction": (
            "Present TWO attraction sections, clearly labelled: "
            "'Top picks' (the well-known ones) and 'Hidden gems' (less-documented, "
            "off-the-beaten-path). Present 2-3 stay options with their type, "
            "estimated nightly rate, rating label, and meal plan — use the exact "
            "labels from the tool result, never invent ratings or prices. "
            "One weather line stating live forecast or climatology. "
            "Only describe a food venue's cuisine if `cuisine` is non-null. "
            "Offer to build the day-by-day schedule next."
        ),
    }

def build_itinerary(args: dict, state: TripState) -> dict:
    rec = state.recommendations or {}
    attrs = rec.get("attractions") or []
    if not attrs:
        return {"error": "no recommendations yet — call get_recommendations first"}
    if not (state.destinations and state.start_date and state.n_days):
        return {"error": "destination and dates required"}

    # Cumulative exclusions: merge any new names with what's already
    # persisted, unless the caller explicitly asks to reset.
    if args.get("clear_excluded"):
        state.excluded_names = []
    new_excludes = args.get("exclude_names") or []
    merged = sorted(set(state.excluded_names) | set(new_excludes))
    state.excluded_names = merged

    d = state.destinations[0]
    rain_by_date = {w["date"]: w.get("precip_mm", 0) or 0
                    for w in (rec.get("weather") or {}).get("days", [])}

    rain = []
    missing = []
    for i in range(state.n_days):
        date_str = (state.start_date + timedelta(days=i)).isoformat()
        if date_str not in rain_by_date:
            missing.append(date_str)
            rain.append(0.0)
        else:
            rain.append(rain_by_date[date_str])

    if missing:
        return {
            "error": (f"weather data missing for {len(missing)} day(s): "
                      f"{missing}. Call get_weather to refresh the forecast "
                      f"for the extended date range, then retry build_itinerary."),
        }

    result = scheduler_mod.build_itinerary(
        attrs, rec.get("food") or [], d.lat, d.lng,
        state.start_date, state.n_days, rain, state.pace,
        exclude_names=merged)
    if "error" in result:
        return result

    state.itinerary = result
    state.stage = "scheduling"
    return {
        "schedule": [
            {"date": day["date"],
             "stops": [s["name"] for s in day["stops"]],
             "km_total": round(sum(s["km_from_prev"] for s in day["stops"]), 1),
             "lunch": day["lunch"],
             "rain_mm": day["expected_rain_mm"],
             "rest_day": day.get("rest_day", False)}
            for day in result["days"]
        ],
        "unscheduled": result["unscheduled"],
        "excluded": merged,
        "solver_status": result["solver_status"],
        "instruction": (
            "Present each day exactly as given: date, stops in order, "
            "total km, lunch, expected rain. Use the exact stop names and "
            "exact `lunch` value from the tool result — never invent or "
            "substitute names. If a day has rest_day=true, present it as "
            "'Rest day' with no stops and no lunch line. If `excluded` is "
            "non-empty, mention in one sentence which stops are currently "
            "removed."
        ),
    }
from agent import budget as budget_mod

from agent import transport as transport_mod


def recommend_transport(args: dict, state: TripState) -> dict:
    """Rank transport modes for origin → destination with cost + time."""
    if not (state.origin and state.destinations):
        return {"error": "origin and destination required"}
    d = state.destinations[0]

    # Geocode origin on first use; cache in state.
    if not state.origin_coords:
        rec = geo.geocode(state.origin)
        if rec and "error" not in rec and rec.get("lat") is not None:
            state.origin_coords = {"lat": rec["lat"], "lng": rec["lng"]}
        else:
            state.origin_coords = None

    oc = state.origin_coords
    if not oc:
        return {"error": f"could not geocode origin '{state.origin}'"}

    res = transport_mod.recommend(
        origin=state.origin,
        dest=d.name,
        origin_coords=(oc["lat"], oc["lng"]),
        dest_coords=(d.lat, d.lng),
        travellers=state.travellers,
        pace=state.pace,
    )
    if "error" in res:
        return res

    # If a mode is already chosen, mark it in the payload so the LLM can
    # confirm the pick rather than re-presenting the full list.
    for o in res["options"]:
        o["selected"] = (o["mode"] == state.travel_mode)

    return {
        "distance_km": res["distance_km"],
        "options": res["options"],
        "chosen": state.travel_mode,
        "instruction": (
            "Present the transport options as a short comparison. For each "
            "mode show: mode name, estimated cost range, estimated hours, "
            "and the 'fit' label if present. If 'chosen' is set, mark that "
            "option as chosen and do not ask again. If no mode is chosen, "
            "ask the user to pick one. Use exact cost labels from the tool "
            "result — never invent prices. These are documented estimates, "
            "not live prices."
        ),
    }

def estimate_budget(args: dict, state: TripState) -> dict:
    if not (state.itinerary and state.n_days):
        return {"error": "build the itinerary first — call build_itinerary"}

    n_stops = sum(len(day["stops"]) for day in state.itinerary.get("days", []))

    # Get distance for intercity fare if we have origin coords.
    distance_km = None
    if state.travel_mode and state.origin_coords and state.destinations:
        from agent import transport as transport_mod
        d = state.destinations[0]
        distance_km = transport_mod._road_distance_km(
            (state.origin_coords["lat"], state.origin_coords["lng"]),
            (d.lat, d.lng),
            state.origin, d.name,
        )

    est = budget_mod.estimate(
        state.travellers, state.n_days, n_stops,
        travel_mode=state.travel_mode,
        distance_km=distance_km,
    )
    delta = (state.budget_total or 0) - est["total_inr"]
    state.recommendations = state.recommendations or {}
    state.recommendations["budget"] = {
        "line_items_inr": est["line_items"],
        "total_inr": est["total_inr"],
        "verdict": (f"within budget by {delta:,.0f} INR" if delta >= 0
                    else f"OVER budget by {-delta:,.0f} INR"),
    }
    return {
        "line_items_inr": est["line_items"],
        "total_inr": est["total_inr"],
        "budget_inr": state.budget_total,
        "verdict": (f"within budget by {delta:,.0f} INR" if delta >= 0
                    else f"OVER budget by {-delta:,.0f} INR"),
        "assumptions": est["assumptions"],
        "instruction": (
            "Present the line items and total as a small table, then the "
            "budget verdict. State clearly these are documented estimates, "
            "not live prices. If an 'intercity_transport' line is present, "
            "label it with the travel_mode. Use exact quantities from the "
            "'assumptions' block — do not recompute. If over budget, suggest "
            "which assumption to lower (e.g. stay tier) — never invent "
            "cheaper venues."
        ),
    }
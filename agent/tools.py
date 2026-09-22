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
import re
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
            if not state.origin_coords:
                try:
                    rec = geo.geocode(val)
                    if rec and "lat" in rec and rec.get("lat") is not None:
                        state.origin_coords = {"lat": rec["lat"], "lng": rec["lng"]}
                except Exception:
                    pass

    # --- destination ---------------------------------------------------- #
    destinations_list = list(args.get("destinations_list") or [])
    if "destination_raw" in args and args["destination_raw"]:
        raw = str(args["destination_raw"]).strip()
        is_vague = bool(args.get("destination_is_vague")) or any(
            v in raw.lower() for v in ["hill station", "hillstation", "hills", "beach", "beaches", "anywhere", "somewhere"]
        )
        if is_vague:
            clean_query = "hill station" if any(h in raw.lower() for h in ["hill", "mountain"]) else (
                "beach" if any(b in raw.lower() for b in ["beach", "coast"]) else "destinations"
            )
            if _already_set(state, "pending_destination_query", clean_query):
                skipped.append("pending_destination_query")
            else:
                state.pending_destination_query = clean_query
                state.destination_candidates = []
                state.destinations = []
                applied.append("pending_destination_query")
        elif destinations_list or ("," in raw or re.search(r"\b(and|&)\b", raw, re.I)):
            if not destinations_list:
                parts = [re.sub(r"^(?:and|&)\s+", "", p.strip(), flags=re.I).strip()
                         for p in re.split(r",|\b(?:and|&)\b", raw, flags=re.I)]
                destinations_list = [p.title() for p in parts if p and p.lower() not in {"the", "a", "an"}]
            if len(destinations_list) > 1:
                dests = []
                for d_name in destinations_list:
                    try:
                        rec = geo.geocode(d_name)
                    except Exception:
                        rec = None
                    if rec and "lat" in rec and rec.get("lat") is not None:
                        dests.append(Destination(
                            name=rec.get("name") or d_name,
                            place_id=rec.get("place_id"),
                            lat=rec["lat"],
                            lng=rec["lng"],
                        ))
                    else:
                        dests.append(Destination(name=d_name))
                state.destinations = dests
                state.destination_candidates = []
                state.pending_destination_query = None
                applied.append(f"destinations({', '.join(d.name for d in dests)})")
            elif destinations_list:
                d_name = destinations_list[0]
                try:
                    rec = geo.geocode(d_name)
                except Exception:
                    rec = None
                if rec and "lat" in rec and rec.get("lat") is not None:
                    state.destinations = [Destination(
                        name=rec.get("name") or d_name,
                        place_id=rec.get("place_id"),
                        lat=rec["lat"],
                        lng=rec["lng"],
                    )]
                else:
                    state.destinations = [Destination(name=d_name)]
                state.destination_candidates = []
                state.pending_destination_query = None
                applied.append(f"destinations({state.destinations[0].name})")
        else:
            # Specific destination name (e.g. "Munnar", "Goa")
            # Geocode and set directly in state.destinations so it is confirmed immediately
            try:
                rec = geo.geocode(raw)
            except Exception:
                rec = None
            if rec and "lat" in rec and rec.get("lat") is not None:
                state.destinations = [Destination(
                    name=rec.get("name") or raw.title(),
                    place_id=rec.get("place_id"),
                    lat=rec["lat"],
                    lng=rec["lng"],
                )]
            else:
                state.destinations = [Destination(name=raw.title())]
            state.destination_candidates = []
            state.pending_destination_query = None
            applied.append(f"destinations({state.destinations[0].name})")

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

    # --- adventure level (optional) ------------------------------------- #
    # How trek-heavy the trip should be; drives the scheduler's trek cap.
    if "adventure_level" in args and args["adventure_level"]:
        lvl = str(args["adventure_level"]).lower()
        if lvl not in {"low", "balanced", "high"}:
            rejected.append(f"adventure_level={args['adventure_level']!r}")
        elif _already_set(state, "adventure_level", lvl):
            skipped.append("adventure_level")
        else:
            state.adventure_level = lvl
            applied.append("adventure_level")

    # --- destination category (optional) --------------------------------- #
    # Category of a vague destination ("hill station", "beach", ...).
    if "destination_category" in args and args["destination_category"]:
        cat = str(args["destination_category"]).lower().strip()
        if not re.fullmatch(r"[a-z_]{2,30}", cat):
            rejected.append(f"destination_category={args['destination_category']!r}")
        elif _already_set(state, "destination_category", cat):
            skipped.append("destination_category")
        else:
            state.destination_category = cat
            applied.append("destination_category")

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
    if getattr(state, "destination_category", None):
        hints.append(state.destination_category.replace("_", " "))
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
                "road_distance_km": next(
                    (c.get("road_distance_km") for c in raw if c["name"] == v.name),
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
    """Lock in the user's pick(s) from state.destination_candidates.

    Accepts:
      - A single index ('2')
      - A single name ('Ooty')
      - A comma/and-joined list of the above ('1 and 5', 'Ooty, Munnar')
      - Ordinals ('first', 'last') are handled upstream by _normalize_choice.

    Multi-select adds ALL named destinations to state.destinations. The
    scheduler currently plans for destinations[0] — a multi-city itinerary
    is a future extension.
    """
    if not state.destination_candidates:
        return {"error": "no candidates pending — call search_destination_candidates first"}

    choice = str(args.get("choice", "")).strip()
    if not choice:
        return {"error": "choice is required"}

    # Split "1 and 5" / "1, 5" / "1 & 5" into ["1", "5"].
    # re.split with capture keeps only the tokens between separators.
    raw_tokens = re.split(r"\s*(?:,|and|&)\s*", choice, flags=re.I)
    tokens = [t.strip() for t in raw_tokens if t and t.strip()]
    if not tokens:
        tokens = [choice]

    picked: list[DestinationCandidate] = []
    unmatched: list[str] = []
    already_picked: set[str] = set()

    for token in tokens:
        cand = None

        # Numeric index — 1-based.
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(state.destination_candidates):
                cand = state.destination_candidates[idx]

        # Name substring match.
        if cand is None:
            needle = token.lower()
            for c in state.destination_candidates:
                if needle in c.name.lower():
                    cand = c
                    break

        if cand is None:
            unmatched.append(token)
            continue

        if cand.name in already_picked:
            continue
        already_picked.add(cand.name)
        picked.append(cand)

    if not picked:
        return {
            "error": f"could not match any of {tokens!r}",
            "available": [c.name for c in state.destination_candidates],
            "unmatched": unmatched,
        }

    # Coordinate check across all picks.
    missing_coords = [c.name for c in picked if c.lat is None or c.lng is None]
    if missing_coords:
        return {"error": f"candidate(s) missing coordinates: {missing_coords}"}

    state.destinations = [
        Destination(
            name=c.name,
            place_id=c.place_id,
            lat=c.lat,
            lng=c.lng,
        )
        for c in picked
    ]
    state.destination_candidates = []
    state.pending_destination_query = None
    state.candidate_offset = 0          # <-- add this

    result = {
        "confirmed": [
            {"name": d.name, "lat": d.lat, "lng": d.lng, "place_id": d.place_id}
            for d in state.destinations
        ],
        "missing_required": state.missing_required(),
        "instruction": (
            "Confirm the pick(s) to the user in one short sentence, then "
            "ask the next missing required slot (if any). If more than "
            "one destination was confirmed, mention that the schedule "
            "will focus on the first for now."
        ),
    }
    if unmatched:
        result["unmatched"] = unmatched
    return result

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
    for cat, items in pois.items():
        items.sort(key=lambda p: _score(p, d.lat, d.lng), reverse=True)
        pois[cat] = _dedupe(items)

    attrs_all = pois["attraction"]
    # The full ranked pool feeds the Ideas tab (the user browses and edits
    # it); the scheduler applies its own cap of min(12, n_days*3) so the
    # solver stays small regardless of how wide this is.
    attrs_pool = attrs_all[:30]
    top_picks = attrs_pool[:6]
    top_names = {p["name"] for p in top_picks}

    remainder = [p for p in attrs_pool if p["name"] not in top_names]
    remainder.sort(key=lambda p: _hidden_score(p, d.lat, d.lng), reverse=True)
    hidden_gems = remainder[:5]

    attrs = attrs_pool                  # scheduler input = full browsable pool
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
            "List food venues by name regardless of cuisine. Only mention cuisine "
            "type when `cuisine` is non-null; otherwise present them as "
            "'popular dining spots' with no cuisine claim. "
            "Offer to build the day-by-day schedule next."
        ),
    }

# --------------------------------------------------------------------------- #
# Multi-destination scheduling
# --------------------------------------------------------------------------- #

def _split_days(n_days: int, weights: list[int]) -> list[int]:
    """Allocate n_days across destinations proportional to `weights`,
    every destination getting at least one day. Deterministic."""
    n = len(weights)
    total = sum(weights) or n
    alloc = [max(1, round(n_days * w / total)) for w in weights]
    # Fix rounding so the allocation sums exactly to n_days.
    while sum(alloc) > n_days and max(alloc) > 1:
        i = max(range(n), key=lambda k: (alloc[k], -k))
        alloc[i] -= 1
    while sum(alloc) < n_days:
        i = max(range(n), key=lambda k: (weights[k], alloc[k], -k))
        alloc[i] += 1
    return alloc


def _build_multi_destination(state: TripState, merged: list[str]) -> dict:
    """Schedule a trip that visits several destinations.

    Each destination gets a share of the trip's days proportional to the
    size of its attraction pool (at least one day each), then the single-
    destination scheduler runs once per destination with that destination
    as the hotel anchor. Days are stitched in date order with an explicit
    transfer leg where the traveller moves between destinations.
    """
    from agent import ola_elevation
    from agent import activities as activities_mod
    from agent import ola_routing

    dests = [d for d in state.destinations if d.lat is not None]
    if len(dests) < 2:
        return {"error": "multi-destination scheduling needs at least two "
                          "geocoded destinations"}
    if state.n_days < len(dests):
        return {"error": (f"{len(dests)} destinations need at least "
                          f"{len(dests)} days — add a day or drop one")}

    # --- POI pools per destination --------------------------------------
    pools = []
    for d in dests:
        pois = pois_mod.search_pois(d.lat, d.lng)
        if "error" in pois:
            pools.append((d, [], []))
            continue
        pools.append((d, pois.get("attraction", [])[:30],
                      pois.get("food", [])[:12]))

    # --- Day split ------------------------------------------------------
    weights = [max(len(attrs), 4) for (_, attrs, _) in pools]
    alloc = _split_days(state.n_days, weights)

    # --- Weather per destination (whole date range) ---------------------
    rain_by_dest = []
    for (d, _, _) in pools:
        w = weather.trip_weather(d.lat, d.lng, state.start_date, state.end_date)
        by_date = {row["date"]: row.get("precip_mm", 0) or 0
                   for row in w.get("days", [])}
        rain_by_dest.append(by_date)

    # --- Transfer legs between consecutive destinations -----------------
    def _leg_km(a, b):
        try:
            from agent.transport import _road_distance_km
            km = _road_distance_km((a.lat, a.lng), (b.lat, b.lng), a.name, b.name)
            if km and km > 0:
                return km
        except Exception:
            pass
        return _haversine_km(a.lat, a.lng, b.lat, b.lng) * 1.3

    transfers = []
    for i in range(1, len(dests)):
        km = round(_leg_km(dests[i - 1], dests[i]), 1)
        transfers.append({"from": dests[i - 1].name, "to": dests[i].name,
                          "km": km, "hours": round(km / 35.0, 1)})

    # --- Shared distance factory (one Ola client, all destinations) -----
    def distance_factory(coords):
        try:
            return ola_routing.batch_matrix(coords)
        except Exception:
            return None

    # The accommodation record drives breakfast/dinner (meal plan vs
    # restaurant suggestions) for every day built below.
    stay_rec = ((state.recommendations or {}).get("stay") or [None])[0]

    # --- Per-destination scheduling -------------------------------------
    all_days = []
    unscheduled = []
    validation = {"trek_rule_ok": True, "overloaded_days": [],
                  "unverified_days": []}
    statuses = []
    adventure_level = getattr(state, "adventure_level", "balanced")
    trek_cap = 0
    offset = 0
    allocation = []

    for i, (d, attrs, food) in enumerate(pools):
        n_d = alloc[i]
        allocation.append({"destination": d.name, "days": n_d})
        start_i = state.start_date + timedelta(days=offset)
        rain = [rain_by_dest[i].get(
                    (start_i + timedelta(days=k)).isoformat(), 0.0)
                for k in range(n_d)]

        if not attrs:
            for k in range(n_d):
                all_days.append({
                    "date": (start_i + timedelta(days=k)).isoformat(),
                    "destination": d.name,
                    "rest_day": True, "stops": [], "lunch": None,
                    "lunch_time": None, "lunch_place": None, "lunch_min": None,
                    "expected_rain_mm": round(rain[k], 1),
                    "start_time": "08:30", "end_time": "08:30",
                    "start_min": 510, "end_min": 510,
                    "total_travel_min": 0, "total_visit_min": 0,
                    "effort_min": 0, "effort_cap_min": 0, "trek_count": 0,
                    "class_mix": [], "overloaded": False, "unverified": True,
                })
            unscheduled.append({
                "name": d.name,
                "reason": "no attraction POIs found here — days kept free",
            })
        else:
            # Elevation for this destination's pool (drives trek evidence).
            with_coords = [a for a in attrs if a.get("lat") is not None]
            elevations = None
            base_elevation = None
            if with_coords:
                try:
                    coords = ([(d.lat, d.lng)]
                              + [(a["lat"], a["lng"]) for a in with_coords])
                    vals = ola_elevation.batch_elevation(coords)
                    if vals is not None and len(vals) == len(coords):
                        base_elevation = vals[0]
                        elevations = vals[1:]
                except Exception:
                    elevations = None
            enriched = []
            for a in attrs:
                item = dict(a)
                if elevations is not None and a.get("lat") is not None:
                    try:
                        idx = with_coords.index(a)
                        item["elevation_m"] = elevations[idx]
                        if base_elevation is not None:
                            item["ascent_m"] = (int(elevations[idx])
                                                - int(base_elevation))
                    except ValueError:
                        pass
                enriched.append(item)

            pool_items = [{"name": a["name"], "kind": a.get("kind"),
                           "elevation_m": a.get("elevation_m"),
                           "ascent_m": a.get("ascent_m")}
                          for a in enriched]
            activity_map = activities_mod.classify_pool(pool_items,
                                                        pace=state.pace)

            res = scheduler_mod.build_itinerary(
                enriched, food, d.lat, d.lng,
                start_i, n_d, rain, state.pace,
                exclude_names=merged,
                distance_factory=distance_factory,
                activities=activity_map,
                adventure_level=adventure_level,
                stay=stay_rec,
            )
            if "error" in res:
                return res
            statuses.append(res.get("solver_status", "?"))
            trek_cap = max(trek_cap, res.get("trek_cap", 0))
            v = res.get("validation") or {}
            if not v.get("trek_rule_ok", True):
                validation["trek_rule_ok"] = False
            validation["overloaded_days"] += v.get("overloaded_days", [])
            validation["unverified_days"] += v.get("unverified_days", [])
            for m in res.get("unscheduled", []):
                unscheduled.append(m)
            for day in res.get("days", []):
                day["destination"] = d.name
                # The first day of a destination (except the first overall)
                # carries the transfer in from the previous one.
                if all_days and "transfer_in" not in day and i > 0 \
                        and day is res["days"][0]:
                    day["transfer_in"] = transfers[i - 1]
                all_days.append(day)
        offset += n_d

    # Days built outside the scheduler (manual rest days above) still need
    # breakfast & dinner; scheduled days already carry theirs.
    pending = [day for day in all_days if "breakfast" not in day]
    if pending:
        all_food = [f for (_, _, fs) in pools for f in fs]
        coord_map = {
            a["name"]: (a["lat"], a["lng"])
            for (_, ats, _) in pools for a in ats
            if a.get("lat") is not None
        }
        hotels = []
        for day in pending:
            dest = next((x for x in dests
                         if x.name == day.get("destination")), dests[0])
            hotels.append((dest.lat, dest.lng))
        scheduler_mod.assign_meals(pending, hotels, all_food, stay_rec,
                                   coords_by_name=coord_map)

    state.itinerary = {
        "days": all_days,
        "unscheduled": unscheduled,
        "solver_status": "+".join(statuses) or "MULTI",
        "validation": validation,
        "adventure_level": adventure_level,
        "trek_cap": trek_cap,
        "multi_destination": True,
        "allocation": allocation,
    }
    state.stage = "scheduling"

    schedule = []
    for idx, day in enumerate(all_days, start=1):
        stops = day.get("stops", [])
        schedule.append({
            "day": idx,
            "date": day["date"],
            "destination": day.get("destination"),
            "transfer_in": day.get("transfer_in"),
            "start": day.get("start_time"),
            "end": day.get("end_time"),
            "stops": [s["name"] for s in stops],
            "km_total": round(sum(s.get("km_from_prev", 0) for s in stops), 1),
            "breakfast": _meal_brief(day.get("breakfast")),
            "lunch": day.get("lunch"),
            "lunch_time": day.get("lunch_time"),
            "dinner": _meal_brief(day.get("dinner")),
            "effort_min": day.get("effort_min"),
            "trek_count": day.get("trek_count"),
            "class_mix": day.get("class_mix"),
            "rain_mm": day.get("expected_rain_mm"),
            "rest_day": day.get("rest_day", False),
            "overloaded": day.get("overloaded", False),
        })

    return {
        "schedule": schedule,
        "unscheduled": unscheduled,
        "unscheduled_names": [m["name"] if isinstance(m, dict) else m
                              for m in unscheduled],
        "excluded": merged,
        "solver_status": state.itinerary["solver_status"],
        "validation": validation,
        "adventure_level": adventure_level,
        "trek_cap": trek_cap,
        "multi_destination": True,
        "allocation": allocation,
        "transfers": transfers,
        "instruction": (
            "This is a MULTI-DESTINATION trip. Present the day split first "
            "(e.g. 'Munnar 2 days · Thekkady 1 day'), then each day exactly "
            "as given: destination, date, start/end time, stops IN ORDER "
            "with arrive/depart times, activity labels, breakfast, lunch, "
            "dinner and rain. A meal with included=true is served at the "
            "accommodation — say 'included with the stay'; otherwise "
            "present the suggested venue with its context; a null meal "
            "means no venue was available — skip the line. "
            "Where a day has transfer_in, open it with the transfer leg "
            "(km and hours). Use exact names from the tool result — never "
            "invent stops, prices or meal venues. Mention each day's "
            "class_mix so the variety is visible. The user can change the "
            "split by chat."
        ),
    }


def _meal_brief(meal: dict | None) -> dict | None:
    """Compact meal line for the chat payload — never the full record."""
    if not meal:
        return None
    return {
        "name": meal.get("name"),
        "time": meal.get("arrive"),
        "included": bool(meal.get("included")),
        "context": meal.get("context"),
    }


def build_itinerary(args: dict, state: TripState) -> dict:
    """Build the day-by-day schedule from stored recommendations.

    Distances between stops go through a distance factory: Ola Maps'
    Distance Matrix API provides real road distances when available;
    otherwise the scheduler falls back to haversine × 1.4 (a documented
    hill-station road approximation). The factory returns None on any
    failure, and the scheduler handles that transparently.
    """
    rec = state.recommendations or {}
    if not (state.destinations and state.start_date and state.n_days):
        return {"error": "destination and dates required"}

    # Cumulative exclusions: merge any new names with what's already
    # persisted, unless the caller explicitly asks to reset.
    if args.get("clear_excluded"):
        state.excluded_names = []
    new_excludes = args.get("exclude_names") or []
    merged = sorted(set(state.excluded_names) | set(new_excludes))
    state.excluded_names = merged

    # Multi-destination: several confirmed destinations each get a share
    # of the days and are scheduled independently, then stitched together.
    geocoded = [d for d in state.destinations if d.lat is not None]
    if len(geocoded) > 1:
        return _build_multi_destination(state, merged)

    attrs = rec.get("attractions") or []
    if not attrs:
        return {"error": "no recommendations yet — call get_recommendations first"}

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

    # --- Elevation --------------------------------------------------------
    # Two jobs: evidence for the activity classifier, and the
    # LLM-independent signal that decides "this is a trek" (a measured
    # ascent of 400 m+) when the model is unreachable. Soft dependency:
    # without it the classifier simply has less to go on.
    from agent import ola_elevation

    with_coords = [a for a in attrs if a.get("lat") is not None]
    base_elevation = None
    elevations = None
    if with_coords:
        try:
            coords_for_elev = ([(d.lat, d.lng)]
                               + [(a["lat"], a["lng"]) for a in with_coords])
            vals = ola_elevation.batch_elevation(coords_for_elev)
            if vals is not None and len(vals) == len(coords_for_elev):
                base_elevation = vals[0]
                elevations = vals[1:]
        except Exception as e:
            print(f"  [build_itinerary] elevation unavailable "
                  f"({type(e).__name__}: {e})", flush=True)

    enriched = []
    for a in attrs:
        item = dict(a)
        if elevations is not None and a.get("lat") is not None:
            try:
                idx = with_coords.index(a)
            except ValueError:
                idx = -1
            if 0 <= idx < len(elevations):
                item["elevation_m"] = elevations[idx]
                if base_elevation is not None:
                    item["ascent_m"] = int(elevations[idx]) - int(base_elevation)
        enriched.append(item)

    # --- Activity classification -----------------------------------------
    # ONE batched LLM call for the whole pool, cached by (kind, name, pace).
    # The model decides what the activity is and how long it takes; the
    # scheduler decides whether it fits. Never raises — unclassifiable
    # stops fall back to a conservative default marked source="default".
    from agent import activities as activities_mod

    pool_items = [
        {
            "name": a["name"],
            "kind": a.get("kind"),
            "elevation_m": a.get("elevation_m"),
            "ascent_m": a.get("ascent_m"),
        }
        for a in enriched
    ]
    activity_map = activities_mod.classify_pool(pool_items, pace=state.pace)

    # --- Distance factory -------------------------------------------------
    # Called once per build with [(hotel_lat, hotel_lng), (stop1_lat, ...),
    # ...]. Returns (distances_km, durations_min): the API hands back both
    # in one response, so real travel times cost nothing extra. None means
    # haversine × 1.4 at the documented fallback speed.
    from agent import ola_routing

    def distance_factory(coords: list[tuple[float, float]]):
        try:
            matrix = ola_routing.batch_matrix(coords)
        except Exception as e:
            print(f"  [build_itinerary] distance factory raised "
                  f"{type(e).__name__}: {e}", flush=True)
            return None
        if matrix is None:
            print("  [build_itinerary] Ola matrix unavailable — "
                  "falling back to haversine × 1.4", flush=True)
        return matrix

    # --- Run the scheduler ------------------------------------------------
    result = scheduler_mod.build_itinerary(
        enriched,
        rec.get("food") or [],
        d.lat, d.lng,
        state.start_date,
        state.n_days,
        rain,
        state.pace,
        exclude_names=merged,
        distance_factory=distance_factory,
        activities=activity_map,
        adventure_level=getattr(state, "adventure_level", "balanced"),
        stay=((rec.get("stay") or [None])[0]),
    )
    if "error" in result:
        return result

    state.itinerary = result
    state.stage = "scheduling"

    # --- Return payload for the router / LLM ------------------------------
    # The instruction block is what tells the LLM how to present the
    # schedule. It must never invent stop names or lunch picks — it reads
    # them verbatim from this payload.
    # Every decision the schedule made for the traveller is in here: what
    # the activity is, when they arrive and leave, how much walking a day
    # holds, and why anything is missing.
    schedule = []
    for idx, day in enumerate(result["days"], start=1):
        stops = day.get("stops", [])
        schedule.append({
            "day": idx,
            "date": day["date"],
            "start": day.get("start_time"),
            "end": day.get("end_time"),
            "stops": [
                {
                    "name": s["name"],
                    "activity": s["activity"],
                    "arrive": s["arrive"],
                    "depart": s["depart"],
                    "visit_min": s["visit_min"],
                    "travel_min": s["travel_min"],
                    "km_from_prev": s["km_from_prev"],
                    "intensity": s["intensity"],
                    "is_trek": s["is_trek"],
                    "note": s["note"],
                }
                for s in stops
            ],
            "km_total": round(sum(s["km_from_prev"] for s in stops), 1),
            "breakfast": _meal_brief(day.get("breakfast")),
            "lunch": day.get("lunch"),
            "lunch_time": day.get("lunch_time"),
            "dinner": _meal_brief(day.get("dinner")),
            "effort_min": day.get("effort_min"),
            "effort_cap_min": day.get("effort_cap_min"),
            "travel_min": day.get("total_travel_min"),
            "trek_count": day.get("trek_count"),
            "class_mix": day.get("class_mix"),
            "rain_mm": day.get("expected_rain_mm"),
            "rest_day": day.get("rest_day", False),
            "overloaded": day.get("overloaded", False),
            "unverified": day.get("unverified", False),
        })

    missing = result.get("unscheduled") or []
    missing_names = [m["name"] if isinstance(m, dict) else m
                     for m in missing]

    return {
        "schedule": schedule,
        "unscheduled": missing,
        "unscheduled_names": missing_names,
        "excluded": merged,
        "solver_status": result["solver_status"],
        "activity_source": result.get("activity_source", "llm"),
        "validation": result.get("validation"),
        "adventure_level": result.get("adventure_level"),
        "trek_cap": result.get("trek_cap"),
        "instruction": (
            "Present each day exactly as given: date, start and end time, "
            "stops IN ORDER with their arrive/depart times, the activity "
            "label, total km, breakfast, lunch and dinner (each with its "
            "time), and expected rain. "
            "Use the exact stop names, activity labels and meal names from "
            "the tool result — never invent or substitute names, "
            "never invent prices or durations. A meal with included=true "
            "is served at the accommodation — say 'included with the "
            "stay'; a meal with included=false is the suggested restaurant "
            "from its `context` (near the first stop / near the stay). "
            "A null breakfast or dinner means no venue was available — "
            "skip the line rather than inventing one. "
            "The trip mixes activity types on purpose: mention each day's "
            "class_mix (e.g. trek + viewpoint + food) so the user sees the "
            "variety, not just the climbs. Trek frequency follows the "
            "user's adventure_level: 'low' spreads treks out, 'balanced' "
            "is one every couple of days, 'high' allows one per day. If "
            "the user asks for more treks than the level allows, explain "
            "the tradeoff and offer to raise the level or add days. "
            "If a day has rest_day=true, present it as 'Rest day'. "
            "If a day has overloaded=true, say plainly that it is a long "
            "day. If a day has unverified=true, state that the effort of "
            "its stops could not be assessed, so it may be ambitious. "
            "If `unscheduled` is non-empty, name each stop that was left "
            "out and give the reason attached to it, in one sentence per "
            "stop. If `excluded` is non-empty, mention which stops are "
            "currently removed."
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


def move_stop(args: dict, state: TripState) -> dict:
    """Drag-and-drop edit: move (or reposition) one stop in the LIVE itinerary.

    Only the touched days are re-timed, via scheduler.retime_day — no LLM,
    no road-matrix API: the clock is rebuilt with the haversine fallback so
    the edit lands instantly. Everything is validated BEFORE the first
    mutation, so a rejected move never leaves half-edited state behind.
    """
    it = state.itinerary or {}
    days = it.get("days") or []
    if not days:
        return {"error": "no itinerary yet — build one first"}

    try:
        from_day = int(args.get("from_day"))
        to_day = int(args.get("to_day"))
    except (TypeError, ValueError):
        return {"error": "from_day and to_day must be day numbers"}
    if not (0 <= from_day < len(days) and 0 <= to_day < len(days)):
        return {"error": f"day out of range — the trip has {len(days)} day(s)"}

    name = str(args.get("stop_name") or "").strip()
    src = days[from_day]
    src_stops = src.get("stops") or []
    idx = next((i for i, s in enumerate(src_stops)
                if s.get("name") == name), -1)
    if idx < 0:
        idx = next((i for i, s in enumerate(src_stops)
                    if str(s.get("name") or "").lower() == name.lower()), -1)
    if idx < 0:
        return {"error": f"'{name}' is not on day {from_day + 1}"}

    to_index = args.get("to_index")
    try:
        to_index = int(to_index)
    except (TypeError, ValueError):
        to_index = None

    # --- Validation, all BEFORE the first mutation -----------------------
    rec = state.recommendations or {}
    coords_by_name = {
        a["name"]: (a["lat"], a["lng"])
        for a in (rec.get("attractions") or [])
        if a.get("lat") is not None and a.get("lng") is not None
    }
    dst_stops = days[to_day].get("stops") or []
    affected = list(src_stops) + ([] if to_day == from_day
                                  else list(dst_stops))
    missing = sorted({str(s.get("name")) for s in affected
                      if s.get("name") not in coords_by_name})
    if missing:
        return {"error": f"no coordinates for: {', '.join(missing)}"}

    def _hotel_for(day: dict):
        """Base coordinates for a day — matched by destination name when
        the trip spans several destinations, else the first geocoded one."""
        dest_name = str(day.get("destination") or "").lower()
        geocoded = [d for d in state.destinations
                    if d.lat is not None and d.lng is not None]
        for d in geocoded:
            if str(d.name or "").lower() == dest_name:
                return (d.lat, d.lng)
        return (geocoded[0].lat, geocoded[0].lng) if geocoded else None

    hotel_src = _hotel_for(src)
    hotel_dst = hotel_src if to_day == from_day else _hotel_for(days[to_day])
    if hotel_src is None or hotel_dst is None:
        return {"error": "destination coordinates missing — cannot re-time"}

    # --- Mutate -----------------------------------------------------------
    stop = src_stops.pop(idx)
    if days[to_day].get("stops") is None:
        days[to_day]["stops"] = []
    dst_stops = days[to_day]["stops"]
    if to_index is None or not (0 <= to_index <= len(dst_stops)):
        to_index = len(dst_stops)
    dst_stops.insert(to_index, stop)

    food_all = [f for f in (rec.get("food") or [])
                if f.get("lat") is not None]
    stay_rec = ((rec.get("stay") or [None])[0])
    days[from_day] = scheduler_mod.retime_day(
        days[from_day], hotel_src, coords_by_name, list(food_all),
        stay=stay_rec,
    )
    if to_day != from_day:
        # Keep the two lunches apart when the pool allows it.
        first_lunch = days[from_day].get("lunch")
        pool = [f for f in food_all if f.get("name") != first_lunch]
        days[to_day] = scheduler_mod.retime_day(
            days[to_day], hotel_dst, coords_by_name,
            pool or list(food_all),
            stay=stay_rec,
        )

    # Sessions built before meals existed (or days the scheduler never
    # touched) get breakfast & dinner here — the first edit heals the trip.
    pending = [dd for dd in days if "breakfast" not in dd]
    if pending:
        hotels = [_hotel_for(dd) or hotel_src for dd in pending]
        scheduler_mod.assign_meals(pending, hotels, food_all, stay_rec,
                                   coords_by_name)

    # --- Flags a user edit can invalidate --------------------------------
    v = it.setdefault("validation", {})
    v["overloaded_days"] = [d.get("date") for d in days
                            if d.get("overloaded")]
    v["unverified_days"] = [d.get("date") for d in days
                            if d.get("unverified")]
    v["trek_rule_ok"] = all((d.get("trek_count") or 0) <= 1 for d in days)

    return {
        "moved": stop["name"],
        "from_day": from_day + 1,
        "to_day": to_day + 1,
        "to_index": to_index,
        "instruction": (
            "The traveller moved a stop by hand. Present the affected days "
            "exactly as returned — new arrive/depart times, lunch and end "
            "time — not the old schedule."
        ),
    }
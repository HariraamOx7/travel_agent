"""Day-by-day itinerary scheduling via OR-Tools CP-SAT.

Assignment: which attractions go on which day   (constraint solver)
Ordering:   visit order within a day            (nearest-neighbor heuristic)
Meals:      nearest lunch per day               (deterministic)

Constraints & objective (all report-explainable):
- every selected attraction is scheduled exactly once
- daily load bounds come from pace (relaxed/balanced/packed),
  relaxed automatically when attractions are scarce
- RAIN COST: outdoor stops on high-precipitation days cost more
- DISPERSION COST: same-day pairs >5 km apart cost more (2x rain unit)
"""
import math
from datetime import timedelta
from ortools.sat.python import cp_model

_PACE = {"relaxed": (1, 2), "balanced": (2, 3), "packed": (3, 4)}
_INDOOR = {"museum", "mall", "aquarium", "gallery"}
_FAR_KM = 5.0


def _haversine(a_lat, a_lng, b_lat, b_lng):
    p = math.pi / 180
    h = (0.5 - math.cos((b_lat - a_lat) * p) / 2
         + math.cos(a_lat * p) * math.cos(b_lat * p)
         * (1 - math.cos((b_lng - a_lng) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))

def build_itinerary(attrs, food, dest_lat, dest_lng,
                    start_date, n_days, rain_mm, pace="balanced",
                    exclude_names=None) -> dict:
    exclude = {n.lower() for n in (exclude_names or [])}
    attrs = [a for a in attrs
             if a.get("lat") is not None and a["name"].lower() not in exclude]
    unscheduled = [a["name"] for a in attrs[12:]]
    attrs = attrs[:12]
    if not attrs or not n_days:
        return {"error": "need attractions and trip dates to schedule"}

    lo_pace, hi_pace = _PACE.get(pace, _PACE["balanced"])
    n = len(attrs)

    # Target an even load per day. Hard cap = hi; deviations from target
    # are penalized softly so the solver spreads attractions evenly
    # (2,2,1,1,1,1) instead of clustering them (1,1,1,1,1,3).
    target = math.ceil(n / n_days)
    hi = max(hi_pace, target + 1)

    m = cp_model.CpModel()
    x = {(i, d): m.NewBoolVar(f"x_{i}_{d}") for i in range(n) for d in range(n_days)}

    for i in range(n):                                   # every attraction exactly once
        m.Add(sum(x[i, d] for d in range(n_days)) == 1)
    

    # Daily load: hard cap on hi; soft target on the low end via imbalance cost.
    load = {d: m.NewIntVar(0, n, f"load_{d}") for d in range(n_days)}
    for d in range(n_days):
        m.Add(load[d] == sum(x[i, d] for i in range(n)))
        m.Add(load[d] <= hi)

    # No empty days when there are enough attractions to fill them.
    # This is what forces the balanced 2,1,1,2,1,1 shape instead of 2,0,0,2,2,2.
    if n >= n_days:
        for d in range(n_days):
            m.Add(load[d] >= 1)

    imbalance = []
    for d in range(n_days):
        dev = m.NewIntVar(0, n, f"dev_{d}")
        m.Add(dev >= load[d] - target)
        m.Add(dev >= target - load[d])
        imbalance.append(dev)

        # If days must be empty (fewer attractions than days), penalize
        # early empty days more than late ones — end-of-trip rest days
        # read as departure rest, mid-trip gaps look broken.
        empty = m.NewBoolVar(f"empty_{d}")
        m.Add(load[d] == 0).OnlyEnforceIf(empty)
        m.Add(load[d] >= 1).OnlyEnforceIf(empty.Not())
        imbalance.append((n_days - d) * empty)

    rain_cost = []
    for i in range(n):
        outdoor = 0 if attrs[i]["kind"] in _INDOOR else 1
        for d in range(n_days):
            rain_cost.append(int(round(rain_mm[d])) * outdoor * x[i, d])

    far_cost = []
    for i in range(n):
        for j in range(i + 1, n):
            if _haversine(attrs[i]["lat"], attrs[i]["lng"],
                          attrs[j]["lat"], attrs[j]["lng"]) <= _FAR_KM:
                continue
            for d in range(n_days):                      # reify "both on day d"
                both = m.NewBoolVar(f"both_{i}_{j}_{d}")
                m.Add(both <= x[i, d])
                m.Add(both <= x[j, d])
                m.Add(both >= x[i, d] + x[j, d] - 1)
                far_cost.append(both)

    # Imbalance is the dominant term — it decides the day distribution.
    # Rain and dispersion then order the stops within that distribution.
    m.Minimize(sum(rain_cost) + 2 * sum(far_cost) + 10 * sum(imbalance))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"error": f"solver found no schedule ({solver.StatusName(status)})"}

    day_members = {d: [i for i in range(n) if solver.Value(x[i, d])]
                   for d in range(n_days)}

    food_pool = [f for f in food if f.get("lat") is not None]
    days_out = []
    for d in range(n_days):
        stops, cur, remaining = [], (dest_lat, dest_lng), list(day_members[d])
        stop_coords = []
        while remaining:                                 # nearest-neighbor ordering
            nxt = min(remaining, key=lambda i: _haversine(
                cur[0], cur[1], attrs[i]["lat"], attrs[i]["lng"]))
            km = _haversine(cur[0], cur[1], attrs[nxt]["lat"], attrs[nxt]["lng"])
            stops.append({"name": attrs[nxt]["name"], "kind": attrs[nxt]["kind"],
                          "km_from_prev": round(km, 1)})
            stop_coords.append((attrs[nxt]["lat"], attrs[nxt]["lng"]))
            cur = (attrs[nxt]["lat"], attrs[nxt]["lng"])
            remaining.remove(nxt)

        lunch = None
        if stop_coords and (food_pool or food):
            mid = (sum(c[0] for c in stop_coords) / len(stop_coords),
                   sum(c[1] for c in stop_coords) / len(stop_coords))
            # Prefer an unused restaurant; if the pool is empty (trip longer
            # than the food list), fall back to reusing the closest from the
            # full list so no day ends up without a lunch pick.
            pool = food_pool if food_pool else food
            best = min(range(len(pool)), key=lambda k: _haversine(
                mid[0], mid[1], pool[k]["lat"], pool[k]["lng"]))
            lunch = pool[best]["name"]
            if pool is food_pool:
                food_pool.pop(best)

        days_out.append({
            "date": (start_date + timedelta(days=d)).isoformat(),
            "stops": stops,
            "lunch": lunch,
            "expected_rain_mm": round(rain_mm[d], 1),
            "rest_day": len(stops) == 0,
        })
    return {"days": days_out, "unscheduled": unscheduled,
            "solver_status": solver.StatusName(status)}
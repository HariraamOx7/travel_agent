"""Day-by-day itinerary scheduling via OR-Tools CP-SAT.

Layered architecture
--------------------
  Stage 1  Pool construction     filter by radius, cap by trip length,
                                 pre-cluster geographically (DBSCAN).
  Stage 2  Feasibility           predict infeasibility, relax the pace cap.
  Stage 3  Assignment            CP-SAT over clusters, tiered dispersion.
  Stage 3b Day ordering          reorder days geographically.
  Stage 4  Sequencing            Nearest-neighbor + 2-opt per day.
  Stage 5  Timeline              Per-stop arrive/depart times, built from
                                 each activity's effort and real road
                                 durations; lunch slotted at midday.
  Stage 6  Validation            Rebalance days that overrun the day
                                 window, re-check the one-trek-per-day
                                 rule, deterministic output.

Effort model
------------
Every stop carries an activity decision (see agent/activities.py) with a
visit duration in minutes. Two HARD constraints keep a day physically
possible:

  H5  at most ONE trek per day. Never relaxed, never traded against the
      rain or dispersion objectives.
  H6  the sum of visit minutes in a day may not exceed the pace's effort
      cap (relaxed 240 / balanced 330 / packed 420 minutes on foot).

This matters because the geographic terms actively reward grouping nearby
peaks: three summits 4-6 km apart look like a tidy day to a dispersion
objective while being ~7 h 45 m of climbing to a human. Effort therefore
has to be a hard constraint; as a soft preference it always loses.

Assignment is done per ATTRACTION (x[i, d]) rather than per geographic
cluster, so a cluster holding two peaks can no longer smuggle both into
one day. Clusters survive only as a soft cohesion reward.

Distance model
--------------
All inter-stop distances go through a `_DistanceMatrix` wrapper. The
caller (tools.py) can supply a real road matrix from the Ola Maps
Distance Matrix API; when unavailable, the wrapper falls back to
haversine × 1.4, a documented hill-station road approximation.

Indices in the wrapper: 0 = the destination hotel, 1..n = the filtered
attractions in the order they were kept.
"""
import logging
import math
from datetime import timedelta

from ortools.sat.python import cp_model

from agent.activities import Activity


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_PACE = {"relaxed": (1, 2), "balanced": (2, 3), "packed": (3, 4)}
_INDOOR = {"museum", "mall", "aquarium", "gallery", "temple", "church"}

_FAR_KM = 3.0
_VERY_FAR_KM = 8.0
_EXTREME_HARD_KM = 25.0
_MAX_ATTR_KM = 12.0

_W_RAIN = 1
_W_LOAD = 3

_SOLVER_TIMEOUT_S = 5.0
_DETERMINISTIC_SEED = 42

# Day window. Treks need an early start to fit the climb inside daylight.
_DAY_START_H = 8.5              # 08:30 for an ordinary day
_TREK_DAY_START_H = 6.0         # 06:00 when the day contains a trek
_DAY_END_H = 19.0               # 19:00 envelope

# Midday meal.
_LUNCH_WINDOW_START_H = 12.5    # insert lunch as the clock passes 12:30
_LUNCH_DWELL_H = 0.75           # 45 minutes at the table

# Daily on-foot effort budget (minutes), by the user's pace.
_EFFORT_CAP_MIN = {"relaxed": 240, "balanced": 330, "packed": 420}

# Trek cadence, by the user's adventure level: one trek every N days at
# most. `high` keeps the old one-per-day ceiling. A 'balanced' trip must
# stay a MIX of activities - four summit days out of four is exactly the
# all-trek schedule the user pushed back on.
_TREK_EVERY_N_DAYS = {"low": 3, "balanced": 2, "high": 1}

# Activity-class diversity. Classes come from agent.activities (trek,
# sightsee, sight, leisure, ...). Days that mix classes read as a fuller
# trip than days that stack four viewpoints, so distinct classes are
# rewarded and a day stacking more than _MAX_SAME_CLASS stops of one class
# is penalized. Both are SOFT: geography and rain still compete.
_MAX_SAME_CLASS_PER_DAY = 2
_W_DIVERSITY = 2
_W_SAME_CLASS = 1

# Speed used only for legs the road matrix does not cover (the lunch
# detour, which is chosen after the matrix is built). Hill roads, so this
# is deliberately slower than the 30 km/h the old meal-timing estimate used.
_FALLBACK_SPEED_KMPH = 25.0

# Soft reward for keeping very close stops on the same day. Replaces the
# DBSCAN cluster-assignment term without its failure mode.
_COHESION_KM = 2.0
_W_COHESION = 1
_W_EFFORT = 1                   # spread effort, not just stop counts

# How many times a day may shed a stop before we accept an overloaded day.
_MAX_REBALANCE_ROUNDS = 12

# Fallback road factor when Ola is unavailable. Hill-station roads run
# 1.3–1.6× straight-line; 1.4 is the midpoint.
_ROAD_FACTOR = 1.4


# --------------------------------------------------------------------------- #
# Geometry + distance wrapper
# --------------------------------------------------------------------------- #

def _haversine(a_lat, a_lng, b_lat, b_lng):
    p = math.pi / 180
    h = (0.5 - math.cos((b_lat - a_lat) * p) / 2
         + math.cos(a_lat * p) * math.cos(b_lat * p)
         * (1 - math.cos((b_lng - a_lng) * p)) / 2)
    return 12742 * math.asin(math.sqrt(h))


class _DistanceMatrix:
    """Pairwise distance provider for a fixed set of points.

    Index 0 is the hotel. Indices 1..n correspond to `attrs[0..n-1]`
    after filtering. When a road matrix is provided by the caller, all
    inter-stop distances come from it; otherwise haversine × ROAD_FACTOR
    is used, a documented approximation.

    The hotel-to-stop leg also uses the road matrix (index 0 to i) when
    available, so every call in the pipeline benefits equally.
    """

    def __init__(
        self,
        coords: list[tuple[float, float]],
        road: list[list[float]] | None = None,
        road_minutes: list[list[float]] | None = None,
    ):
        self._coords = coords
        self._road = road
        self._road_minutes = road_minutes

    def between(self, i: int, j: int) -> float:
        """Distance in km between point i and point j (indices into coords)."""
        if i == j:
            return 0.0
        if self._road is not None:
            return self._road[i][j]
        la1, ln1 = self._coords[i]
        la2, ln2 = self._coords[j]
        return _haversine(la1, ln1, la2, ln2) * _ROAD_FACTOR

    def from_hotel(self, i: int) -> float:
        """Distance from hotel (index 0) to point i."""
        return self.between(0, i)

    def minutes(self, i: int, j: int) -> float:
        """Travel time in minutes between two indices.

        Real road duration from the Ola matrix when available (the API
        returns distance and duration together, so this is free), otherwise
        distance at the documented fallback speed.
        """
        if i == j:
            return 0.0
        if self._road_minutes is not None:
            return float(self._road_minutes[i][j])
        return self.between(i, j) / _FALLBACK_SPEED_KMPH * 60.0

    def travel_minutes_between_coords(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
        """Travel minutes for points outside the matrix (the lunch detour).

        Restaurants are chosen after the matrix is built, so they are not in
        it; this falls back to haversine × road factor at the fallback speed.
        """
        km = _haversine(a[0], a[1], b[0], b[1]) * _ROAD_FACTOR
        return km / _FALLBACK_SPEED_KMPH * 60.0


# --------------------------------------------------------------------------- #
# Stage 1 — pool construction
# --------------------------------------------------------------------------- #

def _filter_and_cap(attrs, dest_lat, dest_lng, n_days, exclude):
    """Radius filter + pool cap, with a stated reason for every leftover.

    Everything left out is returned with a reason, so the reply can say why
    a stop is missing instead of it quietly disappearing.
    """
    kept = []
    unscheduled: list[dict] = []
    for a in attrs:
        if a.get("lat") is None or a.get("lng") is None:
            continue
        if a["name"].lower() in exclude:
            continue                     # the user removed it; they know why
        d_km = _haversine(dest_lat, dest_lng, a["lat"], a["lng"])
        if d_km > _MAX_ATTR_KM:
            unscheduled.append({
                "name": a["name"],
                "reason": (f"{d_km:.0f} km from the destination — beyond the "
                           f"{int(_MAX_ATTR_KM)} km day-trip radius"),
            })
            continue
        kept.append(a)

    max_total = min(12, max(3, n_days * 3))
    for a in kept[max_total:]:
        unscheduled.append({
            "name": a["name"],
            "reason": (f"beyond the {max_total}-stop pool for a "
                       f"{n_days}-day trip"),
        })
    return kept[:max_total], unscheduled


# NOTE: geographic pre-clustering was removed here. Assignment is now per
# attraction (see _solve_assignment), because a cluster could hold two peaks
# and a cluster-level load cap counts two peaks as two stops — exactly how a
# day ended up with three summits. Cohesion is a soft pairwise reward now.


# --------------------------------------------------------------------------- #
# Stage 3 — assignment
# --------------------------------------------------------------------------- #

def _solve_assignment(attrs, n_days, target, max_load, rain_mm,
                      dm: _DistanceMatrix, effort_min, is_trek,
                      max_effort_min, class_of=None, trek_cap=None):
    """Assign each attraction to a day, respecting effort feasibility.

    H5 (at most one trek per day) and H6 (daily on-foot effort cap) are
    hard constraints. The caller guarantees feasibility by capping the
    number of treks at the number of days before calling in.
    """
    n_attrs = len(attrs)
    outdoor = [
        0 if (a.get("kind") or "").lower() in _INDOOR else 1
        for a in attrs
    ]

    # Pairwise terms, now over individual attractions rather than clusters.
    pair_cost: dict[tuple[int, int], int] = {}
    pair_forbidden: set[tuple[int, int]] = set()
    close_pairs: list[tuple[int, int]] = []
    for i in range(n_attrs):
        for j in range(i + 1, n_attrs):
            d_ij = dm.between(i + 1, j + 1)
            if d_ij > _EXTREME_HARD_KM:
                pair_forbidden.add((i, j))
            elif d_ij > _VERY_FAR_KM:
                pair_cost[(i, j)] = 5
            elif d_ij > _FAR_KM:
                pair_cost[(i, j)] = 1
            elif d_ij <= _COHESION_KM:
                close_pairs.append((i, j))

    m = cp_model.CpModel()
    x = {(i, d): m.NewBoolVar(f"x_{i}_{d}")
         for i in range(n_attrs) for d in range(n_days)}

    # H1 — every attraction is scheduled on exactly one day.
    for i in range(n_attrs):
        m.Add(sum(x[i, d] for d in range(n_days)) == 1)

    # H2 — stop count per day.
    for d in range(n_days):
        m.Add(sum(x[i, d] for i in range(n_attrs)) <= max_load)

    # H3 — no empty day while there is enough to fill them.
    if n_attrs >= n_days:
        for d in range(n_days):
            m.Add(sum(x[i, d] for i in range(n_attrs)) >= 1)

    # H4 — an extremely long same-day pair is forbidden.
    for (i, j) in pair_forbidden:
        for d in range(n_days):
            m.Add(x[i, d] + x[j, d] <= 1)

    # H5 — at most `trek_cap` treks across the WHOLE trip (from the user's
    # adventure level), and never more than one per day. Both are hard:
    # the cap is the user's stated preference, the per-day rule is physics.
    total_treks = sum(1 for t in is_trek if t)
    if trek_cap is not None and total_treks > trek_cap:
        # Cap applies trip-wide. Spread across days by bounding each day
        # at min(1, cap) treks and the total at cap.
        for d in range(n_days):
            m.Add(sum(x[i, d] for i in range(n_attrs) if is_trek[i]) <= 1)
        m.Add(sum(x[i, d] for i in range(n_attrs) if is_trek[i]
                  for d in range(n_days)) <= trek_cap)
    else:
        for d in range(n_days):
            m.Add(sum(x[i, d] for i in range(n_attrs) if is_trek[i]) <= 1)

    # H6 — daily on-foot effort budget.
    for d in range(n_days):
        m.Add(sum(effort_min[i] * x[i, d] for i in range(n_attrs))
              <= max_effort_min)

    dispersion_terms = []
    for (i, j), weight in pair_cost.items():
        for d in range(n_days):
            b = m.NewBoolVar(f"both_{i}_{j}_{d}")
            m.Add(b <= x[i, d])
            m.Add(b <= x[j, d])
            m.Add(b >= x[i, d] + x[j, d] - 1)
            dispersion_terms.append(weight * b)

    # Soft cohesion: very close stops prefer the same day. This is what the
    # DBSCAN cluster term used to encode, without letting a cluster carry
    # two peaks into one day.
    cohesion_terms = []
    for (i, j) in close_pairs:
        for d in range(n_days):
            b = m.NewBoolVar(f"near_{i}_{j}_{d}")
            m.Add(b <= x[i, d])
            m.Add(b <= x[j, d])
            m.Add(b >= x[i, d] + x[j, d] - 1)
            cohesion_terms.append(b)

    imbalance_terms = []
    for d in range(n_days):
        load_d = m.NewIntVar(0, n_attrs, f"load_{d}")
        m.Add(load_d == sum(x[i, d] for i in range(n_attrs)))
        dev = m.NewIntVar(0, n_attrs, f"dev_{d}")
        m.Add(dev >= load_d - target)
        m.Add(dev >= target - load_d)
        imbalance_terms.append(dev)

    # Spread EFFORT, not just stop counts: four viewpoints are a day's
    # worth of walking, one long trek is another.
    total_effort = int(sum(effort_min)) or 1
    target_effort = total_effort // max(n_days, 1)
    effort_terms = []
    for d in range(n_days):
        eff_d = m.NewIntVar(0, total_effort, f"eff_{d}")
        m.Add(eff_d == sum(effort_min[i] * x[i, d] for i in range(n_attrs)))
        edev = m.NewIntVar(0, total_effort, f"edev_{d}")
        m.Add(edev >= eff_d - target_effort)
        m.Add(edev >= target_effort - eff_d)
        effort_terms.append(edev)

    rain_terms = []
    for i in range(n_attrs):
        if outdoor[i] == 0:
            continue
        for d in range(n_days):
            rain_terms.append(
                int(round(rain_mm[d] if d < len(rain_mm) else 0))
                * outdoor[i]
                * x[i, d]
            )

    # Class diversity — the "whole experience" term. b[c, d] is true when
    # day d contains at least one stop of class c; every distinct class in
    # a day is rewarded, so a trek+viewpoint+temple day beats four stops
    # of the same kind. Plus a soft cap on stacking one class.
    diversity_terms = []
    same_class_terms = []
    if class_of:
        classes_present = sorted(set(class_of))
        for d in range(n_days):
            for c in classes_present:
                members = [i for i in range(n_attrs) if class_of[i] == c]
                b = m.NewBoolVar(f"cls_{c}_{d}")
                for i in members:
                    m.Add(b >= x[i, d])       # reward pushes b to the max
                diversity_terms.append(b)
                if len(members) > _MAX_SAME_CLASS_PER_DAY:
                    over = m.NewIntVar(0, len(members), f"over_{c}_{d}")
                    m.Add(over >= sum(x[i, d] for i in members)
                          - _MAX_SAME_CLASS_PER_DAY)
                    same_class_terms.append(over)

    m.Minimize(
        _W_RAIN * sum(rain_terms)
        + sum(dispersion_terms)
        - _W_COHESION * sum(cohesion_terms)
        + _W_LOAD * sum(imbalance_terms)
        + _W_EFFORT * sum(effort_terms)
        - _W_DIVERSITY * sum(diversity_terms)
        + _W_SAME_CLASS * sum(same_class_terms)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVER_TIMEOUT_S
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = _DETERMINISTIC_SEED
    status = solver.Solve(m)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        log.warning("CP-SAT failed (%s), using greedy fallback",
                    solver.StatusName(status))
        return (
            _greedy_assign(n_attrs, n_days, max_load, effort_min, is_trek,
                           max_effort_min, trek_cap=trek_cap),
            f"FALLBACK({solver.StatusName(status)})",
        )

    day_members = {d: [] for d in range(n_days)}
    for i in range(n_attrs):
        for d in range(n_days):
            if solver.Value(x[i, d]):
                day_members[d].append(i)
                break
    return day_members, solver.StatusName(status)


def _greedy_assign(n_attrs, n_days, max_load, effort_min, is_trek,
                   max_effort_min, trek_cap=None):
    """Feasibility-first fallback, used only when CP-SAT gives up.

    Treks are placed first, one per day, honouring the trip-wide cap;
    everything else lands on the least-loaded day with room left. The
    effort cap is relaxed before the trek rule, because an overloaded day
    is visible to the user while a silently broken H5 is not.
    """
    day_members: dict[int, list[int]] = {d: [] for d in range(n_days)}
    day_load = {d: 0 for d in range(n_days)}
    day_effort = {d: 0 for d in range(n_days)}
    day_treks = {d: 0 for d in range(n_days)}
    total_treks = 0

    order = sorted(
        range(n_attrs),
        key=lambda i: (0 if is_trek[i] else 1, -effort_min[i], i),
    )

    for i in order:
        if is_trek[i] and trek_cap is not None and total_treks >= trek_cap:
            continue        # trip-wide trek budget spent; stop stays unscheduled
        feasible = [
            d for d in range(n_days)
            if day_load[d] + 1 <= max_load
            and not (is_trek[i] and day_treks[d] >= 1)
            and day_effort[d] + effort_min[i] <= max_effort_min
        ]
        if feasible:
            d = min(feasible,
                    key=lambda k: (day_load[k], day_effort[k], k))
        else:
            relaxed = [d for d in range(n_days)
                       if not (is_trek[i] and day_treks[d] >= 1)]
            pool = relaxed or list(range(n_days))
            d = min(pool, key=lambda k: (day_load[k], day_effort[k], k))
        day_members[d].append(i)
        day_load[d] += 1
        day_effort[d] += effort_min[i]
        if is_trek[i]:
            day_treks[d] += 1
            total_treks += 1
    return day_members


# --------------------------------------------------------------------------- #
# Stage 3b — day ordering
# --------------------------------------------------------------------------- #

def _order_days_geographically(day_members, attrs, dm: _DistanceMatrix):
    """Reorder day slots so consecutive days are geographically adjacent.

    Uses dm.from_hotel for the initial hop, and dm.between for day-to-day
    transitions. Rest days (empty clusters) default to the hotel and end
    up at the tail of the chain.
    """
    if len(day_members) <= 1:
        return list(day_members.keys())

    centroids = {}
    for d, members in day_members.items():
        if not members:
            centroids[d] = None       # hotel
            continue
        lats = [attrs[i]["lat"] for i in members]
        lngs = [attrs[i]["lng"] for i in members]
        centroids[d] = (sum(lats) / len(lats), sum(lngs) / len(lngs))

    def dist_between(a, b) -> float:
        if a is None and b is None:
            return 0.0
        if a is None:
            return _haversine(0, 0, b[0], b[1])   # placeholder, replaced below
        if b is None:
            return _haversine(a[0], a[1], 0, 0)
        return _haversine(a[0], a[1], b[0], b[1])

    # We can't use dm directly because centroids aren't in the matrix.
    # Use haversine × ROAD_FACTOR for centroids — accuracy matters less
    # at the day-chaining scale than at the stop-sequencing scale.
    def centroid_dist(a, b) -> float:
        if a is None and b is None:
            return 0.0
        if a is None or b is None:
            return 0.0     # hotel-to-first-day handled separately
        return _haversine(a[0], a[1], b[0], b[1]) * _ROAD_FACTOR

    unvisited = set(day_members.keys())
    # Start at the hotel. Pick the nearest day's centroid.
    current = None
    order = []
    while unvisited:
        nxt = min(
            unvisited,
            key=lambda d: (centroid_dist(current, centroids[d]), d),
        )
        order.append(nxt)
        unvisited.remove(nxt)
        current = centroids[nxt]
    return order


# --------------------------------------------------------------------------- #
# Stage 4 — sequencing
# --------------------------------------------------------------------------- #

def _order_stops(member_indices, attrs, dm: _DistanceMatrix):
    """Nearest-neighbor seed + 2-opt. Uses dm for real road distances.

    member_indices are indices into `attrs` (0-based); the wrapper indexes
    coords as i+1 (because coords[0] is the hotel).
    """
    if len(member_indices) <= 2:
        return list(member_indices)

    def d(i_idx: int, j_idx: int) -> float:
        return dm.between(i_idx + 1, j_idx + 1)

    # Seed: nearest to hotel first.
    remaining = sorted(
        member_indices,
        key=lambda i: (dm.from_hotel(i + 1), attrs[i]["name"]),
    )
    tour = [remaining.pop(0)]
    while remaining:
        cur = tour[-1]
        nxt = min(remaining,
                  key=lambda i: (d(cur, i), attrs[i]["name"]))
        tour.append(nxt)
        remaining.remove(nxt)

    # 2-opt.
    improved = True
    guard = 0
    while improved and guard < 32:
        guard += 1
        improved = False
        for i in range(1, len(tour) - 1):
            for j in range(i + 1, len(tour)):
                a, b = tour[i - 1], tour[i]
                c = tour[j]
                dd = tour[j + 1] if j + 1 < len(tour) else None
                old = d(a, b) + (d(c, dd) if dd is not None else 0.0)
                new = d(a, c) + (d(b, dd) if dd is not None else 0.0)
                if new < old - 1e-6:
                    tour[i:j + 1] = list(reversed(tour[i:j + 1]))
                    improved = True
    return tour


# --------------------------------------------------------------------------- #
# Stage 5 — meal timing
# --------------------------------------------------------------------------- #

def _fmt_clock(minutes_from_midnight: float) -> str:
    """Format a minutes-from-midnight float as HH:MM."""
    total = int(round(minutes_from_midnight))
    total = max(0, min(24 * 60 - 1, total))
    return f"{total // 60:02d}:{total % 60:02d}"


def _nearest_food(coord, food_pool):
    """Closest restaurant to a coordinate, ties broken by name."""
    return min(
        food_pool,
        key=lambda f: (_haversine(coord[0], coord[1], f["lat"], f["lng"])
                       * _ROAD_FACTOR, f["name"]),
    )


def _build_timeline(tour, attrs, dm: _DistanceMatrix, effort_min, is_trek,
                    activities, food_pool):
    """Walk one day's stops on a clock.

    Returns (stops, totals, lunch_name, lunch_place, lunch_minutes,
    used_food) where every stop carries arrive/depart times.

    The clock is what the old `_pick_lunch` computed and then discarded:
    it walked stops at a fixed 1.5 h each purely to find ~13:00. Here the
    walk is the output, using each activity's real effort and real road
    durations.
    """
    hotel = dm._coords[0]
    day_start_h = (
        _TREK_DAY_START_H if any(is_trek[i] for i in tour)
        else _DAY_START_H
    )
    clock = day_start_h * 60.0
    travel_total = 0.0
    visit_total = 0.0
    stops: list[dict] = []

    lunch_name = None
    lunch_place = None
    lunch_minutes = None
    used_food = None
    lunch_done = False
    cur_idx: int | None = 0          # hotel
    cur_coord = hotel

    for i in tour:
        attr = attrs[i]
        stop_coord = (attr["lat"], attr["lng"])

        if cur_idx is not None:
            travel = dm.minutes(cur_idx, i + 1)
            km = dm.between(cur_idx, i + 1)
        else:
            travel = dm.travel_minutes_between_coords(cur_coord, stop_coord)
            km = (_haversine(cur_coord[0], cur_coord[1],
                             stop_coord[0], stop_coord[1]) * _ROAD_FACTOR)

        clock += travel
        travel_total += travel
        arrive_min = clock
        clock += effort_min[i]
        visit_total += effort_min[i]
        depart_min = clock

        act = activities[i]
        stops.append({
            "name": attr["name"],
            "kind": attr.get("kind"),
            "activity": act.activity,
            "class": act.cls,
            "intensity": act.intensity,
            "strenuous": act.strenuous,
            "note": act.note,
            "is_trek": bool(is_trek[i]),
            "trek_source": act.trek_source,
            "duration_source": act.duration_source,
            "elevation_m": attr.get("elevation_m"),
            "ascent_m": attr.get("ascent_m"),
            "visit_min": int(effort_min[i]),
            "travel_min": int(round(travel)),
            "km_from_prev": round(km, 1),
            "arrive": _fmt_clock(arrive_min),
            "depart": _fmt_clock(depart_min),
            "arrive_min": int(round(arrive_min)),
            "depart_min": int(round(depart_min)),
        })

        cur_idx = i + 1
        cur_coord = stop_coord

        # Slot lunch the moment the clock passes 12:30, once per day.
        if (not lunch_done) and food_pool \
                and clock >= _LUNCH_WINDOW_START_H * 60:
            pick = _nearest_food(cur_coord, food_pool)
            place = (pick["lat"], pick["lng"])
            leg = dm.travel_minutes_between_coords(cur_coord, place)
            clock += leg
            travel_total += leg
            lunch_arrive = clock
            clock += _LUNCH_DWELL_H * 60
            lunch_name = pick["name"]
            lunch_place = {
                "name": pick["name"],
                "arrive": _fmt_clock(lunch_arrive),
                "depart": _fmt_clock(clock),
                "arrive_min": int(round(lunch_arrive)),
                "depart_min": int(round(clock)),
            }
            lunch_minutes = int(round(clock - lunch_arrive))
            used_food = pick
            lunch_done = True
            cur_idx = None           # restaurant is not in the road matrix
            cur_coord = place

    # A trek day can finish by 10:30. The meal still belongs to the day,
    # so it is slotted after the last stop at midday rather than dropped —
    # "no lunch" is not a better answer than "lunch at 12:30".
    if (not lunch_done) and food_pool and stops:
        pick = _nearest_food(cur_coord, food_pool)
        place = (pick["lat"], pick["lng"])
        leg = dm.travel_minutes_between_coords(cur_coord, place)
        clock += leg
        travel_total += leg
        lunch_arrive = max(clock, _LUNCH_WINDOW_START_H * 60)
        clock = lunch_arrive + _LUNCH_DWELL_H * 60
        lunch_name = pick["name"]
        lunch_place = {
            "name": pick["name"],
            "arrive": _fmt_clock(lunch_arrive),
            "depart": _fmt_clock(clock),
            "arrive_min": int(round(lunch_arrive)),
            "depart_min": int(round(clock)),
        }
        lunch_minutes = int(round(clock - lunch_arrive))
        used_food = pick
        lunch_done = True
        cur_idx = None
        cur_coord = place

    # Return leg to the stay — part of the day, so it belongs in end_time.
    if cur_idx is not None:
        back = dm.minutes(cur_idx, 0)
    else:
        back = dm.travel_minutes_between_coords(cur_coord, hotel)
    clock += back
    travel_total += back

    totals = {
        "start_min": int(round(day_start_h * 60)),
        "end_min": int(round(clock)),
        "total_travel_min": int(round(travel_total)),
        "total_visit_min": int(round(visit_total)),
    }
    return (stops, totals, lunch_name, lunch_place, lunch_minutes, used_food)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def _neutral_activity(name: str) -> Activity:
    """Fallback activity when no classification is available at all."""
    return Activity(
        name=name,
        activity="Sightseeing stop",
        cls="sightsee",
        intensity=2,
        visit_min=60,
        source="default",
        duration_source="default",
    )


def _rest_day(date_str: str, rain_d: float) -> dict:
    """A day with nothing scheduled. Keeps the same key set as a full day
    so consumers never have to special-case missing fields."""
    start = int(_DAY_START_H * 60)
    return {
        "date": date_str,
        "stops": [],
        "lunch": None,
        "lunch_time": None,
        "lunch_place": None,
        "lunch_min": None,
        "expected_rain_mm": round(rain_d, 1),
        "rest_day": True,
        "start_time": _fmt_clock(start),
        "end_time": _fmt_clock(start),
        "start_min": start,
        "end_min": start,
        "total_travel_min": 0,
        "total_visit_min": 0,
        "effort_min": 0,
        "effort_cap_min": 0,
        "trek_count": 0,
        "overloaded": False,
        "unverified": False,
    }


def _build_distance_matrix(kept, dest_lat, dest_lng, distance_factory):
    """Distance/time wrapper for hotel + kept stops.

    distance_factory may return a legacy 2D distance matrix or a
    (distances_km, durations_min) tuple. Durations feed the timeline; when
    they are missing every leg falls back to distance / documented speed.
    """
    coords = [(dest_lat, dest_lng)] + [(a["lat"], a["lng"]) for a in kept]
    road = None
    road_minutes = None
    if distance_factory is not None:
        try:
            out = distance_factory(coords)
        except Exception as e:
            log.warning("distance_factory raised %s; using haversine", e)
            out = None
        if isinstance(out, tuple) and len(out) == 2:
            road, road_minutes = out
        else:
            road = out
    return _DistanceMatrix(coords, road, road_minutes)


def _trek_cap(n_days: int, adventure_level: str) -> int:
    """Total treks the trip may contain, from the adventure level.

    low      -> ~1 trek per 3 days (a mostly-relaxed trip)
    balanced -> ~1 trek per 2 days (the mix of experiences default)
    high     -> up to one trek every day (the old ceiling)
    """
    every = _TREK_EVERY_N_DAYS.get(adventure_level, 2)
    return max(1, math.ceil(max(n_days, 1) / every))


def _cap_treks(kept, effort_min, is_trek, dm: _DistanceMatrix, n_days,
               cap=None):
    """Keep at most `cap` treks (default: one per day).

    Trek slots are the scarce resource of a hill trip, so they go to the
    most substantial climbs: ranking is by MEASURED ASCENT first (the one
    number the classifier cannot colour), then by effort, then by name for
    determinism.

    This ordering matters in practice. A model that labels a 136 m tea
    garden walk as a "trek" must never crowd out a 915 m summit, which is
    exactly what happened the first time this ran live.

    Whatever moves out does so LOUDLY: each returns a reason, so the reply
    can explain why a summit is missing instead of losing it silently.
    """
    trek_idx = [i for i in range(len(kept)) if is_trek[i]]
    cap = cap if cap is not None else n_days
    excess = len(trek_idx) - cap
    if excess <= 0:
        return kept, effort_min, is_trek, []

    def substance(i: int) -> tuple:
        ascent = kept[i].get("ascent_m")
        return (
            ascent if isinstance(ascent, (int, float)) else -1,
            effort_min[i],
        )

    ranked = sorted(
        trek_idx,
        key=lambda i: (-substance(i)[0], -substance(i)[1], kept[i]["name"]),
    )
    # Keep the most substantial; the tail of this ranking is what leaves.
    dropped = set(ranked[cap:])

    new_kept = [a for i, a in enumerate(kept) if i not in dropped]
    new_effort = [e for i, e in enumerate(effort_min) if i not in dropped]
    new_trek = [t for i, t in enumerate(is_trek) if i not in dropped]

    reasons = []
    for i in sorted(dropped):
        ascent = kept[i].get("ascent_m")
        climb = (f"+{int(ascent)} m of ascent and {effort_min[i]} min on foot"
                 if isinstance(ascent, (int, float))
                 else f"{effort_min[i]} min on foot")
        reasons.append({
            "name": kept[i]["name"],
            "reason": (
                f"{climb} — the adventure level allows {cap} trek(s) for "
                f"{n_days} day(s) and there are {len(trek_idx)} trek(s). Keeping the "
                f"bigger climbs: {', '.join(kept[j]['name'] for j in ranked[:cap])}"
            ),
        })
    return new_kept, new_effort, new_trek, reasons


def build_itinerary(attrs, food, dest_lat, dest_lng,
                    start_date, n_days, rain_mm, pace="balanced",
                    exclude_names=None,
                    distance_factory=None,
                    activities=None,
                    adventure_level="balanced") -> dict:
    """Build the day-by-day schedule.

    distance_factory: optional callable taking a list of (lat, lng) tuples
        (hotel first, then each kept attraction). Returns either an N×N
        matrix of road distances in km (legacy) or a tuple
        (distances_km, durations_min). With durations, the timeline uses
        real travel times; otherwise haversine × 1.4 at a fallback speed.

    activities: optional {name: Activity} from agent.activities. When it is
        absent, every stop gets a conservative sightseeing default and the
        days are flagged `unverified`: the schedule still builds, but with
        no way to tell a trek from a viewpoint.

    adventure_level: low | balanced | high. Sets how many treks the whole
        trip may contain (see _TREK_EVERY_N_DAYS) — a 'balanced' trip is a
        mix of activities, not a summit every day.
    """
    exclude = {n.lower() for n in (exclude_names or [])}

    # Stage 1 — pool.
    kept, unscheduled = _filter_and_cap(
        attrs, dest_lat, dest_lng, n_days, exclude,
    )
    if not kept or not n_days:
        return {"error": "need attractions and trip dates to schedule"}

    # Activity per stop. Anything the classifier marked "skip" is not a
    # visitor activity at all, so it leaves the pool with a reason.
    meta = activities or {}
    resolved = {
        a["name"]: (meta.get(a["name"]) or _neutral_activity(a["name"]))
        for a in kept
    }
    activities_all_default = all(
        act.source == "default" for act in resolved.values()
    )
    unscheduled += [
        {"name": a["name"],
         "reason": "not a visitor activity — classified as skip"}
        for a in kept if not resolved[a["name"]].is_visit
    ]
    kept = [a for a in kept if resolved[a["name"]].is_visit]
    if not kept:
        return {"error": "nothing left to schedule after activity filtering"}

    effort_min = [resolved[a["name"]].visit_min for a in kept]
    is_trek = [resolved[a["name"]].is_trek for a in kept]
    _, hi_pace = _PACE.get(pace, _PACE["balanced"])

    # Stage 2 — feasibility. H5 caps treks at the adventure level's budget,
    # so trim before solving: the model stays feasible and the reason stays
    # explicit.
    probe_dm = _build_distance_matrix(kept, dest_lat, dest_lng,
                                      distance_factory)
    trek_cap = _trek_cap(n_days, adventure_level)
    kept, effort_min, is_trek, trek_overflow = _cap_treks(
        kept, effort_min, is_trek, probe_dm, n_days, cap=trek_cap,
    )
    unscheduled += trek_overflow
    if not kept:
        return {"error": "no attractions left after effort trimming"}

    dm = _build_distance_matrix(kept, dest_lat, dest_lng, distance_factory)
    acts = [resolved[a["name"]] for a in kept]

    n = len(kept)
    target = math.ceil(n / n_days)
    max_load = max(hi_pace, target + 1)
    if n > max_load * n_days:
        max_load = math.ceil(n / n_days)
    # A single stop must always fit its own day, or arithmetic would drop
    # destinations for no reason a traveller could understand.
    max_effort_min = max(_EFFORT_CAP_MIN.get(pace, 330), max(effort_min))

    # Stage 3 — assignment.
    class_of = [resolved[a["name"]].cls for a in kept]
    if n_days == 1:
        day_members = {0: list(range(n))}
        solver_status = "TRIVIAL"
    else:
        day_members, solver_status = _solve_assignment(
            kept, n_days, target, max_load, rain_mm, dm, effort_min,
            is_trek, max_effort_min, class_of=class_of, trek_cap=trek_cap,
        )

    # Stage 3b — day ordering.
    if n_days > 1 and len(day_members) > 1:
        order = _order_days_geographically(day_members, kept, dm)
        day_members = {
            new: day_members[old] for new, old in enumerate(order)
        }

    # Stages 4, 5 and 6.
    food_pool = [f for f in food if f.get("lat") is not None]
    # A stable copy used when the variety pool runs dry: a destination with
    # one restaurant should still get a meal on every day, not just the
    # first one.
    food_all = list(food_pool)
    days_out = []
    validation = {
        "trek_rule_ok": True,
        "overloaded_days": [],
        "unverified_days": [],
    }

    for d in range(n_days):
        members = day_members.get(d, [])
        date_str = (start_date + timedelta(days=d)).isoformat()
        rain_d = rain_mm[d] if d < len(rain_mm) else 0.0

        if not members:
            days_out.append(_rest_day(date_str, rain_d))
            continue

        tour = _order_stops(members, kept, dm)
        rounds = 0
        stops: list[dict] = []
        totals: dict = {}
        lunch_name = lunch_place = lunch_minutes = used_food = None

        while tour:
            (stops, totals, lunch_name, lunch_place, lunch_minutes,
             used_food) = _build_timeline(
                tour, kept, dm, effort_min, is_trek, acts,
                food_pool or food_all,
            )
            if totals["end_min"] <= _DAY_END_H * 60:
                break
            if len(tour) <= 1 or rounds >= _MAX_REBALANCE_ROUNDS:
                break      # a one-stop day is kept, flagged overloaded
            rounds += 1

            # Rebalance: the stop that pushes the day past the window goes
            # back to the pool, with the arithmetic that condemned it.
            worst = max(stops, key=lambda s: (s["depart_min"], s["name"]))
            tour = [i for i in tour if kept[i]["name"] != worst["name"]]
            unscheduled.append({
                "name": worst["name"],
                "reason": (
                    f"doesn't fit on {date_str}: {worst['activity']} needs "
                    f"{worst['visit_min']} min and the day already ends at "
                    f"{_fmt_clock(totals['end_min'])}"
                ),
            })

        if not stops:
            days_out.append(_rest_day(date_str, rain_d))
            continue

        if used_food is not None and used_food in food_pool:
            food_pool.remove(used_food)

        trek_count = sum(1 for s in stops if s["is_trek"])
        # Per-day activity mix, e.g. ["trek", "viewpoint", "food"] — shown in
        # the UI so the user can see the day is not all one thing.
        seen_mix: list[str] = []
        for s in stops:
            cls = s.get("class") or s.get("activity_cls")
            if cls and cls not in seen_mix:
                seen_mix.append(cls)
        overloaded = totals["end_min"] > _DAY_END_H * 60
        unverified_day = bool(
            activities_all_default
            or any(s["duration_source"] == "default" for s in stops)
        )

        # Stage 6 — validation. H5 should already guarantee this; the
        # re-count is a guard against a future edit that quietly drops the
        # constraint, and it reports rather than trusts.
        if trek_count > 1:
            validation["trek_rule_ok"] = False
        if overloaded:
            validation["overloaded_days"].append(date_str)
        if unverified_day:
            validation["unverified_days"].append(date_str)

        days_out.append({
            "date": date_str,
            "stops": stops,
            "lunch": lunch_name,
            "lunch_time": lunch_place["arrive"] if lunch_place else None,
            "lunch_place": lunch_place,
            "lunch_min": lunch_minutes,
            "expected_rain_mm": round(rain_d, 1),
            "rest_day": False,
            "start_time": _fmt_clock(totals["start_min"]),
            "end_time": _fmt_clock(totals["end_min"]),
            "start_min": totals["start_min"],
            "end_min": totals["end_min"],
            "total_travel_min": totals["total_travel_min"],
            "total_visit_min": totals["total_visit_min"],
            "effort_min": totals["total_visit_min"],
            "effort_cap_min": int(max_effort_min),
            "trek_count": trek_count,
            "class_mix": seen_mix,
            "overloaded": overloaded,
            "unverified": unverified_day,
        })

    return {
        "days": days_out,
        "unscheduled": unscheduled,
        "solver_status": solver_status,
        "activity_source": "default" if activities_all_default else "llm",
        "validation": validation,
        "max_effort_min": int(max_effort_min),
        "adventure_level": adventure_level,
        "trek_cap": trek_cap,
    }
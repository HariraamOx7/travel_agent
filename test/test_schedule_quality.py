"""Scheduler quality regressions: day chaining, cohesion, and hand edits.

Guards for four things a Munnar trip made visible:

  1. Day ordering opened with an arbitrary group — the greedy hotel hop
     computed 0 km for EVERY candidate, so Day 1 could start far from the
     base. The order is now an exact Held–Karp chain over days.
  2. Rain placement from the assignment stage was thrown away when days
     were reordered. The chain DP trades chain distance against rain
     alignment, so a wet date keeps the day that can best afford it.
  3. The 2-opt ignored the return leg (last stop → stay), so a day could
     end on the far side of the valley — the visible "gap" between day N
     and day N+1 on the map. Tours are now closed.
  4. Stops the traveller drags between days must re-time instantly, with
     the same timeline shape the scheduler produces.

All offline: haversine fallback distances, no LLM, no road-matrix API.
"""
from datetime import date

from agent import activities as activities_mod
from agent import scheduler as sm
from agent import tools
from agent.state import Destination, TripState


HOTEL = (10.0, 77.0)


def _attrs(points):
    return [
        {"name": f"P{i}", "lat": lat, "lng": lng, "kind": "viewpoint"}
        for i, (lat, lng) in enumerate(points)
    ]


def _dm(attrs_or_points):
    if attrs_or_points and isinstance(attrs_or_points[0], dict):
        pts = [(a["lat"], a["lng"]) for a in attrs_or_points]
    else:
        pts = list(attrs_or_points)
    return sm._DistanceMatrix([HOTEL] + pts)


def _closed_cost(tour, attrs, dm: sm._DistanceMatrix) -> float:
    """Total km of hotel → tour → hotel."""
    def leg(a, b):
        if a is None:
            return dm.from_hotel(b + 1)
        if b is None:
            return dm.from_hotel(a + 1)
        return dm.between(a + 1, b + 1)

    total = 0.0
    prev = None
    for i in tour:
        total += leg(prev, i)
        prev = i
    total += leg(prev, None)
    return total


# --------------------------------------------------------------------------- #
# Stage 3b — day ordering
# --------------------------------------------------------------------------- #

class TestDayOrdering:
    def test_chain_starts_at_the_day_nearest_the_base(self):
        """Slot 0 holds a far group — the order must open with the near one.

        The old greedy computed hotel→centroid as 0 km for every candidate
        (an `is None` branch that returned 0.0), so it always opened with
        whatever group happened to be index 0.
        """
        attrs = _attrs([
            (12.20, 77.00), (12.21, 77.00),   # group 0 — ~240 km away
            (10.10, 77.00), (10.11, 77.00),   # group 1 — ~11 km away
        ])
        dm = _dm(attrs)
        day_members = {0: [0, 1], 1: [2, 3]}
        order = sm._order_days_geographically(
            day_members, attrs, dm, rain_mm=[0.0, 0.0],
        )
        assert order == [1, 0], "the trip must open with the day nearest the base"

    def test_rain_decides_when_the_chain_is_symmetric(self):
        """Two groups mirror-image around the base: chain cost is equal both
        ways, so the two-outdoor-stop group must stay on the DRY date."""
        attrs = _attrs([
            (9.90, 77.00),                    # group 0 — ONE outdoor stop
            (10.05, 77.00), (10.15, 77.00),   # group 1 — TWO (centroid +0.10)
        ])
        dm = _dm(attrs)
        day_members = {0: [0], 1: [1, 2]}
        # Date 0 is dry, date 1 pours.
        order = sm._order_days_geographically(
            day_members, attrs, dm, rain_mm=[0.0, 30.0],
        )
        # Group 1 (two outdoor stops) must land on position 0 (dry):
        # cost 30×1 = 30 beats 30×2 = 60.
        assert order == [1, 0]

    def test_rest_days_do_not_break_the_chain(self):
        attrs = _attrs([
            (10.10, 77.00), (10.11, 77.00),
            (10.20, 77.00), (10.21, 77.00),
        ])
        dm = _dm(attrs)
        day_members = {0: [0, 1], 1: [], 2: [2, 3]}
        order = sm._order_days_geographically(
            day_members, attrs, dm, rain_mm=[0.0, 0.0, 0.0],
        )
        assert sorted(order) == [0, 1, 2]
        # An empty day sits AT the hotel, so the cheapest chain parks it
        # first (zero opening hop) and walks the two real groups in order.
        assert order == [1, 0, 2]


# --------------------------------------------------------------------------- #
# Stage 4 — sequencing (closed tours)
# --------------------------------------------------------------------------- #

class TestSequencing:
    def test_the_way_home_is_costed(self):
        """2-opt must beat the open-tour NN seed once the return leg counts.

        Geometry: base at HOTEL; A just north (~0.44 km), C east (~3 km),
        B far north (~5 km). Nearest-neighbour walks A → C → B and USED to
        stop there — B is 5 km from the base. With the return leg in the
        objective, 2-opt flips the tail to A → B → C so the day finishes
        3 km from home instead of 5.
        """
        attrs = _attrs([
            (10.004, 77.000),   # 0 = A, nearest the base
            (10.045, 77.000),   # 1 = B, farthest
            (10.000, 77.027),   # 2 = C, east
        ])
        dm = _dm(attrs)

        tour = sm._order_stops([0, 1, 2], attrs, dm)
        assert tour == [0, 1, 2], "the day should end near the base (at C)"

        nn_seed = [0, 2, 1]                       # nearest-neighbour order
        new_cost = _closed_cost(tour, attrs, dm)
        old_cost = _closed_cost(nn_seed, attrs, dm)
        assert new_cost < old_cost - 1e-6, (new_cost, old_cost)

    def test_two_stops_start_with_the_nearer_one(self):
        attrs = _attrs([
            (10.045, 77.000),   # far
            (10.004, 77.000),   # near
        ])
        dm = _dm(attrs)
        assert sm._order_stops([0, 1], attrs, dm) == [1, 0]


# --------------------------------------------------------------------------- #
# Stage 3 — cohesion
# --------------------------------------------------------------------------- #

def _act(name, cls="sightsee", visit=60, trek=False):
    return activities_mod.Activity(
        name=name, activity=f"Visit {name}", cls=cls, intensity=2,
        visit_min=visit, source="test", is_trek=trek,
    )


class TestCohesion:
    def test_tight_pairs_share_a_day(self):
        """Two tight POI clusters 8 km apart: each day holds one cluster."""
        pool = [
            {"name": "T1", "kind": "viewpoint", "lat": 10.000, "lng": 77.000},
            {"name": "T2", "kind": "viewpoint", "lat": 10.005, "lng": 77.000},
            {"name": "U1", "kind": "viewpoint", "lat": 10.000, "lng": 77.072},
            {"name": "U2", "kind": "viewpoint", "lat": 10.005, "lng": 77.072},
        ]
        acts = {a["name"]: _act(a["name"]) for a in pool}
        res = sm.build_itinerary(
            pool, [], 9.99, 76.99, date(2026, 9, 22), 2, [0.0, 0.0],
            "balanced", activities=acts,
        )
        groups = [{s["name"] for s in day["stops"]} for day in res["days"]]
        assert {"T1", "T2"} in groups, groups
        assert {"U1", "U2"} in groups, groups


# --------------------------------------------------------------------------- #
# retime_day — the clock rebuild behind drag-and-drop
# --------------------------------------------------------------------------- #

def _mini_build(stay=None):
    pool = [
        {"name": "S1", "kind": "viewpoint", "lat": 10.010, "lng": 77.000},
        {"name": "S2", "kind": "viewpoint", "lat": 10.020, "lng": 77.000},
        {"name": "S3", "kind": "garden", "lat": 10.030, "lng": 77.010},
        {"name": "S4", "kind": "museum", "lat": 10.040, "lng": 77.010},
    ]
    acts = {a["name"]: _act(a["name"]) for a in pool}
    food = [{"name": "Saravana Bhavan", "lat": 10.015, "lng": 77.005}]
    res = sm.build_itinerary(
        pool, food, 9.99, 76.99, date(2026, 9, 22), 2, [0.0, 0.0],
        "balanced", activities=acts, stay=stay,
    )
    coords = {a["name"]: (a["lat"], a["lng"]) for a in pool}
    return pool, food, res, coords


class TestRetimeDay:
    def test_rebuilt_day_keeps_clock_shape(self):
        _, food, res, coords = _mini_build()
        day = dict(res["days"][0])
        hotel = (9.99, 76.99)
        out = sm.retime_day(day, hotel, coords, list(food))

        assert out["rest_day"] is False
        assert out["start_min"] < out["end_min"]
        assert out["effort_min"] == sum(s["visit_min"] for s in out["stops"])
        # Monotone clock: depart(i) ≤ arrive(i+1).
        for a, b in zip(out["stops"], out["stops"][1:]):
            assert a["depart_min"] <= b["arrive_min"]
        # Lunch is slotted when food is available.
        assert out["lunch"] is not None

    def test_emptied_day_becomes_a_rest_day(self):
        _, food, res, coords = _mini_build()
        day = dict(res["days"][0])
        day["stops"] = []
        out = sm.retime_day(day, (9.99, 76.99), coords, list(food))
        assert out["rest_day"] is True
        assert out["stops"] == []
        assert out["date"] == day["date"]


# --------------------------------------------------------------------------- #
# Breakfast & dinner
# --------------------------------------------------------------------------- #

class TestMeals:
    def test_every_day_carries_breakfast_and_dinner(self):
        """No meal plan → a real venue near the day, at sane times."""
        _, food, res, _ = _mini_build()
        names = {f["name"] for f in food}
        for day in res["days"]:
            b, d = day["breakfast"], day["dinner"]
            assert b and d, day["date"]
            # Breakfast is finished before the day opens...
            assert b["depart_min"] <= day["start_min"]
            # ...dinner is the evening meal back at the base.
            assert d["arrive_min"] == 19 * 60 + 30
            assert b["kind"] == "restaurant" and not b["included"]
            assert b["name"] in names and d["name"] in names

    def test_half_board_eats_at_the_stay(self):
        _, _, res, _ = _mini_build(stay={
            "name": "Cloud five",
            "meal_plan": "Half board (breakfast + dinner)",
        })
        for day in res["days"]:
            for meal in (day["breakfast"], day["dinner"]):
                assert meal["included"] is True
                assert meal["kind"] == "stay"
                assert meal["name"] == "Cloud five"

    def test_breakfast_only_plan_still_suggests_dinner(self):
        _, _, res, _ = _mini_build(stay={
            "name": "Kaippallil Homestay",
            "meal_plan": "Breakfast included",
        })
        day = res["days"][0]
        assert day["breakfast"]["included"] is True
        assert day["dinner"]["included"] is False
        assert day["dinner"]["kind"] == "restaurant"

    def test_unspecified_meal_plan_never_claims_inclusion(self):
        _, _, res, _ = _mini_build(stay={
            "name": "Tea Country",
            "meal_plan": "Meal plan not specified",
        })
        for day in res["days"]:
            assert day["breakfast"]["included"] is False
            assert day["dinner"]["included"] is False

    def test_rest_day_keeps_both_meals_near_the_stay(self):
        _, food, res, coords = _mini_build()
        day = dict(res["days"][0])
        day["stops"] = []
        out = sm.retime_day(day, (9.99, 76.99), coords, list(food))
        assert out["rest_day"] is True
        assert out["breakfast"] and out["dinner"]
        # No first stop: both anchor at the accommodation.
        assert "stay" in out["breakfast"]["context"]


def test_move_heals_days_missing_meals():
    """Sessions built before meals existed gain them on the first edit."""
    state = TestMoveStop()._state()
    for day in state.itinerary["days"]:
        day.pop("breakfast", None)
        day.pop("dinner", None)
    name = state.itinerary["days"][0]["stops"][0]["name"]
    out = tools.move_stop(
        {"stop_name": name, "from_day": 0, "to_day": 1}, state,
    )
    assert "error" not in out, out
    for day in state.itinerary["days"]:
        assert day.get("breakfast") and day.get("dinner"), day["date"]


# --------------------------------------------------------------------------- #
# tools.move_stop — the drag-and-drop endpoint's engine
# --------------------------------------------------------------------------- #

class TestMoveStop:
    def _state(self):
        pool, food, res, _ = _mini_build()
        state = TripState(
            origin="Bengaluru",
            destinations=[Destination(name="Munnar", lat=9.99, lng=76.99)],
            start_date=date(2026, 9, 22),
            end_date=date(2026, 9, 23),
        )
        state.recommendations = {"attractions": pool, "food": food}
        state.itinerary = res
        return state

    def test_move_between_days_retimes_both(self):
        state = self._state()
        src_name = state.itinerary["days"][0]["stops"][0]["name"]
        before_d0 = len(state.itinerary["days"][0]["stops"])
        before_d1 = len(state.itinerary["days"][1]["stops"])
        d1_end_before = state.itinerary["days"][1]["end_min"]

        out = tools.move_stop(
            {"stop_name": src_name, "from_day": 0, "to_day": 1, "to_index": 0},
            state,
        )
        assert "error" not in out, out

        d0, d1 = state.itinerary["days"]
        assert len(d0["stops"]) == before_d0 - 1
        assert len(d1["stops"]) == before_d1 + 1
        assert d1["stops"][0]["name"] == src_name
        # The receiving day re-times: its clock must still be sane.
        assert d1["start_min"] < d1["end_min"]
        for a, b in zip(d1["stops"], d1["stops"][1:]):
            assert a["depart_min"] <= b["arrive_min"]
        # Flags recompute, not rot.
        assert state.itinerary["validation"]["trek_rule_ok"] is True
        assert isinstance(state.itinerary["validation"]["overloaded_days"], list)

    def test_moving_the_only_stop_leaves_a_rest_day(self):
        state = self._state()
        before_d1 = len(state.itinerary["days"][1]["stops"])
        # Force day 0 down to a single stop.
        day0 = state.itinerary["days"][0]
        day0["stops"] = day0["stops"][:1]
        name = day0["stops"][0]["name"]

        out = tools.move_stop(
            {"stop_name": name, "from_day": 0, "to_day": 1}, state,
        )
        assert "error" not in out, out
        assert state.itinerary["days"][0]["rest_day"] is True
        assert state.itinerary["days"][0]["stops"] == []
        assert len(state.itinerary["days"][1]["stops"]) == before_d1 + 1

    def test_unknown_stop_is_rejected_without_mutation(self):
        state = self._state()
        snapshot = [list(d["stops"]) for d in state.itinerary["days"]]
        out = tools.move_stop(
            {"stop_name": "Nowhere", "from_day": 0, "to_day": 1}, state,
        )
        assert "error" in out
        now = [list(d["stops"]) for d in state.itinerary["days"]]
        assert now == snapshot

    def test_day_out_of_range_is_rejected(self):
        state = self._state()
        out = tools.move_stop(
            {"stop_name": "S1", "from_day": 0, "to_day": 9}, state,
        )
        assert "error" in out

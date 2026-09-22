"""Regression tests for the multi-destination, ideas-edit, and
category-discovery features.

All are offline: POI search, weather, LLM classification, and road
matrices are stubbed so the tests assert ORCHESTRATION behavior (day
split, transfers, exclusion round-trips, category routing), not network.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import tools as tools_mod
from agent.state import Destination, TripState


# --------------------------------------------------------------------------- #
# Day split
# --------------------------------------------------------------------------- #

class TestSplitDays:
    def test_sums_exactly_to_trip_length(self):
        assert sum(tools_mod._split_days(5, [10, 5, 5])) == 5
        assert sum(tools_mod._split_days(3, [20, 4])) == 3

    def test_every_destination_gets_at_least_one_day(self):
        alloc = tools_mod._split_days(3, [50, 1, 1])
        assert all(a >= 1 for a in alloc)
        assert sum(alloc) == 3

    def test_weighted_allocation_favors_bigger_pools(self):
        alloc = tools_mod._split_days(6, [10, 2])
        assert alloc[0] > alloc[1]


# --------------------------------------------------------------------------- #
# Multi-destination build
# --------------------------------------------------------------------------- #

def _mk_poi(prefix, lat, lng, n):
    return [{"name": f"{prefix} {i}", "lat": lat + i * 0.01,
             "lng": lng + i * 0.01, "kind": "viewpoint"}
            for i in range(n)]


class _FakePois:
    @staticmethod
    def search_pois(lat, lng):
        if abs(lat - 10.0) < 0.1:
            return {"attraction": _mk_poi("MunnarView", 10.0, 77.0, 9),
                    "food": [], "stay": []}
        return {"attraction": _mk_poi("ThekkadyPark", 9.6, 77.15, 4),
                "food": [], "stay": []}


class _FakeWeather:
    @staticmethod
    def trip_weather(lat, lng, start, end):
        days, d = [], start
        while d <= end:
            days.append({"date": d.isoformat(), "precip_mm": 0.0})
            d = date.fromordinal(d.toordinal() + 1)
        return {"mode": "fake", "days": days}


@pytest.fixture()
def multi_env(monkeypatch):
    """Stub POIs + weather; capture per-destination scheduler calls."""
    captured = []

    real_build = tools_mod.scheduler_mod.build_itinerary

    def spy_build(attrs, food, dest_lat, dest_lng, start_date, n_days, rain,
                  pace, **kw):
        captured.append((dest_lat, dest_lng, start_date, n_days))
        from agent.activities import Activity
        kw["activities"] = {
            a["name"]: Activity(name=a["name"], activity="Viewpoint stop",
                                cls="sightsee", intensity=2, visit_min=90,
                                source="test", duration_source="test")
            for a in attrs
        }
        return real_build(attrs, food, dest_lat, dest_lng, start_date,
                          n_days, rain, pace, **kw)

    monkeypatch.setattr(tools_mod, "pois_mod", _FakePois)
    monkeypatch.setattr(tools_mod, "weather", _FakeWeather)
    monkeypatch.setattr(tools_mod.scheduler_mod, "build_itinerary", spy_build)

    def make_state(n_days=3):
        s = TripState(
            origin="Chennai",
            destinations=[Destination(name="Munnar", lat=10.0, lng=77.0),
                          Destination(name="Thekkady", lat=9.6, lng=77.15)],
            start_date=date(2026, 10, 1),
            end_date=date.fromordinal(
                date(2026, 10, 1).toordinal() + n_days - 1),
            budget_total=30000,
        )
        return s

    return {"make_state": make_state, "captured": captured}


class TestMultiDestination:
    def test_days_split_and_stitched_in_date_order(self, multi_env):
        state = multi_env["make_state"](3)
        out = tools_mod._build_multi_destination(state, [])
        assert "error" not in out
        days = state.itinerary["days"]
        assert len(days) == 3
        dates = [d["date"] for d in days]
        assert dates == sorted(dates)
        dest_seq = [d["destination"] for d in days]
        assert dest_seq.count("Munnar") == 2
        assert dest_seq.count("Thekkady") == 1
        # Munnar's days come first (bigger pool), then Thekkady.
        assert dest_seq == ["Munnar", "Munnar", "Thekkady"]

    def test_transfer_leg_on_first_day_of_second_destination(self, multi_env):
        state = multi_env["make_state"](3)
        out = tools_mod._build_multi_destination(state, [])
        days = state.itinerary["days"]
        tk = [d for d in days if d["destination"] == "Thekkady"]
        assert tk and tk[0].get("transfer_in")
        t = tk[0]["transfer_in"]
        assert t["from"] == "Munnar" and t["to"] == "Thekkady"
        assert t["km"] > 0

    def test_each_destination_scheduled_with_own_anchor(self, multi_env):
        state = multi_env["make_state"](3)
        tools_mod._build_multi_destination(state, [])
        captured = multi_env["captured"]
        assert len(captured) == 2
        assert captured[0][:2] == (10.0, 77.0)
        assert captured[1][:2] == (9.6, 77.15)
        assert captured[0][2] == date(2026, 10, 1)
        assert captured[0][3] == 2
        assert captured[1][2] == date(2026, 10, 3)
        assert captured[1][3] == 1

    def test_payload_contract(self, multi_env):
        state = multi_env["make_state"](3)
        out = tools_mod._build_multi_destination(state, [])
        assert out["multi_destination"] is True
        assert out["allocation"] == [
            {"destination": "Munnar", "days": 2},
            {"destination": "Thekkady", "days": 1},
        ]
        assert out["schedule"][2]["transfer_in"]["km"] > 0
        assert state.itinerary["multi_destination"] is True
        assert state.stage == "scheduling"

    def test_guard_fewer_days_than_destinations(self, multi_env):
        state = multi_env["make_state"](1)
        state.destinations = state.destinations + [
            Destination(name="Kumily", lat=9.55, lng=77.10)]
        err = tools_mod._build_multi_destination(state, [])
        assert "error" in err and "at least" in err["error"]

    def test_single_destination_takes_normal_path(self, multi_env):
        """build_itinerary only routes to the multi path for >1 geocoded
        destination; a single-destination state keeps the legacy flow."""
        state = TripState(
            destinations=[Destination(name="Munnar", lat=10.0, lng=77.0)],
            start_date=date(2026, 10, 1), end_date=date(2026, 10, 2))
        rec = {"attractions": [{"name": "A", "lat": 10.0, "lng": 77.0}],
               "food": [], "weather": {"days": [
                   {"date": (date(2026, 10, 1)
                             if i == 0 else date(2026, 10, 2)).isoformat(),
                    "precip_mm": 0} for i in range(2)]}}
        state.recommendations = rec
        err = tools_mod.build_itinerary({}, state)
        # Falls through to the legacy path (which errors on missing pool
        # richness here) — the point is it did NOT enter the multi branch.
        assert "multi_destination" not in (err or {})


# --------------------------------------------------------------------------- #
# Category-aware discovery
# --------------------------------------------------------------------------- #

class TestCategoryRouting:
    def _route(self, monkeypatch, hints):
        import agent.discovery as D

        def fail_overpass(lat, lng, radius):
            return {"error": "stubbed"}

        monkeypatch.setattr(D, "_discover_via_overpass", fail_overpass)
        monkeypatch.setattr(D, "_discover_via_geonames_tiled", fail_overpass)
        res = D.discover_nearby(13.08, 80.27, radius_km=500,
                                query_hints=hints)
        return res

    def test_hill_hint_uses_curated_path(self, monkeypatch):
        res = self._route(monkeypatch, ["hill", "station"])
        assert res["sources"] == ["curated_destinations_db"]
        names = [c["name"] for c in res["candidates"]]
        assert any("Yelagiri" in n for n in names)
        assert any("Yercaud" in n for n in names)

    def test_beach_hint_uses_curated_path(self, monkeypatch):
        res = self._route(monkeypatch, ["beach"])
        assert res["category"] == "beach"
        assert res["candidates"]

    def test_temple_and_wildlife_hints(self, monkeypatch):
        assert self._route(monkeypatch, ["temple", "town"])["category"] \
            == "temple"
        assert self._route(monkeypatch, ["wildlife", "safari"])["category"] \
            == "wildlife"

    def test_uncategorized_falls_through_to_overpass(self, monkeypatch):
        res = self._route(monkeypatch, ["somewhere", "nice"])
        assert "error" in res          # both live sources stubbed out

    def test_nearby_search_categories_and_radius(self):
        from agent.nearby_search import find_nearby_destinations
        hills = find_nearby_destinations(13.08, 80.27, "hill_station", 400)
        assert hills and hills[0]["distance_km"] <= hills[-1]["distance_km"]
        beaches = find_nearby_destinations(12.97, 77.59, "beach", 700)
        assert any("Gokarna" in x["name"] for x in beaches)
        # Category filter actually filters.
        for entry in hills:
            assert "hill_station" in entry["categories"]

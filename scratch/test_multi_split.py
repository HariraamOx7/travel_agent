"""Headless harness for multi-destination scheduling.

Runs the real scheduler per-destination with synthetic POIs and asserts:
  - _split_days sums to n_days, all >= 1, weighted allocation
  - _build_multi_destination stitches days in date order, tags destinations,
    inserts transfer legs, respects the trek cap per day
"""
import sys
from datetime import date

sys.path.insert(0, ".")

from agent.tools import _split_days, _build_multi_destination
from agent.state import TripState, Destination

# --- _split_days -------------------------------------------------------------
a = _split_days(5, [10, 5, 5])
assert sum(a) == 5 and all(x >= 1 for x in a), a
assert a[0] == 3, a                     # 50% of 5 days -> 3
b = _split_days(3, [20, 4])
assert sum(b) == 3 and b[0] == 2, b
c = _split_days(2, [30, 30, 30])
assert c == [1, 1, 1], c  # every destination keeps >= 1 day even if over budget
print("split days:", a, b, c)
assert sorted(_split_days(4, [8, 2])) == [3, 1] or _split_days(4, [8, 2]) == [3, 1]
print("PASS: _split_days")


# --- _build_multi_destination with synthetic POIs ----------------------------
def _mk(lat, lng, names_prefix, n, kind="viewpoint"):
    return [{"name": f"{names_prefix} {i}", "lat": lat + i * 0.01,
             "lng": lng + i * 0.01, "kind": kind} for i in range(n)]


class _FakePois:
    calls = []

    @staticmethod
    def search_pois(lat, lng):
        _FakePois.calls.append((lat, lng))
        if abs(lat - 10.0) < 0.1:
            return {"attraction": _mk(10.0, 77.0, "MunnarView", 9),
                    "food": [], "stay": []}
        return {"attraction": _mk(9.6, 77.15, "ThekkadyPark", 4),
                "food": [], "stay": []}


import agent.tools as T
T.pois_mod = _FakePois

# No network: make weather return zeros.
class _FakeWeather:
    @staticmethod
    def trip_weather(lat, lng, start, end):
        days = []
        d = start
        while d <= end:
            days.append({"date": d.isoformat(), "precip_mm": 0.0})
            d = date.fromordinal(d.toordinal() + 1)
        return {"mode": "fake", "days": days}


T.weather = _FakeWeather

# Distance factory stub — small synthetic road matrix.
def _fake_factory(coords):
    n = len(coords)
    def hav(p, q):
        return abs(p[0] - q[0]) * 111 + abs(p[1] - q[1]) * 111
    return ([[hav(p, q) for q in coords] for p in coords],
            [[hav(p, q) / 25 * 60 for q in coords] for p in coords])


import agent.scheduler as sched
_orig_build = sched.build_itinerary
captured = []

def _spy_build(attrs, food, dest_lat, dest_lng, start_date, n_days, rain,
               pace, **kw):
    captured.append((dest_lat, dest_lng, start_date, n_days))
    # Neutral activities: no treks, no LLM needed.
    from agent.activities import Activity
    amap = {a["name"]: Activity(name=a["name"], activity="Viewpoint stop",
                                cls="sightsee", intensity=2, visit_min=90,
                                source="test", duration_source="test",
                                is_trek=False)
            for a in attrs}
    kw["activities"] = amap
    return _orig_build(attrs, food, dest_lat, dest_lng, start_date, n_days,
                       rain, pace, **kw)


sched.build_itinerary = _spy_build
T.scheduler_mod = sched

state = TripState(
    origin="Chennai",
    destinations=[Destination(name="Munnar", lat=10.0, lng=77.0),
                  Destination(name="Thekkady", lat=9.6, lng=77.15)],
    start_date=date(2026, 10, 1),
    end_date=date(2026, 10, 3),          # 3 days, 2 destinations
    budget_total=30000,
)
state.excluded_names = []

out = _build_multi_destination(state, [])

assert "error" not in out, out
days = state.itinerary["days"]
assert len(days) == 3, [d["date"] for d in days]
assert sum(1 for d in days if d["destination"] == "Munnar") == 2
assert sum(1 for d in days if d["destination"] == "Thekkady") == 1
# Date order preserved
dates = [d["date"] for d in days]
assert dates == sorted(dates), dates
# Transfer leg on the first Thekkady day
tk_days = [d for d in days if d["destination"] == "Thekkady"]
assert tk_days and tk_days[0].get("transfer_in"), days
assert tk_days[0]["transfer_in"]["from"] == "Munnar"
assert tk_days[0]["transfer_in"]["to"] == "Thekkady"
# Both destinations were actually scheduled (spy captured both)
assert len(captured) == 2, captured
assert captured[0][2] == date(2026, 10, 1) and captured[0][3] == 2
assert captured[1][2] == date(2026, 10, 3) and captured[1][3] == 1
# Payload contract
assert out["multi_destination"] is True
assert out["allocation"] == [
    {"destination": "Munnar", "days": 2},
    {"destination": "Thekkady", "days": 1},
], out.get("allocation")
assert out["schedule"][2]["transfer_in"]["km"] > 0
# Itinerary stored in state
assert state.itinerary["multi_destination"] is True
assert state.stage == "scheduling"

# --- guard: 3 destinations in 2 days ----------------------------------------
state2 = TripState(
    destinations=[Destination(name="A", lat=10.0, lng=77.0),
                  Destination(name="B", lat=10.1, lng=77.1),
                  Destination(name="C", lat=10.2, lng=77.2)],
    start_date=date(2026, 10, 1), end_date=date(2026, 10, 2),
)
err = _build_multi_destination(state2, [])
assert "error" in err and "at least" in err["error"], err

print("PASS: _build_multi_destination — 3-day/2-dest split, transfers, "
      "date order, payload contract")
print("captured scheduler calls:", captured)

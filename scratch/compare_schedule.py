"""Old-vs-new scheduler comparison on the user's REAL Munnar session.

Metrics that matter for the report:
  - day memberships (does the near day3/day4 pair merge?)
  - day-boundary gaps: last stop of day k -> first stop of day k+1
  - where each day's tour ends relative to the base (closed tours)
"""
import importlib.util
import sys
from datetime import timedelta

sys.path.insert(0, ".")

from agent.db import load_session
from agent import activities as activities_mod
from agent import scheduler as new_sched

# Load the committed (old) scheduler for comparison.
spec = importlib.util.spec_from_file_location("_old_sched", "scratch/_old_old.py")


def load_old(path):
    spec = importlib.util.spec_from_file_location("_old_sched", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import subprocess

with open("scratch/_old.py", "w", encoding="utf-8") as f:
    f.write(subprocess.check_output(
        ["git", "show", "HEAD:agent/scheduler.py"], text=True))
old_sched = load_old("scratch/_old.py")

st, _, _ = load_session("337f7b27")
rec = st.recommendations
attrs = rec["attractions"]
food = rec.get("food") or []
dest = st.destinations[0]

rain_by_date = {w["date"]: w.get("precip_mm") or 0
                for w in (rec.get("weather") or {}).get("days", [])}
rain = [rain_by_date.get(
    (st.start_date + timedelta(days=i)).isoformat(), 0.0)
    for i in range(st.n_days)]
print("rain:", rain)

pool_items = [{"name": a["name"], "kind": a.get("kind"),
               "elevation_m": a.get("elevation_m"),
               "ascent_m": a.get("ascent_m")} for a in attrs]
acts = activities_mod.classify_pool(pool_items, pace=st.pace)

coords = {a["name"]: (a["lat"], a["lng"]) for a in attrs
          if a.get("lat") is not None}


def offline_matrix(coords_list):
    n = len(coords_list)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            km = new_sched._haversine(*coords_list[i], *coords_list[j]) * 1.4
            dist[i][j] = round(km, 2)
            dur[i][j] = round(km / 30.0 * 60.0, 2)
    return dist, dur


def haversine(a, b):
    return new_sched._haversine(a[0], a[1], b[0], b[1]) * 1.4


def report(tag, res):
    print(f"\n=== {tag} === status={res.get('solver_status')}")
    prev_last = None
    gaps = []
    for i, day in enumerate(res["days"]):
        names = [s["name"] for s in day["stops"]]
        print(f"  Day {i+1} {day['date']} ({len(names)}): {names}")
        if not names:
            continue
        first = coords[names[0]]
        last = coords[names[-1]]
        if prev_last is not None:
            gap = haversine(prev_last, first)
            gaps.append(gap)
        prev_last = last
    if gaps:
        print(f"  boundary gaps km: {[round(g,1) for g in gaps]}"
              f"  total={sum(gaps):.1f}  max={max(gaps):.1f}")


common = dict(
    activities=acts,
    distance_factory=offline_matrix,
    adventure_level=getattr(st, "adventure_level", "balanced"),
)

old_res = old_sched.build_itinerary(
    attrs, food, dest.lat, dest.lng, st.start_date, st.n_days, rain,
    st.pace, **common)
new_res = new_sched.build_itinerary(
    attrs, food, dest.lat, dest.lng, st.start_date, st.n_days, rain,
    st.pace, **common)

report("OLD", old_res)
report("NEW", new_res)

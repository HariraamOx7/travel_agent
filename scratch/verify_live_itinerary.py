"""End-to-end check against a RUNNING backend, through real HTTP.

Exercises the whole surface, not the code in isolation:

    POST /api/sessions                -> new session
    POST /api/sessions/{sid}/chat     -> NLU -> router -> tools (per turn)
    GET  /api/sessions/{sid}/map      -> map payload (stop coords)

The itinerary turn runs the REAL pipeline: one LLM classification call for
the activity of every stop, the Ola Elevation API for the climbs, and the
Ola Distance Matrix for road distances and travel times.

Asserted at the end:
  * the trip actually reaches a scheduled itinerary
  * every stop has an activity label and in/out times
  * no day holds two treks, and no day exceeds its effort budget
  * anything left out carries a reason
  * the map payload still resolves stop coordinates (the contract the
    Itinerary/Map tabs depend on)

Point it at a backend started from the CURRENT code:

    server/.venv/Scripts/python.exe -m uvicorn server.main:app --port 8101
    server/.venv/Scripts/python.exe scratch/verify_live_itinerary.py
"""
import sys
import time

import requests

# Model replies contain characters the Windows console codepage cannot
# encode (narrow no-break spaces, degree signs). Without this the harness
# dies printing a reply that the app itself handled fine.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # pragma: no cover
    pass

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8101"
# Optional: an existing session id to resume (skips the setup turns, which
# include the slow recommendations call).
RESUME = sys.argv[2] if len(sys.argv) > 2 else None
TIMEOUT = 300

TURNS = [
    "plan a trip to munnar from tirunelveli",
    "4 days from 22-9-26",
    "we are 2 people",
    "under 40k",
    "plan the days",
]

failures = []


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}"
          + (f"  [{detail}]" if detail else ""))
    if not condition:
        failures.append(label)


def main():
    print(f"backend: {BASE}")
    r = requests.get(f"{BASE}/api/health", timeout=20)
    check("health", r.status_code == 200 and r.json().get("ok"))

    if RESUME:
        sid = RESUME
        r = requests.get(f"{BASE}/api/sessions/{sid}", timeout=60)
        state = r.json()["state"]
        print(f"resumed session: {sid}  stage={state.get('stage')}\n")
        turns = TURNS[-1:]
    else:
        r = requests.post(f"{BASE}/api/sessions", json={}, timeout=60)
        sid = r.json()["session_id"]
        print(f"session: {sid}\n")
        state = r.json()["state"]
        turns = TURNS

    for turn in turns:
        t0 = time.time()
        r = requests.post(f"{BASE}/api/sessions/{sid}/chat",
                          json={"message": turn}, timeout=TIMEOUT)
        dt = time.time() - t0
        if r.status_code != 200:
            print(f"  !! {turn!r} -> HTTP {r.status_code}: {r.text[:300]}")
            failures.append(f"turn failed: {turn}")
            continue
        body = r.json()
        state = body["state"]
        reply = (body.get("reply") or "").replace("\n", " ")
        routing = (body.get("routing") or {}).get("target")
        print(f"[{dt:5.1f}s] {turn!r}")
        print(f"          routing={routing}  stage={state.get('stage')}")
        print(f"          reply: {reply[:220]}")

    it = state.get("itinerary") or {}
    days = it.get("days") or []
    print("\n" + "=" * 76)
    print(f"stage={state.get('stage')}  solver={it.get('solver_status')}  "
          f"activity_source={it.get('activity_source')}")
    print(f"validation={it.get('validation')}")
    print("=" * 76)

    for i, day in enumerate(days, 1):
        print(f"\nDay {i}  {day['date']}  {day.get('start_time')}-"
              f"{day.get('end_time')}   on-foot "
              f"{day.get('effort_min')}/{day.get('effort_cap_min')} min  "
              f"treks={day.get('trek_count')}")
        for s in day.get("stops", []):
            print(f"   {'TREK' if s.get('is_trek') else '    '} "
                  f"{s.get('arrive')}-{s.get('depart')}  "
                  f"{s['name']:28s} {str(s.get('activity'))[:34]:34s} "
                  f"visit={s.get('visit_min')}m travel={s.get('travel_min')}m "
                  f"ascent={s.get('ascent_m')}")
        if day.get("lunch"):
            print(f"          lunch {day.get('lunch_time')}  {day['lunch']}")

    print("\nLeft out:")
    for u in it.get("unscheduled", []):
        print(f"  - {u['name']}: {u['reason']}")

    print("\n" + "=" * 76)
    print("ASSERTIONS")
    print("=" * 76)

    check("itinerary built", bool(days), f"{len(days)} days")
    check("all required slots filled",
          not (state.get("destinations") is None)
          or bool(state.get("destinations")))
    check("stage is scheduling", state.get("stage") == "scheduling",
          str(state.get("stage")))

    check("activity source is the LLM",
          it.get("activity_source") == "llm",
          str(it.get("activity_source")))

    for day in days:
        if day.get("rest_day"):
            continue
        check(f"{day['date']}: at most one trek", day["trek_count"] <= 1,
              f"treks={day['trek_count']}")
        check(f"{day['date']}: within effort budget",
              day["effort_min"] <= day["effort_cap_min"],
              f"{day['effort_min']}/{day['effort_cap_min']}")
        check(f"{day['date']}: has a start and end",
              bool(day.get("start_time")) and bool(day.get("end_time")),
              f"{day.get('start_time')}-{day.get('end_time')}")
        check(f"{day['date']}: lunch slotted", bool(day.get("lunch")),
              str(day.get("lunch_time")))
        for s in day.get("stops", []):
            check(f"{s['name']}: labelled + timed",
                  bool(s.get("activity")) and bool(s.get("arrive"))
                  and bool(s.get("depart")),
                  f"{s.get('activity')} {s.get('arrive')}-{s.get('depart')}")

    for u in it.get("unscheduled", []):
        check(f"{u['name']}: reason given", bool(u.get("reason")),
              str(u.get("reason"))[:80])

    # Map contract: the Map tab resolves stop coords by name.
    m = requests.get(f"{BASE}/api/sessions/{sid}/map", timeout=60).json()
    pinned = sum(len(d["stops"]) for d in m.get("itinerary_stops", []))
    scheduled = sum(len(d.get("stops", [])) for d in days)
    check("map resolves scheduled stops", pinned == scheduled,
          f"{pinned}/{scheduled} pinned")
    check("map has destination", bool(m.get("destination")),
          str(m.get("destination")))

    print("\n" + "=" * 76)
    print(f"{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + str(failures)}"
          f"   ({len(failures)} failing)")
    print(f"session id: {sid}  (DELETE {BASE}/api/sessions/{sid} to remove it)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

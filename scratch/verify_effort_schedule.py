"""Behavioural harness for the effort-aware scheduler.

Runs the REAL Munnar pool (11 attractions, 5 summits, real Ola elevations)
through the real scheduler offline and prints the resulting day-by-day plan,
then asserts the properties the change exists for:

  1. no day holds two treks
  2. no day exceeds its effort budget
  3. every stop has an in and an out time, monotonic through the day
  4. lunch lands at midday
  5. days stay inside the window, or are flagged overloaded
  6. the same input twice gives byte-identical output

It reuses the fixtures in test/test_pipeline.py (loaded by path, since
`test/` is not a package) so the harness and the suite can never disagree
about what "the Munnar pool" means.

Run:  server/.venv/Scripts/python.exe scratch/verify_effort_schedule.py
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_tests():
    spec = importlib.util.spec_from_file_location(
        "_pipeline", ROOT / "test" / "test_pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def main():
    t = _load_tests()
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}"
              + (f"  [{detail}]" if detail else ""))
        if not condition:
            failures.append(label)

    activities = t._munnar_activities()
    t0 = time.time()
    res = t._run_munnar(activities=activities)
    elapsed = time.time() - t0

    print("=" * 78)
    print(f"SOLVER={res['solver_status']}  build={elapsed:.2f}s  "
          f"activity_source={res['activity_source']}")
    print(f"validation={res['validation']}")
    print("=" * 78)

    for i, day in enumerate(res["days"], 1):
        print(f"\nDay {i}  {day['date']}  {day['start_time']}-{day['end_time']}"
              f"   on-foot {day['effort_min']}/{day['effort_cap_min']} min"
              f"   travel {day['total_travel_min']} min"
              f"   treks {day['trek_count']}"
              + ("   [LONG DAY]" if day["overloaded"] else "")
              + ("   [UNVERIFIED]" if day["unverified"] else ""))
        for s in day["stops"]:
            print(f"   {'TREK' if s['is_trek'] else '    '} "
                  f"{s['arrive']}-{s['depart']}  {s['name']:26s} "
                  f"{s['activity']:34s} visit={s['visit_min']:3d}m "
                  f"travel={s['travel_min']:3d}m km={s['km_from_prev']:5.1f} "
                  f"ascent={s['ascent_m']}")
        if day["lunch"]:
            print(f"          lunch {day['lunch_time']}  {day['lunch']}")

    print("\nLeft out of the schedule:")
    for entry in res["unscheduled"]:
        print(f"  - {entry['name']}: {entry['reason']}")

    print("\n" + "=" * 78)
    print("ASSERTIONS")
    print("=" * 78)

    for day in res["days"]:
        check(f"{day['date']}: at most one trek", day["trek_count"] <= 1,
              f"treks={day['trek_count']}")
    for day in res["days"]:
        if not day["rest_day"]:
            check(f"{day['date']}: effort within budget",
                  day["effort_min"] <= day["effort_cap_min"],
                  f"{day['effort_min']} <= {day['effort_cap_min']}")
    for day in res["days"]:
        for stop in day["stops"]:
            check(f"{stop['name']}: has in/out times",
                  bool(stop["arrive"]) and bool(stop["depart"]),
                  f"{stop['arrive']}-{stop['depart']}")
            check(f"{stop['name']}: out = in + visit",
                  stop["depart_min"] - stop["arrive_min"] == stop["visit_min"])
    for day in res["days"]:
        clock = day["start_min"]
        ok = True
        for stop in day["stops"]:
            ok = ok and stop["arrive_min"] >= clock
            clock = stop["depart_min"]
        check(f"{day['date']}: times monotonic", ok)
        check(f"{day['date']}: ends inside window or flagged",
              day["end_min"] <= 19 * 60 or day["overloaded"],
              f"end={_fmt(day['end_min'])}")
    for day in res["days"]:
        if day["lunch_place"]:
            arrive = day["lunch_place"]["arrive_min"]
            check(f"{day['date']}: lunch at midday",
                  12 * 60 <= arrive <= 14 * 60 + 30, _fmt(arrive))
    for day in res["days"]:
        if day["trek_count"]:
            check(f"{day['date']}: trek day starts early",
                  day["start_min"] <= 7 * 60, _fmt(day["start_min"]))

    third = t._run_munnar(activities=activities)
    check("deterministic across runs",
          third["days"] == res["days"]
          and third["unscheduled"] == res["unscheduled"])

    trio = {"Kanni Mala", "Umaya Mala", "Naikolli Mala"}
    for day in res["days"]:
        names = {s["name"] for s in day["stops"]}
        check(f"{day['date']}: north-face trio split",
              len(trio & names) <= 1, str(trio & names) or "-")

    print("\n" + "=" * 78)
    print(f"{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + str(failures)}"
          f"   ({len(failures)} failing)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

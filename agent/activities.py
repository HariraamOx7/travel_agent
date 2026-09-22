"""Activity classification — the LLM decides what each stop's activity is.

Why this module exists
----------------------
The scheduler used to treat every attraction as one interchangeable "stop".
That is how a day ended up holding three separate peaks (Kanni Mala,
Umaya Mala, Naikolli Mala — +808 / +915 / +886 m of ascent) alongside
viewpoints that take 45 minutes.

The decision of WHAT the activity is and HOW LONG it takes belongs to the
model, because OSM tags alone can't tell a 45-minute overlook from a
half-day trek. This module owns that decision and nothing else: the
scheduler still owns travel times, the clock, the daily effort cap and the
one-trek-per-day rule.

Two things stay deterministic on purpose
----------------------------------------
1. Validation. The model's output is coerced into a closed vocabulary and
   clamped. A malformed or out-of-range answer is a failed call, not a
   value — it never reaches the solver.
2. The trek signal. `is_trek` is true when the model says "trek" OR when
   the measured ascent exceeds `_TREK_ASCENT_M`. The second test needs no
   LLM, which is what keeps the one-trek-per-day rule enforceable when the
   model is unreachable.

Caching
-------
Classifications are cached to disk keyed by (kind, name, pace) for 30 days.
The cache is what pins visit durations against model drift: a rebuild of an
unchanged trip reuses the same numbers.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

CLASSES = ("trek", "sightsee", "sight", "leisure", "meal", "skip")
TREK = "trek"
SKIP = "skip"

# A stop whose measured climb exceeds this is treated as a trek even if the
# model said otherwise. Measured ascent comes from the Ola Elevation API.
_TREK_ASCENT_M = 400.0

MIN_VISIT_MIN, MAX_VISIT_MIN = 15, 300
MIN_INTENSITY, MAX_INTENSITY = 1, 5

_CACHE_PATH = "cache/activity_classes.json"
_CACHE_TTL_S = 30 * 24 * 3600      # 30 days

# Used only when the model cannot answer. Deliberately conservative: a
# gentle sightseeing stop. `source="default"` marks it, and the scheduler
# raises an `unverified` flag on the day so nothing claims it is checked.
_DEFAULT_CLASS = "sightsee"
_DEFAULT_INTENSITY = 2
_DEFAULT_VISIT_MIN = 60


@dataclass(frozen=True)
class Activity:
    """What the traveller actually does at one stop, and how long it takes."""
    name: str
    activity: str                 # the label the user reads
    cls: str
    intensity: int
    visit_min: int
    note: str = ""
    source: str = "llm"           # llm | default
    is_trek: bool = False
    trek_source: str = ""         # llm | ascent | both
    duration_source: str = "llm"  # llm | default

    @property
    def is_visit(self) -> bool:
        return self.cls != SKIP

    @property
    def strenuous(self) -> bool:
        return self.intensity >= 4


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _norm(value) -> str:
    return (value or "").strip().lower()


def _key(kind: str, name: str, pace: str) -> str:
    return f"{_norm(kind)}|{_norm(name)}|{_norm(pace) or 'balanced'}"


def _default_activity(name: str, *, reason: str = "") -> Activity:
    return Activity(
        name=name,
        activity="Sightseeing stop",
        cls=_DEFAULT_CLASS,
        intensity=_DEFAULT_INTENSITY,
        visit_min=_DEFAULT_VISIT_MIN,
        note=reason,
        source="default",
        duration_source="default",
    )


def _apply_trek_signal(
    cls: str,
    ascent_m: Optional[float],
) -> tuple[bool, str]:
    """Combine the model's verdict with the measured climb.

    Union, not override: a stop that looks like a trek either way should not
    share a day with another one. Returns (is_trek, trek_source).
    """
    by_model = cls == TREK
    by_ascent = (ascent_m is not None) and (ascent_m >= _TREK_ASCENT_M)
    if by_model and by_ascent:
        return True, "both"
    if by_model:
        return True, "llm"
    if by_ascent:
        return True, "ascent"
    return False, ""


def trek_ascent_threshold() -> float:
    """The ascent (metres) above which a stop counts as a trek without the LLM."""
    return _TREK_ASCENT_M


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _load_cache() -> dict:
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        if time.time() - os.path.getmtime(_CACHE_PATH) > _CACHE_TTL_S:
            return {}
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache: dict) -> None:
    directory = os.path.dirname(_CACHE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1, sort_keys=True)
    except OSError:
        pass       # a cache write failure is never fatal


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _coerce(
    entry: dict,
    *,
    ascent_m: Optional[float],
    source: str,
) -> Optional[Activity]:
    """Validate one model entry. Returns None if it cannot be trusted."""
    if not isinstance(entry, dict):
        return None
    name = (entry.get("name") or "").strip()
    if not name:
        return None

    cls = _norm(entry.get("class"))
    if cls not in CLASSES:
        return None

    try:
        intensity = int(round(float(entry.get("intensity"))))
        visit_min = int(round(float(entry.get("visit_min"))))
    except (TypeError, ValueError):
        return None

    intensity = max(MIN_INTENSITY, min(MAX_INTENSITY, intensity))
    visit_min = max(MIN_VISIT_MIN, min(MAX_VISIT_MIN, visit_min))

    label = (entry.get("activity") or "").strip() or cls.replace("_", " ").title()
    label = label[:80]
    note = (entry.get("note") or "").strip()[:160]

    is_trek, trek_source = _apply_trek_signal(cls, ascent_m)

    return Activity(
        name=name,
        activity=label,
        cls=cls,
        intensity=intensity,
        visit_min=visit_min,
        note=note,
        source=source,
        is_trek=is_trek,
        trek_source=trek_source,
        duration_source=source,
    )


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #

_SYSTEM = (
    "You estimate what a traveller actually DOES at each stop of a trip, and "
    "how long it takes. You are given OpenStreetMap POIs with their kind and "
    "measured elevation. Be realistic about physical effort in hills: a peak "
    "needing hundreds of metres of climb is a multi-hour trek, not a quick "
    "photo stop. Reserve class 'trek' for a stop that itself needs a real "
    "climb - roughly 300 m or more of the ascent_m you are given, or a known "
    "multi-hour trail. A short viewpoint, a garden or a plantation walk is "
    "'sightsee' even when it sits in the hills: trust the ascent figure, not "
    "the scenery. Answer with JSON only, no prose."
)

_PACE_GUIDE = {
    "relaxed": "a relaxed traveller: fewer, shorter stops, longer breaks",
    "balanced": "a balanced traveller covering the usual highlights",
    "packed": "a fast-moving traveller who wants to see as much as possible",
}


def _build_prompt(items: list[dict], pace: str) -> str:
    lines = [
        f"Trip pace: {pace} ({_PACE_GUIDE.get(pace, _PACE_GUIDE['balanced'])}).",
        "",
        "For EACH stop below, decide:",
        "- activity: a short human label (e.g. \"Trek to Kanni Mala summit\", "
        "\"Sunset viewpoint\", \"Tea museum visit\")",
        "- class: one of trek, sightsee, sight, leisure, meal, skip",
        "  (trek = the stop itself needs a real climb: about 300 m+ of the "
        "given ascent_m, or a known multi-hour trail. Short viewpoints and "
        "plantation walks are sightsee.)",
        "  (use \"trek\" for any climb that needs real hiking effort; use "
        "\"skip\" if it is not something a visitor actually visits)",
        "- intensity: 1 (barely any walking) to 5 (exhausting)",
        "- visit_min: realistic minutes on site, 15-300, including the walk "
        "to and from the viewpoint but NOT the road transfer",
        "- note: optional, under 15 words, mention the climb if it matters",
        "",
        "Stops:",
    ]
    for item in items:
        ascent = item.get("ascent_m")
        elevation = item.get("elevation_m")
        bits = [f'name="{item["name"]}"', f'kind="{item.get("kind") or "unknown"}"']
        if elevation is not None:
            bits.append(f"elevation_m={elevation}")
        if ascent is not None:
            bits.append(f"ascent_m={ascent}")
        lines.append("  - " + ", ".join(bits))

    lines += [
        "",
        "Reply with exactly this JSON shape:",
        '{"pois": [{"name": "...", "activity": "...", "class": "...", '
        '"intensity": 3, "visit_min": 90, "note": "..."}]}',
        "Include every stop, using the name exactly as given.",
    ]
    return "\n".join(lines)


def _default_llm_fn(prompt: str) -> Optional[dict]:
    """Real LLM call, imported lazily so this module stays offline-testable."""
    from agent import llm_client
    return llm_client.complete_json(prompt, system=_SYSTEM, temperature=0.0)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def classify_pool(
    items: list[dict],
    *,
    pace: str = "balanced",
    llm_fn: Optional[Callable[[str], Optional[dict]]] = None,
    use_cache: bool = True,
) -> dict[str, Activity]:
    """Classify every stop in one batched call.

    items: [{"name": str, "kind": str|None, "elevation_m": int|None,
             "ascent_m": int|None}, ...]

    Returns {name: Activity}. Never raises: anything the model cannot answer
    falls back to a documented conservative profile marked source="default".
    """
    if not items:
        return {}

    pace = _norm(pace) or "balanced"
    cache = _load_cache() if use_cache else {}

    resolved: dict[str, Activity] = {}
    pending: list[dict] = []

    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        cached = cache.get(_key(item.get("kind"), name, pace))
        if isinstance(cached, dict):
            entry = dict(cached)
            entry.setdefault("name", name)
            activity = _coerce(
                entry,
                ascent_m=item.get("ascent_m"),
                source=cached.get("source", "llm"),
            )
            if activity is not None:
                resolved[name] = activity
                continue
        pending.append(item)

    if pending:
        call = llm_fn or _default_llm_fn
        payload = None
        try:
            payload = call(_build_prompt(pending, pace))
        except Exception as e:                 # noqa: BLE001 - degrade, never raise
            print(f"  [activities] classifier raised "
                  f"{type(e).__name__}: {e}", flush=True)
            payload = None

        entries_by_name: dict[str, dict] = {}
        if isinstance(payload, dict):
            raw_list = payload.get("pois")
            if isinstance(raw_list, list):
                for entry in raw_list:
                    if isinstance(entry, dict):
                        key = _norm(entry.get("name"))
                        if key:
                            entries_by_name[key] = entry

        by_name = {(i.get("name") or "").strip().lower(): i for i in pending}
        for item in pending:
            name = (item.get("name") or "").strip()
            entry = entries_by_name.get(_norm(name))
            activity = None
            if entry is not None:
                # Guard against the model renaming or inventing stops.
                entry = dict(entry)
                entry["name"] = name
                activity = _coerce(
                    entry,
                    ascent_m=item.get("ascent_m"),
                    source="llm",
                )
            if activity is None:
                activity = _default_activity(
                    name, reason="activity not assessed"
                )
            resolved[name] = activity

            if activity.source == "llm" and use_cache:
                cache[_key(item.get("kind"), name, pace)] = {
                    "activity": activity.activity,
                    "class": activity.cls,
                    "intensity": activity.intensity,
                    "visit_min": activity.visit_min,
                    "note": activity.note,
                    "source": "llm",
                }
        if use_cache and entries_by_name:
            _save_cache(cache)

    return resolved


def classify_ok(activities: dict[str, Activity]) -> bool:
    """True when every stop was classified (no fallbacks in play)."""
    return bool(activities) and all(
        a.source != "default" for a in activities.values()
    )

"""Ola Maps routing client — batch road distances.

The Ola Distance Matrix API accepts N origins and M destinations and
returns an N×M matrix of road distances (in metres). For our 13-point
itinerary (hotel + 12 stops), one HTTP call returns all pairwise
distances.

Public API
----------
    batch_matrix(coords) -> Optional[tuple[list[list[float]], list[list[float]]]]
        coords: list of (lat, lng) tuples.
        Returns (distances_km, durations_min) — two 2D lists — or None if
        the call fails (no key, network error, rate limit, malformed
        response). The API returns both in one response, so travel TIME is
        free once the matrix is fetched; discarding it forced the scheduler
        to guess speeds.

    batch_distance(coords) -> Optional[list[list[float]]]
        Backwards-compatible wrapper returning just the distance matrix.
        Returns distances only when a legacy (distance-only) cache entry is
        in play, so old caches keep working.

Caching
-------
Road distances for a fixed set of coordinates don't change. The matrix
is cached to disk keyed by a hash of the rounded coordinate list, with
a 7-day TTL. Repeated itinerary builds for the same destination reuse
the cache with zero network calls.

Design
------
- Never raises. Any failure returns None so the scheduler falls back to
  haversine × 1.4 without surfacing an error to the user.
- Respects Ola's 25-point matrix cap. If coords exceed 25, the matrix
  is chunked; if that's too slow, the caller may prefer haversine.
- Chunked requests preserve coordinates order in the output.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

OLA_API_KEY = os.environ.get("OLA_MAPS_API_KEY", "").strip()
OLA_MATRIX_URL = "https://api.olamaps.io/routing/v1/distanceMatrix"

_CACHE_DIR = "cache/road_matrix"
_CACHE_TTL_S = 7 * 24 * 3600      # 7 days
_MAX_POINTS_PER_CALL = 25


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #

def _cache_key(coords: list[tuple[float, float]]) -> str:
    """Round coords to ~100m and hash. Small drift in geocoding doesn't
    invalidate the cache; large drift does."""
    rounded = [(round(la, 3), round(ln, 3)) for la, ln in coords]
    blob = json.dumps(rounded, sort_keys=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _cache_get(key: str) -> Optional[dict]:
    """Return {"distance": 2D, "duration": 2D|None}, or None.

    Legacy entries written as a bare 2D list (before durations were
    captured) are upgraded in place with duration=None rather than being
    treated as a miss — no need to re-pay for a matrix we already have.
    """
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > _CACHE_TTL_S:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None

    if isinstance(data, list):                 # legacy distance-only entry
        return {"distance": data, "duration": None}
    if isinstance(data, dict) and isinstance(data.get("distance"), list):
        duration = data.get("duration")
        return {
            "distance": data["distance"],
            "duration": duration if isinstance(duration, list) else None,
        }
    return None


def _cache_set(
    key: str,
    distances: list[list[float]],
    durations: Optional[list[list[float]]] = None,
) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    payload = {
        "version": 2,
        "distance": distances,
        "duration": durations,
    }
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass       # cache write failure is not fatal


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #

def _call_matrix(
    coords: list[tuple[float, float]],
) -> Optional[tuple[list[list[float]], Optional[list[list[float]]]]]:
    """One Ola Distance Matrix call.

    Returns (distances_km, durations_min). Durations are None only if the
    provider omitted them; the distances are still usable. Returns None
    outright on any failure.
    """
    if not OLA_API_KEY:
        return None
    if len(coords) > _MAX_POINTS_PER_CALL:
        return None      # caller must chunk; we don't do it here

    origins = "|".join(f"{la},{ln}" for la, ln in coords)
    destinations = "|".join(f"{la},{ln}" for la, ln in coords)

    try:
        r = requests.get(
            OLA_MATRIX_URL,
            params={
                "origins": origins,
                "destinations": destinations,
                "api_key": OLA_API_KEY,
            },
            headers={"X-Request-Id": "travel-agent-matrix"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  [ola/matrix] request failed: {type(e).__name__}", flush=True)
        return None

    if r.status_code == 429:
        print("  [ola/matrix] rate limited", flush=True)
        return None
    if r.status_code != 200:
        print(f"  [ola/matrix] HTTP {r.status_code}", flush=True)
        return None

    try:
        data = r.json()
    except ValueError:
        return None

    rows = data.get("rows") or []
    if len(rows) != len(coords):
        print(f"  [ola/matrix] expected {len(coords)} rows, got {len(rows)}",
              flush=True)
        return None

    matrix: list[list[float]] = []
    dur_matrix: list[list[float]] = []
    have_all_durations = True
    for row in rows:
        elements = row.get("elements") or []
        if len(elements) != len(coords):
            print(f"  [ola/matrix] row width mismatch", flush=True)
            return None
        line = []
        dur_line = []
        for el in elements:
            meters = el.get("distance")
            if meters is None:
                return None
            line.append(round(float(meters) / 1000.0, 2))
            seconds = el.get("duration")
            if seconds is None:
                have_all_durations = False
                dur_line.append(0.0)
            else:
                dur_line.append(round(float(seconds) / 60.0, 2))
        matrix.append(line)
        dur_matrix.append(dur_line)
    return matrix, (dur_matrix if have_all_durations else None)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def batch_matrix(
    coords: list[tuple[float, float]],
) -> Optional[tuple[list[list[float]], Optional[list[list[float]]]]]:
    """Return (distances_km, durations_min) for all pairs, or None.

    Chunks the request if coords exceed the API's per-call cap. Chunk
    results are stitched so the output is always N×N in the input order.
    durations_min is None when the provider returned no durations; callers
    must then fall back to distance / assumed speed.
    """
    n = len(coords)
    if n == 0:
        return [], []
    if not OLA_API_KEY:
        return None

    key = _cache_key(coords)
    cached = _cache_get(key)
    if cached is not None:
        print(f"  [ola/matrix] cache hit ({n}×{n})", flush=True)
        return cached["distance"], cached["duration"]

    # Single-call path (the common case: n ≤ 25).
    if n <= _MAX_POINTS_PER_CALL:
        t0 = time.time()
        m = _call_matrix(coords)
        if m is None:
            return None
        distances, durations = m
        print(f"  [ola/matrix] OK {n}×{n} in {time.time()-t0:.1f}s"
              f"{' (with durations)' if durations else ''}", flush=True)
        _cache_set(key, distances, durations)
        return distances, durations

    # Chunked path: split into blocks of up to 25, call each pair-block
    # once. For n = 30 that's 4 calls (2 origin blocks × 2 dest blocks).
    print(f"  [ola/matrix] chunking {n} points", flush=True)
    blocks = [coords[i:i + _MAX_POINTS_PER_CALL]
              for i in range(0, n, _MAX_POINTS_PER_CALL)]
    full: list[list[Optional[float]]] = [[None] * n for _ in range(n)]
    full_dur: list[list[Optional[float]]] = [[None] * n for _ in range(n)]
    have_durations = True

    for bi, origin_block in enumerate(blocks):
        for bj, dest_block in enumerate(blocks):
            sub = _sub_matrix(origin_block, dest_block)
            if sub is None:
                return None
            sub_dist, sub_dur = sub
            if sub_dur is None:
                have_durations = False
            for i_local, i_global in enumerate(
                range(bi * _MAX_POINTS_PER_CALL,
                      bi * _MAX_POINTS_PER_CALL + len(origin_block))
            ):
                for j_local, j_global in enumerate(
                    range(bj * _MAX_POINTS_PER_CALL,
                          bj * _MAX_POINTS_PER_CALL + len(dest_block))
                ):
                    full[i_global][j_global] = sub_dist[i_local][j_local]
                    if sub_dur is not None:
                        full_dur[i_global][j_global] = sub_dur[i_local][j_local]
            time.sleep(0.2)          # be polite

    # Type-narrow: every distance cell must be filled.
    result: list[list[float]] = []
    for row in full:
        if any(v is None for v in row):
            return None
        result.append([float(v) for v in row])

    durations: Optional[list[list[float]]] = None
    if have_durations and all(v is not None for row in full_dur for v in row):
        durations = [[float(v) for v in row] for row in full_dur]

    _cache_set(key, result, durations)
    return result, durations


def batch_distance(
    coords: list[tuple[float, float]],
) -> Optional[list[list[float]]]:
    """Backwards-compatible wrapper: pairwise road distances in km, or None."""
    m = batch_matrix(coords)
    if m is None:
        return None
    return m[0]


def _sub_matrix(
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
) -> Optional[tuple[list[list[float]], Optional[list[list[float]]]]]:
    """Call the API for a rectangular origin × destination block.

    Returns (distances_km, durations_min); durations are None when the
    provider omitted them.
    """
    o_str = "|".join(f"{la},{ln}" for la, ln in origins)
    d_str = "|".join(f"{la},{ln}" for la, ln in destinations)
    try:
        r = requests.get(
            OLA_MATRIX_URL,
            params={"origins": o_str, "destinations": d_str,
                    "api_key": OLA_API_KEY},
            headers={"X-Request-Id": "travel-agent-matrix"},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    rows = data.get("rows") or []
    if len(rows) != len(origins):
        return None
    out = []
    out_dur = []
    have_durations = True
    for row in rows:
        els = row.get("elements") or []
        if len(els) != len(destinations):
            return None
        out.append([round(float(e["distance"]) / 1000.0, 2)
                    if e.get("distance") is not None else 0.0
                    for e in els])
        dur_row = []
        for e in els:
            if e.get("duration") is None:
                have_durations = False
                dur_row.append(0.0)
            else:
                dur_row.append(round(float(e["duration"]) / 60.0, 2))
        out_dur.append(dur_row)
    return out, (out_dur if have_durations else None)
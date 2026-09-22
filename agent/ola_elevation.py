"""Ola Maps Elevation API client — terrain height per coordinate.

Verified against the live API:

    POST https://api.olamaps.io/places/v1/elevation?api_key=...
    body: {"locations": ["10.1368151,77.0866393", ...]}
    ->    {"results": [{"elevation": 2292, "location": {...}}, ...], "status": "ok"}

Notes that matter and are easy to get wrong:

- The endpoint lives under /places/v1/, but unlike Ola Places it serves REAL
  data on this tier (verified: 2292 m for a Munnar peak over a 1484 m base).
- The documented batch parameter name is misleading. GET accepts a single
  `location=lat,lng` and returns 400 for `locations` or for pipe-joined
  values. The batched form is POST with a JSON body {"locations": [...]}.
- One POST covers the whole itinerary (hotel + every kept POI).

Public API
----------
    batch_elevation(coords) -> Optional[list[int]]
        coords: list of (lat, lng) tuples, in order. Returns elevations in
        the same order, or None if anything failed.

Caching
-------
Elevation for a fixed point doesn't change, so results are cached to disk
keyed by a hash of ~100 m-rounded coordinates (the same rounding as the road
matrix, so geocoder drift invalidates both consistently) with a 30-day TTL.

Design
------
- Never raises. Any failure returns None so the caller degrades to
  class-default durations and still builds an itinerary.
- Chunks requests if the batch exceeds the per-call cap.
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
OLA_ELEVATION_URL = "https://api.olamaps.io/places/v1/elevation"

_CACHE_DIR = "cache/elevation"
_CACHE_TTL_S = 30 * 24 * 3600      # 30 days
_MAX_POINTS_PER_CALL = 100


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #

def _coords_are_rounded(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(round(la, 3), round(ln, 3)) for la, ln in coords]


def _cache_key(coords: list[tuple[float, float]]) -> str:
    blob = json.dumps(_coords_are_rounded(coords), sort_keys=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _cache_get(key: str) -> Optional[list[int]]:
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
    if not isinstance(data, list) or not all(isinstance(v, int) for v in data):
        return None
    return data


def _cache_set(key: str, elevations: list[int]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(elevations, f)
    except OSError:
        pass       # a cache write failure is never fatal


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #

def _call_elevation(
    coords: list[tuple[float, float]],
) -> Optional[list[int]]:
    """One POST. Returns elevations in input order, or None on any failure."""
    if not OLA_API_KEY:
        return None
    if not coords:
        return []
    if len(coords) > _MAX_POINTS_PER_CALL:
        return None      # caller must chunk

    try:
        r = requests.post(
            OLA_ELEVATION_URL,
            params={"api_key": OLA_API_KEY},
            json={"locations": [f"{la},{ln}" for la, ln in coords]},
            headers={"X-Request-Id": "travel-agent-elevation"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  [ola/elevation] request failed: {type(e).__name__}",
              flush=True)
        return None

    if r.status_code == 429:
        print("  [ola/elevation] rate limited", flush=True)
        return None
    if r.status_code != 200:
        print(f"  [ola/elevation] HTTP {r.status_code}", flush=True)
        return None

    try:
        data = r.json()
    except ValueError:
        return None

    results = data.get("results")
    if not isinstance(results, list) or len(results) != len(coords):
        print(f"  [ola/elevation] expected {len(coords)} results, "
              f"got {len(results) if isinstance(results, list) else 'n/a'}",
              flush=True)
        return None

    out: list[int] = []
    for el in results:
        value = el.get("elevation")
        if value is None:
            return None
        try:
            out.append(int(round(float(value))))
        except (TypeError, ValueError):
            return None
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def batch_elevation(
    coords: list[tuple[float, float]],
) -> Optional[list[int]]:
    """Return elevations (metres) for coords in order, or None if unavailable."""
    n = len(coords)
    if n == 0:
        return []
    if not OLA_API_KEY:
        return None

    key = _cache_key(coords)
    cached = _cache_get(key)
    if cached is not None:
        print(f"  [ola/elevation] cache hit ({n} points)", flush=True)
        return cached

    if n <= _MAX_POINTS_PER_CALL:
        t0 = time.time()
        values = _call_elevation(coords)
        if values is None:
            return None
        print(f"  [ola/elevation] OK {n} points in {time.time()-t0:.1f}s",
              flush=True)
        _cache_set(key, values)
        return values

    chunked: list[int] = []
    for i in range(0, n, _MAX_POINTS_PER_CALL):
        block = coords[i:i + _MAX_POINTS_PER_CALL]
        values = _call_elevation(block)
        if values is None:
            return None
        chunked.extend(values)
        time.sleep(0.2)          # be polite
    _cache_set(key, chunked)
    return chunked


def elevation_gain(
    base_elevation: Optional[int],
    elevations: Optional[list[int]],
) -> list[Optional[int]]:
    """Ascent per point relative to the base (destination/hotel) elevation.

    The base stands in for the trailhead: we have coordinates for the town
    centre, not for each trail, so the gain is an approximation of the climb
    rather than a surveyed trail profile. Any missing input yields None, which
    callers must treat as "unknown", never as zero.
    """
    if base_elevation is None or elevations is None:
        return [None] * (len(elevations) if elevations is not None else 0)
    return [
        (e - base_elevation) if e is not None else None
        for e in elevations
    ]

"""Weather via Open-Meteo — keyless. Two modes:
- forecast : trip entirely within the 16-day horizon -> live daily forecast
- climate  : further out -> average of the same calendar days over 3 past
             years (one archive call), explicitly labelled as climatology.
"""
import requests
from datetime import date, timedelta

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Full WMO 4677 weather-code table used by Open-Meteo.
WEATHERCODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains",
    80: "rain showers", 81: "heavy showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def _desc(code):
    # Unknown code — never expose the raw number to the LLM.
    return WEATHERCODES.get(code, "mixed conditions")


def _forecast(lat: float, lng: float, start: date, end: date) -> dict:
    r = requests.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lng,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "timezone": "auto"}, timeout=15)
    r.raise_for_status()
    d = r.json()["daily"]
    days = [{"date": d["time"][i],
             "t_max": d["temperature_2m_max"][i],
             "t_min": d["temperature_2m_min"][i],
             "precip_mm": d["precipitation_sum"][i],
             "sky": _desc(d["weathercode"][i])}
            for i in range(len(d["time"]))]
    return {"mode": "live forecast", "days": days}

def _climatology(lat: float, lng: float, start: date, end: date) -> dict:
    # Build the set of (month, day) pairs the trip spans.

    want = {((start + timedelta(days=k)).month,
             (start + timedelta(days=k)).day)
            for k in range((end - start).days + 1)}

    y0 = date.today().year - 3
    r = requests.get(ARCHIVE_URL, params={
        "latitude": lat, "longitude": lng,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "start_date": f"{y0}-01-01", "end_date": f"{y0 + 2}-12-31",
        "timezone": "auto"}, timeout=30)
    r.raise_for_status()
    d = r.json()["daily"]

    # Group historical rows by (month, day).
    from collections import defaultdict
    by_md = defaultdict(list)
    for i, t in enumerate(d["time"]):
        dt = date.fromisoformat(t)
        key = (dt.month, dt.day)
        if key in want:
            by_md[key].append((
                d["temperature_2m_max"][i] or 0,
                d["temperature_2m_min"][i] or 0,
                d["precipitation_sum"][i] or 0,
            ))

    if not by_md:
        return {"mode": "climatology",
                "error": "no historical rows matched",
                "days": []}

    # Build per-day array matching the forecast schema.
    days = []
    for k in range((end - start).days + 1):
        cur = start + timedelta(days=k)
        rows = by_md.get((cur.month, cur.day), [])
        if not rows:
            continue
        n = len(rows)
        days.append({
            "date": cur.isoformat(),
            "t_max": round(sum(x[0] for x in rows) / n, 1),
            "t_min": round(sum(x[1] for x in rows) / n, 1),
            "precip_mm": round(sum(x[2] for x in rows) / n, 1),
            "sky": "typical (climatology)",
        })

    all_rows = [row for rows in by_md.values() for row in rows]
    n_all = len(all_rows)
    return {
        "mode": f"climatology (avg of {y0}-{y0 + 2}, same calendar days)",
        "days": days,
        "t_max_avg_c": round(sum(x[0] for x in all_rows) / n_all, 1),
        "t_min_avg_c": round(sum(x[1] for x in all_rows) / n_all, 1),
        "precip_mm_per_day": round(sum(x[2] for x in all_rows) / n_all, 1),
        "rainy_day_share": round(sum(1 for x in all_rows if x[2] > 1) / n_all, 2),
    }
def trip_weather(lat: float, lng: float, start: date, end: date) -> dict:
    try:
        horizon = date.today() + timedelta(days=16)
        if date.today() <= start <= horizon and end <= horizon:
            return _forecast(lat, lng, start, end)
        return _climatology(lat, lng, start, end)
    except requests.RequestException as e:
        return {"error": f"Open-Meteo failed: {e}"}
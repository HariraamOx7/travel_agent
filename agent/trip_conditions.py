"""Explainable, trip-date-dependent place suitability estimates."""
from __future__ import annotations

from datetime import date


def score(place: dict, day: date, weather: dict | None = None,
          interests: list[str] | None = None) -> dict:
    weather = weather or {}
    weather_known = any(k in weather for k in
                        ("precip_mm", "rain_probability", "wind_kmh", "t_max"))
    outdoor = place.get("indoor_outdoor") == "outdoor"
    mixed = place.get("indoor_outdoor") == "mixed"
    rain_probability = weather.get("rain_probability")
    rain_mm = float(weather.get("precip_mm") or 0)
    wind_kmh = float(weather.get("wind_kmh") or 0)
    uv_index = float(weather.get("uv_index") or 0)
    high_heat = float(weather.get("t_max") or 0) >= 38
    rainy = (rain_probability is not None and rain_probability >= 60) or rain_mm >= 5
    weather_score = 1.0 if not rainy else (0.25 if outdoor else 0.65 if mixed else 1.0)
    if outdoor and (wind_kmh >= 40 or high_heat):
        weather_score = min(weather_score, 0.4)
    if not weather_known:
        weather_score = 0.5
    kind = str(place.get("kind") or "").lower()
    famous = kind in {"monument", "museum", "fort", "castle", "waterfall"}
    peak_month = day.month in (place.get("best_months") or [])
    months = place.get("best_months") or []
    season_score = 1.0 if not months or peak_month else 0.5
    crowd_score = min(1.0, 0.2 + (0.3 if day.weekday() >= 5 else 0)
                      + (0.2 if peak_month else 0) + (0.2 if famous else 0))
    intensity = int(place.get("intensity") or 2)
    risk_score = min(1.0, 0.1 + (0.1 * max(0, intensity - 2))
                     + (0.35 if rainy and outdoor else 0)
                     + (0.2 if outdoor and wind_kmh >= 40 else 0)
                     + (0.15 if outdoor and uv_index >= 8 else 0))
    wanted = {str(i).lower() for i in (interests or [])}
    tags = {str(t).lower() for t in place.get("activity_tags") or []}
    interest_score = len(tags & wanted) / max(len(wanted), 1) if wanted else 0.5
    return {"weather_score": round(weather_score, 2),
            "crowd_score": round(crowd_score, 2),
            "risk_score": round(risk_score, 2),
            "interest_score": round(interest_score, 2),
            "season_score": season_score,
            "rain_probability": rain_probability,
            "score_source": "rule_estimate"}

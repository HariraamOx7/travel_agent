import json
from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field


class Destination(BaseModel):
    name: str
    place_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
class DestinationCandidate(BaseModel):
    place_id: Optional[str] = None
    name: str
    address: Optional[str] = None
    kind: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance_km: Optional[float] = None
    road_distance_km: Optional[float] = None


class TripState(BaseModel):
    """Single source of truth — persisted in SQLite, re-injected into every LLM call."""

    # Required slots
    origin: Optional[str] = None
    destinations: List[Destination] = Field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_total: Optional[float] = None

    # Optional slots (defaults acceptable)
    budget_currency: str = "INR"
    travellers: int = 1
    interests: List[str] = Field(default_factory=list)
    pace: str = "balanced"          # relaxed | balanced | packed
    # How trek-heavy the trip should be: low (~1 trek per 3 days),
    # balanced (~1 per 2), high (up to one per day). Drives the trek cap
    # in the scheduler, so a 'balanced' trip stays a MIX of activities
    # instead of turning every day into a summit day.
    adventure_level: str = "balanced"   # low | balanced | high
    travel_mode: Optional[str] = None        # flight | train | bus | car | bike
    origin_coords: Optional[dict] = None     # {"lat": float, "lng": float}

    # Pipeline
    # Category of a vague destination query ("hill station", "beach",
    # "temple town"). Feeds the curated destinations-DB fast path.
    destination_category: Optional[str] = None
    pending_destination_query: Optional[str] = None
    destination_candidates: List[DestinationCandidate] = Field(default_factory=list)   # vague input — Phase 2 resolves it
    candidate_offset: int = 0
    recommendations: Optional[dict] = None
    itinerary: Optional[dict] = None
    excluded_names: List[str] = Field(default_factory=list)
    stage: str = "collecting_requirements"            # -> recommending -> scheduling -> confirmed

    def missing_required(self) -> List[str]:
        missing = []
        if not self.origin:
            missing.append("origin")
        if not self.destinations:
            missing.append("destinations")
        if not (self.start_date and self.end_date):
            missing.append("dates")
        if self.budget_total is None:
            missing.append("budget")
        return missing

    @property
    def n_days(self) -> Optional[int]:
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return None

    def summary_for_prompt(self) -> str:
        """Compact state block re-injected into the system prompt on every call."""
        d = self.model_dump(mode="json", exclude_none=True)
        d.pop("recommendations", None)
        d.pop("itinerary", None)                 # keep prompts lean
        if self.recommendations:
            w = self.recommendations.get("weather") or {}
            d["recommendations_digest"] = {
                "weather": w,
                "top_attractions": [a["name"] for a in
                                    self.recommendations.get("attractions", [])[:5]],
                "counts": self.recommendations.get("counts"),
            }
        if self.itinerary:
            d["itinerary_digest"] = {
                "solver": self.itinerary.get("solver_status"),
                "days": [[s["name"] for s in day["stops"]]
                         for day in self.itinerary.get("days", [])],
            }

        # Expose weather-days that the itinerary needs but the current
        # weather block doesn't cover. The LLM should call get_weather
        # to refresh before presenting or rebuilding a schedule that
        # spans these dates.
        if self.itinerary and self.recommendations:
            weather_dates = {w["date"] for w in
                             (self.recommendations.get("weather") or {})
                             .get("days", [])}
            itinerary_dates = {day["date"]
                               for day in self.itinerary.get("days", [])}
            missing = sorted(itinerary_dates - weather_dates)
            if missing:
                d["weather_stale_days"] = missing
                d["weather_stale_hint"] = (
                    "Call get_weather to refresh the forecast, then "
                    "build_itinerary, before presenting a schedule that "
                    "includes these dates."
                )

        d["missing_required"] = self.missing_required()
        d["n_days"] = self.n_days
        return json.dumps(d, indent=2)
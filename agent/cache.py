"""Lightweight state-aware response caching to reduce redundant LLM and tool calls.

Caches query responses keyed by a normalized version of the user's utterance
and the relevant trip state signature (destination, dates, travellers, etc.).
If the user asks the same question in the same state (e.g. "what's the weather?"
or "how much does it cost?"), it returns in 0ms without hitting the LLM.
"""
from collections import OrderedDict
import hashlib
from typing import Optional

from agent.state import TripState


class ResponseCache:
    """Bounded LRU cache for conversation responses."""

    def __init__(self, maxsize: int = 100):
        self.maxsize = maxsize
        self._cache: OrderedDict[str, str] = OrderedDict()

    @staticmethod
    def _state_key(state: Optional[TripState]) -> str:
        if not state:
            return "empty"
        dest = state.destinations[0].name if state.destinations else ""
        cands = ",".join(c.name for c in (state.destination_candidates or []))
        dates = f"{state.start_date}_{state.end_date}"
        budget = f"{state.budget_total}_{state.budget_currency}"
        travellers = f"{state.travellers}"
        mode = f"{state.travel_mode}"
        has_rec = "1" if state.recommendations else "0"
        has_itin = "1" if state.itinerary else "0"
        return f"{state.origin}|{dest}|{cands}|{dates}|{budget}|{travellers}|{mode}|{has_rec}|{has_itin}"

    def _make_key(self, text: str, state: Optional[TripState]) -> str:
        norm_text = " ".join(text.lower().strip().split())
        st_key = self._state_key(state)
        raw = f"{norm_text}::{st_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, text: str, state: Optional[TripState]) -> Optional[str]:
        key = self._make_key(text, state)
        if key in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, text: str, state: Optional[TripState], response: str) -> None:
        if not response or not text:
            return
        key = self._make_key(text, state)
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = response
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

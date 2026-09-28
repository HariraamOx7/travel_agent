"""Trip-initiation intent + misspelled-origin repair regressions.

Live transcript (Tirunelveli again, this time with two typos):

    plan a trip frmo tiruneveli to any hill station
      -> "Please confirm a destination and dates first before building the
         itinerary."

Three independent breaks produced that dead end:

  1. no intent rule matched "plan a trip …" unless the words "from … to"
     followed literally, so the turn fell to the ML classifier, which reads
     the word "plan" as build_itinerary;
  2. build_itinerary with no destination answered with a flat refusal
     instead of discovering / asking for what was missing;
  3. "frmo" is not "from", so the origin was never captured — and once it
     was, the literal geocoders resolve "Tirunelveli" but return nothing
     for "Tiruneveli".

Each layer is pinned here, and the geocoder tests run fully offline.
"""

from agent import geo, intent as intent_mod, intent_rules, ner, nlu, tools
from agent.state import TripState
from agent.tool_router import ToolRouter


def _intent(text: str) -> str:
    return intent_mod.classify(text)[0]


# ── trip initiation is a slot turn, never build_itinerary ──────────────

def test_plan_a_trip_phrasings_raise_slots_not_build_itinerary():
    for text in (
        "plan a trip frmo tiruneveli to any hill station",
        "plan a trip from tirunelveli to any hill station",
        "plan a trip to any hill station",
        "planning our holiday next month",
        "trip to any hill station",
    ):
        intent, _, source = intent_mod.classify(text)
        assert intent == "provide_slot", f"{text!r} -> {intent}"
        assert source == "rule", f"{text!r} needed the ML classifier"


def test_real_itinerary_requests_still_build():
    for text in ("build the itinerary", "plan the days", "ok, plan it"):
        assert _intent(text) == "build_itinerary", text


# ── build_itinerary with nothing to build asks instead of refusing ─────

class _StubTools:
    """Tool impls that answer from memory — no network in these tests."""

    def __init__(self):
        self.calls: list[str] = []
        self.impls = {
            "search_destination_candidates": self._discover,
            "get_recommendations": self._recs,
            "build_itinerary": self._build,
        }

    def _discover(self, args, state):
        self.calls.append("search_destination_candidates")
        state.destination_candidates = []
        return {"verified": [
            {"name": "Manjolai", "distance_km": 44.6, "road_distance_km": 39.9},
            {"name": "Ponmudi", "distance_km": 172.1, "road_distance_km": 150.5},
        ]}

    def _recs(self, args, state):
        self.calls.append("get_recommendations")
        state.recommendations = {"attractions": [{"name": "Kuthiraivetti Falls"}]}
        return {"attractions": [{"name": "Kuthiraivetti Falls"}]}

    def _build(self, args, state):
        self.calls.append("build_itinerary")
        return {"schedule": []}


def _router(stub: _StubTools) -> ToolRouter:
    return ToolRouter(stub.impls)


def test_vague_destination_request_discovers_instead_of_dead_end():
    """The regression: a trip request with an origin but no destination."""
    stub = _StubTools()
    state = TripState()
    state.origin = "Tirunelveli"
    state.pending_destination_query = "hill station"
    text = "build the itinerary"          # classifier's build_itinerary read
    result = nlu.understand(text, state=state)
    assert result.intent == "build_itinerary"

    handled, reply, tool, _ = _router(stub).route_and_execute(text, result, state)
    assert handled
    assert tool == "search_destination_candidates", reply
    assert "Manjolai" in reply and "Ponmudi" in reply, reply
    assert "confirm a destination" not in reply


def test_itinerary_request_with_destination_still_builds():
    stub = _StubTools()
    state = TripState()
    state.origin = "Tirunelveli"
    tools.extract_trip_slots({"destination_raw": "Manjolai"}, state)
    assert [d.name for d in state.destinations] == ["Manjolai"]
    result = nlu.understand("build the itinerary", state=state)

    handled, reply, tool, _ = _router(stub).route_and_execute(
        "build the itinerary", result, state)
    assert handled and tool == "build_itinerary", reply
    assert "Itinerary" in reply, reply


def test_bare_itinerary_request_still_refuses_without_any_details():
    stub = _StubTools()
    state = TripState()
    text = "build the itinerary"
    handled, reply, tool, _ = _router(stub).route_and_execute(
        text, nlu.understand(text, state=state), state)
    assert handled and tool is None
    assert "confirm a destination" in reply, reply


# ── typo'd origin keyword and city name ────────────────────────────────

def test_misspelled_from_still_yields_the_origin():
    out = ner.extract_entities("plan a trip frmo tiruneveli to any hill station")
    assert out["origin"] == "Tiruneveli", out
    assert out["destination_raw"] == "hill station", out
    assert out["destination_is_vague"] is True


def test_known_city_fallback_no_longer_double_books_the_destination():
    """Bug #3, widened: the city vocabulary is much larger now."""
    for city in ("Chennai", "Ooty", "Madurai", "Tirunelveli"):
        out = ner.extract_entities(f"I want to go to {city}")
        assert "origin" not in out, out
        assert out["destination_raw"] == city, out


def test_spelling_repair_only_rewrites_close_names():
    assert geo._spelling_repair("Tiruneveli") == "tirunelveli"
    assert geo._spelling_repair("Kodaikonal") == "kodaikanal"
    assert geo._spelling_repair("Munnar") is None       # already correct
    assert geo._spelling_repair("Wakanda") is None      # nothing close
    assert geo._spelling_repair("goa") is None
    assert geo._spelling_repair("ab") is None           # too short to guess


def test_geocode_repairs_a_typo_when_providers_come_up_empty(monkeypatch):
    monkeypatch.setattr(geo, "_geocode_providers",
                        lambda name, country_codes, india: None)
    rec = geo.geocode("Kodaikonal")
    assert rec is not None and rec.get("lat") is not None, rec
    assert rec["corrected_from"] == "Kodaikonal"
    assert rec["name"] == "Kodaikanal"


def test_geocode_still_reports_an_unknown_place(monkeypatch):
    monkeypatch.setattr(geo, "_geocode_providers",
                        lambda name, country_codes, india: None)
    assert geo.geocode("Xqzwv") is None


def test_origin_adopts_the_repaired_spelling(monkeypatch):
    monkeypatch.setattr(geo, "_geocode_providers",
                        lambda name, country_codes, india: None)
    state = TripState(origin="Kodaikonal")
    tools._geocode_origin(state)
    assert state.origin == "Kodaikanal", state.origin
    assert state.origin_coords and state.origin_coords["lat"] > 8

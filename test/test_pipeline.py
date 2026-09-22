"""End-to-end regression tests for the travel-agent NLP pipeline.

Run from the project root:
    python -m pytest tests/ -v

Design rules
------------
- Every test maps to a bug that actually occurred during development, or
  to a data-shape contract between two modules that has drifted before.
- When a test fails, it points at a bug to FIX — not a test to delete.
  Update the assertion only when the intended behaviour itself changed.
- Prefer asserting on state (the source of truth) over asserting on reply
  text, except where the reply wording IS the bug (e.g. the phantom
  "which destinations?" question).
- Tests marked `xfail` document known bugs we have not yet fixed. When
  the bug is fixed, delete the `xfail` marker and the test becomes a
  live guard.

Coverage map
------------
    TestNER              -> agent/ner.py        (slot extraction)
    TestIntentRules      -> agent/intent_rules.py (rule ordering, guards)
    TestChoiceNorm       -> agent/tool_router.py  (_normalize_choice)
    TestToolRouter       -> agent/tool_router.py  (dispatch, dialog)
    TestFormatterContracts -> agent/tool_router.py (templated NLG vs tool payloads)
    TestStateInvariants  -> agent/state.py      (missing_required, n_days)
    TestOverpassFixes    -> agent/pois.py       (bbox helper, no regression)
    TestActivityModel    -> agent/activities.py (trek signal, validation)
    TestActivityClassification -> agent/activities.py (LLM contract)
    TestOneTrekPerDay    -> agent/scheduler.py  (H5, the rule that matters)
    TestEffortFeasibility -> agent/scheduler.py (H6 effort budget)
    TestTimeline         -> agent/scheduler.py  (arrive/depart, lunch)

The effort tests use the REAL Munnar POI pool from the session that
produced three separate summits on one day (Kanni Mala, Umaya Mala,
Naikolli Mala — +808 / +915 / +886 m), with real elevations from the Ola
Elevation API. They are the regression guard for that exact bug.
"""
import re as _re
from datetime import date, timedelta

import pytest

from agent import activities as activities_mod
from agent import ner, tools
from agent import scheduler as scheduler_mod
from agent.nlu import NLUResult
from agent.state import Destination, DestinationCandidate, TripState
from agent.tool_router import ToolRouter, _normalize_choice


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

TOOL_IMPLS = {
    "extract_trip_slots": tools.extract_trip_slots,
    "search_destination_candidates": tools.search_destination_candidates,
    "confirm_destination": tools.confirm_destination,
    "get_weather": tools.get_weather,
    "build_itinerary": tools.build_itinerary,
    "recommend_transport": tools.recommend_transport,
    "get_recommendations": tools.get_recommendations,
    "estimate_budget": tools.estimate_budget,
}


@pytest.fixture
def router():
    return ToolRouter(TOOL_IMPLS)


@pytest.fixture
def empty_state():
    return TripState()


@pytest.fixture
def partial_state():
    """State matching the exact point in the CLI where we hit the date bug.

    Origin set, destination confirmed, coords cached. Dates and budget
    still missing."""
    s = TripState(origin="Tirunelveli")
    s.destinations = [Destination(name="Munnar", lat=10.0883, lng=77.0570)]
    s.origin_coords = {"lat": 8.7140, "lng": 77.7338}
    return s


def make_nlu(intent, slots=None, source="rule", confidence=0.9, text=""):
    """Build an NLUResult for tests that don't go through the classifier."""
    return NLUResult(
        text=text, intent=intent, confidence=confidence,
        source=source, slots=slots or {},
    )


# --------------------------------------------------------------------------- #
# NER — slot extraction
# --------------------------------------------------------------------------- #

class TestNER:
    def test_from_to_extracts_both_places(self):
        out = ner.extract_entities("can u plan a trip to munnar from tirunelveli")
        assert out["origin"] == "Tirunelveli"
        assert out["destination_raw"] == "Munnar"

    def test_duration_phrase_derives_end_date(self):
        """The 4-days-from-a-start-date case that blocked the pipeline."""
        out = ner.extract_entities("4 days from 22-9-26")
        assert out["start_date"] == "2026-09-22"
        # "4 days" reads inclusively: 22, 23, 24, 25.
        assert out["end_date"] == "2026-09-25"

    def test_two_explicit_dates_beat_duration(self):
        out = ner.extract_entities("22-9-26 to 25-9-26")
        assert out["start_date"] == "2026-09-22"
        assert out["end_date"] == "2026-09-25"

    def test_duration_for_n_nights(self):
        out = ner.extract_entities("for 3 nights starting 1-10")
        assert out["start_date"] == "2026-10-01"
        assert out["end_date"] == "2026-10-03"

    def test_duration_only_no_start_date(self):
        """A bare duration with no anchor date should set neither field."""
        out = ner.extract_entities("a 5-day trip")
        assert "start_date" not in out
        assert "end_date" not in out

    def test_money_with_k_suffix(self):
        out = ner.extract_entities("our budget is around 40k")
        assert out["budget_total"] == 40000

    def test_money_with_rupee_symbol(self):
        out = ner.extract_entities("budget ₹35,000")
        assert out["budget_total"] == 35000

    def test_money_inr_suffix(self):
        out = ner.extract_entities("total budget 60000 INR")
        assert out["budget_total"] == 60000

    def test_travellers_forward_phrasing(self):
        out = ner.extract_entities("we are 4 people")
        assert out["travellers"] == 4

    def test_travellers_reverse_phrasing(self):
        """The 'Travellers count is 4' case that escalated to the LLM."""
        out = ner.extract_entities("Travellers count is 4")
        assert out["travellers"] == 4

    def test_travellers_family_of(self):
        out = ner.extract_entities("we are a family of 6")
        assert out["travellers"] == 6

    def test_travellers_solo(self):
        out = ner.extract_entities("it's just me")
        assert out["travellers"] == 1

    def test_travellers_group_of(self):
        out = ner.extract_entities("group of 8")
        assert out["travellers"] == 8

    def test_travel_mode_flight(self):
        out = ner.extract_entities("we'll fly there")
        assert out["travel_mode"] == "flight"

    def test_travel_mode_car(self):
        out = ner.extract_entities("we'll drive down")
        assert out["travel_mode"] == "car"

    @pytest.mark.xfail(reason="Bug #3: bare city mention sets origin as well as destination")
    def test_bare_city_is_not_treated_as_origin(self):
        out = ner.extract_entities("I want to go to Chennai")
        assert out.get("origin") != "Chennai"
        assert out["destination_raw"] == "Chennai"

    @pytest.mark.xfail(reason="Bug #2: 'hill' substring in name triggers vague path")
    def test_named_hill_station_is_not_vague(self):
        out = ner.extract_entities("I want to go to Nandi Hills")
        assert not out.get("destination_is_vague"), \
            "Nandi Hills is a specific place, not a vague category"


# --------------------------------------------------------------------------- #
# Intent rules — high-precision, order-sensitive
# --------------------------------------------------------------------------- #

class TestIntentRules:
    def test_greeting_rule_fires(self):
        from agent import intent_rules
        assert intent_rules.rule_intent("hi") == ("greet", 0.95)

    def test_good_morning_rule_fires(self):
        from agent import intent_rules
        r = intent_rules.rule_intent("good morning")
        assert r is not None and r[0] == "greet"

    def test_duration_phrase_hits_provide_slot_rule(self):
        """Regression: '4 days from 22-9-26' used to fall to the classifier."""
        from agent import intent_rules
        r = intent_rules.rule_intent("4 days from 22-9-26")
        assert r is not None, "should have a rule, not rely on the ML classifier"
        assert r[0] == "provide_slot"
        assert r[1] >= 0.85

    def test_suggest_plus_duration_stays_recommendations(self):
        """Ordering guard: 'suggest hotels on 4 days from 22-9' must NOT
        be swallowed by the duration rule."""
        from agent import intent_rules
        r = intent_rules.rule_intent("suggest hotels on 4 days from 22-9")
        assert r is not None
        assert r[0] == "request_recommendations", \
            f"duration rule preempted recommendations: got {r}"

    def test_numeric_pick_requires_candidates(self):
        """Bug guard: '2' is only a confirm_destination when candidates exist."""
        from agent import intent_rules
        # No state → '2' should not resolve to confirm_destination.
        r = intent_rules.rule_intent("2", state=None)
        assert r is None or r[0] != "confirm_destination"

    def test_numeric_pick_with_candidates(self):
        """With candidates in state, '2' must resolve."""
        from agent import intent_rules
        s = TripState()
        s.destination_candidates = [
            DestinationCandidate(name="Ooty", lat=11.4, lng=76.7),
            DestinationCandidate(name="Munnar", lat=10.0, lng=77.0),
        ]
        r = intent_rules.rule_intent("2", state=s)
        assert r is not None
        assert r[0] == "confirm_destination"

    def test_more_relaxed_is_change_pace_not_show_more(self):
        """Bug #4 guard: 'more relaxed' must not hit show_more_candidates."""
        from agent import intent_rules
        r = intent_rules.rule_intent("more relaxed please")
        # Either no rule fires (falls to classifier), or change_pace fires.
        # It must NOT be show_more_candidates.
        assert r is None or r[0] != "show_more_candidates", \
            f"'more relaxed' routed to {r}"

    def test_remove_stop_rule(self):
        from agent import intent_rules
        r = intent_rules.rule_intent("drop the Sunset Deck")
        assert r is not None
        assert r[0] == "remove_stop"

    def test_weather_rule(self):
        from agent import intent_rules
        r = intent_rules.rule_intent("what's the weather like")
        assert r is not None
        assert r[0] == "ask_weather"


# --------------------------------------------------------------------------- #
# Choice normalisation
# --------------------------------------------------------------------------- #

class TestChoiceNormalization:
    def test_preamble_is_stripped(self):
        assert _normalize_choice("let's go with Munnar", 3) == "Munnar"
        assert _normalize_choice("pick Ooty", 3) == "Ooty"
        assert _normalize_choice("choose option 2", 3) == "2"
        assert _normalize_choice("I'll take Kodaikanal", 3) == "Kodaikanal"

    def test_ordinals_resolve_to_digits(self):
        assert _normalize_choice("the first one", 5) == "1"
        assert _normalize_choice("second", 5) == "2"
        assert _normalize_choice("third option", 5) == "3"
        assert _normalize_choice("last", 5) == "5"

    def test_bare_digit_is_preserved(self):
        assert _normalize_choice("3", 5) == "3"
        assert _normalize_choice("option 4", 5) == "4"

    def test_bare_name_is_preserved(self):
        assert _normalize_choice("Munnar", 3) == "Munnar"

    def test_empty_input(self):
        assert _normalize_choice("", 3) == ""


# --------------------------------------------------------------------------- #
# Tool router — dispatch and dialog management
# --------------------------------------------------------------------------- #

class TestToolRouter:
    def test_single_named_destination_auto_confirms(self, router, empty_state):
        """The exact bug the user hit: NER extracts a specific place,
        tool_router falls through and asks 'which destinations?'."""
        empty_state.origin = "Tirunelveli"
        empty_state.destination_candidates = [
            DestinationCandidate(name="Munnar", lat=10.0883, lng=77.0570),
        ]
        nlu = make_nlu("provide_slot", slots={"destination_raw": "Munnar"})
        handled, reply, _, _ = router.route_and_execute(
            "plan a trip to munnar from tirunelveli", nlu, empty_state,
        )
        assert handled
        assert empty_state.destinations, \
            "single named candidate should be auto-confirmed"
        assert empty_state.destinations[0].name == "Munnar"
        # Must ask for the NEXT slot (dates), not "which destinations?"
        assert "where would you like to travel" not in reply.lower()
        assert "date" in reply.lower() or "budget" in reply.lower()

    def test_single_candidate_from_discovery_is_not_auto_confirmed(
        self, router, empty_state,
    ):
        """Guard: a candidate that came from discovery (not from a user-
        named place) should NOT auto-confirm, because the user hasn't
        seen it yet."""
        empty_state.origin = "Chennai"
        empty_state.destination_candidates = [
            DestinationCandidate(name="Yelagiri", lat=12.58, lng=78.64),
        ]
        # No destination_raw in slots — NER didn't extract a name.
        nlu = make_nlu("provide_slot", slots={"origin": "Chennai"})
        handled, reply, _, _ = router.route_and_execute(
            "I'm from Chennai", nlu, empty_state,
        )
        assert handled
        assert not empty_state.destinations, \
            "discovery results must not auto-confirm"

    def test_confirm_normalises_preamble(self, router, empty_state):
        """'let's go with Munnar' should confirm, not error."""
        empty_state.destination_candidates = [
            DestinationCandidate(name="Ooty", lat=11.4, lng=76.7),
            DestinationCandidate(name="Munnar", lat=10.0, lng=77.0),
            DestinationCandidate(name="Kodaikanal", lat=10.2, lng=77.5),
        ]
        nlu = make_nlu("confirm_destination", source="rule", confidence=0.95)
        handled, reply, tool, _ = router.route_and_execute(
            "let's go with Munnar", nlu, empty_state,
        )
        assert handled
        assert empty_state.destinations[0].name == "Munnar"
        assert tool == "confirm_destination"

    def test_confirm_ordinal(self, router, empty_state):
        empty_state.destination_candidates = [
            DestinationCandidate(name="Ooty", lat=11.4, lng=76.7),
            DestinationCandidate(name="Munnar", lat=10.0, lng=77.0),
        ]
        nlu = make_nlu("confirm_destination", source="rule", confidence=0.9)
        handled, _, _, _ = router.route_and_execute(
            "the second one", nlu, empty_state,
        )
        assert handled
        assert empty_state.destinations[0].name == "Munnar"

    def test_confirm_last_ordinal(self, router, empty_state):
        empty_state.destination_candidates = [
            DestinationCandidate(name="Ooty", lat=11.4, lng=76.7),
            DestinationCandidate(name="Munnar", lat=10.0, lng=77.0),
            DestinationCandidate(name="Kodaikanal", lat=10.2, lng=77.5),
        ]
        nlu = make_nlu("confirm_destination", source="rule", confidence=0.9)
        handled, _, _, _ = router.route_and_execute(
            "last", nlu, empty_state,
        )
        assert handled
        assert empty_state.destinations[0].name == "Kodaikanal"

    def test_missing_slot_prompt_uses_correct_key(self, router, empty_state):
        """Bug A guard: state.missing_required() returns 'destinations'
        (plural); the prompts dict used to only have 'destination'."""
        nlu = make_nlu("provide_slot", slots={"origin": "Chennai"})
        empty_state.origin = "Chennai"
        handled, reply, _, _ = router.route_and_execute(
            "I'm from Chennai", nlu, empty_state,
        )
        assert handled
        # The fallback message ("Could you provide your destinations?") is
        # the specific symptom. Assert we ask a normal, natural question.
        assert "could you provide your destinations" not in reply.lower()

    def test_greet_responds_shortly(self, router, empty_state):
        nlu = make_nlu("greet", source="rule", confidence=0.95)
        handled, reply, _, _ = router.route_and_execute(
            "hi", nlu, empty_state,
        )
        assert handled
        assert len(reply) < 300  # short prompt, not an essay
        assert "where" in reply.lower() or "tell me" in reply.lower()

    def test_weather_without_destination_prompts_for_it(
        self, router, empty_state,
    ):
        nlu = make_nlu("ask_weather")
        handled, reply, _, _ = router.route_and_execute(
            "what's the weather", nlu, empty_state,
        )
        assert handled
        assert "destination" in reply.lower() or "where" in reply.lower()

    def test_weather_with_destination_but_no_dates_prompts_dates(
        self, router, partial_state,
    ):
        nlu = make_nlu("ask_weather")
        handled, reply, _, _ = router.route_and_execute(
            "what's the weather", nlu, partial_state,
        )
        assert handled
        assert "date" in reply.lower()

    def test_show_more_without_candidates_says_so(self, router, empty_state):
        nlu = make_nlu("show_more_candidates", source="rule", confidence=0.95)
        handled, reply, _, _ = router.route_and_execute(
            "more", nlu, empty_state,
        )
        assert handled
        assert "no destinations" in reply.lower() or "where" in reply.lower()

    def test_build_itinerary_without_destination_prompts(
        self, router, empty_state,
    ):
        nlu = make_nlu("build_itinerary")
        handled, reply, _, _ = router.route_and_execute(
            "plan the days", nlu, empty_state,
        )
        assert handled
        assert "confirm" in reply.lower() or "destination" in reply.lower()


# --------------------------------------------------------------------------- #
# Formatter contracts — the templated NLG must match the tool payloads
# --------------------------------------------------------------------------- #

class TestFormatterContracts:
    """These are the highest-value tests in the file. Every formatter bug
    we've hit was a mismatch between what a tool RETURNS and what the
    formatter READS. Assert against a realistic payload, not the tool
    itself, so the test stays fast and doesn't need the network."""

    def test_format_itinerary_matches_scheduler_output(self):
        """Regression: formatter used to read day['slots']['morning'],
        but tools.build_itinerary returns day['stops'] as a flat list."""
        state = TripState(travellers=2, pace="balanced")
        payload = {
            "schedule": [
                {"date": "2026-09-22",
                 "stops": ["Top Station", "Echo Point"],
                 "km_total": 12.4, "lunch": "Saravana Bhavan",
                 "rain_mm": 3.0, "rest_day": False},
                {"date": "2026-09-23",
                 "stops": [], "km_total": 0.0,
                 "lunch": None, "rain_mm": 5.0, "rest_day": True},
            ],
            "unscheduled": ["Pothamedu Viewpoint"],
            "excluded": [],
            "solver_status": "OPTIMAL",
        }
        out = ToolRouter._format_itinerary("Munnar", payload, state)
        assert "Top Station" in out
        assert "Echo Point" in out
        assert "Saravana Bhavan" in out
        assert "Rest day" in out

    def test_format_itinerary_renders_excluded_list(self):
        state = TripState(travellers=2)
        payload = {
            "schedule": [],
            "unscheduled": [],
            "excluded": ["Sunset Deck", "Tea Museum"],
            "solver_status": "OPTIMAL",
        }
        out = ToolRouter._format_itinerary("Munnar", payload, state)
        assert "Sunset Deck" in out
        assert "Tea Museum" in out

    def test_format_transport_matches_transport_payload(self):
        """Regression: formatter read opt['total_cost_inr'] which
        transport.recommend() never produces."""
        payload = {
            "distance_km": 590.0,
            "options": [
                {"mode": "flight", "hours": 3.5,
                 "cost_inr_low": 8100, "cost_inr_high": 12000,
                 "cost_label": "₹8,000 – ₹12,000",
                 "notes": "via Kochi + road transfer", "fit": "fastest"},
                {"mode": "car", "hours": 13.1,
                 "cost_inr_low": 7000, "cost_inr_high": 9000,
                 "cost_label": "₹7,000 – ₹9,000",
                 "notes": "~80 L fuel (round trip)", "fit": None},
            ],
        }
        out = ToolRouter._format_transport("Chennai", "Munnar", payload)
        assert "₹8,000" in out
        assert "flight" in out.lower()
        assert "80 L fuel" in out or "fuel" in out.lower()

    def test_format_transport_falls_back_to_low_high(self):
        """If `cost_label` is missing, formatter must build one from
        cost_inr_low / cost_inr_high rather than KeyError."""
        payload = {
            "distance_km": 200.0,
            "options": [
                {"mode": "train", "hours": 5.0,
                 "cost_inr_low": 300, "cost_inr_high": 800},
            ],
        }
        out = ToolRouter._format_transport("A", "B", payload)
        assert "300" in out and "800" in out

    def test_format_budget_matches_estimate_payload(self):
        """Regression: formatter read 'line_items' but the tool returns
        'line_items_inr'."""
        state = TripState(budget_total=50000, travellers=2)
        payload = {
            "line_items_inr": {
                "stay": 10500, "food": 3200,
                "local_transport": 4500, "activities": 800,
                "intercity_transport": 15000,
            },
            "total_inr": 34000,
            "budget_inr": 50000,
            "verdict": "within budget by 16,000 INR",
        }
        out = ToolRouter._format_budget(payload, state)
        assert "34,000" in out
        assert "50,000" in out
        assert "Stay" in out or "stay" in out
        assert "Intercity" in out  # intercity_transport branch must render

    def test_format_recommendations_matches_get_recommendations_payload(self):
        """Regression: formatter read 'attractions'/'stay'/'food',
        but the tool returns 'top_picks'/'top_stay'/'top_food'."""
        payload = {
            "top_picks": [
                {"name": "Mattupetty Dam", "kind": "attraction",
                 "km_from_center": 13.2},
                {"name": "Eravikulam National Park", "kind": "attraction",
                 "km_from_center": 15.0},
            ],
            "hidden_gems": [
                {"name": "Chokramudi Peak", "kind": "peak",
                 "km_from_center": 12.0},
            ],
            "top_food": [{"name": "Saravana Bhavan", "cuisine": "south_indian"}],
            "top_stay": [{"name": "Tea County", "type": "Resort",
                          "nightly_inr": 4500, "rating_label": "Not rated",
                          "meal_plan": "Breakfast included"}],
            "counts": {"attraction": 42, "food": 12, "stay": 8},
        }
        out = ToolRouter._format_recommendations("Munnar", payload)
        assert "Mattupetty Dam" in out
        assert "Chokramudi Peak" in out
        assert "Saravana Bhavan" in out
        assert "Tea County" in out

    def test_format_weather_handles_empty_days(self):
        state = TripState(travellers=2)
        payload = {"mode": "forecast", "days": []}
        out = ToolRouter._format_weather("Munnar", payload, state)
        assert "unavailable" in out.lower() or "no weather" in out.lower()

    def test_format_weather_produces_advice(self):
        from datetime import date
        state = TripState(travellers=2)
        state.start_date = date(2026, 9, 22)
        state.end_date = date(2026, 9, 23)
        payload = {
            "mode": "live forecast",
            "days": [
                {"date": "2026-09-22", "sky": "drizzle",
                 "t_min": 15, "t_max": 21, "precip_mm": 5},
                {"date": "2026-09-23", "sky": "rain",
                 "t_min": 14, "t_max": 19, "precip_mm": 10},
            ],
        }
        out = ToolRouter._format_weather("Munnar", payload, state)
        assert "21" in out  # max temp rendered
        assert "umbrella" in out.lower() or "rain" in out.lower()


# --------------------------------------------------------------------------- #
# State invariants
# --------------------------------------------------------------------------- #

class TestStateInvariants:
    def test_missing_required_uses_plural_key(self):
        """The 'destinations' vs 'destination' mismatch that produced the
        phantom 'where would you like to travel?' question."""
        s = TripState()
        missing = s.missing_required()
        assert "destinations" in missing
        assert "destination" not in missing, \
            "state.missing_required() must use 'destinations' (plural)"

    def test_missing_required_reports_dates_not_start_only(self):
        """A start_date without an end_date still counts as 'dates' missing."""
        s = TripState()
        s.start_date = date(2026, 9, 22)
        assert "dates" in s.missing_required()

    def test_missing_required_empty_when_complete(self):
        s = TripState(origin="Chennai", travellers=2, budget_total=50000)
        s.destinations = [Destination(name="Munnar", lat=10.0, lng=77.0)]
        s.start_date = date(2026, 9, 22)
        s.end_date = date(2026, 9, 25)
        assert s.missing_required() == []

    def test_n_days_is_inclusive(self):
        s = TripState()
        s.start_date = date(2026, 9, 22)
        s.end_date = date(2026, 9, 25)
        assert s.n_days == 4  # 22, 23, 24, 25

    def test_n_days_is_none_without_dates(self):
        assert TripState().n_days is None

    def test_summary_exposes_missing_required(self):
        s = TripState(origin="Chennai", travellers=2)
        blob = s.summary_for_prompt()
        assert "missing_required" in blob

    def test_summary_digest_excludes_full_recommendations(self):
        """Prompt size discipline: the digest must not include the full
        recommendations blob, only the condensed fields."""
        s = TripState()
        s.recommendations = {
            "attractions": [{"name": f"P{i}", "lat": 10.0, "lng": 77.0}
                            for i in range(50)],
            "food": [{"name": f"F{i}"} for i in range(30)],
            "counts": {"attraction": 50, "food": 30, "stay": 5},
        }
        blob = s.summary_for_prompt()
        # The full 'food' list should not leak into the prompt.
        assert "F29" not in blob


# --------------------------------------------------------------------------- #
# Overpass / POI fixes
# --------------------------------------------------------------------------- #

class TestOverpassFixes:
    def test_bbox_around_produces_symmetric_box(self):
        from agent.pois import _bbox_around
        s, w, n, e = _bbox_around(10.0883, 77.0570, 15)
        # Box spans ~30 km N-S at this latitude (15 km in each direction)
        lat_span_km = (n - s) * 111.0
        assert 29 < lat_span_km < 31
        # Center is preserved
        assert abs((n + s) / 2 - 10.0883) < 1e-6
        assert abs((e + w) / 2 - 77.0570) < 1e-6

    def test_bbox_respects_longitude_compression(self):
        """At 60°N, one degree of longitude is ~55 km, not 111 km. The
        bbox should widen in longitude to compensate."""
        from agent.pois import _bbox_around
        s, w, n, e = _bbox_around(60.0, 10.0, 20)
        lat_span_km = (n - s) * 111.0
        lng_span_km = (e - w) * 111.0 * 0.5  # cos(60) = 0.5
        # Both spans should be ~40 km (20 km each direction)
        assert abs(lat_span_km - 40) < 1
        assert abs(lng_span_km - 40) < 1

    def test_query_template_uses_bbox(self):
        """Guard against regression to (around:...) syntax which is
        dramatically slower on the server side."""
        from agent.pois import _QUERY
        assert "[bbox:" in _QUERY
        assert "(around:" not in _QUERY

    def test_query_template_is_node_only(self):
        """Way queries are 5-10x more expensive; we removed them for a
        reason. If someone adds them back, they should do so knowingly."""
        from agent.pois import _QUERY
        assert "way[" not in _QUERY.replace("highway[", ""), \
            "way queries were removed intentionally — see pois.py docstring"


# --------------------------------------------------------------------------- #
# Ola Places — parser contract
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# End-to-end pipeline walk
# --------------------------------------------------------------------------- #

class TestEndToEnd:
    """One test per realistic user journey, asserting state at each step.
    These use the router only — no LLM, no network. If they pass, the
    deterministic path is healthy."""

    def test_munnar_journey_slots_to_recommendations(
        self, router, empty_state,
    ):
        """The exact sequence from the CLI session that motivated this
        suite: origin + destination, then dates, then budget."""

        # Turn 1: origin + destination
        nlu = make_nlu(
            "provide_slot",
            slots={"origin": "Tirunelveli", "destination_raw": "Munnar"},
        )
        tools.extract_trip_slots(nlu.slots, empty_state)
        empty_state.destination_candidates = [
            DestinationCandidate(name="Munnar", lat=10.0883, lng=77.0570),
        ]
        handled, reply, _, _ = router.route_and_execute(
            "plan a trip to munnar from tirunelveli", nlu, empty_state,
        )
        assert handled
        assert empty_state.origin == "Tirunelveli"
        assert empty_state.destinations[0].name == "Munnar"
        assert "date" in reply.lower()

        # Turn 2: dates via duration phrase
        nlu2 = make_nlu(
            "provide_slot",
            slots={"start_date": "2026-09-22", "end_date": "2026-09-25"},
        )
        tools.extract_trip_slots(nlu2.slots, empty_state)
        handled, reply, _, _ = router.route_and_execute(
            "4 days from 22-9-26", nlu2, empty_state,
        )
        assert handled
        assert empty_state.start_date == date(2026, 9, 22)
        assert empty_state.end_date == date(2026, 9, 25)
        assert "budget" in reply.lower()

        # Turn 3: budget
        nlu3 = make_nlu("provide_slot", slots={"budget_total": 40000})
        tools.extract_trip_slots(nlu3.slots, empty_state)
        handled, reply, _, _ = router.route_and_execute(
            "under 40k", nlu3, empty_state,
        )
        assert handled
        assert empty_state.budget_total == 40000
        # All required slots filled -> pipeline fires automatically.
        # The reply must mention the trip summary. It may or may not
        # include recommendations depending on whether the network call
        # succeeded in the test environment.
        assert "all set" in reply.lower()
        assert "tirunelveli" in reply.lower()
        assert "munnar" in reply.lower()

    def test_travellers_reverse_phrasing_updates_state(self, empty_state):
        """The 'Travellers count is 4' case, end-to-end through NER +
        extract_trip_slots. Bug guard for the escalation we saw."""
        out = ner.extract_entities("Travellers count is 4")
        assert out.get("travellers") == 4
        tools.extract_trip_slots(out, empty_state)
        assert empty_state.travellers == 4


# --------------------------------------------------------------------------- #
# The real Munnar pool
# --------------------------------------------------------------------------- #

# 11 attractions from the live session, with the elevations the Ola
# Elevation API returns for each (base: Munnar town 1484 m).
MUNNAR = [
    ("Pothamedu", "viewpoint", 10.059648, 77.064075, 1560),
    ("Tea paddies", "viewpoint", 10.0918752, 77.0872786, 1610),
    ("Sunset Deck", "viewpoint", 10.0532226, 77.0656179, 1520),
    ("Hill Top View Point", "viewpoint", 10.0541086, 77.0436079, 1590),
    ("Sunset point", "viewpoint", 10.041295, 77.0390793, 1545),
    ("Circular Tea Plantations", "viewpoint", 10.0564953, 77.1151731, 1650),
    ("Kanni Mala", "peak", 10.1368151, 77.0866393, 2292),
    ("Naikolli Mala", "peak", 10.1518335, 77.0431326, 2370),
    ("Chokramudi", "peak", 10.0354742, 77.1057685, 2139),
    ("Umaya Mala", "peak", 10.174543, 77.0846271, 2399),
    ("Devi Malai", "peak", 10.0590477, 77.1504885, 2161),
]
MUNNAR_BASE_ELEVATION = 1484
MUNNAR_LAT, MUNNAR_LNG = 10.0883, 77.0570
MUNNAR_RAIN = [8.1, 8.1, 7.5, 3.3]

# Effort a realistic classifier returns for each summit (minutes), derived
# from the measured climb (~600 m/h ascent, faster descent).
TREK_MINUTES = {
    "Kanni Mala": 143,
    "Naikolli Mala": 157,
    "Chokramudi": 130,
    "Umaya Mala": 163,
    "Devi Malai": 135,
}
MUNNAR_TREKS = set(TREK_MINUTES)


def _munnar_pool():
    return [
        {
            "name": name,
            "kind": kind,
            "lat": lat,
            "lng": lng,
            "elevation_m": elevation,
            "ascent_m": elevation - MUNNAR_BASE_ELEVATION,
        }
        for (name, kind, lat, lng, elevation) in MUNNAR
    ]


def _fake_llm(prompt: str):
    """Stand-in for the activity classifier: answers for the names asked about."""
    found = _re.findall(r'name="([^"]+)"', prompt)
    pois = []
    for name in found:
        if name in TREK_MINUTES:
            pois.append({
                "name": name,
                "activity": f"Trek to {name} summit",
                "class": "trek",
                "intensity": 5,
                "visit_min": TREK_MINUTES[name],
                "note": "steep climb",
            })
        else:
            pois.append({
                "name": name,
                "activity": f"{name} viewpoint",
                "class": "sightsee",
                "intensity": 1,
                "visit_min": 45,
            })
    return {"pois": pois}


def _munnar_activities(**kwargs):
    return activities_mod.classify_pool(
        _munnar_pool(), pace="balanced", llm_fn=_fake_llm,
        use_cache=False, **kwargs,
    )


def _offline_distance_factory(coords):
    """Road-ish distances and durations, no network."""
    n = len(coords)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            km = scheduler_mod._haversine(
                coords[i][0], coords[i][1], coords[j][0], coords[j][1]
            ) * 1.4
            dist[i][j] = round(km, 2)
            dur[i][j] = round(km / 30.0 * 60.0, 2)   # ~30 km/h hill roads
    return dist, dur


_UNSET = object()


def _run_munnar(n_days=4, pace="balanced", activities=None,
                distance_factory=_UNSET, adventure_level="balanced"):
    """Build a Munnar itinerary offline.

    `distance_factory=None` means "no road matrix at all", which is how the
    haversine fallback path gets exercised.
    """
    if activities is None:
        activities = _munnar_activities()
    if distance_factory is _UNSET:
        distance_factory = _offline_distance_factory
    return scheduler_mod.build_itinerary(
        _munnar_pool(),
        [{"name": "Saravana Bhavan", "lat": 10.0880, "lng": 77.0590}],
        MUNNAR_LAT, MUNNAR_LNG,
        date(2026, 9, 22),
        n_days,
        MUNNAR_RAIN[:n_days],
        pace,
        activities=activities,
        distance_factory=distance_factory,
        adventure_level=adventure_level,
    )


# --------------------------------------------------------------------------- #
# Activity model — validation and the LLM-independent trek signal
# --------------------------------------------------------------------------- #

class TestActivityModel:
    def test_measured_ascent_alone_marks_a_trek(self):
        """815 m of climb is a trek even if the model never says so.

        This is what keeps the one-trek-per-day rule enforceable when the
        classifier is unreachable.
        """
        is_trek, source = activities_mod._apply_trek_signal("sightsee", 808)
        assert is_trek
        assert source == "ascent"

    def test_model_and_ascent_agree(self):
        is_trek, source = activities_mod._apply_trek_signal("trek", 915)
        assert is_trek and source == "both"

    def test_flat_stop_is_not_a_trek(self):
        is_trek, source = activities_mod._apply_trek_signal("sightsee", 60)
        assert not is_trek and source == ""

    def test_unknown_class_is_rejected(self):
        assert activities_mod._coerce(
            {"name": "X", "class": "stroll", "intensity": 2,
             "visit_min": 60},
            ascent_m=None, source="llm",
        ) is None

    def test_intensity_and_duration_are_clamped(self):
        act = activities_mod._coerce(
            {"name": "X", "class": "sightsee", "intensity": 99,
             "visit_min": 5000},
            ascent_m=None, source="llm",
        )
        assert act.intensity == activities_mod.MAX_INTENSITY
        assert act.visit_min == activities_mod.MAX_VISIT_MIN

    def test_missing_duration_is_rejected_not_defaulted_to_zero(self):
        assert activities_mod._coerce(
            {"name": "X", "class": "sightsee", "intensity": 2},
            ascent_m=None, source="llm",
        ) is None


class TestActivityClassification:
    def test_one_call_classifies_the_whole_pool(self):
        calls = []

        def counting(prompt):
            calls.append(prompt)
            return _fake_llm(prompt)

        acts = activities_mod.classify_pool(
            _munnar_pool(), pace="balanced", llm_fn=counting,
            use_cache=False,
        )
        assert len(calls) == 1, "the pool must be classified in ONE call"
        assert len(acts) == len(MUNNAR)

    def test_cache_prevents_a_second_call(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "activity_classes.json"
        monkeypatch.setattr(activities_mod, "_CACHE_PATH", str(cache_file))
        calls = []

        def counting(prompt):
            calls.append(prompt)
            return _fake_llm(prompt)

        activities_mod.classify_pool(_munnar_pool(), pace="balanced",
                                     llm_fn=counting)
        activities_mod.classify_pool(_munnar_pool(), pace="balanced",
                                     llm_fn=counting)
        assert len(calls) == 1, "the second build must come from cache"

    def test_peaks_come_back_as_treks(self):
        acts = _munnar_activities()
        for name in MUNNAR_TREKS:
            assert acts[name].cls == "trek"
            assert acts[name].is_trek
            assert acts[name].intensity >= 4
        assert acts["Sunset Deck"].cls == "sightsee"
        assert not acts["Sunset Deck"].is_trek

    def test_unparseable_answer_falls_back_without_raising(self):
        acts = activities_mod.classify_pool(
            _munnar_pool(), pace="balanced", llm_fn=lambda p: "not json",
            use_cache=False,
        )
        assert len(acts) == len(MUNNAR)
        assert all(a.source == "default" for a in acts.values())
        assert not activities_mod.classify_ok(acts)

    def test_classifier_raising_is_contained(self):
        def boom(prompt):
            raise RuntimeError("provider down")

        acts = activities_mod.classify_pool(
            _munnar_pool(), pace="balanced", llm_fn=boom, use_cache=False,
        )
        assert all(a.source == "default" for a in acts.values())

    def test_hallucinated_stop_is_ignored(self):
        def inventing(prompt):
            return {"pois": [
                {"name": "Mount Everest", "class": "trek", "intensity": 5,
                 "visit_min": 300},
                {"name": "Sunset Deck", "class": "sightsee",
                 "intensity": 1, "visit_min": 45},
            ]}

        acts = activities_mod.classify_pool(
            _munnar_pool(), pace="balanced", llm_fn=inventing,
            use_cache=False,
        )
        assert "Mount Everest" not in acts
        assert acts["Kanni Mala"].source == "default"

    def test_skip_is_not_a_visit(self):
        def skipping(prompt):
            return {"pois": [{"name": "Tea paddies", "class": "skip",
                              "intensity": 1, "visit_min": 15}]}

        acts = activities_mod.classify_pool(
            [{"name": "Tea paddies", "kind": "wood"}], pace="balanced",
            llm_fn=skipping, use_cache=False,
        )
        assert not acts["Tea paddies"].is_visit


# --------------------------------------------------------------------------- #
# The rule this change exists for: at most ONE trek per day
# --------------------------------------------------------------------------- #

class TestOneTrekPerDay:
    def test_no_day_ever_holds_two_treks(self):
        res = _run_munnar()
        for day in res["days"]:
            assert day["trek_count"] <= 1, (
                f"{day['date']} has {day['trek_count']} treks: "
                f"{[s['name'] for s in day['stops']]}"
            )

    def test_the_three_northern_peaks_are_split(self):
        """The reported bug: Kanni Mala + Umaya Mala + Naikolli Mala in
        one day, ~2.5 km of ascent."""
        res = _run_munnar()
        trio = {"Kanni Mala", "Umaya Mala", "Naikolli Mala"}
        for day in res["days"]:
            names = {s["name"] for s in day["stops"]}
            assert len(trio & names) <= 1, (
                f"{day['date']} still holds {trio & names}"
            )

    def test_excess_treks_are_reported_with_a_reason(self):
        """5 treks, 4 days, balanced level: 3 leave, and the user is TOLD why.

        The adventure level caps the whole trip at ~1 trek per 2 days, so a
        4-day balanced trip allows 2 treks — not one per day. The kept
        treks are the bigger climbs; every dropped one carries a reason.
        """
        res = _run_munnar(n_days=4)
        dropped = [u for u in res["unscheduled"]
                   if isinstance(u, dict) and u["name"] in MUNNAR_TREKS]
        assert len(dropped) == 3, dropped
        for entry in dropped:
            reason = entry["reason"].lower()
            assert "adventure level" in reason and "trek" in reason
        # Exactly 2 treks survive (the balanced cap for 4 days), and they
        # are the most substantial climbs.
        kept_treks = [s for day in res["days"] for s in day["stops"]
                      if s["is_trek"]]
        assert len(kept_treks) == res.get("trek_cap") == 2, kept_treks

    def test_high_adventure_level_keeps_one_trek_per_day(self):
        """adventure_level='high' restores the old one-per-day ceiling."""
        res = _run_munnar(n_days=4, adventure_level="high")
        dropped = [u for u in res["unscheduled"]
                   if isinstance(u, dict) and u["name"] in MUNNAR_TREKS]
        assert len(dropped) == 1, dropped
        kept_treks = [s for day in res["days"] for s in day["stops"]
                      if s["is_trek"]]
        assert len(kept_treks) == 4

    def test_days_mix_activity_classes(self):
        """Days should carry a mix of activity classes, not stack one kind.

        Uses a pool with four classes so mixing is achievable at all; the
        soft diversity reward must visibly spread them across days.
        """
        pool = [
            {"name": "Summit A", "kind": "peak", "lat": 10.10, "lng": 77.05,
             "elevation_m": 2200, "ascent_m": 700},
            {"name": "Summit B", "kind": "peak", "lat": 10.15, "lng": 77.10,
             "elevation_m": 2300, "ascent_m": 800},
            {"name": "View A", "kind": "viewpoint", "lat": 10.05, "lng": 77.00,
             "elevation_m": 1500, "ascent_m": 0},
            {"name": "View B", "kind": "viewpoint", "lat": 10.06, "lng": 77.01,
             "elevation_m": 1510, "ascent_m": 0},
            {"name": "Museum A", "kind": "museum", "lat": 10.09, "lng": 77.06,
             "elevation_m": 1500, "ascent_m": 0},
            {"name": "Temple A", "kind": "temple", "lat": 10.07, "lng": 77.03,
             "elevation_m": 1500, "ascent_m": 0},
            {"name": "Garden A", "kind": "garden", "lat": 10.08, "lng": 77.02,
             "elevation_m": 1500, "ascent_m": 0},
            {"name": "Park A", "kind": "park", "lat": 10.11, "lng": 77.07,
             "elevation_m": 1500, "ascent_m": 0},
        ]
        acts = {}
        for a in pool:
            if a["name"].startswith("Summit"):
                acts[a["name"]] = activities_mod.Activity(
                    name=a["name"], activity=f"Trek up {a['name']}",
                    cls="trek", intensity=5, visit_min=150, source="test")
            else:
                cls = {"museum": "sight", "temple": "sight",
                       "garden": "leisure", "park": "leisure"}.get(
                    a["kind"], "sightsee")
                acts[a["name"]] = activities_mod.Activity(
                    name=a["name"], activity=f"Visit {a['name']}",
                    cls=cls, intensity=1, visit_min=45, source="test")
        res = scheduler_mod.build_itinerary(
            pool,
            [{"name": "Saravana Bhavan", "lat": 10.0880, "lng": 77.0590}],
            MUNNAR_LAT, MUNNAR_LNG,
            date(2026, 9, 22), 4, MUNNAR_RAIN[:4], "balanced",
            activities=acts, distance_factory=_offline_distance_factory,
            adventure_level="balanced",
        )
        mixes = [set(day.get("class_mix") or []) for day in res["days"]]
        multi = sum(1 for m in mixes if len(m) >= 2)
        assert multi >= 2, [sorted(m) for m in mixes]
        # Every class present in the pool made it into some day.
        seen = set().union(*mixes)
        assert {"trek", "sightsee", "leisure"} <= seen, [sorted(m) for m in mixes]

    def test_rule_holds_across_paces(self):
        for pace in ("relaxed", "balanced", "packed"):
            res = _run_munnar(pace=pace)
            for day in res["days"]:
                assert day["trek_count"] <= 1, f"{pace} / {day['date']}"

    def test_rule_survives_a_dead_classifier(self):
        """No LLM: ascent alone must still keep treks apart."""
        acts = activities_mod.classify_pool(
            _munnar_pool(), pace="balanced",
            llm_fn=lambda p: (_ for _ in ()).throw(RuntimeError("down")),
            use_cache=False,
        )
        assert all(a.source == "default" for a in acts.values())
        res = _run_munnar(activities=acts)
        for day in res["days"]:
            assert day["trek_count"] <= 1
        assert res["days"][0]["unverified"] is True
        assert res["activity_source"] == "default"

    def test_validation_reports_the_rule_held(self):
        res = _run_munnar()
        assert res["validation"]["trek_rule_ok"] is True


# --------------------------------------------------------------------------- #
# Effort budget (H6)
# --------------------------------------------------------------------------- #

class TestEffortFeasibility:
    def test_no_day_exceeds_the_effort_cap(self):
        res = _run_munnar()
        for day in res["days"]:
            assert day["effort_min"] <= day["effort_cap_min"] or day["rest_day"]

    def test_a_lone_trek_always_fits_its_own_day(self):
        """Arithmetic must never drop a destination: a single stop is
        always admitted, even when it exceeds the pace cap alone."""
        res = _run_munnar(n_days=1)
        scheduled = sum(len(d["stops"]) for d in res["days"])
        assert scheduled >= 1

    def test_effort_is_spread_across_days(self):
        """The old model could park every summit on one day while the
        others were empty; effort balance is now part of the objective."""
        res = _run_munnar()
        efforts = [d["effort_min"] for d in res["days"] if not d["rest_day"]]
        assert max(efforts) > 0
        assert len(efforts) >= 2


# --------------------------------------------------------------------------- #
# Timeline — in and out times
# --------------------------------------------------------------------------- #

class TestTimeline:
    def test_every_stop_has_in_and_out_times(self):
        res = _run_munnar()
        for day in res["days"]:
            for stop in day["stops"]:
                assert stop["arrive"] and stop["depart"]
                assert stop["arrive_min"] < stop["depart_min"]

    def test_time_out_equals_in_plus_visit(self):
        res = _run_munnar()
        for day in res["days"]:
            for stop in day["stops"]:
                assert stop["depart_min"] - stop["arrive_min"] \
                    == stop["visit_min"]

    def test_times_are_monotonic_within_a_day(self):
        res = _run_munnar()
        for day in res["days"]:
            clock = day["start_min"]
            for stop in day["stops"]:
                assert stop["arrive_min"] >= clock
                clock = stop["depart_min"]
            assert day["end_min"] >= clock      # includes the return leg

    def test_lunch_falls_at_midday(self):
        res = _run_munnar()
        for day in res["days"]:
            if not day["lunch_place"]:
                continue
            arrive = day["lunch_place"]["arrive_min"]
            assert 12 * 60 <= arrive <= 14 * 60 + 30
            assert day["lunch_min"] >= 30

    def test_days_end_inside_the_window_or_are_flagged(self):
        res = _run_munnar()
        for day in res["days"]:
            if day["end_min"] > 19 * 60:
                assert day["overloaded"], (
                    f"{day['date']} ends at {_fmt(day['end_min'])} without "
                    "being flagged"
                )

    def test_trek_days_start_early(self):
        res = _run_munnar()
        for day in res["days"]:
            if day["trek_count"]:
                assert day["start_min"] <= 7 * 60, (
                    f"trek day {day['date']} starts at "
                    f"{_fmt(day['start_min'])}"
                )

    def test_timeline_is_deterministic(self):
        first = _run_munnar()
        second = _run_munnar()
        assert first["days"] == second["days"]
        assert first["unscheduled"] == second["unscheduled"]

    def test_no_network_falls_back_to_distance_over_speed(self):
        """Without a road matrix the clock still advances sensibly."""
        res = _run_munnar(distance_factory=None)
        assert res["days"]
        assert any(s["travel_min"] > 0
                   for d in res["days"] for s in d["stops"])

    def test_real_road_durations_are_used_when_present(self):
        """Same geometry, 4x slower roads => proportionally more travel.

        Asserted on total travel minutes rather than end_time: a short day
        is bounded by the midday meal, so end_time can be identical even
        when the driving time clearly is not.
        """
        def slow_factory(coords):
            dist, _dur = _offline_distance_factory(coords)
            return dist, [[v * 4 for v in row] for row in dist]

        fast = _run_munnar()
        slow = _run_munnar(distance_factory=slow_factory)
        fast_travel = sum(d["total_travel_min"] for d in fast["days"])
        slow_travel = sum(d["total_travel_min"] for d in slow["days"])
        assert slow_travel > fast_travel, (
            f"durations ignored: fast={fast_travel} slow={slow_travel}"
        )

    def test_every_day_with_stops_gets_a_lunch(self):
        """Regression: a trek day finishes before 12:30. Dropping the meal
        for those days is not an improvement, so the meal is slotted at
        midday after the stops instead."""
        res = _run_munnar()
        for day in res["days"]:
            if day["stops"]:
                assert day["lunch"], f"{day['date']} has stops but no lunch"
                assert day["lunch_time"], f"{day['date']} lunch has no time"


def _fmt(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class TestItineraryFormatter:
    def test_renders_times_activity_and_effort(self):
        res = _run_munnar()
        payload = {
            "schedule": [
                {
                    "day": 1,
                    "date": d["date"],
                    "start": d["start_time"],
                    "end": d["end_time"],
                    "stops": [
                        {"name": s["name"], "activity": s["activity"],
                         "arrive": s["arrive"], "depart": s["depart"],
                         "visit_min": s["visit_min"],
                         "km_from_prev": s["km_from_prev"],
                         "is_trek": s["is_trek"],
                         "intensity": s["intensity"]}
                        for s in d["stops"]
                    ],
                    "lunch": d["lunch"],
                    "lunch_time": d["lunch_time"],
                    "km_total": 10.0,
                    "effort_min": d["effort_min"],
                    "travel_min": d["total_travel_min"],
                    "trek_count": d["trek_count"],
                }
                for d in res["days"]
            ],
            "excluded": [],
        }
        out = ToolRouter._format_itinerary("Munnar", payload, TripState())
        first_stop = res["days"][0]["stops"][0]
        assert first_stop["name"] in out
        assert first_stop["arrive"] in out
        assert first_stop["depart"] in out
        assert "on foot" in out

    def test_lists_unscheduled_stops_with_reasons(self):
        payload = {
            "schedule": [],
            "unscheduled": [
                {"name": "Umaya Mala",
                 "reason": "163 min of climbing — only one trek fits per "
                           "day"},
            ],
            "excluded": [],
        }
        out = ToolRouter._format_itinerary("Munnar", payload, TripState())
        assert "Umaya Mala" in out
        assert "one trek" in out

    def test_formatter_works_through_an_instance(self):
        """Regression: the router calls self._format_itinerary(...). Losing
        the @staticmethod therefore breaks production while every class-level
        call still passes — which is precisely how it shipped once."""
        router = ToolRouter(TOOL_IMPLS)
        payload = {"schedule": [], "unscheduled": [], "excluded": []}
        out = router._format_itinerary("Munnar", payload, TripState())
        assert "Day-by-Day Itinerary" in out

    def test_still_accepts_plain_string_stops(self):
        """Backward compatibility with the original payload shape."""
        payload = {
            "schedule": [{"date": "2026-09-22", "stops": ["Top Station"],
                          "lunch": "Saravana Bhavan", "km_total": 12.4,
                          "rain_mm": 3.0, "rest_day": False}],
            "unscheduled": [],
            "excluded": [],
        }
        out = ToolRouter._format_itinerary("Munnar", payload, TripState())
        assert "Top Station" in out
        assert "Saravana Bhavan" in out
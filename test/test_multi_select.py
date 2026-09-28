"""Multi-destination selection regressions.

A Tirunelveli trip exposed three breaks in picking MORE THAN ONE candidate:

  1. intent rules only matched a single `^\\d{1,2}$` pick, so "1 and 2"
     fell through to the LLM, which asked a clarifying question and never
     recorded anything;
  2. on "both" the LLM called confirm_destination(choice="1") — the tool
     schema described a singular pick — collapsing the selection to the
     first candidate (Ponmudi vanished from state);
  3. bare "both"/"all"/multi-name replies matched no rule at all.

The deterministic path already knew how to split "1 and 2"; these tests pin
the rules, the whole-list expansion inside the tool, and the end-to-end NLP
router reply so the selection can never silently drop a destination again.
"""

from agent import intent_rules, nlu, tools
from agent.state import DestinationCandidate, TripState
from agent.tool_router import ToolRouter

NAMES5 = ["Manjolai", "Ponmudi", "Thekkady", "Peermade", "Vagamon"]


def _state(n_cands: int) -> TripState:
    s = TripState()
    s.destination_candidates = [
        DestinationCandidate(name=x, lat=8.0 + i, lng=77.0 + i)
        for i, x in enumerate(NAMES5[:n_cands])
    ]
    return s


def _intent(text: str, state: TripState):
    got = intent_rules.rule_intent(text, state)
    return got[0] if got else None


# ── intent rules ───────────────────────────────────────────────────────

def test_multi_index_pick_fires_rule():
    assert _intent("1 and 2", _state(5)) == "confirm_destination"
    assert _intent("1, 3", _state(5)) == "confirm_destination"
    assert _intent("1 & 4", _state(5)) == "confirm_destination"


def test_both_fires_only_with_exactly_two_candidates():
    assert _intent("both", _state(2)) == "confirm_destination"
    assert _intent("both of them", _state(2)) == "confirm_destination"
    # Five candidates: "both" is ambiguous — leave it to the LLM, which
    # can read the chat history to know which two the user means.
    assert _intent("both", _state(5)) is None


def test_all_phrases_fire_with_any_candidates():
    for text in ("all", "all of them", "everything", "each of them",
                 "the whole list"):
        assert _intent(text, _state(5)) == "confirm_destination", text


def test_pick_rules_need_pending_candidates():
    empty = TripState()
    assert _intent("1 and 2", empty) is None
    assert _intent("both", empty) is None
    assert _intent("all", empty) is None
    # Single index without candidates stays valid as a budget/traveller
    # style number only if some other rule claims it — it must not become
    # a destination pick.
    assert _intent("1", empty) is None


def test_multi_name_pick_fires_rule():
    assert _intent("Manjolai and Ponmudi", _state(5)) == "confirm_destination"
    assert _intent("Manjolai, Ponmudi", _state(5)) == "confirm_destination"
    # Every token must resolve — an unknown name must NOT half-match.
    assert _intent("Manjolai and Nowhere", _state(5)) is None


def test_single_pick_still_works():
    assert _intent("1", _state(5)) == "confirm_destination"
    assert _intent("option 2", _state(5)) == "confirm_destination"
    assert _intent("the first one", _state(5)) == "confirm_destination"
    assert _intent("Ponmudi", _state(5)) == "confirm_destination"


def test_nlu_reports_rule_source_for_multi_pick():
    res = nlu.understand("1 and 2", state=_state(5))
    assert res.intent == "confirm_destination"
    assert res.source == "rule"


# ── tools.confirm_destination whole-list expansion ────────────────────

def test_tool_expands_multi_index():
    res = tools.confirm_destination({"choice": "1 and 2"}, _state(5))
    assert [c["name"] for c in res["confirmed"]] == ["Manjolai", "Ponmudi"]


def test_tool_expands_both_when_two_candidates():
    res = tools.confirm_destination({"choice": "both"}, _state(2))
    assert [c["name"] for c in res["confirmed"]] == ["Manjolai", "Ponmudi"]


def test_tool_rejects_both_when_ambiguous():
    # 5 candidates: "both" matches nothing → error, never a silent
    # first-pick collapse like choice="1".
    res = tools.confirm_destination({"choice": "both"}, _state(5))
    assert "error" in res


def test_tool_expands_all_regardless_of_count():
    res = tools.confirm_destination({"choice": "all of them"}, _state(5))
    assert len(res["confirmed"]) == 5


def test_tool_clears_candidates_after_multi_pick():
    s = _state(5)
    tools.confirm_destination({"choice": "1 and 2"}, s)
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]
    assert s.destination_candidates == []


# ── filler words must not become part of a place name ────────────────

def test_ner_strips_filler_around_multi_destination():
    from agent import ner
    ents = ner.extract_entities("i would like to see manjolai and ponmudi both")
    assert ents["destinations_list"] == ["Manjolai", "Ponmudi"]
    assert ents["destination_raw"] == "Manjolai, Ponmudi"


def test_ner_strips_trailing_too():
    from agent import ner
    ents = ner.extract_entities("let's visit ooty and kodaikanal too")
    assert ents["destinations_list"] == ["Ooty", "Kodaikanal"]


def test_clean_place_name_keeps_real_names_intact():
    from agent.ner import clean_place_name
    for name in ("Munnar", "Udhagamandalam", "Thiruvananthapuram",
                 "Thekkady", "Goa"):
        assert clean_place_name(name) == name


def test_slot_extraction_rejects_filler_names():
    # Even if the LLM hands over an unclean list, state must not receive a
    # geocode-less "See Manjolai".
    s = TripState()
    s.destination_candidates = [
        DestinationCandidate(name="Manjolai", lat=8.6333, lng=77.4167),
        DestinationCandidate(name="Ponmudi", lat=8.76, lng=77.1167),
    ]
    tools.extract_trip_slots(
        {"destinations_list": ["see manjolai", "ponmudi both"]}, s)
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]


def test_slot_extraction_reuses_verified_candidate_coords():
    # Candidates carry verified coordinates from the discovery stage; a
    # selection must inherit them rather than re-geocoding the raw text.
    s = TripState()
    s.destination_candidates = [
        DestinationCandidate(name="Manjolai", lat=8.6333, lng=77.4167,
                             place_id="curated:Manjolai"),
        DestinationCandidate(name="Ponmudi", lat=8.76, lng=77.1167,
                             place_id="curated:Ponmudi"),
    ]
    tools.extract_trip_slots({"destination_raw": "manjolai and ponmudi"}, s)
    got = {(d.name, d.lat, d.lng, d.place_id) for d in s.destinations}
    assert ("Manjolai", 8.6333, 77.4167, "curated:Manjolai") in got
    assert ("Ponmudi", 8.76, 77.1167, "curated:Ponmudi") in got


# ── end-to-end NLP tool router ────────────────────────────────────────

def _router() -> ToolRouter:
    return ToolRouter({"confirm_destination": tools.confirm_destination})


def test_route_multi_index_confirms_both():
    s = _state(5)
    handled, reply, tool, _ = _router().route_and_execute(
        "1 and 2", nlu.understand("1 and 2", state=s), s)
    assert handled and tool == "confirm_destination"
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]
    assert "Manjolai and Ponmudi" in reply, reply


def test_route_reply_uses_plural_for_multiple():
    s = _state(2)
    _, reply, _, _ = _router().route_and_execute(
        "both", nlu.understand("both", state=s), s)
    assert "as your destinations" in reply, reply


def test_route_single_pick_reply_stays_singular():
    s = _state(5)
    _, reply, _, _ = _router().route_and_execute(
        "1", nlu.understand("1", state=s), s)
    assert "as your destination." in reply, reply


def test_route_multi_name_confirms_both():
    s = _state(5)
    handled, reply, tool, _ = _router().route_and_execute(
        "Manjolai and Ponmudi", nlu.understand("Manjolai and Ponmudi", state=s), s)
    assert handled and tool == "confirm_destination"
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]


def test_route_ambiguous_both_escalates_instead_of_guessing():
    # 5 candidates and "both": the NLP path must NOT pick for the user.
    s = _state(5)
    handled, _, tool, _ = _router().route_and_execute(
        "both", nlu.understand("both", state=s), s)
    assert not handled
    assert [d.name for d in s.destinations] == []
    assert len(s.destination_candidates) == 5


# ── redundant pick after the destinations are already locked in ────────


def _confirmed_two() -> TripState:
    """A trip whose two destinations are already confirmed (candidates gone)."""
    s = _state(2)
    tools.confirm_destination({"choice": "1 and 2"}, s)
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]
    assert s.destination_candidates == []
    return s


def test_redundant_both_fires_rule_after_confirmation():
    # Previously this dropped to the LLM, which babbled a half-finished
    # date prompt. It must now be handled deterministically.
    s = _confirmed_two()
    assert _intent("both", s) == "confirm_destination"
    assert _intent("all of them", s) == "confirm_destination"


def test_both_still_escalates_without_candidates_or_destinations():
    s = TripState()
    assert _intent("both", s) is None


def test_route_redundant_both_acknowledges_and_keeps_both():
    s = _confirmed_two()
    handled, reply, tool, _ = _router().route_and_execute(
        "both", nlu.understand("both", state=s), s)
    assert handled and tool == "confirm_destination"
    assert "Manjolai and Ponmudi are already set" in reply, reply
    # Both destinations survive; the router must not re-pick or drop one.
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]


def test_route_recap_names_every_destination():
    # The recap line on a later turn (e.g. dates) used to say
    # "Destination: Manjolai", quietly hiding Ponmudi.
    s = _confirmed_two()
    text = "4 days from 25-9"
    handled, reply, _, _ = _router().route_and_execute(
        text, nlu.understand(text, state=s), s)
    assert handled, reply
    assert "Destinations: Manjolai, Ponmudi" in reply, reply
    assert [d.name for d in s.destinations] == ["Manjolai", "Ponmudi"]

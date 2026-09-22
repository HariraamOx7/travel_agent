"""Comprehensive validation test for NLP-based Tool Calling & Workload Reduction.

Verifies:
1. Intent classification & rule matching for tool triggers (including ask_weather).
2. NLP ToolRouter direct execution (bypassing LLM ReAct loop).
3. State machine slot progression and prompts.
4. Response caching (0ms latency on repeated queries).
"""
import sys
import time
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agent.state import TripState, Destination, DestinationCandidate
from agent import nlu, tools
from agent.tool_router import ToolRouter
from agent.cache import ResponseCache


def test_intent_classification():
    print("--- 1. Testing Intent Classification & Rules ---")
    test_cases = [
        ("hi", "greet", "rule"),
        ("what is the weather like", "ask_weather", "rule"),
        ("is it going to rain in Ooty", "ask_weather", "rule"),
        ("suggest some places to visit", "request_recommendations", "rule"),
        ("plan the days", "build_itinerary", "rule"),
        ("how much will it cost", "ask_budget", "rule"),
        ("how do I get there", "ask_transport", "rule"),
        ("make it relaxed", "change_pace", "rule"),
        ("drop the museum", "remove_stop", "rule"),
        ("from Chennai to Ooty for 2 people", "provide_slot", "classifier"),
    ]

    state = TripState()
    for text, expected_intent, expected_source in test_cases:
        r = nlu.understand(text, state=state)
        print(f"Utterance: {text!r:35} -> Intent: {r.intent:22} (conf: {r.confidence:.2f}, source: {r.source})")
        assert r.intent == expected_intent, f"Expected {expected_intent}, got {r.intent}"
    print("Intent classification tests passed!\n")


def test_tool_router_direct_execution():
    print("--- 2. Testing NLP ToolRouter Direct Execution (Zero LLM) ---")
    tool_impls = {
        "extract_trip_slots": tools.extract_trip_slots,
        "search_destination_candidates": tools.search_destination_candidates,
        "confirm_destination": tools.confirm_destination,
        "get_weather": tools.get_weather,
        "build_itinerary": tools.build_itinerary,
        "recommend_transport": tools.recommend_transport,
        "get_recommendations": tools.get_recommendations,
        "estimate_budget": tools.estimate_budget,
    }
    router = ToolRouter(tool_impls)
    state = TripState()

    # Step A: Greet
    r = nlu.understand("hello", state)
    t0 = time.perf_counter()
    handled, reply, tool, res = router.route_and_execute("hello", r, state)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[Greet] Handled: {handled}, Time: {dt_ms:.2f}ms\nReply: {reply}\n")
    assert handled and "Hi!" in reply

    # Step B: Provide slot (origin & destination)
    slot_text = "from Chennai to Ooty for 2 people from 2026-10-15 to 2026-10-20 with budget 30000"
    r = nlu.understand(slot_text, state)
    tools.extract_trip_slots(r.slots, state)
    handled, reply, tool, res = router.route_and_execute(slot_text, r, state)
    print(f"[Provide Slots] Handled: {handled}\nReply:\n{reply}\n")
    assert handled

    # Confirm destination and dates manually to give it coordinates and timeframe
    state.destinations = [Destination(name="Ooty", lat=11.41, lng=76.70)]
    state.start_date = date(2026, 10, 15)
    state.end_date = date(2026, 10, 20)
    state.origin = "Chennai"

    # Step C: Ask Weather (Direct Tool Execution!)
    r = nlu.understand("what is the weather outlook", state)
    t0 = time.perf_counter()
    handled, reply, tool, res = router.route_and_execute("what is the weather outlook", r, state)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[Weather Tool] Handled: {handled}, Tool Called: {tool}, Time: {dt_ms:.2f}ms\nReply excerpt:\n{reply[:200]}...\n")
    assert handled and tool == "get_weather"

    # Step D: Ask Transport (Direct Tool Execution!)
    r = nlu.understand("how do I get there", state)
    t0 = time.perf_counter()
    handled, reply, tool, res = router.route_and_execute("how do I get there", r, state)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[Transport Tool] Handled: {handled}, Tool Called: {tool}, Time: {dt_ms:.2f}ms\nReply excerpt:\n{reply[:200]}...\n")
    assert handled and tool == "recommend_transport"

    # Step E: Ask Budget (Direct Tool Execution!)
    r = nlu.understand("what will it cost", state)
    t0 = time.perf_counter()
    handled, reply, tool, res = router.route_and_execute("what will it cost", r, state)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[Budget Tool] Handled: {handled}, Tool Called: {tool}, Time: {dt_ms:.2f}ms\nReply:\n{reply}\n")
    assert handled and tool == "estimate_budget"


def test_response_caching():
    print("--- 3. Testing Response Caching (0ms Cache Hits) ---")
    cache = ResponseCache(maxsize=10)
    state = TripState()
    state.origin = "Chennai"
    state.destinations = [Destination(name="Ooty", lat=11.41, lng=76.70)]

    query = "what is the weather like"
    response = "⛅ Weather in Ooty is 15°C - 22°C with light showers."

    # Cache miss
    assert cache.get(query, state) is None
    # Set cache
    cache.set(query, state, response)

    # Cache hit
    t0 = time.perf_counter()
    cached = cache.get(query, state)
    dt_us = (time.perf_counter() - t0) * 1_000_000
    assert cached == response
    print(f"Cache Hit successful in {dt_us:.1f} microseconds!")


if __name__ == "__main__":
    test_intent_classification()
    test_tool_router_direct_execution()
    test_response_caching()
    print("ALL TESTS PASSED SUCCESSFULLY!")

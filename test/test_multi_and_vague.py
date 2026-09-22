"""Test script for vague destination discovery and multi-destination parsing."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agent.state import TripState
from agent import nlu, tools
from agent.tool_router import ToolRouter

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

print("=== TEST 1: Vague Destination Discovery ===")
state1 = TripState()
query1 = "plan a trip from tirunelveli to any hill station"
r1 = nlu.understand(query1, state1)
print("Intent:", r1.intent, "(source:", r1.source, "conf:", r1.confidence, ")")
print("Slots:", r1.slots)
tools.extract_trip_slots(r1.slots, state1)
print("State origin:", state1.origin)
print("State pending query:", state1.pending_destination_query)

handled1, reply1, tool1, res1 = router.route_and_execute(query1, r1, state1)
print("Handled:", handled1)
print("Tool called:", tool1)
print("Reply:\n", reply1)
assert handled1 and tool1 == "search_destination_candidates"
assert len(res1.get("verified", [])) > 0
print("Test 1 passed!")

print("\n=== TEST 2: Multi-Destination Specification ===")
state2 = TripState()
query2 = "plan a trip from chennai to ooty, kodaikanal and munnar"
r2 = nlu.understand(query2, state2)
print("Intent:", r2.intent, "(source:", r2.source, "conf:", r2.confidence, ")")
print("Slots:", r2.slots)
tools.extract_trip_slots(r2.slots, state2)
print("State origin:", state2.origin)
print("State destination candidates:", [c.name for c in state2.destination_candidates])

handled2, reply2, tool2, res2 = router.route_and_execute(query2, r2, state2)
print("Handled:", handled2)
print("Reply:\n", reply2)
assert handled2
# The reply echoes the geocoded (canonical) names — 'Ooty' resolves to
# 'Udhagamandalam' via the geocoder.
assert "Udhagamandalam" in reply2 and "Kodaikanal" in reply2 \
    and "Munnar" in reply2, reply2

print("\nALL VAGUE & MULTI-DESTINATION TESTS PASSED!")

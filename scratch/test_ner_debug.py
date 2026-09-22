import sys, os
sys.path.insert(0, os.path.abspath("."))
from agent import nlu, tools
from agent.state import TripState
from agent.tool_router import ToolRouter

tool_impls = {
    "confirm_destination": tools.confirm_destination,
    "search_destination_candidates": tools.search_destination_candidates,
    "get_weather": tools.get_weather,
    "build_itinerary": tools.build_itinerary,
    "recommend_transport": tools.recommend_transport,
    "get_recommendations": tools.get_recommendations,
    "estimate_budget": tools.estimate_budget,
    "extract_trip_slots": tools.extract_trip_slots,
}
router = ToolRouter(tool_impls)

def run_test(query: str):
    print("\n" + "="*60)
    print(f"USER: {query}")
    print("="*60)
    state = TripState()
    res_nlu = nlu.understand(query, state=state)
    print("NER slots:", res_nlu.slots)
    if res_nlu.has_slots:
        applied = tools.extract_trip_slots(res_nlu.slots, state)
        print("Applied:", applied.get("applied"))

    handled, reply, tool_called, tool_res = router.route_and_execute(query, res_nlu, state)
    print(f"Router handled: {handled}")
    print(f"Tool called: {tool_called}")
    print(f"State destinations: {[d.name for d in state.destinations]}")
    print(f"State destination_candidates: {[c.name for c in state.destination_candidates]}")
    print(f"State origin: {state.origin} (coords: {state.origin_coords})")
    print(f"State missing: {state.missing_required()}")
    print("\nAGENT REPLY:\n" + reply)

if __name__ == "__main__":
    # Test 1: User's exact prompt
    run_test("can u plan a trip to munnar from tirunelveli")

    # Test 2: All slots given in one go
    run_test("make a plan from tirunelveli to munnar from 20-9 to 25-9 for 4 members under 40k")

    # Test 3: Multi-destination
    run_test("plan a trip from chennai to ooty and kodaikanal")

    # Test 4: Vague destination
    run_test("plan a trip from tirunelveli to any hill station")

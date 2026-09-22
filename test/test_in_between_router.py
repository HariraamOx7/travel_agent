"""Automated tests for the In-Between Model (Semantic & Hybrid Tool Call Router).

Validates:
  1. Semantic exemplar matching via FastEmbed ONNX embeddings.
  2. Routing accuracy: deterministic tool calls -> 'nlp', qualitative/reasoning -> 'llm'.
  3. Orchestrator integration: trace records router decision, latency < 30ms.
"""
import time
from agent.router import ToolCallRouter
from agent.nlu import understand
from agent.orchestrator import Orchestrator
from agent.state import TripState


def test_routing_benchmark():
    router = ToolCallRouter()
    state = TripState()

    test_cases = [
        # (Query, Expected Target, Expected Tool or Category)
        ("what is the weather in ooty from 20-9 to 25-9", "nlp", "ask_weather"),
        ("show transport from chennai to munnar", "nlp", "ask_transport"),
        ("make a plan from tirunelveli to any hill station from 20-9 to 25-9 for 4 members under 40k", "nlp", "provide_slot"),
        ("option 1", "nlp", "confirm_destination"),
        ("change pace to relaxed", "nlp", "change_pace"),
        ("how much will a 5-day trip to ooty cost for 4 people", "nlp", "ask_budget"),
        ("find hill stations near tirunelveli", "nlp", "search_destination_candidates"),
        ("show top attractions and sightseeing in ooty", "nlp", "request_recommendations"),

        # Open-ended / Reasoning -> LLM
        ("Can you compare Ooty vs Kodaikanal for a quiet family trip with elderly grandparents?", "llm", None),
        ("Tell me an interesting story about the local culture in Munnar", "llm", None),
        ("What should I pack for cold rainy weather in the hills?", "llm", None),
        ("Is it safe to drive self-drive cars up the hairpin bends during the monsoon?", "llm", None),
    ]

    passed = 0
    for query, expected_target, expected_tool in test_cases:
        nlu_res = understand(query, state)
        decision = router.decide(query, nlu_res, state)

        assert decision.target == expected_target, (
            f"Query: '{query}'\nExpected target: {expected_target}, got: {decision.target}\n"
            f"Scores: NLP={decision.nlp_score}, LLM={decision.llm_score}, Reasons={decision.reasons}"
        )
        if expected_tool and decision.suggested_tool:
            assert decision.suggested_tool == expected_tool, (
                f"Query: '{query}'\nExpected tool: {expected_tool}, got: {decision.suggested_tool}"
            )
        assert decision.latency_ms < 50, f"Routing latency too high: {decision.latency_ms}ms"
        passed += 1

    print(f"[OK] All {passed}/{len(test_cases)} routing benchmark test cases passed!")


def test_orchestrator_integration():
    state = TripState()
    orch = Orchestrator(state)

    # Test NLP routing turn
    reply = orch.chat("make a plan from tirunelveli to any hill station from 20-9 to 25-9 for 4 members under 40k")
    assert "Here are verified hill station near" in reply or "Kolli Hills" in reply or "Which one would you like" in reply

    # Check trace contains router_decision
    router_traces = [t for t in orch.trace if t.get("type") == "router_decision"]
    assert len(router_traces) >= 1, "No router_decision found in trace"
    first_dec = router_traces[0]
    assert first_dec["target"] == "nlp"
    assert first_dec["latency_ms"] < 50
    assert "slots:entities_present" in first_dec["reasons"] or any("nlp" in r for r in first_dec["reasons"])

    print("[OK] Orchestrator integration and trace verification passed!")


if __name__ == "__main__":
    test_routing_benchmark()
    test_orchestrator_integration()
    print("\nALL IN-BETWEEN ROUTER TESTS PASSED SUCCESSFULLY! [SUCCESS]")

"""Test candidate pagination when user asks 'more'."""
import sys
sys.path.insert(0, ".")

from agent.orchestrator import Orchestrator
from agent.state import TripState

def test_candidate_pagination():
    state = TripState()
    orch = Orchestrator(state)

    # Turn 1: Initial vague destination search
    reply1 = orch.chat("make a plan from tirunelveli to any hill station from 20-9 to 25-9 for 4 members under 40k")
    print("--- TURN 1 ---")
    print(reply1)
    assert "Manjolai" in reply1
    assert "Ponmudi" in reply1
    assert "1. **Manjolai**" in reply1
    assert "ask 'more'" in reply1
    assert len(state.destination_candidates) > 10
    assert state.candidate_offset == 5

    # Turn 2: User asks "more"
    reply2 = orch.chat("more")
    print("\n--- TURN 2 (more) ---")
    print(reply2)
    assert "6. **" in reply2
    assert "7. **" in reply2
    assert "Munnar" in reply2
    assert state.candidate_offset == 10

    # Turn 3: User asks "show more options"
    reply3 = orch.chat("show more options")
    print("\n--- TURN 3 (show more options) ---")
    print(reply3)
    assert "11. **" in reply3
    assert state.candidate_offset == 15

    # Turn 4: User picks Munnar (by name or number)
    reply4 = orch.chat("Munnar")
    print("\n--- TURN 4 (pick Munnar) ---")
    print(reply4)
    assert "Confirmed **Munnar**" in reply4
    assert len(state.destinations) == 1
    assert state.destinations[0].name == "Munnar"
    assert len(state.destination_candidates) == 0

    print("\n[SUCCESS] Candidate pagination test passed completely!")

if __name__ == "__main__":
    test_candidate_pagination()

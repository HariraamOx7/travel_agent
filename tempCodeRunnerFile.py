import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from agent.db import load_state, save_state
from agent.orchestrator import Orchestrator
from agent.state import TripState


def main() -> None:
    session_id = input("Session id (Enter for new): ").strip() or uuid.uuid4().hex[:8]
    state = load_state(session_id) or TripState()
    orch = Orchestrator(state)

    print(f"[session {session_id}] loaded state:\n{orch.state.summary_for_prompt()}\n")
    print("Commands: 'state' = dump state, 'trace' = ReAct log, 'quit' = exit.\n")

    while True:
        try:
            user_text = input("you   > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text:
            continue
        if user_text == "quit":
            break
        if user_text == "state":
            print(orch.state.summary_for_prompt(), "\n")
            continue
        if user_text == "trace":
            print(orch.pretty_trace, "\n")
            continue

        # Deterministic shortcut: numeric pick while candidates are active
        # bypasses the LLM entirely — structured sub-dialog, structured code.
        if user_text.isdigit() and orch.state.destination_candidates:
            from agent import tools as agent_tools
            res = agent_tools.confirm_destination({"choice": user_text}, orch.state)
            if "error" in res:
                print(f"agent > Could not confirm: {res['error']}\n")
                continue
            d = orch.state.destinations[0]
            print(f"agent > Confirmed: {d.name} ({d.lat:.2f}, {d.lng:.2f})\n")

            # Advance stage so the next LLM turn knows we're past collecting.
            if not orch.state.missing_required():
                orch.state.stage = "recommending"
                orch.state.pending_destination_query = None
                orch.state.destination_candidates = []

            save_state(session_id, orch.state)
            continue

        # ← THIS IS THE MISSING BLOCK
        reply = orch.chat(user_text)
        print(f"agent > {reply}\n")
        save_state(session_id, orch.state)   # persist after every turn


if __name__ == "__main__":
    main()
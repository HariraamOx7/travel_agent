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
        

        # ← THIS IS THE MISSING BLOCK
        reply = orch.chat(user_text)
        print(f"agent > {reply}\n")
        save_state(session_id, orch.state)   # persist after every turn


if __name__ == "__main__":
    main()
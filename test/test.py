# temp file: debug_chat.py  (run from repo root, in the same venv)
from agent.db import load_state
from agent.orchestrator import Orchestrator
from agent.state import TripState

sid = input("session id: ").strip()
state = load_state(sid) or TripState()
orch = Orchestrator(state)
print("--- state ---")
print(state.summary_for_prompt())
print("--- calling chat('ok plan it') ---")
reply = orch.chat("ok plan it")
print("--- reply ---")
print(reply)

from agent.db import save_state
save_state(sid, orch.state)          # <-- ADD THIS

print("--- trace ---")
print(orch.pretty_trace)
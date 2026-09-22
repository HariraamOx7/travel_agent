"""Test end-to-end chat persistence in SQLite and FastAPI endpoints."""
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
from server.main import app, _sessions
from agent.db import load_session, list_sessions

client = TestClient(app)

def test_chat_persistence():
    print("--- 1. Creating a new session ---")
    resp = client.post("/api/sessions", json={"origin": "Tirunelveli"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    sid = data["session_id"]
    print(f"Created session {sid}, messages: {data.get('messages')}")

    print("\n--- 2. Sending Chat Turn 1 ---")
    # This should be handled by NLP router (deterministic) or LLM
    resp1 = client.post(f"/api/sessions/{sid}/chat", json={"message": "plan a trip to munnar"})
    assert resp1.status_code == 200, resp1.text
    d1 = resp1.json()
    print(f"Turn 1 reply: {d1['reply'][:80]}...")
    print(f"Turn 1 messages count: {len(d1.get('messages', []))}")
    assert len(d1.get("messages", [])) >= 2
    assert d1["messages"][0]["role"] == "user"
    assert d1["messages"][0]["content"] == "plan a trip to munnar"
    assert d1["messages"][1]["role"] == "assistant"

    print("\n--- 3. Sending Chat Turn 2 ---")
    resp2 = client.post(f"/api/sessions/{sid}/chat", json={"message": "from 20-9 to 25-9 for 4 members"})
    assert resp2.status_code == 200, resp2.text
    d2 = resp2.json()
    print(f"Turn 2 reply: {d2['reply'][:80]}...")
    print(f"Turn 2 messages count: {len(d2.get('messages', []))}")
    assert len(d2.get("messages", [])) >= 4

    print("\n--- 4. Simulating Server Restart / Cache Eviction ---")
    # Clear in-memory cache
    _sessions.clear()
    assert sid not in _sessions

    print("\n--- 5. GET /api/sessions/{sid} (Restoring from SQLite) ---")
    resp_get = client.get(f"/api/sessions/{sid}")
    assert resp_get.status_code == 200, resp_get.text
    d_get = resp_get.json()
    restored_msgs = d_get.get("messages", [])
    print(f"Restored messages count: {len(restored_msgs)}")
    assert len(restored_msgs) >= 4
    for idx, m in enumerate(restored_msgs):
        print(f"  [{idx}] {m['role'].upper()}: {m['content'][:60]}... (routing: {m.get('routing', {}).get('target') if isinstance(m.get('routing'), dict) else None})")

    print("\n--- 6. Checking list_sessions() message_count ---")
    sessions_list = list_sessions()
    target_session = next((s for s in sessions_list if s["session_id"] == sid), None)
    assert target_session is not None
    print(f"Session {sid} in list: message_count = {target_session.get('message_count')}")
    assert target_session.get("message_count") >= 4

    print("\n[SUCCESS] All chat persistence tests passed successfully!")

if __name__ == "__main__":
    test_chat_persistence()

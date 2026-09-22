import json
import sqlite3
from typing import Optional, Tuple
from agent.state import TripState

DB_PATH = "sessions.db"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trip_state ("
    " session_id TEXT PRIMARY KEY,"
    " state_json TEXT NOT NULL,"
    " messages_json TEXT DEFAULT '[]',"
    " trace_json TEXT DEFAULT '[]',"
    " updated_at TEXT DEFAULT (datetime('now')))"
)


def _init_db(con: sqlite3.Connection) -> None:
    con.execute(_SCHEMA)
    cur = con.execute("PRAGMA table_info(trip_state)")
    cols = {row[1] for row in cur.fetchall()}
    if "messages_json" not in cols:
        con.execute("ALTER TABLE trip_state ADD COLUMN messages_json TEXT DEFAULT '[]'")
    if "trace_json" not in cols:
        con.execute("ALTER TABLE trip_state ADD COLUMN trace_json TEXT DEFAULT '[]'")


def save_session(
    session_id: str,
    state: TripState,
    messages: Optional[list[dict]] = None,
    trace: Optional[list[dict]] = None,
) -> None:
    """Save trip state, chat message history, and trace logs to SQLite."""
    con = sqlite3.connect(DB_PATH)
    _init_db(con)
    messages_json = json.dumps(messages or [])
    trace_json = json.dumps(trace or [], default=str)
    con.execute(
        "INSERT INTO trip_state (session_id, state_json, messages_json, trace_json) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE "
        "SET state_json = excluded.state_json, "
        "    messages_json = excluded.messages_json, "
        "    trace_json = excluded.trace_json, "
        "    updated_at = datetime('now')",
        (session_id, state.model_dump_json(), messages_json, trace_json),
    )
    con.commit()
    con.close()


def load_session(session_id: str) -> Tuple[Optional[TripState], list[dict], list[dict]]:
    """Load trip state, chat messages, and trace logs from SQLite."""
    con = sqlite3.connect(DB_PATH)
    _init_db(con)
    row = con.execute(
        "SELECT state_json, messages_json, trace_json FROM trip_state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    con.close()
    if not row:
        return None, [], []

    try:
        state = TripState.model_validate_json(row[0])
    except Exception:
        state = None

    try:
        messages = json.loads(row[1]) if row[1] else []
    except Exception:
        messages = []

    try:
        trace = json.loads(row[2]) if row[2] else []
    except Exception:
        trace = []

    return state, messages, trace


# Backward compatibility wrappers
def save_state(session_id: str, state: TripState) -> None:
    save_session(session_id, state)


def load_state(session_id: str) -> TripState | None:
    state, _, _ = load_session(session_id)
    return state


def delete_state(session_id: str) -> bool:
    """Permanently remove a saved trip and return whether it existed."""
    con = sqlite3.connect(DB_PATH)
    _init_db(con)
    cursor = con.execute("DELETE FROM trip_state WHERE session_id = ?", (session_id,))
    con.commit()
    con.close()
    return cursor.rowcount > 0


def list_sessions(limit: int = 50) -> list[dict]:
    """Return recent sessions, newest first, as flat dicts for the UI."""
    con = sqlite3.connect(DB_PATH)
    _init_db(con)
    rows = con.execute(
        "SELECT session_id, state_json, messages_json, updated_at FROM trip_state "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()

    out = []
    for sid, js, msgs_js, updated in rows:
        try:
            s = TripState.model_validate_json(js)
        except Exception:
            continue

        try:
            msgs = json.loads(msgs_js) if msgs_js else []
        except Exception:
            msgs = []

        out.append({
            "session_id": sid,
            "destination": s.destinations[0].name if s.destinations else None,
            "origin": s.origin,
            "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "travellers": s.travellers,
            "stage": s.stage,
            "message_count": len(msgs),
            "updated_at": updated,
        })
    return out

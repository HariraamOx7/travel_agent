import sqlite3
from agent.state import TripState

DB_PATH = "sessions.db"
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trip_state ("
    " session_id TEXT PRIMARY KEY,"
    " state_json TEXT NOT NULL,"
    " updated_at TEXT DEFAULT (datetime('now')))"
)

def save_state(session_id: str, state: TripState) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(_SCHEMA)
    con.execute(
        "INSERT INTO trip_state (session_id, state_json) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO UPDATE "
        "SET state_json = excluded.state_json, updated_at = datetime('now')",
        (session_id, state.model_dump_json()),
    )
    con.commit()
    con.close()

def load_state(session_id: str) -> TripState | None:
    con = sqlite3.connect(DB_PATH)
    con.execute(_SCHEMA)
    row = con.execute(
        "SELECT state_json FROM trip_state WHERE session_id = ?", (session_id,)
    ).fetchone()
    con.close()
    return TripState.model_validate_json(row[0]) if row else None

def delete_state(session_id: str) -> bool:
    """Permanently remove a saved trip and return whether it existed."""
    con = sqlite3.connect(DB_PATH)
    con.execute(_SCHEMA)
    cursor = con.execute("DELETE FROM trip_state WHERE session_id = ?", (session_id,))
    con.commit()
    con.close()
    return cursor.rowcount > 0

def list_sessions(limit: int = 50) -> list[dict]:
    """Return recent sessions, newest first, as flat dicts for the UI.

    Parsing failures are skipped silently so one corrupt row doesn't break
    the whole list.
    """
    con = sqlite3.connect(DB_PATH)
    con.execute(_SCHEMA)
    rows = con.execute(
        "SELECT session_id, state_json, updated_at FROM trip_state "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()

    out = []
    for sid, js, updated in rows:
        try:
            s = TripState.model_validate_json(js)
        except Exception:
            continue
        out.append({
            "session_id": sid,
            "destination": s.destinations[0].name if s.destinations else None,
            "origin": s.origin,
            "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "travellers": s.travellers,
            "stage": s.stage,
            "updated_at": updated,
        })
    return out

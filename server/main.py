"""FastAPI backend for the travel-agent React frontend.

Wraps the existing agent.orchestrator.Orchestrator without modification.
Session state is cached in memory and persisted to SQLite via agent.db.
"""
import os
import uuid
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.db import delete_state, load_state, save_state, list_sessions
from agent.orchestrator import Orchestrator
from agent.state import TripState
from agent.discovery import _haversine_km


app = FastAPI(title="Travel Agent API")

# CORS: allow the Vite dev server during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite default
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory orchestrator cache. For a real deployment, use Redis or
# always reload from SQLite. This is fine for a single-process dev server.
_sessions: dict[str, Orchestrator] = {}


def _get_orch(session_id: str) -> Orchestrator:
    if session_id not in _sessions:
        state = load_state(session_id) or TripState()
        _sessions[session_id] = Orchestrator(state)
    return _sessions[session_id]


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

class CreateTripIn(BaseModel):
    origin: str
    destination: str
    start_date: str          # ISO YYYY-MM-DD
    end_date: str
    travellers: int = 1
    budget_total: Optional[float] = None


class ChatIn(BaseModel):
    message: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/sessions")
def sessions():
    return list_sessions()


@app.post("/api/sessions")
def create_session(body: CreateTripIn):
    from datetime import date as _date

    sid = uuid.uuid4().hex[:8]
    state = TripState(
        origin=body.origin.strip(),
        start_date=_date.fromisoformat(body.start_date),
        end_date=_date.fromisoformat(body.end_date),
        travellers=body.travellers,
        budget_total=body.budget_total,
    )
    orch = Orchestrator(state)
    _sessions[sid] = orch
    save_state(sid, state)
    return {"session_id": sid, "state": state.model_dump(mode="json")}


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    orch = _get_orch(sid)
    return {
        "session_id": sid,
        "state": orch.state.model_dump(mode="json"),
        "trace": orch.trace,
    }


@app.post("/api/sessions/{sid}/chat")
def chat(sid: str, body: ChatIn):
    orch = _get_orch(sid)
    try:
        reply = orch.chat(body.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    save_state(sid, orch.state)
    return {
        "reply": reply,
        "state": orch.state.model_dump(mode="json"),
        "trace": orch.trace,
    }


@app.get("/api/sessions/{sid}/map")
def map_data(sid: str):
    """Everything the map needs: origin, destination/candidates, itinerary stops."""
    orch = _get_orch(sid)
    s = orch.state

    out = {
        "origin": None,
        "destination": None,
        "candidates": [],
        "itinerary_stops": [],
    }

    if s.origin_coords:
        out["origin"] = {
            "name": s.origin,
            "lat": s.origin_coords["lat"],
            "lng": s.origin_coords["lng"],
        }

    if s.destinations and s.destinations[0].lat is not None:
        d = s.destinations[0]
        out["destination"] = {"name": d.name, "lat": d.lat, "lng": d.lng}

    for c in s.destination_candidates:
        if c.lat is None or c.lng is None:
            continue
        distance_km = None
        if s.origin_coords:
            distance_km = round(_haversine_km(
                s.origin_coords["lat"], s.origin_coords["lng"],
                c.lat, c.lng,
            ), 1)
        out["candidates"].append({
            "name": c.name,
            "lat": c.lat,
            "lng": c.lng,
            "distance_km": distance_km,
        })

    # If itinerary exists, resolve stop coords from recommendations.
    if s.itinerary and s.recommendations:
        coords_by_name = {
            a["name"]: (a["lat"], a["lng"])
            for a in (s.recommendations.get("attractions") or [])
            if a.get("lat") is not None
        }
        for day_idx, day in enumerate(s.itinerary.get("days", [])):
            stops = []
            for stop in day.get("stops", []):
                coord = coords_by_name.get(stop["name"])
                if coord:
                    stops.append({
                        "name": stop["name"],
                        "lat": coord[0],
                        "lng": coord[1],
                    })
            out["itinerary_stops"].append({
                "day": day_idx + 1,
                "date": day.get("date"),
                "stops": stops,
            })

    return out


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    _sessions.pop(sid, None)
    deleted = delete_state(sid)
    return {"deleted": sid, "existed": deleted}


# Serve the built React app in production.
# In development, Vite serves it on :5173 and proxies /api to :8000.
if os.path.isdir("web/dist"):
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="web")

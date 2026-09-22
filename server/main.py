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

from agent.db import (
    delete_state,
    load_state,
    save_state,
    list_sessions,
    load_session,
    save_session,
)
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
        state, messages, trace = load_session(session_id)
        _sessions[session_id] = Orchestrator(
            state or TripState(),
            client_messages=messages,
            trace=trace,
        )
    return _sessions[session_id]


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

class CreateTripIn(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    start_date: Optional[str] = None          # ISO YYYY-MM-DD
    end_date: Optional[str] = None
    travellers: int = 1
    budget_total: Optional[float] = None


class ChatIn(BaseModel):
    message: str


class IdeasIn(BaseModel):
    exclude: list[str] = []
    include: list[str] = []


class MoveStopIn(BaseModel):
    stop_name: str
    from_day: int                     # 0-based index into itinerary.days
    to_day: int
    to_index: Optional[int] = None    # position within the target day


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
def create_session(body: Optional[CreateTripIn] = None):
    from datetime import date as _date

    sid = uuid.uuid4().hex[:8]
    state = TripState()
    if body:
        if body.origin:
            state.origin = body.origin.strip()
        if body.start_date:
            try:
                state.start_date = _date.fromisoformat(body.start_date)
            except (ValueError, TypeError):
                pass
        if body.end_date:
            try:
                state.end_date = _date.fromisoformat(body.end_date)
            except (ValueError, TypeError):
                pass
        if body.travellers:
            state.travellers = body.travellers
        if body.budget_total is not None:
            state.budget_total = body.budget_total
        if body.destination:
            from agent.state import DestinationCandidate
            state.destination_candidates = [DestinationCandidate(name=body.destination.strip())]

    orch = Orchestrator(state)
    _sessions[sid] = orch
    save_session(sid, state, orch.client_messages, orch.trace)
    return {
        "session_id": sid,
        "state": state.model_dump(mode="json"),
        "messages": orch.client_messages,
    }


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    orch = _get_orch(sid)
    return {
        "session_id": sid,
        "state": orch.state.model_dump(mode="json"),
        "trace": orch.trace,
        "messages": orch.client_messages,
    }


@app.post("/api/sessions/{sid}/chat")
def chat(sid: str, body: ChatIn):
    orch = _get_orch(sid)
    try:
        reply = orch.chat(body.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    save_session(sid, orch.state, orch.client_messages, orch.trace)
    latest_routing = next(
        (t for t in reversed(orch.trace) if t.get("type") == "router_decision"),
        None,
    )
    return {
        "reply": reply,
        "state": orch.state.model_dump(mode="json"),
        "trace": orch.trace,
        "messages": orch.client_messages,
        "routing": latest_routing,
    }


@app.post("/api/sessions/{sid}/ideas")
def edit_ideas(sid: str, body: IdeasIn):
    """User-driven idea edits: remove attractions (or bring them back) and
    rebuild the itinerary against the same state. One round trip keeps the
    chat, map and itinerary in sync because everything reads the same state."""
    orch = _get_orch(sid)
    s = orch.state
    if not s.itinerary:
        raise HTTPException(
            status_code=400,
            detail="no itinerary yet — build one first (ask the agent to plan the days)",
        )

    from agent import tools as tools_mod

    excluded = {n.lower() for n in s.excluded_names}
    for name in body.exclude:
        excluded.add(name.lower())
    for name in body.include:
        excluded.discard(name.lower())
    s.excluded_names = sorted(excluded)

    result = tools_mod.build_itinerary({}, s)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    save_session(sid, s, orch.client_messages, orch.trace)
    return {
        "state": s.model_dump(mode="json"),
        "schedule": result.get("schedule"),
        "unscheduled": result.get("unscheduled"),
        "excluded": result.get("excluded"),
    }


@app.post("/api/sessions/{sid}/itinerary/move")
def move_stop(sid: str, body: MoveStopIn):
    """Itinerary drag-and-drop: move one stop between days (or reorder it)
    and re-time only the affected days. Instant and offline — no LLM and
    no road-matrix call; the clock is rebuilt with the haversine fallback."""
    orch = _get_orch(sid)
    s = orch.state
    if not s.itinerary:
        raise HTTPException(
            status_code=400,
            detail="no itinerary yet — build one first", 
        )

    from agent import tools as tools_mod

    result = tools_mod.move_stop({
        "stop_name": body.stop_name,
        "from_day": body.from_day,
        "to_day": body.to_day,
        "to_index": body.to_index,
    }, s)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    save_session(sid, s, orch.client_messages, orch.trace)
    return {"state": s.model_dump(mode="json"), "moved": result}


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
# In development, Vite serves it on :5173 and proxies /api to :8100.
if os.path.isdir("web/dist"):
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="web")


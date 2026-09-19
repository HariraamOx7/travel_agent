"""Folium map rendering for the Streamlit UI.

Renders the current trip's origin, destination (or candidate list), and
an optional dashed route line. All tiles are OpenStreetMap — no API key,
no Mapbox account, no billing.

Design notes:
  - Before confirmation: candidates render as numbered black circles that
    match the numbered list in the chat and in the side panel.
  - After confirmation: origin is a red home pin, destination is a green
    flag pin, and a dashed orange line connects them.
  - The map auto-centres on the destination if one exists, otherwise on
    the first candidate, otherwise on the origin.
"""
from typing import Optional

import folium
import streamlit as st
from streamlit_folium import st_folium

from agent.discovery import _haversine_km


# Brand colours reused from app_streamlit.py — keep these in sync if you
# change the global CSS.
_COLOR_ORIGIN = "#d1495b"        # red pin
_COLOR_DESTINATION = "#3f9b5f"   # green pin
_COLOR_ROUTE = "#ff8a3d"         # dashed route line
_COLOR_CANDIDATE_BG = "#1c1c1e"
_COLOR_CANDIDATE_BORDER = "#4a4a4c"


def _numbered_icon(index: int) -> folium.DivIcon:
    """A small round pin with the candidate's 1-based index."""
    html = (
        f'<div style="'
        f'background:{_COLOR_CANDIDATE_BG};'
        f'color:#fff;'
        f'border:2px solid {_COLOR_CANDIDATE_BORDER};'
        f'border-radius:50%;'
        f'width:24px;height:24px;'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:0.72rem;font-weight:600;'
        f'font-family:system-ui,-apple-system,sans-serif;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.4);'
        f'">{index}</div>'
    )
    return folium.DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))


def _centre_for(state) -> Optional[tuple[float, float]]:
    """Pick the map centre from available state, in priority order."""
    if state.destinations and state.destinations[0].lat is not None:
        d = state.destinations[0]
        return (d.lat, d.lng)
    for c in state.destination_candidates:
        if c.lat is not None and c.lng is not None:
            return (c.lat, c.lng)
    if state.origin_coords:
        return (state.origin_coords["lat"], state.origin_coords["lng"])
    return None


def _build_map(state) -> Optional[folium.Map]:
    """Construct the folium map for the current trip state.

    Returns None if there is nothing to plot.
    """
    centre = _centre_for(state)
    if centre is None:
        return None

    m = folium.Map(
        location=centre,
        zoom_start=6,
        tiles="OpenStreetMap",
        control_scale=True,
        attribution_control=True,
    )

    # --- Origin ---------------------------------------------------------
    if state.origin_coords:
        o_lat = state.origin_coords["lat"]
        o_lng = state.origin_coords["lng"]
        folium.Marker(
            location=(o_lat, o_lng),
            tooltip=f"Origin: {state.origin or 'Unknown'}",
            popup=folium.Popup(
                f"<b>{state.origin or 'Origin'}</b>",
                max_width=200,
            ),
            icon=folium.Icon(color="red", icon="home", prefix="fa"),
        ).add_to(m)

    # --- Confirmed destination ------------------------------------------
    if state.destinations and state.destinations[0].lat is not None:
        d = state.destinations[0]
        folium.Marker(
            location=(d.lat, d.lng),
            tooltip=d.name,
            popup=folium.Popup(f"<b>{d.name}</b>", max_width=200),
            icon=folium.Icon(color="green", icon="flag", prefix="fa"),
        ).add_to(m)

        # Dashed route from origin if we have both endpoints.
        if state.origin_coords:
            folium.PolyLine(
                locations=[
                    (state.origin_coords["lat"], state.origin_coords["lng"]),
                    (d.lat, d.lng),
                ],
                color=_COLOR_ROUTE,
                weight=2,
                opacity=0.8,
                dash_array="6 8",
            ).add_to(m)

    # --- Candidate list (before confirmation) ---------------------------
    elif state.destination_candidates:
        for i, c in enumerate(state.destination_candidates, start=1):
            if c.lat is None or c.lng is None:
                continue

            # Show distance from origin in the popup when available.
            distance_line = ""
            if state.origin_coords:
                km = _haversine_km(
                    state.origin_coords["lat"], state.origin_coords["lng"],
                    c.lat, c.lng,
                )
                distance_line = f"<br/><span style='color:#888'>{km:.0f} km from {state.origin or 'origin'}</span>"

            folium.Marker(
                location=(c.lat, c.lng),
                tooltip=f"{i}. {c.name}",
                popup=folium.Popup(
                    f"<b>{i}. {c.name}</b>{distance_line}",
                    max_width=220,
                ),
                icon=_numbered_icon(i),
            ).add_to(m)
        # --- Itinerary stops (once built) ----------------------------------
    if state.itinerary:
        coords_by_name = {
            a["name"]: (a["lat"], a["lng"])
            for a in (state.recommendations or {}).get("attractions", [])
            if a.get("lat") is not None
        }
        for day_idx, day in enumerate(state.itinerary.get("days", [])):
            stop_coords = [
                coords_by_name[s["name"]]
                for s in day["stops"]
                if s["name"] in coords_by_name
            ]
            for (lat, lng), s in zip(stop_coords, day["stops"]):
                folium.CircleMarker(
                    location=(lat, lng),
                    radius=5,
                    color="#1c1c1e",
                    weight=2,
                    fill=True,
                    fill_color="#ffffff",
                    fill_opacity=1,
                    tooltip=f"Day {day_idx + 1}: {s['name']}",
                ).add_to(m)
            if len(stop_coords) > 1:
                folium.PolyLine(
                    stop_coords,
                    color="#4a90d9",
                    weight=2,
                    opacity=0.7,
                ).add_to(m)
    return m


def render_map(height: int = 360) -> None:
    """Render the map inline. Shows an info box if there is nothing to show."""
    state = st.session_state.orch.state
    m = _build_map(state)

    if m is None:
        st.info("No location yet — pick an origin or a destination to see the map.")
        return

    st_folium(
        m,
        use_container_width=True,
        height=height,
        returned_objects=[],   # we don't need click events back
        key="trip_map",
    )


def render_map_and_picker(height: int = 360) -> None:
    """Split view: map on the left, candidate list on the right.

    Clicking a candidate confirms it via the same tool the LLM uses, so
    state stays consistent whether the user picks via chat, via NLU
    shortcut, or via this panel.
    """
    state = st.session_state.orch.state

    left, right = st.columns([3, 2], gap="large")

    with left:
        render_map(height=height)

    with right:
        _render_picker(state)


def _render_picker(state) -> None:
    """The right-hand column: candidate buttons or confirmed summary."""
    if state.destinations:
        d = state.destinations[0]
        dist_line = ""
        if state.origin_coords:
            km = _haversine_km(
                state.origin_coords["lat"], state.origin_coords["lng"],
                d.lat, d.lng,
            )
            dist_line = f" · {km:.0f} km from {state.origin or 'origin'}"
        st.markdown(
            f"""
            <div class="hotel-card" style="margin-bottom:12px;">
              <div class="hotel-thumb">📍</div>
              <div class="stop-body">
                <div class="hotel-name">{d.name}</div>
                <div class="hotel-label">Confirmed destination{dist_line}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if not state.destination_candidates:
        st.caption("No candidates yet. Ask the agent to suggest destinations.")
        return

    st.markdown(
        '<div class="chat-title">Pick a destination</div>',
        unsafe_allow_html=True,
    )

    for i, c in enumerate(state.destination_candidates, start=1):
        dist = ""
        if state.origin_coords and c.lat is not None and c.lng is not None:
            km = _haversine_km(
                state.origin_coords["lat"], state.origin_coords["lng"],
                c.lat, c.lng,
            )
            dist = f"  ·  {km:.0f} km"

        label = f"{i}.  {c.name}{dist}"
        if st.button(
            label,
            key=f"map_pick_{i}",
            use_container_width=True,
        ):
            _confirm_from_panel(i)


def _confirm_from_panel(index: int) -> None:
    """Confirm candidate by index, save state, rerun the page.

    Routes through the same agent.tools.confirm_destination function the
    LLM uses, so downstream behaviour (clearing pending_destination_query,
    emptying the candidate list, updating state.destinations) is identical.
    """
    from agent import tools
    from agent.db import save_state

    state = st.session_state.orch.state
    result = tools.confirm_destination({"choice": str(index)}, state)

    if "error" in result:
        st.error(result["error"])
        return

    save_state(st.session_state.session_id, state)
    st.rerun()
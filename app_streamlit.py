"""Streamlit UI — landing → create trip → two-pane builder.

Left pane: chat. Right pane: itinerary/ideas/bookings/calendar/chats/media tabs.
The orchestrator is unchanged; state persistence still goes through SQLite.

Visual style is inspired by a MindTrip-like reference: a trip header bar,
pill buttons (incl. Budget / Preferences), photo-style stop cards, and a
"Distances" toggle to show/hide the km/mi chips between stops.
"""
import html
import uuid
from datetime import date, datetime, timedelta

import streamlit as st

from dotenv import load_dotenv
load_dotenv()

from agent.db import load_state, save_state, list_sessions
from agent.orchestrator import Orchestrator
from agent.state import TripState
from agent.map_view import render_map_and_picker


st.set_page_config(page_title="Travel Agent", page_icon="✈️", layout="wide")

# --------------------------------------------------------------------------- #
# Global styles
# --------------------------------------------------------------------------- #

st.markdown("""
<style>
/* ---------- trip header bar ---------- */
.trip-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 4px 14px 4px; border-bottom: 1px solid #262628;
  margin-bottom: 0.75rem;
}
.trip-header .left { display: flex; align-items: center; gap: 10px; }
.trip-header .thumb {
  width: 34px; height: 34px; border-radius: 8px;
  background: linear-gradient(135deg, #ff8a3d, #d1495b);
  display: flex; align-items: center; justify-content: center; font-size: 1rem;
}
.trip-header .title { font-weight: 700; font-size: 1.02rem; color: #fff; line-height: 1.1; }
.trip-header .subtitle { font-size: 0.74rem; color: #888; }

.pill-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 1rem; }
.pill {
  background: #1c1c1e; color: #eaeaea; border: 1px solid #2c2c2e;
  padding: 6px 14px; border-radius: 18px; font-size: 0.82rem;
}
.pill.muted { color: #888; }

.day-label {
  display: flex; align-items: baseline; gap: 12px;
  margin: 1.25rem 0 0.6rem 0; padding-bottom: 0.4rem;
  border-bottom: 1px solid #2a2a2c;
}
.day-label .day-num { font-size: 1rem; font-weight: 600; color: #fff; }
.day-label .day-theme { font-size: 0.9rem; color: #cfcfcf; }
.day-label .day-date { font-size: 0.82rem; color: #888; margin-left: auto; }

/* ---------- photo-style stop card ---------- */
.stop-card {
  display: flex; gap: 12px; padding: 10px 12px;
  background: #1a1a1c; border: 1px solid #262628;
  border-radius: 12px; align-items: center;
}
.stop-thumb {
  width: 56px; height: 56px; border-radius: 8px; flex-shrink: 0;
  background-size: cover; background-position: center;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.4rem; color: #eee;
}
.stop-body { flex: 1; min-width: 0; }
.stop-name { font-weight: 600; font-size: 0.93rem; color: #f5f5f5; display: flex; align-items: center; gap: 6px; }
.stop-meta { font-size: 0.78rem; color: #929292; margin-top: 3px; display: flex; align-items: center; gap: 5px; }
.stop-badge {
  font-size: 0.64rem; padding: 2px 7px; border-radius: 5px;
  background: #2a2a2c; color: #b0b0b0; text-transform: uppercase;
  letter-spacing: 0.03em;
}

.dist-chip { font-size: 0.74rem; color: #6a6a6a; padding: 4px 0 4px 68px; }

.meal-card {
  display: flex; gap: 12px; padding: 10px 12px;
  background: #1e1a14; border: 1px solid #2e261c;
  border-radius: 12px; align-items: center;
}
.meal-thumb {
  width: 56px; height: 56px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, #6b4a1e, #3a2a12);
  display: flex; align-items: center; justify-content: center; font-size: 1.3rem;
}
.meal-name { font-weight: 600; font-size: 0.93rem; color: #e8dcc8; }
.meal-label { font-size: 0.76rem; color: #9a8b70; margin-top: 3px; }

.hotel-card {
  display: flex; gap: 12px; padding: 10px 12px;
  background: #181c22; border: 1px solid #232a33;
  border-radius: 12px; align-items: center;
}
.hotel-thumb {
  width: 56px; height: 56px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, #3d5a80, #1b2735);
  display: flex; align-items: center; justify-content: center; font-size: 1.3rem;
}
.hotel-name { font-weight: 600; font-size: 0.93rem; color: #eef3f8; }
.hotel-label { font-size: 0.76rem; color: #8ba0b8; margin-top: 3px; }

.rest-day {
  padding: 12px 14px; border-radius: 10px;
  background: #191d1a; border: 1px dashed #2c352e;
  color: #7a8a80; font-size: 0.88rem;
}

.chat-title {
  font-size: 0.75rem; color: #888; text-transform: uppercase;
  letter-spacing: 0.08em; margin-bottom: 0.5rem;
  padding-bottom: 0.4rem; border-bottom: 1px solid #262628;
}

.section-count { color: #888; font-weight: 400; font-size: 0.85rem; margin-left: 6px; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #

def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _attach_session(session_id: str, state: TripState | None = None) -> None:
    state = state if state is not None else (load_state(session_id) or TripState())
    st.session_state.session_id = session_id
    st.session_state.orch = Orchestrator(state)
    st.session_state.messages = []


if "view" not in st.session_state:
    st.session_state.view = "landing"
if "show_distances" not in st.session_state:
    st.session_state.show_distances = True

# Only create an Orchestrator when we're actually in the builder view with
# a session_id. The landing screen doesn't need one, and creating one on
# every page load leaves blank rows in the DB.
if st.session_state.view == "builder" and "orch" not in st.session_state:
    if "session_id" in st.session_state:
        _attach_session(st.session_state.session_id)
    else:
        # Defensive — no session to resume, bounce to landing.
        st.session_state.view = "landing"

_KIND_ICON = {
    "viewpoint": "🏔️", "museum": "🏛️", "zoo": "🦁", "park": "🌳",
    "attraction": "📍", "restaurant": "🍽️", "cafe": "☕",
    "hotel": "🏨", "guest_house": "🏨", "resort": "🏨", "beach": "🏖️",
}
_CATEGORY_LABEL = {
    "viewpoint": "Viewpoint", "museum": "Museum", "zoo": "Zoo",
    "park": "Park", "attraction": "Attraction", "beach": "Beach",
}
_DAY_ICONS = ["🌅", "🚋", "🏝️", "🍜", "🛕", "🎒", "🌿"]


def _icon_for(kind: str) -> str:
    return _KIND_ICON.get((kind or "").lower(), "📍")


def _badge_for(kind: str) -> str:
    return _CATEGORY_LABEL.get((kind or "").lower(), "")


def _fmt_date(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso).date()
        return d.strftime("%a, %b %d")
    except Exception:
        return iso


_START_HOUR = 9
_MIN_PER_STOP = 60
_KMH = 30.0


def _times_for_day(stops: list[dict]) -> list[tuple[str, str]]:
    """Return a list of (start, end) formatted time strings for each stop."""
    t = datetime.combine(date.today(), datetime.min.time()).replace(hour=_START_HOUR)
    out = []
    for s in stops:
        start = t
        t = t + timedelta(minutes=_MIN_PER_STOP + (s.get("km_from_prev", 0) or 0) / _KMH * 60)
        out.append((start.strftime("%I:%M %p").lstrip("0"), t.strftime("%I:%M %p").lstrip("0")))
    return out


def _km_to_mi(km: float) -> float:
    return km * 0.621371


# --------------------------------------------------------------------------- #
# Create Trip dialog
# --------------------------------------------------------------------------- #

@st.dialog("What's the plan?", width="large")
def create_trip_dialog() -> None:
    origin = st.text_input("From", value="Chennai")
    where = st.text_input("Where", placeholder="e.g. Munnar — or 'hill stations near Chennai'")
    date_range = st.date_input(
        "When",
        value=(date.today() + timedelta(days=30), date.today() + timedelta(days=35)),
        min_value=date.today(),
    )
    c1, c2 = st.columns(2)
    with c1:
        who = st.number_input("Who", min_value=1, max_value=12, value=1, step=1)
    with c2:
        budget = st.number_input("Budget (INR, optional)",
                                 min_value=0, value=40000, step=5000)

    if st.button("Create trip", type="primary", use_container_width=True):
        if not where.strip():
            st.error("Please enter a destination.")
            return
        if not (isinstance(date_range, (list, tuple)) and len(date_range) == 2):
            st.error("Please pick both start and end dates.")
            return

        new_state = TripState(
            origin=origin.strip(),
            start_date=date_range[0],
            end_date=date_range[1],
            travellers=int(who),
            budget_total=float(budget) if budget > 0 else None,
        )
        new_id = _new_id()
        _attach_session(new_id, new_state)
        st.session_state.pending_where = where.strip()
        st.session_state.view = "builder"
        save_state(new_id, new_state)
        st.rerun()


@st.dialog("Budget", width="small")
def budget_dialog() -> None:
    s = st.session_state.orch.state
    current = float(s.budget_total) if s.budget_total else 0.0
    new_budget = st.number_input("Total trip budget (INR)", min_value=0.0,
                                  value=current, step=5000.0)
    if st.button("Save", type="primary", use_container_width=True):
        s.budget_total = new_budget if new_budget > 0 else None
        save_state(st.session_state.session_id, s)
        st.rerun()


@st.dialog("Preferences", width="small")
def preferences_dialog() -> None:
    s = st.session_state.orch.state
    pace_options = ["relaxed", "balanced", "packed"]
    current_pace = getattr(s, "pace", "balanced") or "balanced"
    pace = st.select_slider("Pace", options=pace_options,
                             value=current_pace if current_pace in pace_options else "balanced")
    if st.button("Save", type="primary", use_container_width=True):
        s.pace = pace
        save_state(st.session_state.session_id, s)
        st.rerun()


# --------------------------------------------------------------------------- #
# Landing
# --------------------------------------------------------------------------- #
def render_landing() -> None:
    # Header
    st.markdown(
        """
        <div style="text-align:center;padding:3rem 1rem 1rem 1rem;">
          <div style="font-size:3rem;">✈️</div>
          <h1 style="margin-bottom:0.5rem;">Plan your next trip</h1>
          <p style="color:#888;font-size:1.05rem;max-width:520px;margin:0 auto;">
            Tell me where, when, and who — I'll build a grounded itinerary
            with real POIs, live weather, and a cost estimate.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Create trip button
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        if st.button("➕  Create Trip", type="primary", use_container_width=True):
            create_trip_dialog()

    # Previous sessions
    sessions = list_sessions()
    if not sessions:
        return

    st.write("")
    st.write("")
    st.markdown("### Continue a trip")
    st.caption("Pick up where you left off — all state, recommendations, "
               "and itineraries are preserved.")

    # Status emoji per stage
    STAGE_EMOJI = {
        "collecting_requirements": "🟡 Collecting details",
        "recommending": "🟠 Recommendations ready",
        "scheduling": "🟢 Itinerary ready",
        "confirmed": "✅ Confirmed",
    }

    # Grid of cards — 3 per row
    cols = st.columns(3)
    for i, s in enumerate(sessions):
        col = cols[i % 3]
        with col:
            with st.container(border=True):
                title = s["destination"] or "Untitled trip"
                st.markdown(f"**{title}**")

                meta_lines = []
                if s["origin"]:
                    meta_lines.append(f"from {s['origin'].title()}")
                if s["start_date"] and s["end_date"]:
                    meta_lines.append(f"{s['start_date']} → {s['end_date']}")
                if s["travellers"] and s["travellers"] > 1:
                    meta_lines.append(f"{s['travellers']} travelers")
                if meta_lines:
                    st.caption("  ·  ".join(meta_lines))

                stage_label = STAGE_EMOJI.get(s["stage"], s["stage"])
                st.caption(f"{stage_label}")

                # Truncate the raw timestamp for readability
                updated = (s["updated_at"] or "")[:16]
                st.caption(f"🕒 {updated}")

                if st.button(
                    "Open",
                    key=f"open_{s['session_id']}",
                    use_container_width=True,
                ):
                    _attach_session(s["session_id"])
                    st.session_state.view = "builder"
                    st.rerun()

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

def _render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Session")
        st.code(st.session_state.session_id, language=None)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("All trips", use_container_width=True):
                st.session_state.view = "landing"
                st.session_state.messages = []
                st.rerun()
        with c2:
            if st.button("Reset", use_container_width=True):
                sid = st.session_state.session_id
                _attach_session(sid, TripState())
                save_state(sid, st.session_state.orch.state)
                st.rerun()

        with st.expander("State", expanded=False):
            st.json(st.session_state.orch.state.model_dump(mode="json"))

        with st.expander("Trace", expanded=False):
            trace = st.session_state.orch.trace
            if not trace:
                st.caption("(no tool calls yet)")
            for i, e in enumerate(trace):
                if e.get("type") == "tool_call":
                    st.markdown(f"**{i+1}. `{e['tool']}`**")
                    with st.expander("result"):
                        st.json(e.get("result"))
                elif e.get("type") == "final_answer":
                    st.markdown(f"**{i+1}. final answer**")
                    st.caption((e.get("text") or "")[:180] + "…")
                elif e.get("type") == "shortcut":
                    st.markdown(f"**{i+1}. shortcut — {e.get('intent')}**")
                    st.caption(f"source={e.get('source')} "
                               f"confidence={e.get('confidence')}")
                elif e.get("type") == "nlu_slots":
                    st.markdown(f"**{i+1}. nlu_slots — {e.get('intent')}**")
                    st.caption(f"source={e.get('source')} "
                               f"confidence={e.get('confidence')}")


# --------------------------------------------------------------------------- #
# Trip header bar (title + back/reset + destination/date/pax/budget/prefs)
# --------------------------------------------------------------------------- #

def _render_trip_header() -> None:
    s = st.session_state.orch.state
    dest = (s.origin or "Trip").title()

    left, right = st.columns([3, 1])
    with left:
        st.markdown(
            f"""
            <div class="trip-header">
              <div class="left">
                <div class="thumb">✈️</div>
                <div>
                  <div class="title">{html.escape(dest)} Trip Planning</div>
                  <div class="subtitle">Trip to {html.escape(dest)}</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        if st.button("⟵ Back to trips", use_container_width=True):
            st.session_state.view = "landing"
            st.rerun()


def _render_header_pills() -> None:
    s = st.session_state.orch.state
    pills = []
    if s.origin:
        pills.append(f"📍 {html.escape(s.origin.title())}")
    if s.start_date and s.end_date:
        pills.append(f"📅 {s.start_date.strftime('%b %d')} – {s.end_date.strftime('%b %d')}")
    if s.travellers:
        pills.append(f"👤 {s.travellers} traveler{'s' if s.travellers > 1 else ''}")
    pace = getattr(s, "pace", None)
    if pace and pace != "balanced":
        pills.append(f"⚡ {pace}")

    html_pills = "".join(f'<span class="pill">{p}</span>' for p in pills) \
        or '<span class="pill muted">No trip details yet</span>'

    p1, p2, p3 = st.columns([5, 1, 1])
    with p1:
        st.markdown(f'<div class="pill-row">{html_pills}</div>', unsafe_allow_html=True)
    with p2:
        if st.button("💰 Budget", use_container_width=True):
            budget_dialog()
    with p3:
        if st.button("⚙️ Preferences", use_container_width=True):
            preferences_dialog()


# --------------------------------------------------------------------------- #
# Timeline rendering (right column)
# --------------------------------------------------------------------------- #

def _render_stop_card(stop: dict, times: tuple[str, str]) -> None:
    name = html.escape(stop.get("name", "Unknown"))
    kind = stop.get("kind", "")
    icon = _icon_for(kind)
    badge = _badge_for(kind)
    badge_html = f'<span class="stop-badge">{badge}</span>' if badge else ""
    img_url = stop.get("image_url")
    thumb_style = f'background-image:url("{img_url}");' if img_url else ""
    thumb_content = "" if img_url else icon
    start, end = times
    st.markdown(
        f"""
        <div class="stop-card">
          <div class="stop-thumb" style="{thumb_style}">{thumb_content}</div>
          <div class="stop-body">
            <div class="stop-name">{name}{badge_html}</div>
            <div class="stop-meta">🕒 {start} – {end}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_meal_card(name: str) -> None:
    st.markdown(
        f"""
        <div class="meal-card">
          <div class="meal-thumb">🍽️</div>
          <div class="stop-body">
            <div class="meal-name">{html.escape(name)}</div>
            <div class="meal-label">Suggested lunch · ~1:00 PM</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_hotel_card(hotel: dict) -> None:
    name = html.escape(hotel.get("name", "Hotel"))
    checkin = hotel.get("checkin", "3:00 PM")
    nights = hotel.get("nights", 1)
    st.markdown(
        f"""
        <div class="hotel-card">
          <div class="hotel-thumb">🏨</div>
          <div class="stop-body">
            <div class="hotel-name">{name}</div>
            <div class="hotel-label">Check-in {html.escape(str(checkin))} · {nights} night{'s' if nights != 1 else ''}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_rest_day() -> None:
    st.markdown(
        '<div class="rest-day">🌿 Rest day — no scheduled stops.</div>',
        unsafe_allow_html=True,
    )


def _render_itinerary() -> None:
    _render_transport()
    s = st.session_state.orch.state
    if not s.itinerary:
        st.info("Itinerary hasn't been built yet. Ask the agent in the chat.")
        return

    days = s.itinerary.get("days", [])

    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.markdown(f"### Itinerary <span class='section-count'>{len(days)} days</span>",
                    unsafe_allow_html=True)
    with top_r:
        st.session_state.show_distances = st.toggle(
            "Distances", value=st.session_state.show_distances
        )

    for i, day in enumerate(days):
        theme = day.get("theme", "")
        icon = _DAY_ICONS[i % len(_DAY_ICONS)]
        theme_html = f'<span class="day-theme">{icon} {html.escape(theme)}</span>' if theme else ""
        st.markdown(
            f'<div class="day-label">'
            f'<span class="day-num">Day {i+1}</span>'
            f'{theme_html}'
            f'<span class="day-date">{_fmt_date(day["date"])}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if day.get("rest_day") or not day.get("stops"):
            _render_rest_day()
            continue

        stops = day["stops"]
        times = _times_for_day(stops)
        for j, stop in enumerate(stops):
            if j > 0 and st.session_state.show_distances:
                km = stop.get("km_from_prev", 0) or 0
                mi = _km_to_mi(km)
                st.markdown(f'<div class="dist-chip">↓ {mi:.2f} mi</div>',
                            unsafe_allow_html=True)
            _render_stop_card(stop, times[j])

        if day.get("lunch"):
            if st.session_state.show_distances:
                st.markdown('<div class="dist-chip">&nbsp;</div>', unsafe_allow_html=True)
            _render_meal_card(day["lunch"])

        if day.get("hotel"):
            if st.session_state.show_distances:
                st.markdown('<div class="dist-chip">&nbsp;</div>', unsafe_allow_html=True)
            _render_hotel_card(day["hotel"])

        rain = day.get("expected_rain_mm", 0)
        if rain and rain > 5:
            st.caption(f"🌧️ Expected rain: {rain} mm")


def _render_ideas() -> None:
    s = st.session_state.orch.state
    if not s.recommendations:
        st.info("Recommendations not loaded yet.")
        return
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**🏔️ Attractions**")
        for a in (s.recommendations.get("attractions") or []):
            st.markdown(f"- {a['name']}")
    with c2:
        st.markdown("**🍽️ Food**")
        for f in (s.recommendations.get("food") or []):
            st.markdown(f"- {f['name']}")
    with c3:
        st.markdown("**🏨 Stay**")
        for h in (s.recommendations.get("stay") or []):
            st.markdown(f"- {h['name']}")


def _render_bookings() -> None:
    st.info(
        "Booking integration is not wired up. Future work: flight/hotel deep "
        "links, price stubs, and a checklist of what to book when."
    )


def _render_hidden_gems() -> None:
    s = st.session_state.orch.state
    gems = (s.recommendations or {}).get("hidden_gems") or []
    if not gems:
        return
    st.markdown("#### 🌿 Hidden gems")
    st.caption("Less-documented, off-center, or rarer-category POIs from OSM.")
    for g in gems:
        st.markdown(
            f"- **{html.escape(g['name'])}**  ·  "
            f"{html.escape(g.get('kind','').replace('_',' '))}  ·  "
            f"{g.get('km_from_center','?')} km from center"
        )


def _render_calendar() -> None:
    s = st.session_state.orch.state
    if not s.itinerary:
        st.info("Nothing to show on a calendar yet — build an itinerary first.")
        return
    for i, day in enumerate(s.itinerary.get("days", [])):
        with st.expander(f"Day {i+1} · {_fmt_date(day['date'])}", expanded=False):
            if day.get("rest_day") or not day.get("stops"):
                st.caption("Rest day")
            else:
                for stop in day["stops"]:
                    st.markdown(f"- {stop.get('name', 'Unknown')}")


def _render_chats() -> None:
    st.info("This tab will hold shared trip chats/collaborators. Not wired up yet — "
            "use the Chat panel on the left for now.")


def _render_media() -> None:
    st.info("Photos and media saved to this trip will show up here. Not wired up yet.")


# --------------------------------------------------------------------------- #
# Chat column (left)
# --------------------------------------------------------------------------- #

def _render_chat_column() -> None:
    st.markdown('<div class="chat-title">Chat</div>', unsafe_allow_html=True)

    # Scrollable message container
    chat_box = st.container(height=520, border=False)
    with chat_box:
        if not st.session_state.messages:
            st.caption("Ask anything — 'plan it', 'make it more relaxed', "
                       "'swap Sunset Deck', 'how much will this cost?'")
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    # Input form inside the column
    with st.form("chat_form", clear_on_submit=True):
        user_text = st.text_input(
            "Message",
            placeholder="Ask for changes…",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Send", use_container_width=True)

    if submitted and user_text.strip():
        text = user_text.strip()
        st.session_state.messages.append({"role": "user", "content": text})
        with st.spinner("Thinking…"):
            try:
                reply = st.session_state.orch.chat(text)
            except Exception as e:
                reply = f"⚠️ {type(e).__name__}: {e}"
        st.session_state.messages.append({"role": "assistant", "content": reply})
        save_state(st.session_state.session_id, st.session_state.orch.state)
        st.rerun()


def _render_transport() -> None:
    """Small transport comparison block, shown at the top of Itinerary."""
    s = st.session_state.orch.state
    if not s.destinations or not s.origin:
        return
    # If mode already chosen, render a single pill.
    if s.travel_mode:
        st.markdown(
            f'<div class="pill-row">'
            f'<span class="pill">🚗 Travel mode: {html.escape(s.travel_mode.title())}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return
    # Otherwise, prompt to see options.
    if st.button("🚗 Compare transport modes", use_container_width=False):
        with st.spinner("Estimating costs…"):
            try:
                st.session_state.orch.chat("How should I get there?")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
        save_state(st.session_state.session_id, st.session_state.orch.state)
        st.rerun()


def _render_stay() -> None:
    s = st.session_state.orch.state
    stay = (s.recommendations or {}).get("stay") or []
    if not stay:
        st.info("No accommodation loaded yet.")
        return
    for h in stay:
        nightly = h.get("nightly_label") or (
            f"~₹{h.get('nightly_inr', 0):,}/night (est.)"
            if h.get("nightly_inr") else "Rate not estimated"
        )
        rating = h.get("rating_label") or "Not rated"
        meal = h.get("meal_plan") or "Meal plan not specified"
        kind = h.get("type") or h.get("kind") or "Stay"
        st.markdown(
            f"""
            <div class="hotel-card">
              <div class="hotel-thumb">🏨</div>
              <div class="stop-body">
                <div class="hotel-name">{html.escape(h['name'])}
                  <span class="stop-badge">{html.escape(kind)}</span>
                </div>
                <div class="hotel-label">
                  {html.escape(nightly)} · {html.escape(rating)} · {html.escape(meal)}
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.write("")


def _render_costs() -> None:
    s = st.session_state.orch.state
    if not s.itinerary:
        st.info("Build the itinerary first to see a cost estimate.")
        return
    # Compute on-demand via the tool.
    if st.button("💰 Compute cost estimate", use_container_width=False):
        with st.spinner("Estimating…"):
            try:
                st.session_state.orch.chat("What will this trip cost?")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
        save_state(st.session_state.session_id, st.session_state.orch.state)
        st.rerun()

    # If we have a prior budget payload cached in state, render it.
    est = (s.recommendations or {}).get("budget")
    if not est:
        st.caption("No estimate computed yet — click above.")
        return
    rows = est.get("line_items_inr", {})
    for label, amount in rows.items():
        st.markdown(
            f'<div class="pill-row" style="justify-content:space-between;">'
            f'<span class="pill">{html.escape(label.replace("_", " ").title())}</span>'
            f'<span class="pill">₹{amount:,}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div class="pill-row" style="justify-content:space-between;'
        f'border-top:1px solid #2a2a2c;padding-top:8px;margin-top:8px;">'
        f'<span class="pill"><b>Total</b></span>'
        f'<span class="pill"><b>₹{est.get("total_inr", 0):,}</b></span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if est.get("verdict"):
        st.success(est["verdict"])


# --------------------------------------------------------------------------- #
# Builder screen
# --------------------------------------------------------------------------- #

def render_builder() -> None:
    _render_sidebar()
    _render_trip_header()
    _render_header_pills()

    # Auto-plan on first entry from the create-trip dialog
    if st.session_state.get("pending_where"):
        where = st.session_state.pop("pending_where")
        orch = st.session_state.orch
        for user_msg, spinner in [
            (f"I want to go to {where}. Give me recommendations.",
             "Searching POIs and weather…"),
            ("Yes, build the day-by-day itinerary now.",
             "Solving the schedule…"),
        ]:
            st.session_state.messages.append({"role": "user", "content": user_msg})
            with st.spinner(spinner):
                try:
                    reply = orch.chat(user_msg)
                except Exception as e:
                    reply = f"⚠️ {type(e).__name__}: {e}"
            st.session_state.messages.append({"role": "assistant", "content": reply})
        save_state(st.session_state.session_id, orch.state)
        st.rerun()

    # Two-column layout: chat | tabs
    chat_col, trip_col = st.columns([1, 1.3], gap="large")

    with chat_col:
        _render_chat_column()

    with trip_col:
        tabs = st.tabs([
            "Map", "Itinerary", "Ideas", "Stay", "Costs",
            "Bookings", "Calendar", "Chats", "Media",
        ])

        with tabs[0]:
            try:
                render_map_and_picker(height=420)
            except Exception as e:
                import traceback
                st.error(f"Map failed: {type(e).__name__}: {e}")
                st.code(traceback.format_exc())

        with tabs[1]:
            _render_itinerary()
            st.divider()
            _render_hidden_gems()

        with tabs[2]:
            _render_ideas()

        with tabs[3]:
            _render_stay()

        with tabs[4]:
            _render_costs()

        with tabs[5]:
            _render_bookings()

        with tabs[6]:
            _render_calendar()

        with tabs[7]:
            _render_chats()

        with tabs[8]:
            _render_media()


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #

if st.session_state.view == "landing":
    render_landing()
else:
    render_builder()
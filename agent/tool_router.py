"""NLP-based Tool Router and Deterministic Dialog State Manager.

Replaces LLM ReAct tool-calling rounds with deterministic Natural Language
Understanding (Intent Classification + Slot Extraction + State-Guarded Dispatch).

When an utterance matches a known tool intent and the trip state satisfies its
preconditions, the tool is called directly in Python and a structured/templated
response is returned. This cuts latency from ~3-10s down to <50ms and saves 100%
of the LLM tokens for that turn.

Output formatting
-----------------
All formatters return PLAIN TEXT. No markdown syntax (no ###, **, *, `).
Both the CLI and the React frontend print these strings verbatim, so
markdown would leak through as literal characters. Emoji and whitespace
indentation are used for visual structure instead.
"""
from __future__ import annotations
import re
from typing import Any, Callable, Optional, Tuple

from agent.nlu import NLUResult
from agent.state import TripState
from agent import tools


# Pick-preamble stripper. The (+)+ group lets it strip multiple leading
# words — "choose option 2" reduces all the way to "2", not "option 2".
_PICK_PREAMBLE = re.compile(
    r"^(?:(?:let'?s\s+go\s+with|go\s+with|i'?ll\s+take|i\s+pick|i\s+choose|"
    r"choose|select|pick|option|number|#)\s+)+",
    re.I,
)
_ORDINAL_PICK = re.compile(
    r"^(?:the\s+)?(first|second|third|fourth|fifth|last)\s*(?:one|option)?\s*$",
    re.I,
)
_ORDINAL_MAP = {"first": "1", "second": "2", "third": "3",
                "fourth": "4", "fifth": "5"}


def _normalize_choice(text: str, n_candidates: int) -> str:
    """Return a value tools.confirm_destination can resolve: a digit or a name."""
    s = text.strip()

    m = _ORDINAL_PICK.match(s)
    if m:
        w = m.group(1).lower()
        if w == "last":
            return str(n_candidates)
        return _ORDINAL_MAP[w]

    s = _PICK_PREAMBLE.sub("", s, count=1).strip()
    return s


class ToolRouter:
    """Dispatches tool calls directly based on NLUResult and TripState."""

    def __init__(self, tool_impls: dict[str, Callable[[dict, TripState], dict]]):
        self.tool_impls = tool_impls

    def route_and_execute(
        self,
        user_text: str,
        nlu_result: NLUResult,
        state: TripState,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[dict]]:
        """Attempt to route user_text to a deterministic tool or dialog turn.

        Returns:
            (handled, response_text, tool_name, tool_result)
            If handled is False, the orchestrator should fall back to the LLM.
        """
        if not nlu_result.high_confidence and nlu_result.source != "rule" and not nlu_result.has_slots:
            return False, None, None, None

        intent = nlu_result.intent

        # ------------------------------------------------------------------ #
        # 1. Greet
        # ------------------------------------------------------------------ #
        if intent == "greet":
            return True, (
                "Hi! Tell me where you'd like to go, when, and who's coming — "
                "I'll build the trip from there."
            ), None, None

        # ------------------------------------------------------------------ #
        # 2. Confirm Destination
        # ------------------------------------------------------------------ #
        if intent == "confirm_destination":
            if state.destination_candidates:
                choice = _normalize_choice(user_text, len(state.destination_candidates))
                res = self.tool_impls["confirm_destination"]({"choice": choice}, state)

                if "error" not in res:
                    confirmed = res.get("confirmed", [])
                    names = [c["name"] for c in confirmed]
                    if len(names) == 1:
                        name_str = names[0]
                    elif len(names) == 2:
                        name_str = f"{names[0]} and {names[1]}"
                    else:
                        name_str = ", ".join(names[:-1]) + f", and {names[-1]}"

                    missing = res.get("missing_required") or []
                    if missing:
                        prompts = {
                            "origin": "Where will you be starting your trip from?",
                            "destinations": "Where would you like to travel?",
                            "dates": "What are your travel dates (start and end date)?",
                            "budget": "What is your approximate budget for the trip in INR?",
                        }
                        next_q = prompts.get(missing[0], f"Could you provide your {missing[0]}?")
                        reply = f"Confirmed {name_str} as your destination. Next — {next_q}"
                    else:
                        state.stage = "recommending"
                        reply = (
                            f"Confirmed {name_str}! All required details are set. "
                            "Would you like me to pull up attractions and stay recommendations?"
                        )
                    return True, reply, "confirm_destination", res

                return False, None, None, None

        # ------------------------------------------------------------------ #
        # 2b. Show More Destination Candidates (Pagination)
        # ------------------------------------------------------------------ #
        if intent == "show_more_candidates":
            if state.destination_candidates and not state.destinations:
                total = len(state.destination_candidates)
                start = getattr(state, "candidate_offset", 0)
                query_label = state.pending_destination_query or "destination"
                label_plural = query_label if query_label.endswith("s") else query_label + "s"

                if start >= total:
                    return True, (
                        f"You have already seen all {total} verified {label_plural} "
                        f"near {state.origin}.\n\n"
                        f"Which one would you like to pick? "
                        f"(Reply with name or number 1-{total})"
                    ), "show_more_candidates", {"offset": start, "total": total}

                end = min(start + 5, total)
                cands = state.destination_candidates[start:end]
                state.candidate_offset = end

                cand_lines = []
                for i, c in enumerate(cands):
                    num = start + i + 1
                    road = ", road" if c.road_distance_km else ""
                    cand_lines.append(
                        f"  {num}. {c.name}  ({c.distance_km} km away{road})"
                    )
                cand_text = "\n".join(cand_lines)

                remaining = total - end
                more_hint = (
                    f", or ask 'more' to see {remaining} more"
                    if remaining > 0 else ""
                )

                if start == 0:
                    header = f"Here are verified {label_plural} near {state.origin}:"
                else:
                    header = (
                        f"Continuing from item {start + 1} "
                        f"(of {total} total {label_plural}):"
                    )

                reply = (
                    f"{header}\n\n"
                    f"{cand_text}\n\n"
                    f"Which one would you like? "
                    f"(Reply with name or number{more_hint})"
                )
                return True, reply, "show_more_candidates", {
                    "offset": start,
                    "count": len(cands),
                    "total": total,
                    "remaining": remaining,
                }

            elif not state.destination_candidates:
                return True, (
                    "No destinations are currently being compared. "
                    "Where would you like to travel?"
                ), None, None

        # ------------------------------------------------------------------ #
        # 3. Weather
        # ------------------------------------------------------------------ #
        if intent == "ask_weather":
            dest = state.destinations[0] if state.destinations else None
            if not dest or dest.lat is None:
                if state.destination_candidates:
                    return True, (
                        "Please select your destination from the candidates "
                        "first so I can check the weather!"
                    ), None, None
                return True, (
                    "Where are you planning to travel? Let me know your "
                    "destination and dates to fetch the weather outlook."
                ), None, None

            if not (state.start_date and state.end_date):
                return True, (
                    f"What dates are you traveling to {dest.name}? "
                    "Share your start and end dates to see the forecast."
                ), None, None

            res = self.tool_impls["get_weather"]({}, state)
            if "error" in res:
                return True, (
                    f"Could not retrieve weather for {dest.name}: {res['error']}"
                ), "get_weather", res

            reply = self._format_weather(dest.name, res, state)
            return True, reply, "get_weather", res

        # ------------------------------------------------------------------ #
        # 4. Recommendations
        # ------------------------------------------------------------------ #
        if intent == "request_recommendations":
            dest = state.destinations[0] if state.destinations else None
            if not dest or dest.lat is None:
                if state.destination_candidates:
                    return True, (
                        "Please select one of the suggested destinations "
                        "first to view recommendations."
                    ), None, None
                return True, (
                    "Where would you like to go? Once we pick a destination, "
                    "I'll find top attractions, stays, and food spots!"
                ), None, None

            res = self.tool_impls["get_recommendations"]({}, state)
            if "error" in res:
                return True, (
                    f"Could not fetch recommendations: {res['error']}"
                ), "get_recommendations", res

            reply = self._format_recommendations(dest.name, res)
            return True, reply, "get_recommendations", res

        # ------------------------------------------------------------------ #
        # 5. Build Itinerary
        # ------------------------------------------------------------------ #
        if intent == "build_itinerary":
            dest = state.destinations[0] if state.destinations else None
            if not dest:
                return True, (
                    "Please confirm a destination and dates first "
                    "before building the itinerary."
                ), None, None

            if not state.recommendations or not state.recommendations.get("attractions"):
                self.tool_impls["get_recommendations"]({}, state)

            res = self.tool_impls["build_itinerary"]({}, state)
            if "error" in res:
                return True, (
                    f"Could not build itinerary: {res['error']}"
                ), "build_itinerary", res

            reply = self._format_itinerary(dest.name, res, state)
            return True, reply, "build_itinerary", res

        # ------------------------------------------------------------------ #
        # 6. Transport
        # ------------------------------------------------------------------ #
        if intent == "ask_transport":
            dest = state.destinations[0] if state.destinations else None
            if not state.origin:
                return True, (
                    "Where will you be traveling from? Let me know your "
                    "origin city to compare transport options."
                ), None, None
            if not dest:
                return True, (
                    "Where is your destination? Confirm a place to see "
                    "flight, train, bus, and driving routes."
                ), None, None

            res = self.tool_impls["recommend_transport"]({}, state)
            if "error" in res:
                return True, (
                    f"Could not calculate transport: {res['error']}"
                ), "recommend_transport", res

            reply = self._format_transport(state.origin, dest.name, res)
            return True, reply, "recommend_transport", res

        # ------------------------------------------------------------------ #
        # 7. Budget
        # ------------------------------------------------------------------ #
        if intent == "ask_budget":
            dest = state.destinations[0] if state.destinations else None
            if not dest or not (state.start_date and state.end_date):
                return True, (
                    "To estimate your budget, please provide your destination, "
                    "dates, and number of travellers."
                ), None, None

            res = self.tool_impls["estimate_budget"]({}, state)
            if "error" in res:
                return True, (
                    f"Could not estimate budget: {res['error']}"
                ), "estimate_budget", res

            reply = self._format_budget(res, state)
            return True, reply, "estimate_budget", res

        # ------------------------------------------------------------------ #
        # 8. Change Pace
        # ------------------------------------------------------------------ #
        if intent == "change_pace":
            pace = "balanced"
            low = user_text.lower()
            if any(w in low for w in ["relaxed", "slow", "chill", "less", "easy"]):
                pace = "relaxed"
            elif any(w in low for w in ["packed", "fast", "busy", "more", "cram"]):
                pace = "packed"

            state.pace = pace
            if state.itinerary:
                res = self.tool_impls["build_itinerary"]({}, state)
                return True, (
                    f"Pace updated to {pace}. I've regenerated your "
                    "day-by-day schedule in the Itinerary tab."
                ), "build_itinerary", res
            return True, (
                f"Trip pace set to {pace}. I'll design the schedule "
                "with this pacing."
            ), None, None

        # ------------------------------------------------------------------ #
        # 9. Remove Stop
        # ------------------------------------------------------------------ #
        if intent == "remove_stop":
            m = re.search(
                r"(?:remove|drop|skip|exclude|delete|swap out)\s+"
                r"(?:the\s+)?([A-Za-z0-9\s]+)",
                user_text, re.I,
            )
            if m:
                raw_target = m.group(1).strip()
                match_name = self._find_matching_stop(raw_target, state)
                if match_name:
                    res = self.tool_impls["build_itinerary"](
                        {"exclude_names": [match_name]}, state
                    )
                    return True, (
                        f"Removed {match_name} from your itinerary "
                        "and refreshed the schedule."
                    ), "build_itinerary", res

        # ------------------------------------------------------------------ #
        # 10. Provide Slot
        # ------------------------------------------------------------------ #
        if intent == "provide_slot" or nlu_result.has_slots:
            dest = state.destinations[0].name if state.destinations else None

            # (a) Vague destination pending -> run discovery
            if state.pending_destination_query and not state.destinations:
                if not state.origin:
                    return True, (
                        "Where will you be starting your trip from? "
                        "Let me know your origin so I can find destinations nearby."
                    ), None, None

                c_res = self.tool_impls["search_destination_candidates"]({}, state)
                if "verified" in c_res and c_res["verified"]:
                    cands = c_res["verified"][:5]
                    state.candidate_offset = len(cands)
                    total = len(c_res["verified"])
                    query_label = state.pending_destination_query or "destination"
                    label_plural = (
                        query_label if query_label.endswith("s")
                        else query_label + "s"
                    )

                    cand_lines = []
                    for i, c in enumerate(cands):
                        road = ", road" if c.get("road_distance_km") else ""
                        cand_lines.append(
                            f"  {i+1}. {c['name']}  ({c['distance_km']} km away{road})"
                        )
                    cand_text = "\n".join(cand_lines)

                    remaining = total - len(cands)
                    more_hint = (
                        f", or ask 'more' to see {remaining} more"
                        if remaining > 0 else ""
                    )
                    return True, (
                        f"Here are verified {label_plural} near {state.origin}:\n\n"
                        f"{cand_text}\n\n"
                        f"Which one would you like? "
                        f"(Reply with name or number{more_hint})"
                    ), "search_destination_candidates", c_res
                elif "error" in c_res:
                    return True, (
                        f"Could not find destinations near {state.origin}: "
                        f"{c_res['error']}"
                    ), "search_destination_candidates", c_res

            # (b) Multiple explicit destinations (already geocoded into
            # state.destinations by extract_trip_slots, OR pending as
            # destination_candidates). Either way the user sees the full
            # list echoed back — the geocoded names are the canonical
            # forms (e.g. 'Ooty' -> 'Udhagamandalam').
            if ((len(state.destinations) > 1
                 or len(state.destination_candidates) > 1)
                    and (state.destinations or state.destination_candidates)):
                if state.destinations and len(state.destinations) > 1:
                    names = [d.name for d in state.destinations]
                else:
                    names = [c.name for c in state.destination_candidates]
                if len(names) == 2:
                    names_str = f"{names[0]} and {names[1]}"
                else:
                    names_str = ", ".join(names[:-1]) + f", and {names[-1]}"

                missing = state.missing_required()
                dates_prompt = (
                    " What dates are you planning to travel?"
                    if "dates" in missing
                    else " Would you like me to fetch recommendations or check transport options?"
                )
                return True, (
                    f"Got it — multi-destination trip: {names_str} "
                    f"starting from {state.origin or 'your origin'}.\n\n"
                    f"I've added these destinations to your trip.{dates_prompt}"
                ), None, None

            # (c) Auto-confirm single named destination
            if (not state.destinations
                    and len(state.destination_candidates) == 1):
                c = state.destination_candidates[0]
                raw_dest = (nlu_result.slots or {}).get("destination_raw", "")
                if raw_dest and c.name.lower() == raw_dest.lower():
                    self.tool_impls["confirm_destination"]({"choice": c.name}, state)

            # (d) Prompt for next missing slot
            missing = state.missing_required()
            if missing:
                prompts = {
                    "origin":       "Where will you be starting your trip from?",
                    "destinations": "Where would you like to travel?",
                    "destination":  "Where would you like to travel?",
                    "dates":        "What are your travel dates (start and end date)?",
                    "budget":       "What is your approximate budget for the trip in INR?",
                }
                next_q = prompts.get(missing[0], f"Could you provide your {missing[0]}?")

                details = []
                if state.origin:
                    details.append(f"Origin: {state.origin}")
                if state.destinations:
                    details.append(f"Destination: {state.destinations[0].name}")
                if state.start_date and state.end_date:
                    details.append(f"Dates: {state.start_date} to {state.end_date}")
                if state.travellers:
                    details.append(f"Travellers: {state.travellers}")
                if state.budget_total:
                    details.append(f"Budget: Rs {state.budget_total:,.0f}")

                prefix = (
                    f"Got it! Updated your details ({', '.join(details)}).\n\n"
                    if details else ""
                )
                return True, f"{prefix}{next_q}", None, None

            else:
                # All required slots filled — auto-fire weather + recommendations.
                dest_name = state.destinations[0].name

                weather_res = self.tool_impls["get_weather"]({}, state)
                rec_res = self.tool_impls["get_recommendations"]({}, state)

                header = (
                    f"All set! Trip to {dest_name} from {state.origin} "
                    f"for {state.travellers} traveller(s) "
                    f"({state.start_date} to {state.end_date})."
                )

                if "error" in rec_res:
                    return True, (
                        f"{header}\n\n"
                        f"I couldn't fetch recommendations right now "
                        f"({rec_res['error']}). Try again in a moment, or "
                        f"ask me for weather, recommendations, or the itinerary."
                    ), "get_recommendations", rec_res

                parts = [header]
                if "error" not in weather_res:
                    parts.append(self._format_weather(dest_name, weather_res, state))
                parts.append(self._format_recommendations(dest_name, rec_res))
                reply = "\n\n".join(parts)
                return True, reply, "get_recommendations", rec_res

        return False, None, None, None

    # ------------------------------------------------------------------ #
    # Plain-text formatters
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_weather(dest_name: str, res: dict, state: TripState) -> str:
        mode = res.get("mode", "forecast")
        days = res.get("days", [])
        if not days:
            return f"Weather data is currently unavailable for {dest_name}."

        lines = [
            f"Weather Outlook — {dest_name}",
            f"{mode.capitalize()} from {state.start_date} to {state.end_date}",
            "",
        ]
        for d in days:
            date = d["date"]
            sky = (d.get("sky") or "").capitalize()
            t_min = d.get("t_min")
            t_max = d.get("t_max")
            rain = d.get("precip_mm")
            temp = f"{t_min}-{t_max}°C" if t_min is not None and t_max is not None else "n/a"
            rain_str = f"{rain} mm" if rain is not None else ""
            line = f"  {date}  {sky:<18}  {temp:>10}  {rain_str}"
            lines.append(line.rstrip())

        # Summary advice
        max_temps = [d['t_max'] for d in days if d.get('t_max') is not None]
        avg_max = sum(max_temps) / len(max_temps) if max_temps else 20.0
        rain_days = sum(1 for d in days if (d.get('precip_mm') or 0) > 1.0)

        advice = "Expect pleasant weather."
        if avg_max < 15:
            advice = "It will be quite chilly — be sure to pack warm woollens."
        elif avg_max > 32:
            advice = "It will be warm — stay hydrated and wear light clothing."
        if rain_days > 0:
            advice += (
                f" Rainfall is expected on {rain_days} day(s); "
                "bring an umbrella or rain jacket."
            )

        lines.append("")
        lines.append(f"Note: {advice}")
        return "\n".join(lines)

    @staticmethod
    def _format_recommendations(dest_name: str, res: dict) -> str:
        top_picks = res.get("top_picks", [])
        hidden_gems = res.get("hidden_gems", [])
        foods = res.get("top_food", [])
        stays = res.get("top_stay", [])

        lines = [
            f"Top Recommendations — {dest_name}",
            f"Found {len(top_picks)} top picks, {len(hidden_gems)} hidden gems, "
            f"{len(stays)} stays, and {len(foods)} dining options.",
            "",
            "Highlights:",
        ]
        for i, a in enumerate(top_picks[:5]):
            kind = (a.get("kind") or "sight").replace("_", " ").title()
            km = a.get("km_from_center")
            dist = f"  ({km} km)" if km is not None else ""
            lines.append(f"  {i+1}. {a['name']} — {kind}{dist}")

        if hidden_gems:
            lines.append("")
            lines.append("Hidden gems:")
            for a in hidden_gems[:5]:
                lines.append(f"  - {a['name']}")

        if foods:
            food_names = ", ".join(f['name'] for f in foods[:3])
            lines.append("")
            lines.append(f"Popular food spots: {food_names}")

        if stays:
            stay_names = ", ".join(s['name'] for s in stays[:3])
            lines.append(f"Recommended stays: {stay_names}")

        lines.append("")
        lines.append("Would you like me to plan the days for your trip?")
        return "\n".join(lines)

    @staticmethod
    @staticmethod
    def _stop_line(stop) -> str:
        """One stop line.

        Tolerant of both shapes on purpose: the timed dict produced by
        tools.build_itinerary, and a plain name string (the shape the
        formatter contract tests pin).
        """
        if not isinstance(stop, dict):
            return f"  - {stop}"

        name = stop.get("name") or "?"
        bits = []
        arrive, depart = stop.get("arrive"), stop.get("depart")
        if arrive and depart:
            bits.append(f"{arrive}-{depart}")
        label = stop.get("activity") or (stop.get("kind") or "").replace(
            "_", " "
        ).title()
        if label:
            bits.append(label)
        if stop.get("is_trek"):
            bits.append("trek")
        elif isinstance(stop.get("intensity"), int) \
                and stop["intensity"] >= 4:
            bits.append("strenuous")
        if stop.get("note"):
            bits.append(stop["note"])

        tail = f" ({', '.join(bits)})" if bits else ""
        km = stop.get("km_from_prev")
        km_str = f" — {km} km" if km else ""
        return f"  - {name}{tail}{km_str}"

    @staticmethod
    def _effort_line(d: dict) -> str:
        parts = []
        visit = d.get("effort_min")
        travel = d.get("travel_min")
        if visit:
            parts.append(f"{visit // 60}h {visit % 60:02d}m on foot")
        if travel:
            parts.append(f"{travel} min travel")
        if d.get("trek_count"):
            parts.append("1 trek" if d["trek_count"] == 1
                         else f"{d['trek_count']} treks")
        return " · ".join(parts)

    @staticmethod
    def _format_itinerary(dest_name: str, res: dict, state: TripState) -> str:
        days = res.get("schedule", [])
        pace = getattr(state, "pace", "balanced").capitalize()
        lines = [
            f"Day-by-Day Itinerary — {dest_name}",
            f"{len(days)} Days · {pace} Pace · {state.travellers} Traveller(s)",
            "",
        ]

        for i, d in enumerate(days, start=1):
            date_str = f" ({d.get('date')})" if d.get('date') else ""
            window = ""
            if d.get("start") and d.get("end"):
                window = f"  {d['start']}-{d['end']}"

            if d.get("rest_day"):
                lines.append(f"Day {i}{date_str} — Rest day")
                lines.append("")
                continue

            lines.append(f"Day {i}{date_str}{window}")
            for stop in d.get("stops", []):
                lines.append(ToolRouter._stop_line(stop))
            if d.get("lunch"):
                when = f" ({d['lunch_time']})" if d.get("lunch_time") else ""
                lines.append(f"  - Lunch{when}: {d['lunch']}")
            if d.get("km_total") is not None:
                lines.append(f"  - ~{d['km_total']} km total")
            effort = ToolRouter._effort_line(d)
            if effort:
                lines.append(f"  - {effort}")
            if d.get("overloaded"):
                lines.append("  - Note: this is a long day; consider "
                             "moving a stop or adding a day.")
            if d.get("unverified"):
                lines.append("  - Note: the effort of these stops could "
                             "not be assessed, so this day may be "
                             "ambitious.")
            lines.append("")

        unscheduled = res.get("unscheduled") or []
        if unscheduled:
            lines.append("Left out of the schedule:")
            for entry in unscheduled:
                if isinstance(entry, dict):
                    lines.append(
                        f"  - {entry.get('name')}: {entry.get('reason')}"
                    )
                else:
                    lines.append(f"  - {entry}")
            lines.append("")

        excluded = res.get("excluded") or []
        if excluded:
            lines.append(f"Currently excluded stops: {', '.join(excluded)}.")
            lines.append("")

        lines.append(
            "Only one trek is planned per day on purpose — a second climb "
            "would not fit realistically. You can explore the interactive "
            "route and map in the Itinerary and Map tabs. Let me know if "
            "you'd like to adjust any stops or estimate the budget."
        )
        return "\n".join(lines)

    @staticmethod
    def _format_transport(origin: str, dest_name: str, res: dict) -> str:
        opts = res.get("options", [])
        dist = res.get("distance_km")
        dist_str = f" (~{dist:.0f} km)" if dist else ""

        lines = [
            f"Transport Options — {origin} to {dest_name}{dist_str}",
            "",
        ]

        for opt in opts:
            mode = opt.get("mode", "").capitalize()
            hours = opt.get("hours", 0)
            cost = opt.get("cost_label")
            if not cost:
                lo = opt.get("cost_inr_low", 0)
                hi = opt.get("cost_inr_high", 0)
                cost = f"Rs {lo:,} – {hi:,}"
            note = opt.get("notes") or opt.get("fit") or ""
            line = f"  {mode:<8}  {hours:>5.1f} hrs   {cost:<24}"
            if note:
                line += f"  {note}"
            lines.append(line.rstrip())

        lines.append("")
        lines.append(
            "Costs are estimated round-trip totals. Pick your preferred "
            "mode or let me know if you want to proceed."
        )
        return "\n".join(lines)

    @staticmethod
    def _format_budget(res: dict, state: TripState) -> str:
        total = res.get("total_inr", 0)
        items = res.get("line_items_inr", {})
        verdict = res.get("verdict", "Estimated total")
        user_budget = getattr(state, "budget_total", None)

        lines = [
            "Trip Budget Estimate",
            f"Total Estimated Cost: Rs {total:,.0f}",
        ]
        if user_budget:
            lines.append(f"Your Budget: Rs {user_budget:,.0f}  ({verdict})")

        lines.append("")
        lines.append("Cost Breakdown:")
        lines.append(f"  Stay                Rs {items.get('stay', 0):>10,.0f}")
        lines.append(f"  Food                Rs {items.get('food', 0):>10,.0f}")
        lines.append(f"  Local Transport     Rs {items.get('local_transport', 0):>10,.0f}")
        lines.append(f"  Activities & Entry  Rs {items.get('activities', 0):>10,.0f}")
        if "intercity_transport" in items:
            lines.append(f"  Intercity Travel    Rs {items.get('intercity_transport', 0):>10,.0f}")

        lines.append("")
        lines.append("These are documented estimates, not live prices.")
        return "\n".join(lines)

    @staticmethod
    def _find_matching_stop(target: str, state: TripState) -> Optional[str]:
        target_low = target.lower().strip()
        if state.itinerary and isinstance(state.itinerary, dict):
            for day in state.itinerary.get("days", []):
                for stop in day.get("stops", []):
                    if target_low in stop.get("name", "").lower():
                        return stop["name"]
        if state.recommendations and "attractions" in state.recommendations:
            for a in state.recommendations["attractions"]:
                if target_low in a.get("name", "").lower():
                    return a["name"]
        return None
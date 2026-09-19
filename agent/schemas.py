def _fn(name: str, description: str, properties: dict, required: list | None = None) -> dict:
    """Build one tool declaration in OpenAI format (plain dicts).
    Works for any OpenAI-compatible endpoint: NVIDIA, OpenRouter, etc."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required or []},
    }}


EXTRACT_TRIP_SLOTS = _fn(
    "extract_trip_slots",
    "Extract/update structured trip fields from the user's latest message. "
    "Include only keys present in or changed by the message. Never invent values.",
    {
        "origin": {"type": "string",
            "description": "City/place the traveler starts from"},
        "destination_raw": {"type": "string",
            "description": "Destination exactly as the user said it (may be vague)"},
        "destination_is_vague": {"type": "boolean",
            "description": "True if destination is a category ('a hill station'), not a specific place"},
        "start_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "budget_total": {"type": "number"},
        "budget_currency": {"type": "string"},
        "travel_mode": {"type": "string",
            "enum": ["flight", "train", "bus", "car", "bike"],
            "description": "How the traveler reaches the destination"},
        "travellers": {"type": "integer"},
        "interests": {"type": "array", "items": {"type": "string"}},
        "confidence_flags": {"type": "array", "items": {"type": "string"},
            "description": "Names of fields extracted with low confidence"},
    },
)

SEARCH_DESTINATION_CANDIDATES = _fn(
    "search_destination_candidates",
    "Resolve a vague destination into verified candidates. Propose 4-6 specific, "
    "well-known REAL place names fitting the user's request near the origin. "
    "Each name is verified against OpenStreetMap; only real places are returned.",
    {"candidate_names": {"type": "array", "items": {"type": "string"},
        "description": "e.g. ['Ooty', 'Kodaikanal', 'Yercaud', 'Munnar']"}},
    required=["candidate_names"],
)

CONFIRM_DESTINATION = _fn(
    "confirm_destination",
    "Lock in the user's chosen destination from the candidate list.",
    {"choice": {"type": "string",
        "description": "User's pick as list number ('2') or name ('Ooty')"}},
    required=["choice"],
)
GET_WEATHER = _fn(
    "get_weather",
    "Weather outlook for the confirmed destination over the trip dates. Live "
    "forecast if near, otherwise historical climatology (labelled as such).",
    {},
)

GET_RECOMMENDATIONS = _fn(
    "get_recommendations",
    "Search attractions/food/stay around the confirmed destination via "
    "OpenStreetMap, rank them, and cluster attractions into geographic "
    "day-groups. No arguments needed.",
    {},
)

BUILD_ITINERARY = _fn(
    "build_itinerary",
    "Build the day-by-day schedule from stored recommendations. Optionally "
    "exclude named attractions (cumulative across calls). Pass clear_excluded=true "
    "to reset exclusions before adding new ones.",
    {"exclude_names": {"type": "array", "items": {"type": "string"},
        "description": "Attraction names to skip, e.g. ['Sunset Deck']"},
     "clear_excluded": {"type": "boolean",
        "description": "If true, wipe the persisted exclusion list first"}},
)


RECOMMEND_TRANSPORT = _fn(
    "recommend_transport",
    "Recommend transport options between origin and destination with "
    "estimated cost and time for each mode. Takes no arguments; reads from state.",
    {},
)

ESTIMATE_BUDGET = _fn(
    "estimate_budget",
    "Estimate trip cost (stay, food, transport, activities) from documented "
    "assumptions and compare against the user's budget. Takes no arguments.",
    {},
)

TOOL_DECLARATIONS = [EXTRACT_TRIP_SLOTS, SEARCH_DESTINATION_CANDIDATES,
                     CONFIRM_DESTINATION, GET_WEATHER, GET_RECOMMENDATIONS,
                     BUILD_ITINERARY, ESTIMATE_BUDGET, RECOMMEND_TRANSPORT]
"""Rule-based intent matcher.

Fires only on high-precision patterns, so a match can safely short-circuit
the LLM. Everything the rules miss falls through to the ML classifier.
"""
import re
from typing import Optional

# (pattern, intent, confidence) — order matters, first match wins.
_RULES: list[tuple[re.Pattern, str, float]] = [
    # -- numeric pick (state-aware; guard applied in rule_intent) -------
    (re.compile(r"^\s*\d{1,2}\s*$"), "confirm_destination", 0.95),
    (re.compile(r"^\s*(option|number|#)\s*\d{1,2}\s*$", re.I),
     "confirm_destination", 0.90),
    (re.compile(r"^\s*(the\s+)?(first|second|third|fourth|fifth|last)\s+(one|option)?\s*$",
                re.I),
     "confirm_destination", 0.85),


     # -- change pace -----------------------------------------------------
    (re.compile(r"\b(more|less|too)\s+(packed|relaxed|busy)\b", re.I),
          "change_pace", 0.90),
    (re.compile(r"\b(relaxed|packed)\s*(please|pace)?\b", re.I),
          "change_pace", 0.80),
    (re.compile(r"\b(slow it down|speed it up|take it easy|chill|"r"more per day|less per day)\b", re.I),
          "change_pace", 0.85),

    # -- show more candidate destinations (pagination) -----------------
    (re.compile(r"^\s*(?:more(?:\s+(?:options?|places?|stations?|destinations?|"r"hill\s+stations?|results?|choices?))?|"     # "more" alone OR "more X"
        r"show\s+more|what\s+else|any\s+others?|"
        r"any\s+other\s+(?:places?|options?|hill\s+stations?|destinations?)|"
        r"see\s+more|next|next\s+page|show\s+remaining|other\s+options?)"
        r"\s*$",                                        # <-- anchored to end
        re.I,
    ),
     "show_more_candidates", 0.95),



    # -- greetings -------------------------------------------------------
    (re.compile(r"^\s*(hi|hello|hey|yo|hola|namaste)\b", re.I), "greet", 0.95),
    (re.compile(r"^\s*good\s+(morning|afternoon|evening|day)\b", re.I),
     "greet", 0.95),

    # -- provide_slot / trip initiation (from X to Y, plan trip from/to) --
    (re.compile(r"\b(from\s+.+\s+to\b|plan\s+(?:a\s+)?trip\s+(?:from|to)|trip\s+(?:from|to)|travel\s+(?:from|to))\b", re.I),
     "provide_slot", 0.95),

    

    # -- build itinerary -------------------------------------------------
    (re.compile(r"\b(plan|build|create|make|generate)\s+(?:the\s+|my\s+|a\s+)?"
                r"(itinerary|schedule|day[\s-]?by[\s-]?day|days?)\b",
                re.I),
     "build_itinerary", 0.90),
    (re.compile(r"\b(ok|okay|yes|yep|sure|go ahead)[,!. ]+"
                r"(plan|build|go|proceed|do it|schedule)\b", re.I),
     "build_itinerary", 0.90),
    (re.compile(r"\bplan\s+(?:it|the\s+days?)\b", re.I),
     "build_itinerary", 0.85),

    

    # -- remove / swap stop ---------------------------------------------
    (re.compile(r"\b(remove|drop|skip|swap out|take out|exclude|delete)\b",
                re.I),
     "remove_stop", 0.85),

    # -- budget ----------------------------------------------------------
    (re.compile(r"\b(how much|total cost|what.*cost|cost estimate|"
                r"estimate.*budget|is it within|over budget|under budget)\b",
                re.I),
     "ask_budget", 0.85),
    (re.compile(r"^\s*(budget|cost)\??\s*$", re.I), "ask_budget", 0.80),

    # -- transport -------------------------------------------------------
    (re.compile(r"\b(how (do|can|should) (i|we|you) (get|reach|travel)|"
                r"transport|how to reach|fly or drive|train or bus|"
                r"what.*(flight|train|bus))\b", re.I),
     "ask_transport", 0.85),

    # -- recommendations -------------------------------------------------
    (re.compile(r"\b(suggest|recommend|show me|give me|"
                r"what (can|should) (i|we) (see|do|visit|eat)|"
                r"top (sights|attractions|things)|hidden gems?)\b", re.I),
     "request_recommendations", 0.85),

      # "4 days from 22-9-26", "for 3 nights starting 1-10", "5-day trip on 15/9/26"
    (re.compile(r"\b\d{1,2}\s*(?:days?|nights?)\b.*\b\d{1,2}[/\-.]\d{1,2}\b"r"|\bfor\s+\d{1,2}\s*(?:days?|nights?)\b.*\b\d{1,2}[/\-.]\d{1,2}\b",re.I),
      "provide_slot", 0.90),

    # -- weather ---------------------------------------------------------
    (re.compile(r"\b(weather|forecast|rain|rainfall|temperature|climate)\b",
                re.I),
     "ask_weather", 0.90),
]

# Precompiled guards for the confirm_destination branch.
_NUMERIC_PICK = re.compile(r"(?:option\s+|number\s+|#)?\s*\d{1,2}\s*", re.I)
_ORDINAL_PICK = re.compile(
    r"\s*(?:the\s+)?(?:first|second|third|fourth|fifth|last)\s+"
    r"(?:one|option)?\s*", re.I,
)


def rule_intent(text: str, state=None) -> Optional[tuple[str, float]]:
    """Return (intent, confidence) if a high-precision rule fires, else None."""
    if not text:
        return None

    # Check candidate name match when destination candidates are pending
    if state and getattr(state, "destination_candidates", None):
        cands = state.destination_candidates
        user_clean = re.sub(r"^(?:let's\s+go\s+with|go\s+with|i'll\s+take|choose|select|pick|i\s+pick|i\s+choose)\s+", "", text.strip(), flags=re.I).strip()
        for c in cands:
            if user_clean.lower() == c.name.lower() or (len(user_clean) >= 4 and user_clean.lower() in c.name.lower()):
                return "confirm_destination", 0.95

    for pattern, intent, conf in _RULES:
        if not pattern.search(text):
            continue

        # Extra guard: a "confirm_destination" match only makes sense when
        # the state actually has candidates to confirm.
        if intent == "confirm_destination":
            cands = list(getattr(state, "destination_candidates", []) or [])
            if _NUMERIC_PICK.fullmatch(text) or _ORDINAL_PICK.fullmatch(text):
                if not cands:
                    continue
            else:
                if not any(c.name.lower() in text.lower() for c in cands):
                    continue

        return intent, conf
    return None
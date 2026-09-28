"""Rule-based intent matcher.

Fires only on high-precision patterns, so a match can safely short-circuit
the LLM. Everything the rules miss falls through to the ML classifier.
"""
import re
from typing import Optional

# Precompiled guards for the confirm_destination branch.
_NUMERIC_PICK = re.compile(r"(?:option\s+|number\s+|#)?\s*\d{1,2}\s*", re.I)
_ORDINAL_PICK = re.compile(
    r"\s*(?:the\s+)?(?:first|second|third|fourth|fifth|last)\s+"
    r"(?:one|option)?\s*", re.I,
)
_MULTI_NUMERIC_PICK = re.compile(
    r"^\s*(?:\d{1,2}\s*(?:,|&|and)\s*)+\d{1,2}\s*$", re.I,
)
# Phrases that select exactly two options ("both", "the two").
_TWO_PICK_PHRASE = re.compile(
    r"^\s*(?:both(?:\s+(?:of\s+)?(?:them|these|two))?|"
    r"both\s+(?:options?|places?|destinations?|hill\s+stations?)|"
    r"two\s+of\s+them|the\s+two)\s*$", re.I,
)
# Phrases that select the whole candidate list ("all", "everything").
_ALL_PICK_PHRASE = re.compile(
    r"^\s*(?:all(?:\s+of\s+(?:them|these|the\s+(?:above|list)))?|"
    r"all\s+(?:options?|places?|destinations?|hill\s+stations?)|"
    r"each\s+of\s+them|everything|the\s+(?:whole|entire)\s+list)\s*$", re.I,
)
# Superset used as a _RULES pattern row; the guard below narrows it.
_LIST_PICK_PHRASE = re.compile(
    r"^\s*(?:(?:both|all)(?:\s+(?:of\s+)?(?:them|these|two|above|list))?|"
    r"(?:both|all)\s+(?:options?|places?|destinations?|hill\s+stations?)|"
    r"each\s+of\s+them|everything|two\s+of\s+them|the\s+(?:two|(?:whole|entire)\s+list))\s*$",
    re.I,
)

# (pattern, intent, confidence) — order matters, first match wins.
_RULES: list[tuple[re.Pattern, str, float]] = [
    # -- numeric pick (state-aware; guard applied in rule_intent) -------
    (re.compile(r"^\s*\d{1,2}\s*$"), "confirm_destination", 0.95),
    (re.compile(r"^\s*(option|number|#)\s*\d{1,2}\s*$", re.I),
     "confirm_destination", 0.90),
    (re.compile(r"^\s*(the\s+)?(first|second|third|fourth|fifth|last)\s+(one|option)?\s*$",
                re.I),
     "confirm_destination", 0.85),
    # multi-index pick: "1 and 2" / "1, 3 & 4"
    (re.compile(r"^\s*(?:\d{1,2}\s*(?:,|&|and)\s*)+\d{1,2}\s*$", re.I),
     "confirm_destination", 0.95),
    # whole-list pick: "both" / "all of them" / "everything"
    (_LIST_PICK_PHRASE, "confirm_destination", 0.90),


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
    # A trip *request* is slot-providing even when it has no explicit
    # "from … to" pair: "plan a trip to any hill station", "planning our
    # holiday". Without this the weak ML classifier reads the word "plan"
    # as build_itinerary and dead-ends on "confirm a destination and dates
    # first" instead of discovering destinations.
    (re.compile(
        r"\b(?:plan|planning|organi[sz]e|organi[sz]ing|book|booking)\s+"
        r"(?:me\s+|us\s+)?(?:a|an|my|our|the)?\s*"
        r"(?:trip|vacation|holiday|getaway)\b"
        r"|\b(?:trip|vacation|holiday|getaway)\s+(?:from|to|for)\b",
        re.I),
     "provide_slot", 0.90),
    (re.compile(r"\b(from\s+.+\s+to\b|trip\s+(?:from|to)|travel\s+(?:from|to))\b", re.I),
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

        # Multi-name pick: "Ooty and Munnar" / "Ooty, Kodaikanal" — every
        # token must resolve to a distinct pending candidate.
        tokens = [t.strip() for t in re.split(r"\s*(?:,|&|\band)\s*", user_clean, flags=re.I) if t.strip()]
        if len(tokens) >= 2:
            matched: set = set()
            ok = True
            for t in tokens:
                hit = next((c.name for c in cands
                            if t.lower() == c.name.lower()
                            or (len(t) >= 4 and t.lower() in c.name.lower())), None)
                if hit is None or hit in matched:
                    ok = False
                    break
                matched.add(hit)
            if ok:
                return "confirm_destination", 0.95

    for pattern, intent, conf in _RULES:
        if not pattern.search(text):
            continue

        # Extra guard: a "confirm_destination" match only makes sense when
        # the state actually has candidates to confirm.
        if intent == "confirm_destination":
            cands = list(getattr(state, "destination_candidates", []) or [])
            dests = list(getattr(state, "destinations", []) or [])
            if (_NUMERIC_PICK.fullmatch(text) or _ORDINAL_PICK.fullmatch(text)
                    or _MULTI_NUMERIC_PICK.fullmatch(text)):
                if not cands:
                    continue
            elif _TWO_PICK_PHRASE.fullmatch(text):
                # "both" means exactly two pending candidates — or, once the
                # pick is already locked in, the two destinations we confirmed
                # earlier. Otherwise leave it to the LLM, which has the chat
                # context (e.g. a reply about something else entirely).
                if len(cands) != 2 and not (not cands and len(dests) >= 2):
                    continue
            elif _ALL_PICK_PHRASE.fullmatch(text):
                if not cands and len(dests) < 2:
                    continue
            else:
                if not any(c.name.lower() in text.lower() for c in cands):
                    continue

        return intent, conf
    return None
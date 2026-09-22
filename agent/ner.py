"""Named Entity Recognition for trip slot extraction.

Uses spaCy's en_core_web_sm if available; regex fallbacks always run so
Indian date formats (DD/MM/YY), money (40k, ₹40,000), and traveller
counts are captured even without spaCy.

Extracted slots are merged into TripState *before* the LLM is called,
so the LLM only has to reason about what NER could not resolve.

Regex inventory:
  _DATE_RE            explicit dates: "22-9-26", "22/9/2026", "22.9.26"
  _DURATION_RE        durations:       "4 days", "3 nights", "a 5-day trip"
  _MONEY_RE           currency:        "₹40k", "40k rupees", "budget 50000"
  _TRAVELLERS_RE      forward count:   "4 travellers", "for 2 people"
  _TRAVELLERS_REV_RE  reverse count:   "travellers count is 4", "members = 3"
  _FAMILY_OF_RE       family phrasing: "family of 6", "group of 8"
  _SOLO_RE            solo phrasing:   "just me", "travelling solo"
  _NEAR_CITY          origin hints:    "near Chennai", "close to Kochi"
  _FROM_TO            origin+dest:     "from Chennai to Munnar"
  _GO_TO              dest only:       "go to Ooty"
"""
import re
from datetime import date, timedelta
from typing import Optional

try:
    import spacy
    try:
        _NLP = spacy.load("en_core_web_sm")
    except OSError:
        _NLP = None
except ImportError:
    _NLP = None


# --------------------------------------------------------------------------- #
# Date / duration
# --------------------------------------------------------------------------- #

_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b")

# Matches "4 days", "for 3 nights", "a 5-day trip", "10 day stay".
# Used together with _DATE_RE: when only a start date is present, the
# duration is applied to derive end_date (inclusive — "4 days from 22-9"
# means 22, 23, 24, 25 → end = start + 3).
_DURATION_RE = re.compile(
    r"\b(?:for\s+)?(\d{1,2})\s*(?:days?|nights?)\b"
    r"|\b(\d{1,2})[\s\-]?days?[\s\-]?(?:trip|stay|holiday)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #

_MONEY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*([kK])?"                       # ₹40k
    r"|\b([\d,]+(?:\.\d+)?)\s*([kK])?\s*(?:rupees|inr|rs)\b"                # 40k rupees
    r"|\b(?:budget|under|around|about|max|upto|up to|within)\s*"
    r"(?:of\s*)?(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*([kK])?\b",        # budget 40k
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Travellers — four complementary patterns
# --------------------------------------------------------------------------- #

# Forward form: "4 travellers", "for 2 people", "we are 4 adults".
_TRAVELLERS_RE = re.compile(
    r"\b(?:for\s+|we\s+are\s+|party\s+of\s+)?(\d{1,2})\s*"
    r"(?:people|persons?|travell?ers?|adults?|pax|members?|guests?)\b",
    re.IGNORECASE,
)

# Reverse form: "Travellers count is 4", "members = 3", "group size: 5".
_TRAVELLERS_REV_RE = re.compile(
    r"\b(?:total\s+)?(?:travell?ers?|people|persons?|adults?|pax|"
    r"members?|guests?|group|party)"
    r"(?:\s+(?:count|size|number))?"
    r"\s*(?:is|are|=|:)?\s*(\d{1,2})\b",
    re.IGNORECASE,
)

# "family of 6", "group of 8".
_FAMILY_OF_RE = re.compile(
    r"\b(?:family|group|party)\s+of\s+(\d{1,2})\b",
    re.IGNORECASE,
)

# Solo phrasing — always resolves to 1.
_SOLO_RE = re.compile(
    r"\b(?:just\s+me|solo|alone|by\s+myself|myself\s+only)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Travel mode
# --------------------------------------------------------------------------- #

_MODE_KEYWORDS = {
    "flight": ["flight", "fly", "plane", "air "],
    "train":  ["train", "railway", "irctc"],
    "bus":    ["bus", "coach"],
    "car":    ["car", "drive", "driving", "taxi", "cab"],
    "bike":   ["bike", "motorcycle", "motorbike", "scooter", "ride"],
}


# --------------------------------------------------------------------------- #
# Origin / destination
# --------------------------------------------------------------------------- #

_KNOWN_ORIGINS = [
    "chennai", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad",
    "kolkata", "pune", "kochi", "coimbatore", "madurai", "trivandrum",
    "kozhikode", "mysore", "mysuru", "goa", "jaipur", "ahmedabad",
]

# "from X to Y", "X to Y", "travel from X to Y" — capitalised for
# precision (spaCy-style NER would help, but regex on capitals works well
# for city names in practice).
_FROM_TO = re.compile(
    r"\bfrom\s+([A-Z][\w]+(?:\s+[A-Z][\w]+)?)\s+to\s+"
    r"([A-Z][\w]+(?:\s+[A-Z][\w]+)?)"
)
_GO_TO = re.compile(r"\b(?:to|visit|see|explore|go to)\s+([A-Z][\w]+(?:\s+[A-Z][\w]+)?)")

# "near Chennai", "around Chennai", "close to Chennai", "in Chennai"
_NEAR_CITY = re.compile(
    r"\b(?:near|around|close\s+to|outside)\s+([A-Za-z][\w]+(?:\s+[A-Za-z][\w]+)?)",
    re.I,
)

_NEAR_STOPWORDS = {
    "the", "a", "an", "here", "there", "home", "hill", "hills",
    "beach", "beaches", "mountain", "mountains", "town", "city",
    "trip", "days", "day", "place", "places", "station", "stations",
}

_VAGUE_DEST_WORDS = [
    "hill station", "hill stations", "hillstation", "hillstations", "hills",
    "beach", "beaches", "mountain", "mountains", "waterfall", "waterfalls",
    "place", "places", "somewhere", "anywhere", "any place", "any hill station",
    "some hill station", "any beach", "some beach", "hill",
]

# Vague-query categories. Checked in order: first match wins, so the more
# specific phrases ("hill station with temples") map to the first listed
# category. These names match the categories in agent/destinations_db.py.
_CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("hill station", "hillstation", "hills", "mountain", "hill"), "hill_station"),
    (("beach", "coast", "seaside", "sea"), "beach"),
    (("waterfall", "falls"), "waterfall"),
    (("backwater", "backwaters", "houseboat", "alleppey-style"), "backwater"),
    (("wildlife", "safari", "tiger", "national park", "sanctuary"), "wildlife"),
    (("temple", "temples", "pilgrimage", "spiritual", "ashram"), "temple"),
    (("heritage", "fort", "palace", "historical", "history", "monument"), "heritage"),
    (("desert", "dunes", "sand"), "desert"),
    (("lake", "lakes"), "lake"),
    (("trek", "trekking", "hik", "hiking", "adventure"), "trekking"),
    (("coffee", "tea", "plantation", "estate"), "coffee"),
    (("cave", "caves"), "cave"),
    (("offbeat", "underrated", "less crowded", "uncrowded", "hidden"), "offbeat"),
]


def _destination_category(dest: str, text: str) -> Optional[str]:
    """Map a vague destination (plus the full message as context) to a
    category from agent/destinations_db.py. None when nothing matches."""
    blob = f"{dest} {text}".lower()
    for keywords, category in _CATEGORY_KEYWORDS:
        if any(k in blob for k in keywords):
            return category
    return None


# --------------------------------------------------------------------------- #
# Adventure level
# --------------------------------------------------------------------------- #

# How trek-heavy the user wants the trip. Ordered: the explicit negatives
# are checked before the positives so "not too much trekking" is never
# read as high-adventure because it contains the word trekking.
_ADVENTURE_HIGH_RE = re.compile(
    r"\b(adventur(?:e|ous)|hardcore|lots?\s+of\s+(?:trek|hik)\w*|"
    r"heavy\s+(?:trek|hik)\w*|trek(?:king|s)?\s*(?:heavy|every|each)|"
    r"as\s+many\s+(?:treks|hikes)|summit\s+hunt(?:ing|er)?)\b",
    re.I,
)
_ADVENTURE_LOW_RE = re.compile(
    r"\b((?:not|no|less|without|minimal|hardly\s+any|avoid)\s+"
    r"(?:too\s+much\s+)?(?:trek\w*|hik\w*|adventur\w*)|"
    r"(?:easy|light|lazy|relaxed)\s+(?:trip|days?|pace|adventure)|"
    r"no\s+strenuous|senior|elderly|family\s+with\s+(?:kids|children))\b",
    re.I,
)
_ADVENTURE_BALANCED_RE = re.compile(
    r"\b((?:balanced|mixed|mix\s+of\s+everything)|"
    r"(?:some|a\s+couple\s+of)\s+(?:treks|hikes)|one\s+or\s+two\s+(?:treks|hikes))\b",
    re.I,
)


def _extract_adventure(text: str) -> Optional[str]:
    """Adventure level from phrasing; None when the message says nothing."""
    if _ADVENTURE_LOW_RE.search(text):
        return "low"
    if _ADVENTURE_HIGH_RE.search(text):
        return "high"
    if _ADVENTURE_BALANCED_RE.search(text):
        return "balanced"
    return None


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def _parse_year(y: int) -> int:
    return 2000 + y if y < 100 else y


def _extract_dates(text: str) -> list[str]:
    out = []
    current_year = date.today().year
    for m in _DATE_RE.finditer(text):
        d, mo = int(m.group(1)), int(m.group(2))
        raw_y = m.group(3)
        y = _parse_year(int(raw_y)) if raw_y else current_year
        try:
            parsed = date(y, mo, d)
            if not raw_y and parsed < date.today():
                parsed = date(y + 1, mo, d)
            out.append(parsed.isoformat())
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- #
# Money / travellers / mode
# --------------------------------------------------------------------------- #

def _extract_money(text: str) -> Optional[float]:
    m = _MONEY_RE.search(text)
    if not m:
        return None
    # Three alternative groups — find the one that matched.
    for i in (1, 3, 5):
        raw = m.group(i)
        if raw:
            k = m.group(i + 1)
            val = float(raw.replace(",", ""))
            return val * 1000 if k else val
    return None


def _extract_travellers(text: str) -> Optional[int]:
    """Traveller count from any of four complementary phrasings.

    Order matters only for defensiveness — a string like "4 people, group
    of 4" should resolve to 4 either way, and the forward pattern is the
    most reliable signal, so it runs first.
    """
    # Forward form: "4 travellers", "for 2 people"
    m = _TRAVELLERS_RE.search(text)
    if m:
        return int(m.group(1))

    # Reverse form: "travellers count is 4", "members = 3"
    m = _TRAVELLERS_REV_RE.search(text)
    if m:
        return int(m.group(1))

    # "family of 5", "group of 8"
    m = _FAMILY_OF_RE.search(text)
    if m:
        return int(m.group(1))

    # Solo phrasing
    if _SOLO_RE.search(text):
        return 1

    return None


def _extract_mode(text: str) -> Optional[str]:
    low = text.lower()
    for mode, kws in _MODE_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return mode.strip()
    return None


# --------------------------------------------------------------------------- #
# Place extraction
# --------------------------------------------------------------------------- #

def _clean_place_name(s: str) -> str:
    s = re.sub(r"^(?:the|a|an|any|some)\s+", "", s.strip(), flags=re.I).strip()
    return s.title()


def _extract_places(text: str, known_origin: Optional[str] = None):
    origin = None
    dest_raw = None
    destinations_list = []
    is_vague = False

    # 1. Pattern: from <origin> to <destination> (case-insensitive)
    m_from_to = re.search(
        r"\bfrom\s+([A-Za-z\s]+?)\s+to\s+([A-Za-z\s,and&]+?)(?=\s+(?:for|with|on|in|from|starting|next|between|\d)|$)",
        text,
        re.I,
    )
    if m_from_to:
        origin = _clean_place_name(m_from_to.group(1))
        dest_raw = m_from_to.group(2).strip()
    else:
        # 2. Check standalone "from <origin>"
        m_from = re.search(
            r"\b(?:from|starting\s+(?:at|from)|leaving\s+(?:from)?)\s+([A-Za-z\s]+?)(?=\s+(?:to|for|with|on|in|starting|next|between|\d)|$)",
            text,
            re.I,
        )
        if m_from:
            origin = _clean_place_name(m_from.group(1))

        # 3. Check standalone "to <destination>"
        m_to = re.search(
            r"\b(?:to|visit|explore|go\s+to|heading\s+to)\s+([A-Za-z\s,and&]+?)(?=\s+(?:for|with|on|in|from|starting|next|between|\d)|$)",
            text,
            re.I,
        )
        if m_to:
            dest_raw = m_to.group(1).strip()

    # Fallback to known origins if origin not yet found
    if not origin:
        low = text.lower()
        for city in _KNOWN_ORIGINS:
            if re.search(rf"\b(?:from\s+)?{city}\b", low):
                origin = city.title()
                break

    # Fallback for "near <city>"
    if origin is None:
        m_near = _NEAR_CITY.search(text)
        if m_near:
            cand = m_near.group(1)
            if (cand.lower() not in _NEAR_STOPWORDS
                    and (dest_raw is None or cand.lower() != dest_raw.lower())):
                origin = _clean_place_name(cand)

    # Process destination string
    if dest_raw:
        dest_low = dest_raw.lower().strip()
        # Check if destination is vague
        if any(v in dest_low for v in _VAGUE_DEST_WORDS):
            is_vague = True
            if any(h in dest_low for h in ["hill", "mountain"]):
                dest_raw = "hill station"
            elif any(b in dest_low for b in ["beach", "coast"]):
                dest_raw = "beach"
            else:
                cleaned = re.sub(r"\b(any|some|a|the|around|near|nearby)\b",
                                 "", dest_low, flags=re.I).strip()
                dest_raw = cleaned or "hill station"
        else:
            # Check for multiple destinations (e.g. "ooty and kodaikanal",
            # "ooty, munnar and kodaikanal")
            if "," in dest_raw or re.search(r"\b(and|&)\b", dest_raw, re.I):
                parts = [
                    re.sub(r"^(?:and|&)\s+", "", p.strip(), flags=re.I).strip()
                    for p in re.split(r",|\b(?:and|&)\b", dest_raw, flags=re.I)
                ]
                parts = [_clean_place_name(p) for p in parts
                         if p and p.lower() not in _NEAR_STOPWORDS]
                if len(parts) > 1:
                    destinations_list = parts
                    dest_raw = ", ".join(parts)
                elif len(parts) == 1:
                    dest_raw = parts[0]
            else:
                dest_raw = _clean_place_name(dest_raw)

    return origin, dest_raw, destinations_list, is_vague


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def extract_entities(text: str, known_origin: Optional[str] = None) -> dict:
    """Return a slots dict with only the fields found in `text`.

    Safe to call on any message — returns {} if nothing matches.
    """
    if not text or not text.strip():
        return {}

    out: dict = {}
    flags: list[str] = []

    origin, dest, dest_list, is_vague = _extract_places(text, known_origin)
    if origin and (not known_origin or origin.lower() != known_origin.lower()):
        out["origin"] = origin
        flags.append("ner_origin")
    if dest:
        out["destination_raw"] = dest
        flags.append("ner_destination")
    if is_vague:
        out["destination_is_vague"] = True
        flags.append("ner_vague_destination")
    if dest_list:
        out["destinations_list"] = dest_list
        flags.append("ner_multi_destination")

    iso_dates = _extract_dates(text)
    if len(iso_dates) >= 1:
        out["start_date"] = iso_dates[0]
        flags.append("ner_start_date")
    if len(iso_dates) >= 2:
        out["end_date"] = iso_dates[1]
        flags.append("ner_end_date")

    # If only a start date was given, try to derive end_date from a
    # duration phrase ("4 days from 22-9-26" → 2026-09-22 to 2026-09-25).
    # The duration reads inclusively: "4 days" spans 22, 23, 24, 25.
    if "start_date" in out and "end_date" not in out:
        m = _DURATION_RE.search(text)
        if m:
            n = int(m.group(1) or m.group(2))
            if 1 <= n <= 30:
                start = date.fromisoformat(out["start_date"])
                out["end_date"] = (start + timedelta(days=n - 1)).isoformat()
                flags.append("ner_end_date_from_duration")

    money = _extract_money(text)
    if money is not None:
        out["budget_total"] = money
        flags.append("ner_budget")

    n = _extract_travellers(text)
    if n is not None and 1 <= n <= 20:
        out["travellers"] = n
        flags.append("ner_travellers")

    mode = _extract_mode(text)
    if mode:
        out["travel_mode"] = mode
        flags.append("ner_travel_mode")

    level = _extract_adventure(text)
    if level:
        out["adventure_level"] = level
        flags.append("ner_adventure_level")

    if is_vague and dest:
        cat = _destination_category(dest, text)
        if cat:
            out["destination_category"] = cat
            flags.append("ner_destination_category")

    if flags:
        out["confidence_flags"] = flags
    return out
"""Named Entity Recognition for trip slot extraction.

Uses spaCy's en_core_web_sm if available; regex fallbacks always run so
Indian date formats (DD/MM/YY), money (40k, ₹40,000), and traveller
counts are captured even without spaCy.

Extracted slots are merged into TripState *before* the LLM is called,
so the LLM only has to reason about what NER could not resolve.
"""
import re
from datetime import date
from typing import Optional

try:
    import spacy
    try:
        _NLP = spacy.load("en_core_web_sm")
    except OSError:
        _NLP = None
except ImportError:
    _NLP = None

_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")

_MONEY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*([kK])?"                       # ₹40k
    r"|\b([\d,]+(?:\.\d+)?)\s*([kK])?\s*(?:rupees|inr|rs)\b"                # 40k rupees
    r"|\b(?:budget|under|around|about|max|upto|up to|within)\s*"
    r"(?:of\s*)?(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*([kK])?\b",        # budget 40k
    re.IGNORECASE,
)

_TRAVELLERS_RE = re.compile(
    r"\b(?:for\s+|we\s+are\s+|party\s+of\s+)?(\d{1,2})\s*"
    r"(?:people|persons?|travell?ers?|adults?|pax|members?|guests?)\b",
    re.IGNORECASE,
)

_MODE_KEYWORDS = {
    "flight": ["flight", "fly", "plane", "air "],
    "train":  ["train", "railway", "irctc"],
    "bus":    ["bus", "coach"],
    "car":    ["car", "drive", "driving", "taxi", "cab"],
    "bike":   ["bike", "motorcycle", "motorbike", "scooter", "ride"],
}

_KNOWN_ORIGINS = [
    "chennai", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad",
    "kolkata", "pune", "kochi", "coimbatore", "madurai", "trivandrum",
    "kozhikode", "mysore", "mysuru", "goa", "jaipur", "ahmedabad",
]

# "from X to Y", "X to Y", "travel from X to Y"
_FROM_TO = re.compile(
    r"\bfrom\s+([A-Z][\w]+(?:\s+[A-Z][\w]+)?)\s+to\s+"
    r"([A-Z][\w]+(?:\s+[A-Z][\w]+)?)"
)
_GO_TO = re.compile(r"\b(?:to|visit|see|explore|go to)\s+([A-Z][\w]+(?:\s+[A-Z][\w]+)?)")


def _parse_year(y: int) -> int:
    return 2000 + y if y < 100 else y


def _extract_dates(text: str) -> list[str]:
    out = []
    for m in _DATE_RE.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), _parse_year(int(m.group(3)))
        try:
            out.append(date(y, mo, d).isoformat())
        except ValueError:
            continue
    return out


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
    m = _TRAVELLERS_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_mode(text: str) -> Optional[str]:
    low = text.lower()
    for mode, kws in _MODE_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return mode.strip()
    return None

# "near Chennai", "around Chennai", "close to Chennai", "in Chennai"
_NEAR_CITY = re.compile(
    r"\b(?:near|around|close\s+to|outside)\s+([A-Z][\w]+(?:\s+[A-Z][\w]+)?)"
)

_NEAR_STOPWORDS = {
    "the", "a", "an", "here", "there", "home", "hill", "hills",
    "beach", "beaches", "mountain", "mountains", "town", "city",
}

def _extract_places(text, known_origin):
    m = _FROM_TO.search(text)
    if m:
        return m.group(1), m.group(2)

    origin = None
    dest = None
    low = text.lower()

    for city in _KNOWN_ORIGINS:
        if re.search(rf"\bfrom\s+{city}\b", low):
            origin = city.title()
            break

    # Explicit destination verb takes priority.
    m_to = _GO_TO.search(text)
    if m_to:
        dest = m_to.group(1)

   
    if origin is None:
        m_near = _NEAR_CITY.search(text)
        if m_near:
            cand = m_near.group(1)
            if (cand.lower() not in _NEAR_STOPWORDS
                    and (dest is None or cand.lower() != dest.lower())):
                has_dep_cue = bool(re.search(
                    r"\b(from|based|starting|coming\s+from|leaving)\b", low
                ))
                if has_dep_cue or cand.lower() in _KNOWN_ORIGINS:
                    origin = cand

    if _NLP is not None and not dest:
        doc = _NLP(text)
        gpes = [e.text for e in doc.ents if e.label_ in ("GPE", "LOC", "FAC")]
        for g in gpes:
            if origin and g.lower() == origin.lower():
                continue
            if re.search(rf"\bto\s+{re.escape(g)}", text):
                dest = g
                break
        if not dest and gpes and not origin:
            origin = gpes[0]

    return origin, dest


def extract_entities(text: str, known_origin: Optional[str] = None) -> dict:
    """Return a slots dict with only the fields found in `text`.

    Safe to call on any message — returns {} if nothing matches.
    """
    if not text or not text.strip():
        return {}

    out: dict = {}
    flags: list[str] = []

    origin, dest = _extract_places(text, known_origin)
    if origin and (not known_origin or origin.lower() != known_origin.lower()):
        out["origin"] = origin
        flags.append("ner_origin")
    if dest:
        out["destination_raw"] = dest
        flags.append("ner_destination")

    iso_dates = _extract_dates(text)
    if len(iso_dates) >= 1:
        out["start_date"] = iso_dates[0]
        flags.append("ner_start_date")
    if len(iso_dates) >= 2:
        out["end_date"] = iso_dates[1]
        flags.append("ner_end_date")

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

    if flags:
        out["confidence_flags"] = flags
    return out
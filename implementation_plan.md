# Proximity-Aware Destination Resolution — Gap Analysis & Implementation Plan

## What Already Exists (Surprising Findings)

After auditing the codebase, the current implementation is **much further along** than the proposal assumes. Here's what already exists:

### ✅ Already Done

| Capability | Current State | File |
|---|---|---|
| **Curated hill stations DB** | 30 entries with lat/lng/elevation across TN, KL, KA, AP | [`hill_stations.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/hill_stations.py) |
| **Proximity search for hills** | `hill_stations_near()` with haversine filter + sort | [`hill_stations.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/hill_stations.py#L55-L76) |
| **Discovery pipeline using curated DB** | `discover_nearby()` detects `wants_hills` and uses curated path | [`discovery.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/discovery.py#L660-L725) |
| **Tool: `search_destination_candidates`** | Geocodes origin, calls `discover_nearby()`, populates candidates | [`tools.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/tools.py#L287-L378) |
| **LLM candidate names are IGNORED** | Tool docstring: "LLM's `candidate_names` argument is ignored" | [`tools.py:291`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/tools.py#L291) |
| **Overpass fallback for non-hill queries** | Full 4-stage pipeline (Overpass → GeoNames → merge → score) | [`discovery.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/discovery.py#L727-L796) |
| **Route overrides** | 18 entries (not 6 as the proposal states) | [`transport.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/transport.py#L61-L80) |
| **18 known origins** | Chennai through Ahmedabad | [`ner.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/ner.py#L116-L120) |
| **Vague destination detection** | Regex-based in both NER and tools.py | [`ner.py:143-148`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/ner.py#L143-L148) |
| **Hill name hints in Overpass** | ~100 name hints for hill detection | [`discovery.py:65-167`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/discovery.py#L65-L167) |

> [!IMPORTANT]
> **Key insight**: The `search_destination_candidates` tool **already ignores LLM-proposed names** and uses `discovery.discover_nearby()` internally. The problem described in the proposal (LLM picks from training data) was already fixed at the tool level. The VAGUE_FLOW prompt still asks the LLM to propose names, but the tool ignores them.

---

## What Actually Needs to Change

The gaps fall into **4 categories**: expanding coverage, adding missing categories, prompt alignment, and NER enrichment.

---

### Phase 1: Expand Curated DB to Multi-Category (HIGH IMPACT)

**Gap**: [`hill_stations.py`](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/agent/hill_stations.py) only covers **hill stations** (30 entries). "Beaches near Bangalore" and "temples near Delhi" fall through to the generic Overpass path, which is slow, unreliable (mirror failures), and lacks category awareness.

#### [NEW] `destinations_db.py`
- Expand from 30 hill-station entries → ~150-200 entries across **all categories**: `hill_station`, `beach`, `temple`, `wildlife`, `heritage`, `backwater`, `desert`, `lake`, `offbeat`, `adventure`, `pilgrimage`, `coffee`, `trekking`, `waterfall`, `cave`.
- Structure:
```python
DESTINATIONS = [
    {"name": "Chikmagalur", "lat": 13.32, "lng": 75.77, "state": "Karnataka",
     "categories": ["hill_station", "coffee", "trekking"], "elevation_m": 1090},
    {"name": "Gokarna", "lat": 14.55, "lng": 74.32, "state": "Karnataka",
     "categories": ["beach", "temple"]},
    ...
]
```
- Subsumes `hill_stations.py` entirely (all 30 entries migrate into `destinations_db.py` with `categories: ["hill_station"]`).

#### [NEW] `nearby_search.py`
- Single function `find_nearby_destinations(origin_lat, origin_lng, category, max_radius_km, limit)`.
- Queries `destinations_db.DESTINATIONS` by haversine + category match.
- This replaces the hill-specific `hill_stations_near()` function with a generic one.

#### [MODIFY] `discovery.py`
- Replace the `wants_hills` → `hill_stations.hill_stations_near()` fast path with a generic category-aware fast path using `nearby_search.find_nearby_destinations()`.
- Add category detection beyond just hills: `wants_beach`, `wants_temple`, `wants_wildlife`, etc.
- Keep Overpass as fallback for uncategorized queries.

#### [DELETE] `hill_stations.py` (eventually)
- After migration, this file is redundant. Can keep as a deprecated alias during transition.

---

### Phase 2: New Discovery Tool + Prompt Alignment (MEDIUM IMPACT)

**Gap**: The `VAGUE_FLOW` prompt still instructs the LLM to "propose 4-6 specific, well-known real place names" even though the tool ignores them. This is confusing and wastes LLM reasoning tokens.

#### [NEW tool] `discover_nearby_destinations` in `schemas.py` and `tools.py`
- Clean API: takes `category` and optional `max_radius_km`.
- Internally calls `nearby_search.find_nearby_destinations()`.
- Returns distance-sorted candidates with distance shown.

> [!IMPORTANT]
> **Design question**: Should this be a NEW tool, or should we just update `search_destination_candidates` to accept a `category` parameter? The existing tool already ignores LLM candidate names and calls discovery internally. Adding a `category` param to the existing tool may be simpler than introducing a new tool + updating all the prompt wiring.
>
> **Recommendation**: Modify `search_destination_candidates` to accept an optional `category` parameter, and update `VAGUE_FLOW` to instruct the LLM to pass the category. This avoids the complexity of a new tool registration.

#### [MODIFY] `prompts.py` — `VAGUE_FLOW`
- Remove instruction to "propose 4-6 names".
- Instead: "Call `search_destination_candidates` with the detected category. The tool discovers candidates by proximity — do not propose names."
- Add instruction to show distances in the numbered list.

#### [MODIFY] `schemas.py`
- Either add `category` param to `SEARCH_DESTINATION_CANDIDATES`, or register `DISCOVER_NEARBY`.

#### [MODIFY] `orchestrator.py`
- Register new tool impl if we go the new-tool route.

---

### Phase 3: NER Enrichment (MEDIUM IMPACT)

**Gap**: NER detects "hill station" and "beach" as vague destinations but doesn't extract a structured `category` that tools can use directly.

#### [MODIFY] `ner.py`
- Add `_CATEGORY_KEYWORDS` mapping (same as proposal).
- Extract `destination_category` into the slots dict.
- Expand `_KNOWN_ORIGINS` from 18 → ~50 cities (tier-1 + tier-2).

#### [MODIFY] `tools.py` — `extract_trip_slots`
- Store extracted `destination_category` in state (needs a new field in `TripState`).
- Pass it to the discovery tool.

#### [MODIFY] `state.py`
- Add `destination_category: Optional[str] = None` to `TripState`.

---

### Phase 4: Expand Transport Coverage (LOW IMPACT)

**Gap**: 18 route overrides exist (not 6). Adding more is incremental improvement.

#### [MODIFY] `transport.py`
- Expand `ROUTE_OVERRIDES` from 18 → ~40 entries.
- Add Hyderabad, Pune, Kolkata, Jaipur as origin hubs.
- The haversine×1.3 fallback + Ola Maps tier already handle unlisted pairs adequately.

---

## Summary of Changes Needed

| Priority | File | Change | Effort |
|---|---|---|---|
| 🔴 HIGH | **[NEW]** `destinations_db.py` | ~150-200 curated destinations with categories + coords | Large (data entry) |
| 🔴 HIGH | **[NEW]** `nearby_search.py` | Generic proximity + category search function | Small |
| 🔴 HIGH | `discovery.py` | Replace hill-only fast path with category-aware fast path | Medium |
| 🟡 MED | `prompts.py` | Fix `VAGUE_FLOW` to stop asking LLM to propose names | Small |
| 🟡 MED | `schemas.py` | Add `category` param to `search_destination_candidates` | Small |
| 🟡 MED | `tools.py` | Pass category through to discovery | Small |
| 🟡 MED | `ner.py` | Add category extraction + expand known origins | Medium |
| 🟡 MED | `state.py` | Add `destination_category` field | Tiny |
| 🟢 LOW | `transport.py` | Expand route overrides from 18 → 40 | Medium (data entry) |
| 🟢 LOW | `hill_stations.py` | Deprecate after migration | Tiny |

## Open Questions

> [!IMPORTANT]
> **Q1: New tool vs. modified existing tool?**
> The current `search_destination_candidates` already ignores LLM names and uses discovery internally. Should we add a `category` parameter to it, or create the new `discover_nearby_destinations` tool as proposed? Modifying the existing tool is simpler.

> [!IMPORTANT]
> **Q2: How deep should category detection go?**
> Currently NER detects "hill station" and "beach" as vague. Should we add full category extraction for all 15 categories (temple, wildlife, heritage, etc.) in NER, or is it enough for the LLM to pass the category via the tool parameter?

> [!IMPORTANT]
> **Q3: Scope of destinations DB?**
> India-only with ~150-200 entries as proposed? The architecture supports expansion but initial data curation is the bottleneck.

## Verification Plan

### Automated Tests
```bash
# Test nearby search with category
python -c "from agent.nearby_search import find_nearby_destinations; r = find_nearby_destinations(13.08, 80.27, 'hill_station', 400); print([x['name'] for x in r])"

# Verify Chikmagalur + Yelagiri appear for Chennai + hill_station
python -c "
from agent.nearby_search import find_nearby_destinations
results = find_nearby_destinations(13.08, 80.27, 'hill_station', 400)
names = [r['name'] for r in results]
assert any('Chikmagalur' in n or 'Chikkamagaluru' in n for n in names)
assert any('Yelagiri' in n for n in names)
print('PASS:', names)
"

# Test beach category
python -c "from agent.nearby_search import find_nearby_destinations; r = find_nearby_destinations(12.97, 77.59, 'beach', 500); print([x['name'] for x in r])"
```

### Manual Verification
- "hill stations near Chennai" → shows Yelagiri, Kolli Hills, Yercaud, Chikmagalur before Munnar
- "beaches near Bangalore" → shows Gokarna, Murudeshwar, Mangalore
- "temples near Delhi" → shows Vrindavan, Mathura, Haridwar

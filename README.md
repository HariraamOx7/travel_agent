# Travel Agent

AI-powered travel itinerary and planning agent with FastAPI backend and React + Vite frontend.

## India place profiles and planning signals

The POI search reads OpenStreetMap tags for opening hours, fees, and wheelchair
access. `agent/place_profiles.py` merges these with approved entries in
`data/india_place_profiles.json`. The existing LLM activity classifier estimates
visit duration, intensity, interest tags, and indoor/outdoor status when a
place-specific approved value is unavailable. Every displayed stop carries its
data source; missing prices and accessibility information remain unknown.

Trip dates and Open-Meteo data feed estimated weather fit, crowd, and risk
scores. The CP-SAT scheduler applies a daily intensity cap (default 5, 8, or
11 for relaxed, balanced, or packed pace), visit time, trek, and verified
accessibility constraints. Simple weekly opening hours are checked; complex
OSM opening-hours expressions remain unverified. A* orders up to eight stops
per day using the Ola Maps road distance matrix when available. Longer days
use nearest-neighbor plus 2-opt. If Ola Maps is unavailable, route distances
are estimates and the itinerary labels them accordingly.

To add reviewed India attraction facts, edit `data/india_place_profiles.json`.
Each row must contain `name`; supported optional fields include
`opening_hours`, `entry_fee_inr`, `duration_minutes`, `intensity`,
`activity_tags`, `indoor_outdoor`, `best_months`, `accessibility`,
`booking_required`, `source_url`, and `last_verified`. Names match the OSM
place name case-insensitively. Add `osm_id` (for example `node/12345`) when
the same place name exists in more than one location.

The optional official-source refresh tool reads
`data/official_place_sources.json` and writes candidate text to
`data/official_place_review.json` for human review:

```powershell
python scripts/refresh_official_places.py
```

It uses Scrapy with `robots.txt` handling and a three-second domain delay.
Review the candidate text and update the approved profile file manually.

## Quick Start (One-Click Auto Start)

### Option 1: Double-Click (Windows Batch)
Double-click [start.bat](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/start.bat) in the project folder, or run from command prompt:
```cmd
start.bat
```

This will automatically:
1. Free ports `8100` and `5173` if occupied by stale processes.
2. Locate the Python virtual environment (`server\.venv`).
3. Check and install frontend dependencies if needed (`web\node_modules`).
4. Start the **FastAPI backend** on `http://127.0.0.1:8100`.
5. Start the **React + Vite frontend** on `http://localhost:5173`.
6. Open your default web browser directly to `http://localhost:5173`.

### Option 2: PowerShell
```powershell
.\start.ps1
```

### Stopping the Services
Double-click or run [stop.bat](file:///d:/ACADEMICS/Semester_5/AINLP/Project/travel-agent%20-%20working-react/stop.bat) to instantly terminate all backend and frontend services on ports 8100 and 5173.


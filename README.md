# Travel Agent

AI-powered travel itinerary and planning agent with FastAPI backend and React + Vite frontend.

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


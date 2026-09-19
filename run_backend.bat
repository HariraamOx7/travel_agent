@echo off
title Travel Agent - Backend (FastAPI :8000)
cd /d "%~dp0"

echo ================================================================
echo             TRAVEL AGENT - FASTAPI BACKEND (:8000)
echo ================================================================
echo.

set "PYTHON_EXE="
if exist "%~dp0server\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0server\.venv\Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Starting Uvicorn on http://127.0.0.1:8000 ...
"%PYTHON_EXE%" -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload

echo.
echo [BACKEND STOPPED]
pause

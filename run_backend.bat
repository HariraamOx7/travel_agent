@echo off
title Travel Agent - Backend (FastAPI :8100)
cd /d "%~dp0"

echo ================================================================
echo             TRAVEL AGENT - FASTAPI BACKEND (:8100)
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

echo Starting Uvicorn on http://127.0.0.1:8100 ...
"%PYTHON_EXE%" -m uvicorn server.main:app --host 127.0.0.1 --port 8100 --reload --reload-dir "%~dp0server" --reload-dir "%~dp0agent"

echo.
echo [BACKEND STOPPED]
pause

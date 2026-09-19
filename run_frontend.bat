@echo off
title Travel Agent - Frontend (Vite :5173)
cd /d "%~dp0web"

echo ================================================================
echo             TRAVEL AGENT - REACT FRONTEND (:5173)
echo ================================================================
echo.

if not exist "%~dp0web\node_modules" (
    echo Installing npm dependencies...
    call npm install
)

echo Starting Vite Dev Server...
call npm run dev

echo.
echo [FRONTEND STOPPED]
pause

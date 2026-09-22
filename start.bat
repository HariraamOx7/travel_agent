@echo off
setlocal enabledelayedexpansion
title Travel Agent Launcher
cd /d "%~dp0"

echo ================================================================
echo               TRAVEL AGENT - AUTO STARTER
echo ================================================================
echo.

:: 1. Free ports 8100 and 5173 if already occupied from previous runs
echo [*] Checking and freeing ports 8100 and 5173...
if exist "%~dp0free_ports.ps1" (
    powershell -ExecutionPolicy Bypass -File "%~dp0free_ports.ps1"
)

:: 2. Locate Python executable
set "PYTHON_EXE="
if exist "%~dp0server\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0server\.venv\Scripts\python.exe"
    echo [OK] Using Python from server\.venv
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    echo [OK] Using Python from .venv
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
        echo [OK] Using system Python
    )
)

if "%PYTHON_EXE%"=="" (
    echo.
    echo [ERROR] Python environment not found!
    echo Please ensure server\.venv exists.
    pause
    exit /b 1
)

:: 3. Verify Node.js and npm
where npm >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Node.js / npm not found in system PATH!
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)
echo [OK] Node.js and npm detected

:: 4. Check frontend dependencies
if not exist "%~dp0web\node_modules" (
    echo.
    echo [INFO] web\node_modules not found. Installing npm dependencies...
    cd /d "%~dp0web"
    call npm install
    if !errorlevel! neq 0 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    cd /d "%~dp0"
)

:: 5. Launch Backend first
echo.
echo [*] Launching FastAPI Backend...
start "Travel Agent - Backend" "%~dp0run_backend.bat"

:: 6. Wait until backend is responding on port 8100
if exist "%~dp0wait_for_backend.ps1" (
    powershell -ExecutionPolicy Bypass -File "%~dp0wait_for_backend.ps1" -Port 8100 -TimeoutSeconds 15
) else (
    ping -n 5 127.0.0.1 >nul
)

:: 7. Launch Frontend
echo [*] Launching Vite Frontend...
start "Travel Agent - Frontend" "%~dp0run_frontend.bat"

:: 8. Give frontend 2 seconds to bind port
ping -n 3 127.0.0.1 >nul

echo [*] Opening browser to http://localhost:5173 ...
start http://localhost:5173

echo.
echo ================================================================
echo               TRAVEL AGENT IS RUNNING!
echo ================================================================
echo   - Web App UI:       http://localhost:5173
echo   - FastAPI Backend:  http://127.0.0.1:8100
echo   - Interactive Docs: http://127.0.0.1:8100/docs
echo.
echo Backend and Frontend are running in separate terminal windows.
echo ================================================================
echo.

:menu
echo Options:
echo   [1] Open Frontend in browser
echo   [2] Open API Documentation (Swagger)
echo   [3] Stop all services and exit
echo   [4] Keep running and close this launcher window
echo.
set /p choice="Enter your choice (1-4): "

if "%choice%"=="1" (
    start http://localhost:5173
    goto menu
)
if "%choice%"=="2" (
    start http://127.0.0.1:8100/docs
    goto menu
)
if "%choice%"=="3" (
    goto stop_all
)
if "%choice%"=="4" (
    exit /b 0
)
echo Invalid option, please try again.
goto menu

:stop_all
echo.
echo Stopping all Travel Agent services...
if exist "%~dp0free_ports.ps1" (
    powershell -ExecutionPolicy Bypass -File "%~dp0free_ports.ps1"
)
echo All services stopped.
ping -n 3 127.0.0.1 >nul
exit /b 0

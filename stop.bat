@echo off
title Stop Travel Agent
cd /d "%~dp0"

echo ================================================================
echo             STOPPING TRAVEL AGENT SERVICES
echo ================================================================
echo.

if exist "%~dp0free_ports.ps1" (
    powershell -ExecutionPolicy Bypass -File "%~dp0free_ports.ps1"
) else (
    echo [ERROR] free_ports.ps1 not found!
)

echo.
echo ================================================================
echo All Travel Agent services have been stopped.
echo ================================================================
echo.
pause

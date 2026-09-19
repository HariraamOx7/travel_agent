<#
.SYNOPSIS
    Starts the Travel Agent FastAPI backend and Vite frontend.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $ScriptDir) { $ScriptDir = Get-Location }
Set-Location $ScriptDir

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "               TRAVEL AGENT - AUTO STARTER" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Release ports 8000 and 5173 if already occupied
Write-Host "[*] Checking and freeing ports 8000 and 5173..." -ForegroundColor Gray
if (Test-Path "$ScriptDir\free_ports.ps1") {
    & "$ScriptDir\free_ports.ps1"
}

# 2. Check Node / npm
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Node.js / npm not found in system PATH!" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}
Write-Host "[OK] Node.js and npm detected" -ForegroundColor Green

# 3. Check dependencies
if (-not (Test-Path "$ScriptDir\web\node_modules")) {
    Write-Host "[INFO] web\node_modules not found. Running npm install..." -ForegroundColor Yellow
    Push-Location "$ScriptDir\web"
    npm install
    Pop-Location
}

# 4. Launch Backend first
Write-Host ""
Write-Host "[*] Launching FastAPI Backend..." -ForegroundColor Cyan
Start-Process -FilePath "$ScriptDir\run_backend.bat" -WindowStyle Normal

# 5. Wait for backend to be ready on port 8000
if (Test-Path "$ScriptDir\wait_for_backend.ps1") {
    & "$ScriptDir\wait_for_backend.ps1" -Port 8000 -TimeoutSeconds 15
} else {
    Start-Sleep -Seconds 4
}

# 6. Launch Frontend
Write-Host "[*] Launching Vite Frontend..." -ForegroundColor Cyan
Start-Process -FilePath "$ScriptDir\run_frontend.bat" -WindowStyle Normal

# 7. Give frontend 2 seconds and open browser
Start-Sleep -Seconds 2
Write-Host "[*] Opening browser to http://localhost:5173 ..." -ForegroundColor Cyan
Start-Process "http://localhost:5173"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "               TRAVEL AGENT IS RUNNING!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  - Web App UI:      http://localhost:5173" -ForegroundColor White
Write-Host "  - FastAPI Backend:  http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  - Interactive Docs: http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""

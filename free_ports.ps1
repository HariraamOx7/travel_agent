# Releases ports 8000 (FastAPI) and 5173 (Vite) if currently in use
param(
    [int[]]$Ports = @(8000, 5173)
)

$stoppedCount = 0
foreach ($port in $Ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($connections) {
        $procIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $procIds) {
            if ($procId -gt 0) {
                try {
                    $procName = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                    Write-Host "  [STOPPED] Terminated PID $procId ($procName) on port $port" -ForegroundColor Yellow
                    $stoppedCount++
                } catch {
                    Write-Host "  [WARN] Failed to terminate PID $procId on port $port" -ForegroundColor Red
                }
            }
        }
    } else {
        Write-Host "  [INFO] Port $port is free" -ForegroundColor DarkGray
    }
}

if ($stoppedCount -eq 0) {
    Write-Host "  [INFO] No conflicting services found on ports $($Ports -join ', ')." -ForegroundColor Gray
}

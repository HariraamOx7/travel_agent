# Waits until the backend server is accepting connections
param(
    [int]$Port = 8000,
    [int]$TimeoutSeconds = 15
)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
Write-Host "  [*] Waiting for backend on port $Port to be ready..." -NoNewline

while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(400, $false) -and $tcp.Connected) {
            $tcp.Close()
            Write-Host " Ready! ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor Green
            exit 0
        }
        $tcp.Close()
    } catch {
        # ignore
    }
    Start-Sleep -Milliseconds 400
    Write-Host "." -NoNewline
}

Write-Host " Backend starting up..." -ForegroundColor Yellow
exit 0

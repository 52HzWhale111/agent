param([ValidateSet('all','gateway','knowledge','sandbox','backend')][string]$Service='all')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stateFile = Join-Path $projectRoot 'data/local/processes.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'No local process record.'; exit 0 }
$records = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
foreach ($record in $records) {
    if ($Service -ne 'all' -and $Service -ne $record.name) { continue }
    if ($record.wsl_keepalive_pid) {
        $holder = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.wsl_keepalive_pid)"
        if ($holder -and $holder.Name -eq 'wsl.exe' -and $holder.CommandLine.Contains('sleep infinity')) {
            Stop-Process -Id $holder.ProcessId -Force
        }
    }
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
    if (-not $candidate) { continue }
    # Refuse to stop an unrelated process if Windows has recycled its PID.
    $command = $candidate.CommandLine
    $portMatches = $command -and ($command.Contains("--port $($record.port)") -or ($record.module -eq 'com.zhigou.SandboxServerLocal' -and $record.port -eq 8200))
    if ($command -and $command.Contains($record.module) -and $portMatches) {
        # The Windows venv launcher has a child Python process; stop its whole tree.
        & taskkill.exe /PID $record.pid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop $($record.name)." }
        Write-Output "Stopped $($record.name) ($($record.pid))"
    } else { Write-Warning "PID $($record.pid) no longer matches the recorded service; skipped." }
}

param([ValidateSet('all','gateway','knowledge','sandbox','backend')][string]$Service='all')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stateFile = Join-Path $projectRoot 'data/local/processes.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'No local process record.'; exit 0 }
$records = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
foreach ($record in $records) {
    if ($Service -ne 'all' -and $Service -ne $record.name) { continue }
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
    if (-not $candidate) { continue }
    # Refuse to stop an unrelated process if Windows has recycled its PID.
    $command = $candidate.CommandLine
    if ($command -and $command.Contains($record.module) -and $command.Contains("--port $($record.port)")) {
        # The Windows venv launcher has a child Python process; stop its whole tree.
        & taskkill.exe /PID $record.pid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop $($record.name)." }
        Write-Output "Stopped $($record.name) ($($record.pid))"
    } else { Write-Warning "PID $($record.pid) no longer matches the recorded service; skipped." }
}

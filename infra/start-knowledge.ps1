param([string]$ConfigFile='')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $projectRoot 'data/local'
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not $ConfigFile) { $ConfigFile = Join-Path $runDir 'knowledge.env' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Install gateway/requirements-dev.txt in .venv first.' }
if ([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port -contains 8100) {
    throw 'Port 8100 is occupied. Stop the recorded service: infra/stop-local.ps1 -Service knowledge'
}
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$names = @('LLM_API_KEY','LLM_BASE_URL','LLM_MODEL')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$process = $null
try {
    if (Test-Path -LiteralPath $ConfigFile) {
        foreach ($line in Get-Content -LiteralPath $ConfigFile -Encoding UTF8) {
            if ($line -match '^\s*(LLM_API_KEY|LLM_BASE_URL|LLM_MODEL)\s*=(.*)$') {
                $value = $Matches[2].Trim()
                if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                [Environment]::SetEnvironmentVariable($Matches[1], $value, 'Process')
            }
        }
    }
    $arguments = @('-m','uvicorn','agents.knowledge.api:app','--host','127.0.0.1','--port','8100','--workers','1','--log-level','info')
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir 'knowledge.stdout.log') -RedirectStandardError (Join-Path $runDir 'knowledge.stderr.log')
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-RestMethod -Uri 'http://127.0.0.1:8100/openapi.json' -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw 'Knowledge service did not start. See data/local/knowledge.stderr.log.' }
    $stateFile = Join-Path $runDir 'processes.json'
    $existing = if (Test-Path -LiteralPath $stateFile) { Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } else { @() }
    $existing = @($existing | Where-Object { $_.name -ne 'knowledge' })
    $record = @{name='knowledge'; pid=$process.Id; module='agents.knowledge.api:app'; port=8100}
    ConvertTo-Json -InputObject @($existing + $record) | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Output 'Knowledge API: http://127.0.0.1:8100/docs. /health reports 503 until model configuration is complete.'
    Write-Output 'Model credentials stay in the child process environment and ignored knowledge.env.'
} catch {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    throw
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}

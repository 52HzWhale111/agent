param([switch]$MockAgents, [switch]$WithBackend, [ValidateSet('all','gateway','knowledge','sandbox','backend')][string]$Service='all')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$runDir = Join-Path $projectRoot 'data/local'
if (-not (Test-Path -LiteralPath $localPython)) { throw 'Missing .venv. Install gateway/requirements-dev.txt first.' }
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$specs = @()
if ($WithBackend) {
    $specs += @{ name='backend'; module='main:app'; port=8001 }
}
if ($MockAgents) {
    $specs += @{ name='knowledge'; module='gateway.dev_mock:app'; port=8100 }
    $specs += @{ name='sandbox'; module='gateway.dev_mock:app'; port=8200 }
}
$specs += @{ name='gateway'; module='gateway.main:app'; port=8000 }
if ($Service -ne 'all') {
    $specs = @($specs | Where-Object { $_.name -eq $Service })
    if ($specs.Count -eq 0) { throw 'To start a Mock Agent, pass -MockAgents.' }
}
$listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
foreach ($spec in $specs) {
    if ($listeners.Port -contains $spec.port) { throw "Port $($spec.port) is already in use. Stop the existing service first." }
}
$environmentNames = @('MOCK_SERVICE','GATEWAY_DATA_DIR','GATEWAY_API_KEY','KNOWLEDGE_AGENT_URL','SANDBOX_AGENT_URL','QA_TIMEOUT','DOC_REVIEW_TIMEOUT','CODE_RUN_TIMEOUT','MAX_UPLOAD_MB','MAX_PENDING_TASKS','BACKEND_URL','SANDBOX_PROTOCOL','DATABASE_URL','JWT_SECRET_KEY','UPLOAD_DIR','APP_ENV')
$previousEnvironment = @{}
foreach ($name in $environmentNames) { $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = @()
$completed = $false
$stateFile = Join-Path $runDir 'processes.json'
$existing = if (Test-Path -LiteralPath $stateFile) { Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } else { @() }
$existing = @($existing | Where-Object { $_.name -notin $specs.name })
try {
    $env:GATEWAY_DATA_DIR = $runDir
    $env:GATEWAY_API_KEY = ''
    $env:QA_TIMEOUT = '30'
    $env:DOC_REVIEW_TIMEOUT = '60'
    $env:CODE_RUN_TIMEOUT = '120'
    $env:MAX_UPLOAD_MB = '20'
    $env:MAX_PENDING_TASKS = '100'
    if ($MockAgents) {
        $env:KNOWLEDGE_AGENT_URL = 'http://127.0.0.1:8100'
        $env:SANDBOX_AGENT_URL = 'http://127.0.0.1:8200'
        $env:SANDBOX_PROTOCOL = if ($WithBackend) { 'text' } else { 'json' }
    } else {
        $env:SANDBOX_PROTOCOL = 'text'
    }
    if ($WithBackend) {
        $backendData = Join-Path $runDir 'backend'
        New-Item -ItemType Directory -Force -Path $backendData | Out-Null
        $keyFile = Join-Path $backendData 'jwt-key'
        if (-not (Test-Path -LiteralPath $keyFile)) {
            [IO.File]::WriteAllText($keyFile, ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')))
        }
        $env:BACKEND_URL = 'http://127.0.0.1:8001'
        $env:APP_ENV = 'development'
        $env:JWT_SECRET_KEY = [IO.File]::ReadAllText($keyFile)
        $env:DATABASE_URL = 'sqlite:///' + (Join-Path $backendData 'business.sqlite3').Replace('\','/')
        $env:UPLOAD_DIR = Join-Path $backendData 'uploads'
        if ($specs.name -contains 'backend') {
            & $localPython (Join-Path $projectRoot 'backend/scripts/manage.py') init-db --demo
            if ($LASTEXITCODE -ne 0) { throw 'Backend initialization failed.' }
        }
    }
    foreach ($spec in $specs) {
        $env:MOCK_SERVICE = $spec.name
        $arguments = @('-m','uvicorn',$spec.module,'--host','127.0.0.1','--port',"$($spec.port)",'--workers','1')
        if ($spec.name -eq 'backend') { $arguments += @('--app-dir','backend') }
        $process = Start-Process -FilePath $localPython -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "$($spec.name).stdout.log") -RedirectStandardError (Join-Path $runDir "$($spec.name).stderr.log")
        $started += @{ name=$spec.name; pid=$process.Id; module=$spec.module; port=$spec.port }
        @($existing + $started) | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    }
    foreach ($spec in $specs) {
        $path = if ($spec.name -eq 'gateway') { '/health/live' } else { '/health' }
        $isReady = $false
        for ($attempt=0; $attempt -lt 30; $attempt++) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:$($spec.port)$path" -TimeoutSec 1 | Out-Null
                $isReady = $true
                break
            } catch { Start-Sleep -Milliseconds 300 }
        }
        if (-not $isReady) { throw "$($spec.name) failed to start. See data/local/$($spec.name).stderr.log." }
    }
    Write-Output 'Gateway: http://127.0.0.1:8000/docs'
    if ($WithBackend) { Write-Output 'REAL business backend: http://127.0.0.1:8001/docs; gateway APIs require its login token.' }
    if ($MockAgents) { Write-Output 'MOCK mode: 8100=knowledge, 8200=sandbox. No real AI or compilation.' }
    Write-Output 'Stop: powershell -ExecutionPolicy Bypass -File infra/stop-local.ps1'
    $completed = $true
} finally {
    if (-not $completed) {
        # Roll back only processes started in this invocation; preserve existing services.
        foreach ($record in $started) {
            if (Get-Process -Id $record.pid -ErrorAction SilentlyContinue) {
                & taskkill.exe /PID $record.pid /T /F 2>$null | Out-Null
            }
        }
        if ($started.Count -gt 0) {
            ConvertTo-Json -InputObject @($existing) | Set-Content -LiteralPath $stateFile -Encoding UTF8
        }
    }
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process') }
}

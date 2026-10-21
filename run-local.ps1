param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    py -3 -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Please install Python 3.11 or later first.' }
    & $taskPython -m pip install -r (Join-Path $taskRoot 'backend\requirements-lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if ($env:APP_ENV -eq 'production') { throw 'This script creates demo accounts. Use manage.py init-db without --demo in production.' }
Push-Location (Join-Path $taskRoot 'backend')
try {
    & $taskPython scripts/manage.py init-db --demo
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
    Write-Host "Swagger: http://127.0.0.1:$Port/docs"
    & $taskPython -m uvicorn main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}

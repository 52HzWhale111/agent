param([string]$WslDistro='')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $projectRoot 'data/local'
$toolsDir = Join-Path $runDir 'tools'
$sandboxDir = Join-Path $projectRoot 'agents/sandbox'

$java = Get-Command java.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
if (-not $java) {
    $jdk = Get-ChildItem -LiteralPath (Join-Path $toolsDir 'java') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($jdk) { $java = Join-Path $jdk.FullName 'bin/java.exe' }
}
$maven = Get-Command mvn.cmd -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
if (-not $maven) {
    $distribution = Get-ChildItem -LiteralPath (Join-Path $toolsDir 'maven') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($distribution) { $maven = Join-Path $distribution.FullName 'bin/mvn.cmd' }
}
$docker = Get-Command docker.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
$dockerArguments = @()
if ($WslDistro) {
    $docker = (Get-Command wsl.exe).Source
    $dockerArguments = @('--distribution',$WslDistro,'--user','root','--exec','docker')
} elseif (-not $docker) {
    foreach ($candidate in @(
        (Join-Path $toolsDir 'DockerDesktop/resources/bin/docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) { $docker = $candidate; break }
    }
}
if (-not $java -or -not $maven -or -not $docker) { throw 'Install JDK 17, Maven and Docker first. For WSL Engine, use -WslDistro Debian. See docs/1021-sandbox-local-v1.md.' }
if ([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port -contains 8200) {
    throw 'Port 8200 is occupied. Stop only the recorded sandbox first: infra/stop-local.ps1 -Service sandbox'
}
$previousEnvironment = @{}
foreach ($key in @('Path','JAVA_HOME','SANDBOX_TMP_DIR','SANDBOX_WSL_DISTRO','WSL_UTF8')) { $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
$previousConsoleEncoding = [Console]::OutputEncoding
$process = $null
$stateFile = Join-Path $runDir 'processes.json'
$existing = if (Test-Path -LiteralPath $stateFile) { Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } else { @() }
$existing = @($existing | Where-Object { $_.name -ne 'sandbox' })
try {
    $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $java)
    $env:Path = (Split-Path -Parent $java) + ';' + (Split-Path -Parent $docker) + ';' + $env:Path
    $env:SANDBOX_TMP_DIR = Join-Path $sandboxDir 'tmp'
    $env:SANDBOX_WSL_DISTRO = $WslDistro
    if ($WslDistro) {
        $env:WSL_UTF8 = '1'
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        & $docker --distribution $WslDistro --user root --exec /usr/sbin/service docker start
        if ($LASTEXITCODE -ne 0) { throw 'Unable to start Docker Engine in WSL.' }
    }
    # Windows PowerShell 5.1 treats a WSL advisory on stderr as NativeCommandError.
    # Check the real command exit code, rather than failing on an advisory.
    $ErrorActionPreference = 'Continue'
    $engine = & $docker @dockerArguments info --format '{{.OSType}}' 2>$null
    $engineExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($engineExit -ne 0 -or $engine -ne 'linux') { throw 'Docker Linux engine is unavailable. Start Docker Engine in the selected WSL distro, or start Docker Desktop.' }
    $image = & $docker @dockerArguments images -q zhigou-sandbox-jdk17:v1
    if ($LASTEXITCODE -ne 0) { throw 'Unable to query Docker images.' }
    if (-not $image) {
        $buildContext = $sandboxDir
        if ($WslDistro) {
            $buildContext = & $docker --distribution $WslDistro --user root --exec wslpath -a -u $sandboxDir
            if ($LASTEXITCODE -ne 0) { throw 'Unable to translate build directory for WSL.' }
        }
        & $docker @dockerArguments build -t zhigou-sandbox-jdk17:v1 $buildContext
        if ($LASTEXITCODE -ne 0) { throw 'Sandbox image build failed.' }
    }
    & $maven ('-Dmaven.repo.local=' + (Join-Path $toolsDir 'm2')) -f (Join-Path $sandboxDir 'pom.xml') compile -B -ntp
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox Java compilation failed.' }
    $arguments = @('-Dfile.encoding=UTF-8','-cp',('"' + (Join-Path $sandboxDir 'target/classes') + '"'),'com.zhigou.SandboxServerLocal')
    $process = Start-Process -FilePath $java -ArgumentList $arguments -WorkingDirectory $sandboxDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir 'sandbox.stdout.log') -RedirectStandardError (Join-Path $runDir 'sandbox.stderr.log')
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Method Options -Uri 'http://127.0.0.1:8200/sandbox/run-tests' -TimeoutSec 2
            if ($response.StatusCode -eq 204) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw 'Real sandbox failed to start. See data/local/sandbox.stderr.log.' }
    $record = @{name='sandbox'; pid=$process.Id; module='com.zhigou.SandboxServerLocal'; port=8200}
    ConvertTo-Json -InputObject @($existing + $record) | Set-Content -LiteralPath $stateFile -Encoding UTF8
    $verification = if ($WslDistro) { 'python -m gateway.smoke_sandbox --wsl-distro ' + $WslDistro } else { 'python -m gateway.smoke_sandbox' }
    Write-Output ('REAL Docker sandbox listening on 127.0.0.1:8200. Verify: ' + $verification)
} catch {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    throw
} finally {
    foreach ($key in $previousEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], 'Process') }
    [Console]::OutputEncoding = $previousConsoleEncoding
}

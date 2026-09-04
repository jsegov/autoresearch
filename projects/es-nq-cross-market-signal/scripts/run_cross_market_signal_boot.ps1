# Launches the ES/NQ cross-market direction signal service detached (never blocks
# the caller). Stops any existing instance first so a re-run cannot double-write
# es_nq_direction_signals.jsonl.
#
#   powershell -ExecutionPolicy Bypass -File "C:\Users\14342\autoresearch-win-rtx\projects\es-nq-cross-market-signal\scripts\run_cross_market_signal_boot.ps1"

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $ProjectRoot "logs"
$pidFile = Join-Path $logDir "cross_market_signal.pid"
$runner = Join-Path $ProjectRoot "scripts\run_live_signal.py"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path -LiteralPath $runner)) {
    Write-Error "Missing runner: $runner"
}

# Resolve python: prefer the project venv, then the saved path, then py/PATH.
$pythonExe = $null
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPy) { $pythonExe = $venvPy }
if (-not $pythonExe) {
    $saved = Join-Path $PSScriptRoot "python_for_task.txt"
    if (Test-Path -LiteralPath $saved) {
        $candidate = (Get-Content -LiteralPath $saved -Raw).Trim()
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $pythonExe = $candidate }
    }
}
if (-not $pythonExe) {
    try {
        $raw = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($raw) { $pythonExe = $raw.Trim() }
    } catch {}
}
if (-not $pythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $pythonExe = $cmd.Source }
}
if (-not $pythonExe -or -not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error "Could not resolve python.exe for $ProjectRoot"
}

# Stop any running instance (pid file first, then command-line match).
if (Test-Path -LiteralPath $pidFile) {
    try {
        $oldPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ($oldPid -gt 0) {
            Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { ([string]$_.CommandLine) -match 'run_live_signal\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$stamp = Get-Date -Format "yyyyMMdd"
$outLog = Join-Path $logDir "cross_market_signal_$stamp.out.log"
$errLog = Join-Path $logDir "cross_market_signal_$stamp.err.log"

$proc = Start-Process -FilePath $pythonExe `
    -ArgumentList @("`"$runner`"") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding UTF8
Write-Host "Cross-market signal service started pid=$($proc.Id)"
Write-Host "Python: $pythonExe"
Write-Host "Logs:   $errLog"

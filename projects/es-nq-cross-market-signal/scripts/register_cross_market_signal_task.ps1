# Registers a scheduled task so the ES/NQ cross-market direction signal service
# starts automatically (at logon and every weekday pre-open), instead of needing
# a manual foreground launch. The producer stopped after RTH close 2026-08-03 and
# never came back, which is what this task prevents.
#
# Run ONCE, interactively:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\14342\autoresearch-win-rtx\projects\es-nq-cross-market-signal\scripts\register_cross_market_signal_task.ps1"
#
# Requires the Sierra cross-market exporter listening on 127.0.0.1:5563.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot "scripts\run_live_signal.py"))) {
    Write-Error "Expected scripts\run_live_signal.py in $ProjectRoot"
}

# Resolve python to a FULL path (Task Scheduler does not use PATH).
$pythonExe = $null
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPy) {
    $pythonExe = $venvPy
    Write-Host "Using venv: $pythonExe"
}
if (-not $pythonExe) {
    try {
        $raw = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($raw) {
            $pythonExe = $raw.Trim()
            Write-Host "Using py launcher: $pythonExe"
        }
    } catch {}
}
if (-not $pythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonExe = $cmd.Source
        Write-Host "Using PATH python: $pythonExe"
    }
}
if (-not $pythonExe -or -not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error "Could not resolve python.exe for $ProjectRoot"
}

# Saved for the boot script's fallback resolution.
Set-Content -Path (Join-Path $PSScriptRoot "python_for_task.txt") -Value $pythonExe -Encoding UTF8

$bootScript = Join-Path $PSScriptRoot "run_cross_market_signal_boot.ps1"
if (-not (Test-Path -LiteralPath $bootScript)) {
    Write-Error "Missing $bootScript"
}

$TaskName = "ESNQCrossMarketSignal"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$ps = (Get-Command powershell.exe).Source
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bootScript`""

$Action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $ProjectRoot

# Two triggers: at logon, and 09:00 ET each weekday (well before the 09:35 ET
# alert window). The boot script stops any existing instance, so overlapping
# triggers cannot produce two writers.
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$TriggerDaily = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:00AM

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $TriggerLogon,$TriggerDaily -Settings $Settings -RunLevel Limited -Description "ES/NQ cross-market OFI direction signal producer (writes es_nq_direction_signals.jsonl)"

Write-Host ""
Write-Host "OK: Scheduled task '$TaskName' registered (at logon + weekdays 09:00)."
Write-Host "Project: $ProjectRoot"
Write-Host "Python:  $pythonExe"
Write-Host "Signals: $(Join-Path $ProjectRoot 'data\es_nq_direction_signals.jsonl')"
Write-Host ""
Write-Host "Start now: Start-ScheduledTask -TaskName $TaskName"
Write-Host "Remove:    Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""

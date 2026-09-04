$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = "C:\Python314\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$OutLog = Join-Path $LogDir "live_signal.out.log"
$ErrLog = Join-Path $LogDir "live_signal.err.log"

New-Item -ItemType Directory -Force $LogDir | Out-Null

$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*" -and
        $_.CommandLine -match "run_live_signal\.py"
    }

if ($running) {
    exit 0
}

Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @("scripts\run_live_signal.py") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

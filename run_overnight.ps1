<#
.SYNOPSIS
  Unattended overnight runner for the AutoResearch propeller loop.
  Kick this off AFTER a supervised Phase 0 + analytical shakedown has passed.

.DESCRIPTION
  Babysits `python -m autoresearch.researcher`:
    - resumes on crash (the loop reads data/research.db to pick up where it left off)
    - caps restarts and total wall-clock so it can never run away
    - logs everything, timestamped, to docs/logs/
    - writes data/RUN_STATUS.txt with the final verdict for you to read in the morning
    - stops gracefully if you drop a file named STOP in the project root
    - optional Telegram ping on finish (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)

.EXAMPLE
  # full swarm loop, default guards:
  powershell -ExecutionPolicy Bypass -File .\run_overnight.ps1

.EXAMPLE
  # deterministic check (no LLM), shorter:
  .\run_overnight.ps1 -NoLLM -Budget 30 -MaxHours 1
#>
[CmdletBinding()]
param(
    [int]$Budget      = 28800,   # passed straight to the researcher (--budget)
    [int]$MaxRestarts = 12,      # crash restarts before giving up
    [double]$MaxHours = 9.0,     # hard wall-clock cap for the whole session
    [switch]$NoLLM               # run the deterministic loop instead of the swarm
)

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$srcDir  = Join-Path $root 'src'
$logDir  = Join-Path $root 'docs\logs'
$stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'
$logFile = Join-Path $logDir "overnight_$stamp.log"
$status  = Join-Path $root 'data\RUN_STATUS.txt'
$stopFile= Join-Path $root 'STOP'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

function Write-Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

function Send-Telegram([string]$text) {
    $tok = $env:TELEGRAM_BOT_TOKEN; $chat = $env:TELEGRAM_CHAT_ID
    if ([string]::IsNullOrWhiteSpace($tok) -or [string]::IsNullOrWhiteSpace($chat)) { return }
    try {
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$tok/sendMessage" `
            -Body @{ chat_id = $chat; text = $text } -TimeoutSec 20 | Out-Null
        Write-Log "Telegram notified."
    } catch { Write-Log "Telegram ping failed: $($_.Exception.Message)" }
}

function Set-Status([string]$verdict) {
    Set-Content -Path $status -Value @"
Run finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Verdict:      $verdict
Log:          $logFile
Started:      $stamp
"@ -Encoding utf8
}

# --- build the researcher command -------------------------------------------
$argList = @('-m','autoresearch.researcher','--budget',"$Budget")
if ($NoLLM) { $argList += '--no-llm' }
$cmdStr = "python $($argList -join ' ')"

Write-Log "=== Overnight AutoResearch run started ==="
Write-Log "cwd=$srcDir  cmd=$cmdStr  MaxRestarts=$MaxRestarts  MaxHours=$MaxHours"
Send-Telegram "Propeller overnight run started ($stamp). Budget=$Budget, cap=$MaxHours h."

if (-not (Test-Path (Join-Path $srcDir 'autoresearch'))) {
    Write-Log "FATAL: src/autoresearch not found. Phase 0 (port code) must be done first."
    Set-Status "ABORTED - code not ported yet"
    Send-Telegram "Propeller run ABORTED: code not ported (run Phase 0 first)."
    exit 1
}

$deadline = (Get-Date).AddHours($MaxHours)
$attempt  = 0
$verdict  = "UNKNOWN"

while ($true) {
    if (Test-Path $stopFile) {
        Write-Log "STOP file detected - halting gracefully."
        $verdict = "STOPPED by user (STOP file)"
        break
    }
    if ((Get-Date) -ge $deadline) {
        Write-Log "Wall-clock cap of $MaxHours h reached - halting."
        $verdict = "TIME CAP reached after $attempt attempt(s)"
        break
    }

    $attempt++
    Write-Log "--- attempt $attempt / $MaxRestarts ---"

    # run the researcher, appending its stdout+stderr to our log
    Push-Location $srcDir
    try {
        & cmd /c "$cmdStr 1>> `"$logFile`" 2>>&1"
        $code = $LASTEXITCODE
    } finally { Pop-Location }

    Write-Log "researcher exited with code $code"

    if ($code -eq 0) {
        $verdict = "SUCCESS (budget/convergence reached) after $attempt attempt(s)"
        break
    }
    if ($attempt -ge $MaxRestarts) {
        $verdict = "GAVE UP after $MaxRestarts crashes (last exit $code)"
        break
    }

    # backoff before resuming, but stay responsive to STOP / deadline
    $wait = [math]::Min(300, 15 * $attempt)
    Write-Log "crash - resuming in ${wait}s (loop resumes from data/research.db)"
    for ($i = 0; $i -lt $wait; $i += 5) {
        if (Test-Path $stopFile) { break }
        Start-Sleep -Seconds 5
    }
}

Write-Log "=== Run finished: $verdict ==="
Set-Status $verdict
Send-Telegram "Propeller overnight run done: $verdict. Log: $logFile"

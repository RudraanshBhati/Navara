<#
Register the news agent as a Windows scheduled task, every 6 hours.

    powershell -ExecutionPolicy Bypass -File scripts\schedule_news_agent.ps1
    powershell -ExecutionPolicy Bypass -File scripts\schedule_news_agent.ps1 -Remove

Why a schedule at all: RSS feeds hold the last ~20-50 items, so a story is
gone from them within a day or two. Every day the agent does not run is a day
of incidents the archive can never get back. Six hours is well inside that.

The task runs as you, only while you are logged on, so it needs nothing
beyond what you already have. Each run appends one line to
backend\data\archive\agent_runs.jsonl, and its console output goes to
backend\data\processed\news_agent_last_run.log.
#>
param(
    [switch]$Remove,
    [int]$EveryHours = 6
)

$ErrorActionPreference = "Stop"
$TaskName = "NAVARA news agent"
$Backend = (Resolve-Path (Join-Path $PSScriptRoot "..\backend")).Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
    return
}

# A scheduled run cannot ask which extractor to use; it reads backend\.env.
# With neither an Anthropic key nor EXTRACTION_PROVIDER=ollama there, every
# run would fail its preflight and record nothing.
$envFile = Join-Path $Backend ".env"
$envText = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }
$hasAnthropic = $envText -match "(?m)^ANTHROPIC_API_KEY=\S+"
$usesOllama = $envText -match "(?m)^EXTRACTION_PROVIDER=ollama\s*$"
if (-not ($hasAnthropic -or $usesOllama)) {
    Write-Error ("No extractor configured in backend\.env. Set ANTHROPIC_API_KEY, or " +
        "EXTRACTION_PROVIDER=ollama with Ollama running, then re-run this script.")
}

$uv = (Get-Command uv).Source
$log = Join-Path $Backend "data\processed\news_agent_last_run.log"
$command = "Set-Location '$Backend'; & '$uv' run python ..\scripts\run_news_agent.py *> '$log'"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$command`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours)
# Catch up after the laptop was asleep or off at the scheduled time.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Fetch Delhi NCR safety news into the NAVARA archive." -Force | Out-Null

Write-Host "Registered '$TaskName': every $EveryHours h, first run in 5 minutes."
Write-Host "Console output: $log"

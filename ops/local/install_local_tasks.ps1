$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$SupportScript = Join-Path $RepoRoot 'ops\local\run_local_support_refresh.ps1'
$CalllogScript = Join-Path $RepoRoot 'ops\local\run_local_calllog_scraper.ps1'

function Register-RepoPowerShellTask {
    param(
        [Parameter(Mandatory=$true)][string]$TaskName,
        [Parameter(Mandatory=$true)][string]$ScriptPath,
        [Parameter(Mandatory=$true)]$Trigger,
        [switch]$Disabled
    )

    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath) `
        -WorkingDirectory $RepoRoot
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $Trigger `
        -Settings $settings `
        -Description 'SBCO local automation managed from sbco-calllog-scraper repo' `
        -Force | Out-Null

    if ($Disabled) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    } else {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
    }
}

$supportTrigger = New-ScheduledTaskTrigger -Daily -At 11:53am
Register-RepoPowerShellTask `
    -TaskName 'SBCO Support Files Refresh' `
    -ScriptPath $SupportScript `
    -Trigger $supportTrigger

$calllogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date.AddHours(3) `
    -RepetitionInterval (New-TimeSpan -Minutes 20) `
    -RepetitionDuration (New-TimeSpan -Days 1)
Register-RepoPowerShellTask `
    -TaskName 'SBCO Local SBSO CHP Fire Feed Scraper' `
    -ScriptPath $CalllogScript `
    -Trigger $calllogTrigger `
    -Disabled

if (Get-ScheduledTask -TaskName 'download release list' -ErrorAction SilentlyContinue) {
    try {
        Disable-ScheduledTask -TaskName 'download release list' -ErrorAction Stop | Out-Null
        Write-Host 'Disabled old task: download release list'
    } catch {
        Write-Warning "Could not disable old task 'download release list': $($_.Exception.Message)"
    }
}

Write-Host 'Installed/updated local SBCO tasks.'
Write-Host 'Enabled:  SBCO Support Files Refresh (daily arrest/release/death support refresh)'
Write-Host 'Disabled: SBCO Local SBSO CHP Fire Feed Scraper (manual/standby feed scraper; no support refresh, no publish by default)'

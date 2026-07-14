$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$DefaultBaseDir = Join-Path $env:USERPROFILE 'Documents\python'
$BaseDir = if ($env:SBCO_BASE_DIR) { $env:SBCO_BASE_DIR } else { $DefaultBaseDir }
$LogDir = Join-Path $BaseDir 'output\task_logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("local_feed_scraper_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

function Write-LocalLog {
    param([string]$Message)
    "[{0}] {1}" -f (Get-Date -Format s), $Message | Tee-Object -FilePath $LogPath -Append
}

Set-Location $RepoRoot
$python = (Get-Command python -ErrorAction Stop).Source

$env:SBCO_BASE_DIR = $BaseDir
$env:SBCO_SERVER_CALLLOG_URL = if ($env:SBCO_SERVER_CALLLOG_URL) { $env:SBCO_SERVER_CALLLOG_URL } else { 'https://upnexx.xyz/osint/calllog.csv' }
$env:SBCO_REMOTE_BACKED_DAILY_FILES = if ($env:SBCO_REMOTE_BACKED_DAILY_FILES) { $env:SBCO_REMOTE_BACKED_DAILY_FILES } else { '1' }
$env:SBCO_ENABLE_DAILY_RELEASES = if ($env:SBCO_ENABLE_DAILY_RELEASES) { $env:SBCO_ENABLE_DAILY_RELEASES } else { '1' }
$env:SBCO_HTTP_UPLOAD_SOURCE = if ($env:SBCO_HTTP_UPLOAD_SOURCE) { $env:SBCO_HTTP_UPLOAD_SOURCE } else { 'local-windows' }
$env:SBCO_RUNNER_ID = if ($env:SBCO_RUNNER_ID) { $env:SBCO_RUNNER_ID } else { "local-windows-{0}" -f (Get-Date -Format 'yyyyMMddHHmmss') }
$env:SBCO_RUN_HARD_TIMEOUT_SECONDS = if ($env:SBCO_RUN_HARD_TIMEOUT_SECONDS) { $env:SBCO_RUN_HARD_TIMEOUT_SECONDS } else { '1200' }
$env:SBCO_SKIP_SUPPORT_FILES = if ($env:SBCO_SKIP_SUPPORT_FILES) { $env:SBCO_SKIP_SUPPORT_FILES } else { '1' }
$env:SBCO_SKIP_ARREST_INDEX_REBUILD = if ($env:SBCO_SKIP_ARREST_INDEX_REBUILD) { $env:SBCO_SKIP_ARREST_INDEX_REBUILD } else { '1' }
$env:SBCO_SKIP_PUBLISH = if ($env:SBCO_SKIP_PUBLISH) { $env:SBCO_SKIP_PUBLISH } else { '1' }
$env:SBCO_DEATH_INDEX_REFRESH_TIMEOUT_SECONDS = if ($env:SBCO_DEATH_INDEX_REFRESH_TIMEOUT_SECONDS) { $env:SBCO_DEATH_INDEX_REFRESH_TIMEOUT_SECONDS } else { '600' }
$env:SBCO_ARREST_LOG_REFRESH_TIMEOUT_SECONDS = if ($env:SBCO_ARREST_LOG_REFRESH_TIMEOUT_SECONDS) { $env:SBCO_ARREST_LOG_REFRESH_TIMEOUT_SECONDS } else { '600' }
$env:SBCO_ARREST_LOG_REQUEST_DELAY_SECONDS = if ($env:SBCO_ARREST_LOG_REQUEST_DELAY_SECONDS) { $env:SBCO_ARREST_LOG_REQUEST_DELAY_SECONDS } else { '2.0' }
$env:SBCO_ARREST_LOG_MAX_PAGES = if ($env:SBCO_ARREST_LOG_MAX_PAGES) { $env:SBCO_ARREST_LOG_MAX_PAGES } else { '3' }

Write-LocalLog "Starting local SBSO/CHP/Fire feed scraper from $RepoRoot with base dir $BaseDir"
& $python (Join-Path $RepoRoot 'scraper_run.py') 2>&1 | Tee-Object -FilePath $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    throw "Local SBSO/CHP/Fire feed scraper failed with exit code $LASTEXITCODE. Log: $LogPath"
}
Write-LocalLog "Local SBSO/CHP/Fire feed scraper completed"

$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$DefaultBaseDir = Join-Path $env:USERPROFILE 'Documents\python'
$BaseDir = if ($env:SBCO_BASE_DIR) { $env:SBCO_BASE_DIR } else { $DefaultBaseDir }
$LogDir = Join-Path $BaseDir 'output\task_logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("local_support_refresh_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$ForceArgs = if ($env:SBCO_FORCE_SUPPORT_REFRESH -eq '1') { @('--force') } else { @() }

Set-Location $RepoRoot
$python = (Get-Command python -ErrorAction Stop).Source

$env:SBCO_BASE_DIR = $BaseDir
$env:SBCO_SERVER_CALLLOG_URL = if ($env:SBCO_SERVER_CALLLOG_URL) { $env:SBCO_SERVER_CALLLOG_URL } else { 'https://upnexx.xyz/osint/calllog.csv' }
$env:SBCO_REMOTE_BACKED_DAILY_FILES = if ($env:SBCO_REMOTE_BACKED_DAILY_FILES) { $env:SBCO_REMOTE_BACKED_DAILY_FILES } else { '1' }
$env:SBCO_ENABLE_DAILY_RELEASES = if ($env:SBCO_ENABLE_DAILY_RELEASES) { $env:SBCO_ENABLE_DAILY_RELEASES } else { '1' }

"[{0}] Starting local arrest/release/death support refresh" -f (Get-Date -Format s) | Tee-Object -FilePath $LogPath -Append
$SupportArgs = @((Join-Path $RepoRoot 'local_support_refresh.py'), '--base-dir', $BaseDir) + $ForceArgs
& $python @SupportArgs 2>&1 |
    Tee-Object -FilePath $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    throw "Local support refresh failed with exit code $LASTEXITCODE. Log: $LogPath"
}
"[{0}] Local support refresh completed" -f (Get-Date -Format s) | Tee-Object -FilePath $LogPath -Append

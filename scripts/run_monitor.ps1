# LVS read-only monitoring invocation for Windows Task Scheduler.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "data\monitor_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not $env:BASE_RPC_URL) { $env:BASE_RPC_URL = "https://mainnet.base.org" }
Set-Location $ProjectRoot
$Stamp = Get-Date -Format "yyyy-MM-dd"
$Log = Join-Path $LogDir "$Stamp.log"
if (-not (Test-Path $Python)) { throw "Python venv missing: $Python" }
& $Python -m lapidus_vaults.monitor_once --wallet 0x8685F85E3afcE79310658FB09d85BB98ecFc7EdE --allowlist data/paper_test_allowlist.json --drift-bps 500 --db data/lapidus.sqlite3 --alert-file data/monitor_status.json *>> $Log
exit $LASTEXITCODE

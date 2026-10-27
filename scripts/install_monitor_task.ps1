# Run once in PowerShell to install 15-minute read-only LVS monitoring.
# This creates a Windows scheduled task, not a persistent Python process.
$ErrorActionPreference = "Stop"
$Runner = Join-Path $PSScriptRoot "run_monitor.ps1"
if (-not (Test-Path $Runner)) { throw "Missing run_monitor.ps1" }
$TaskName = "Lapidus Vault Systems - Read Only Monitor"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $Runner + '"')
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 365)
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Read-only Morpho API/Base RPC monitoring; no signing or funds movement." -Force | Out-Null
Write-Host "Installed: $TaskName (15-minute interval; task ends after one year unless renewed)"
Write-Host "Inspect: Get-ScheduledTask -TaskName '$TaskName'"
Write-Host 'To remove: Unregister-ScheduledTask -TaskName "Lapidus Vault Systems - Read Only Monitor" -Confirm:$false'

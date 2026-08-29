param(
    [string]$Time = "21:00",
    [string]$ExePath = "$PSScriptRoot\dist\PcStorc.exe"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ExePath)) {
    throw "PcStorc.exe не найден: $ExePath. Сначала выполните build_windows.ps1 или укажите -ExePath."
}

$action = New-ScheduledTaskAction -Execute $ExePath -Argument "--backup"
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "PcStorc Daily Backup" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Ежедневный backup PcStorc зарегистрирован на $Time"

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*backend\scripts\watchdog.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
& "$root\stop.ps1" -InstallDir $root

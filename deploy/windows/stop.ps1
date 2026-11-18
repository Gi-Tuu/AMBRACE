# M8 Windows 停止脚本：先优雅关 Core（/v1/shutdown），再停 AMBRACE。
param([string]$InstallDir = "D:\AiCompanion", [int]$AmbragePort = 8000, [int]$CorePort = 8001, [switch]$DryRun)
$ErrorActionPreference = "Continue"
if ($DryRun) { Write-Host "[DryRun] 计划：POST /v1/shutdown → 停 8000 → 清理残留 Core（InstallDir=$InstallDir）"; return }
$secretDir = Join-Path $InstallDir "secrets"
$keyFile = Join-Path $secretDir "yai_service_key.txt"
if (-not (Test-Path $keyFile)) { Write-Host "未找到 service key，跳过 Core shutdown"; }
else {
  $key = (Get-Content $keyFile -Raw).Trim()
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$CorePort/v1/shutdown" -Method POST `
      -Headers @{ "X-Service-Key" = $key } -TimeoutSec 5 -UseBasicParsing
    Write-Host "Core shutdown: $($r.StatusCode)"
  } catch { Write-Host "Core 可能已停止：$($_.Exception.Message)" }
}

$line = netstat -ano | Select-String ":$AmbragePort" | Select-String "LISTENING" | Select-Object -First 1
if ($line) {
  $pid8000 = ($line.Line -split "\s+")[-1]
  Stop-Process -Id $pid8000 -Force -ErrorAction SilentlyContinue
  Write-Host "AMBRACE $AmbragePort 已停止（pid=$pid8000）"
}
Start-Sleep -Seconds 2
$coreLeft = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*yai_core.server*" }
if ($coreLeft) { $coreLeft | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } ; Write-Host "已清理残留 Core" }
else { Write-Host "无 Core 残留" }

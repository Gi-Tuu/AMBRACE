# Core M7 supervisor：独立于 AMBRACE 启停；等 AMBRACE 健康后再起 Core；校验工具挂载；异常退出自动重启并记录 exit code。
# 用法：pwsh -NoProfile -File core_supervisor.ps1 -Port 8001
param(
  [int]$Port = 8001,
  [string]$AmbraceBaseUrl = $(if ($env:AMBRACE_BASE_URL) { $env:AMBRACE_BASE_URL } else { "http://127.0.0.1:8000" }),
  [string]$CoreRoot = $(if ($env:CORE_ROOT) { $env:CORE_ROOT } else { "E:\Competition\YAI Agent Core" }),
  [string]$TokenFile = $env:AMBRACE_TOKEN_FILE,
  [string]$ServiceKey = $env:YAI_SERVICE_KEY
)
$ErrorActionPreference = "Continue"
if ([string]::IsNullOrWhiteSpace($ServiceKey)) {
  Write-Error "YAI_SERVICE_KEY 未设置，拒绝启动（不再提供默认密钥）"
  exit 1
}
if ([string]::IsNullOrWhiteSpace($TokenFile)) {
  Write-Error "AMBRACE_TOKEN_FILE 未设置，拒绝启动（避免可预测默认路径）"
  exit 1
}
$logDir = Join-Path  "..\backend\data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$supLog = Join-Path $logDir "core-supervisor.log"
Set-Location $CoreRoot
while ($true) {
  # 1) 等 AMBRACE 健康（避免 OpenAPI 挂载降级）
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $h = Invoke-WebRequest -Uri "$AmbraceBaseUrl/api/v1/system/health" -TimeoutSec 3
      if ($h.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $ready) { Start-Sleep -Seconds 2; continue }

  $token = if (Test-Path $TokenFile) { (Get-Content $TokenFile -Raw).Trim() } else { "" }
  $env:OPENAPI_SPEC_URL = "$AmbraceBaseUrl/openapi.json"
  $env:OPENAPI_BASE_URL = $AmbraceBaseUrl
  $env:OPENAPI_AUTH_TOKEN = $token
  $env:OPENAPI_ALLOW_WRITES = "true"
  $env:YAI_TOOL_TIMEOUT = "60"
  $env:YAI_SERVICE_KEY = $ServiceKey
  Add-Content -LiteralPath $supLog -Value ("[{0}] starting core port={1} base={2}" -f (Get-Date -Format o), $Port, $AmbraceBaseUrl)
  $out = Join-Path $logDir "core-m7.out.log"
  $err = Join-Path $logDir "core-m7.err.log"
  $p = Start-Process -FilePath "$CoreRoot\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "scripts.serve_example:app", "--host", "127.0.0.1", "--port", "$Port" `
    -PassThru -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError $err

  # 2) 校验工具挂载；0 个则重启本次子进程（避免降级跳过）
  $mounted = $false
  for ($j = 0; $j -lt 20; $j++) {
    Start-Sleep -Seconds 1
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/v1/tools" -TimeoutSec 3
      $tools = ($resp.Content | ConvertFrom-Json) | Where-Object { $_.source -eq "openapi" }
      if (($tools | Measure-Object).Count -gt 0) { $mounted = $true; break }
    } catch {}
  }
  if (-not $mounted) {
    Add-Content -LiteralPath $supLog -Value ("[{0}] mount failed (openapi=0), restarting child" -f (Get-Date -Format o))
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    continue
  }

  Wait-Process -Id $p.Id -ErrorAction SilentlyContinue
  Add-Content -LiteralPath $supLog -Value ("[{0}] core exited code={1}" -f (Get-Date -Format o), $p.ExitCode)
  Start-Sleep -Seconds 2
}

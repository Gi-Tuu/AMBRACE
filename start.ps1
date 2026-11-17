# M8 Windows 安装器 / 启动脚本（骨架，待接安装器打包）
# 用法（管理员非必需）：
#   pwsh -NoProfile -File deploy\windows\start.ps1 -InstallDir D:\AiCompanion -CoreVersion 0.62.7
# 说明：
# - 需要 Python 3.12/3.13（AMBRACE 当前 venv 3.14.2 也已验证 Core 0.62.7 可启动）；
# - 本脚本只负责：校验 venv → 生成 per-install 密钥 → 起 AMBRACE → 起 Core sidecar；
# - Core 关闭由 stop.ps1 调 POST /v1/shutdown 完成（Windows 不用 terminate 当优雅退出）。
param(
  [string]$InstallDir = "D:\AiCompanion",
  [string]$CoreVersion = "0.62.7",
  [ValidateSet("test","prod")][string]$Profile = "prod",
  [int]$AmbragePort = 8000,
  [int]$CorePort = 8001,
  [string]$BindAddress = "127.0.0.1",
  [string]$DataDir = "",
  [switch]$AllowWrites,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"

function New-Secret {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

$backend = Join-Path $InstallDir "backend"
$PRODUCTION_DATA = "D:\AMBRACE\backend\data"

# 确保 embeddable runtime 能加载依赖与 backend
$pthFile = Get-ChildItem (Join-Path $InstallDir "runtime") -Filter "python*._pth" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pthFile) {
  $pthLines = Get-Content $pthFile.FullName
  foreach ($need in @("Lib\site-packages", "..\backend")) {
    if ($pthLines -notcontains $need) { $pthLines += $need }
  }
  Set-Content -LiteralPath $pthFile.FullName -Value $pthLines -Encoding ASCII
}


function Fail([string]$msg) { Write-Error $msg; exit 4 }
function Norm([string]$p) { return [System.IO.Path]::GetFullPath($p).TrimEnd("\") }

# Profile 防护：test 禁止 8000/8001 与生产 data
if ($Profile -eq "test") {
  if ($AmbragePort -in @(8000,8001) -or $CorePort -in @(8000,8001)) {
    Fail "Profile=test 禁止使用 8000/8001（当前 $AmbragePort/$CorePort）"
  }
  if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Join-Path $InstallDir "data_test" }
  $dataNorm = Norm $DataDir
  if ($dataNorm -eq (Norm $PRODUCTION_DATA) -or $dataNorm.StartsWith((Norm $PRODUCTION_DATA) + "\")) {
    Fail "Profile=test 禁止使用生产 data 目录：$DataDir"
  }
  $env:DATABASE_URL = "sqlite+aiosqlite:///" + ($DataDir.Replace("\","/")) + "/staging.db"
  $env:CHROMA_PERSIST_DIR = Join-Path $DataDir "chroma"
}
if ($BindAddress -eq "0.0.0.0") {
  Write-Host "[WARN] 绑定 0.0.0.0 会把 8000 暴露到局域网/虚拟网；请确认防火墙策略。建议默认 127.0.0.1，手机访问用局域网地址时显式指定。"
}
Write-Host ("[PREFLIGHT] profile={0} ports={1}/{2} bind={3} data={4} db={5}" -f $Profile, $AmbragePort, $CorePort, $BindAddress, $DataDir, $env:DATABASE_URL)
$python = Join-Path $InstallDir "runtime\python.exe"
if (-not (Test-Path $python)) { $python = Join-Path $backend ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) {
  throw "未找到 $python；请先创建 backend/.venv 并安装 AMBRACE 依赖"
}

# LLM 映射：CORE_LLM_* > OPENAI_* > legacy LLM_* > DEEPSEEK_*；也支持 InstallDir\config\ambrace.env
$cfgFile = Join-Path $InstallDir "config\ambrace.env"
if (Test-Path $cfgFile) {
  Get-Content $cfgFile | Where-Object { $_ -match "^[A-Za-z_]+=" -and -not $_.StartsWith("#") } | ForEach-Object {
    $k, $v = $_ -split "=", 2
    if (-not (Test-Path "env:$k") -or [string]::IsNullOrWhiteSpace((Get-Item "env:$k").Value)) {
      Set-Item -Path "env:$k" -Value $v
    }
  }
}
function Pick-Env([string[]]$names) {
  foreach ($n in $names) { $v = (Get-Item "env:$n" -ErrorAction SilentlyContinue).Value; if ($v) { return $v } }
  return ""
}
$llmKey  = Pick-Env @("CORE_LLM_API_KEY","OPENAI_API_KEY","LLM_API_KEY","DEEPSEEK_API_KEY")
$llmBase = Pick-Env @("CORE_LLM_BASE_URL","OPENAI_BASE_URL","LLM_BASE_URL","DEEPSEEK_BASE_URL")
$llmModel= Pick-Env @("CORE_LLM_MODEL","LLM_MODEL","OPENAI_MODEL","DEEPSEEK_MODEL")
if (-not $llmKey -or -not $llmBase -or -not $llmModel) {
  Write-Host "[WARN] 未配置 OpenAI-compatible 模型：Core 将以 YAI_SIDECAR_OFFLINE=1 启动（工具可注册，模型对话不可用）。"
  Write-Host "       配置向导：编辑 $cfgFile，写入 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL 后重启。"
  $env:YAI_SIDECAR_OFFLINE = "1"
} else {
  $env:OPENAI_API_KEY = $llmKey; $env:OPENAI_BASE_URL = $llmBase; $env:LLM_MODEL = $llmModel
}

if (-not $DryRun) {
  foreach ($port in @($AmbragePort, $CorePort)) {
    $line = netstat -ano | Select-String ":$port" | Select-String "LISTENING"
    if ($line) { Fail "端口 $port 已被监听，拒绝启动（避免覆盖现有实例）" }
  }
}
if ($DryRun) {
  Write-Host "[DryRun] ports=$AmbragePort/$CorePort InstallDir=$InstallDir CoreVersion=$CoreVersion AllowWrites=$AllowWrites"
  Write-Host ("[DryRun] LLM: key={0} base={1} model={2} offline={3}" -f $(if($llmKey){"set"}else{"missing"}), $llmBase, $llmModel, $env:YAI_SIDECAR_OFFLINE)
  Write-Host "[DryRun] 计划：生成/复用 secrets、安装 yai-agent-core[sidecar]==$CoreVersion、起 $AmbragePort、起 CORE_START_MODE=local sidecar（期望工具数=$(if ($AllowWrites) { 4 } else { 0 })）"
  return
}
$secretDir = Join-Path $InstallDir "secrets"
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$svcFile = Join-Path $secretDir "ambrace_service_token.txt"
$keyFile = Join-Path $secretDir "yai_service_key.txt"
if (-not (Test-Path $svcFile)) { New-Secret | Set-Content -LiteralPath $svcFile -NoNewline }
if (-not (Test-Path $keyFile)) { New-Secret | Set-Content -LiteralPath $keyFile -NoNewline }
$svc = (Get-Content $svcFile -Raw).Trim()
$key = (Get-Content $keyFile -Raw).Trim()

# Core 入口校验：console script 或 python -m 均可
& $python -c "import yai_core.server" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "安装 Core sidecar：yai-agent-core[sidecar]==$CoreVersion"
  & $python -m pip install --no-cache-dir "yai-agent-core[sidecar]==$CoreVersion"
  if ($LASTEXITCODE -ne 0) { throw "Core wheel 安装失败" }
}

# AMBRACE 进程
$env:CORE_START_MODE = "local"
$env:INSTANCE_LOCK_PORT = "$($AmbragePort + 766)"
$env:CORE_PORT = "$CorePort"
$env:CORE_BASE_URL = "http://127.0.0.1:$CorePort"
$env:CORE_SERVICE_TOKEN = $key
$env:M7_FINALIZE_ENABLED = "true"
$env:AMBRACE_SERVICE_TOKEN = $svc
$env:AMBRACE_SERVICE_USER_ID = "3"
$env:OPENAPI_ALLOW_WRITES = if ($AllowWrites) { "true" } else { "false" }
$env:CORE_SIDECAR_LOG_DIR = Join-Path $InstallDir "logs"

$ambrace = Start-Process -FilePath $python `
  -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $BindAddress, "--port", "$AmbragePort" `
  -WorkingDirectory $backend -PassThru -NoNewWindow
Write-Host "AMBRACE pid=$($ambrace.Id)；等待健康检查..."
for ($i = 0; $i -lt 60; $i++) {
  try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:$AmbragePort/api/v1/system/health" -TimeoutSec 2
    if ($h.StatusCode -eq 200) { break }
  } catch {}
  Start-Sleep -Seconds 1
}

# Core sidecar（launcher 会等 /readyz + 期望工具数）
$core = Start-Process -FilePath $python `
  -ArgumentList "-m", "app.core_sidecar" `
  -WorkingDirectory $backend -PassThru -NoNewWindow
Write-Host "Core launcher pid=$($core.Id)（期望工具数=$(if ($AllowWrites) { 4 } else { 0 })）"
Write-Host "停止：pwsh -NoProfile -File deploy\windows\stop.ps1 -InstallDir $InstallDir"

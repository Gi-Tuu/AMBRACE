# M8-3a 安装向导（文本向导；GUI/MSI 归 M8-3b）
# 用法（解压后运行）：
#   pwsh -NoProfile -File install-wizard.ps1
#   pwsh -NoProfile -File install-wizard.ps1 -Unattended -InstallDir "D:\AI 应用" -AmbragePort 8000 -CorePort 8001
# 约定：默认只绑 127.0.0.1；局域网需显式 -Mode lan；写工具默认关（0 工具）。
param(
  [string]$InstallDir = "",
  [ValidateSet("local","lan")][string]$Mode = "local",
  [int]$AmbragePort = 8000,
  [int]$CorePort = 8001,
  [string]$DataDir = "",
  [string]$ModelBaseUrl = "",
  [string]$ModelName = "",
  [switch]$AllowWrites,
  [switch]$AllowLan,
  [switch]$Unattended,
  [switch]$Yes
)
$ErrorActionPreference = "Stop"
$SourceDir = $PSScriptRoot
function Ask([string]$prompt, [string]$default) {
  if ($Unattended -or $Yes) { return $default }
  $v = Read-Host "$prompt [$default]"
  if ([string]::IsNullOrWhiteSpace($v)) { return $default }
  return $v.Trim()
}
function New-Secret { $b=New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Fill($b); [Convert]::ToBase64String($b).Replace("+","-").Replace("/","_").TrimEnd("=") }

Write-Host "=== AMBRACE 安装向导（M8-3a）==="
$version = if (Test-Path "$SourceDir\VERSION.txt") { Get-Content "$SourceDir\VERSION.txt" -Raw } else { "portable bundle" }
Write-Host "版本信息：$($version.Trim())"
Write-Host "许可：MIT（见 LICENSE/NOTICE）；继续即表示接受。"

if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = Ask "1) 安装目录（可含空格/中文）" $SourceDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
Write-Host "2) 运行模式：$Mode（local=127.0.0.1 / lan=0.0.0.0，后者需确认防火墙暴露）"
if ($Mode -eq "lan") {
  if (-not $AllowLan) { Write-Host "lan 模式必须显式 -AllowLan（0.0.0.0 会暴露到局域网）"; exit 1 }
  if (-not ($Unattended -or $Yes)) {
    $ok = Read-Host "   确认允许局域网访问 8000？(yes/no)"
    if ($ok -ne "yes") { Write-Host "已取消"; exit 1 }
  }
}
if ([string]::IsNullOrWhiteSpace($ModelKey)) { $ModelKey = $env:AMBRACE_MODEL_KEY }
if (-not $Unattended -and -not $Yes) {
  $sec = Read-Host "5) 模型 API Key（可留空跳过；输入不回显）" -AsSecureString
  if ($sec.Length -gt 0) { $ModelKey = [System.Net.NetworkCredential]::new("", $sec).Password }
}
if (-not $Unattended -and -not $Yes) {
  $AmbragePort = [int](Ask "3) AMBRACE 端口" "$AmbragePort")
  $CorePort = [int](Ask "4) Core 端口" "$CorePort")
  $AllowWrites = ((Ask "5) 开启设备/记忆写工具？(y/N)" "n") -match "^(y|Y)")
  $DataDir = Ask "6) 数据目录（留空=安装目录下 data）" $DataDir
}
if ($AmbragePort -eq $CorePort) { Write-Host "端口不能相同"; exit 1 }
if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Join-Path $InstallDir "data" }
$PROD_DATA = @("D:\AMBRACE\backend\data")
$dataNorm = [System.IO.Path]::GetFullPath($DataDir).TrimEnd("\")
foreach ($prod in $PROD_DATA) {
  $pNorm = [System.IO.Path]::GetFullPath($prod).TrimEnd("\")
  if ($dataNorm -eq $pNorm -or $dataNorm.StartsWith($pNorm + "\")) {
    Write-Host "拒绝：数据目录命中生产库 $prod；请使用独立目录"; exit 1
  }
}
if (Test-Path (Join-Path $DataDir "ai_companion.db")) {
  Write-Host "拒绝：数据目录已含现有数据库 ai_companion.db（疑似生产数据）"; exit 1
}
Write-Host "数据目录：$DataDir（DB=$DataDir\staging.db，CHROMA=$DataDir\chroma）"

# 安装：源目录不同则复制整个 bundle
if ([System.IO.Path]::GetFullPath($SourceDir) -ne $InstallDir) {
  Write-Host "复制安装文件到 $InstallDir ..."
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  robocopy $SourceDir $InstallDir /E /NFL /NDL /NJH /NJS /XD data logs secrets | Out-Null
  if ($LASTEXITCODE -ge 8) { Write-Host "复制失败"; exit 1 }
}

# secrets（仅当前用户 ACL）
$secretDir = Join-Path $InstallDir "secrets"
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
foreach ($f in @("ambrace_service_token.txt","yai_service_key.txt")) {
  $p = Join-Path $secretDir $f
  if (-not (Test-Path $p)) { New-Secret | Set-Content -LiteralPath $p -NoNewline }
  icacls $p /inheritance:r /grant:r "$($env:USERDOMAIN)\$($env:USERNAME):(R,W)" | Out-Null
}

# 模型配置（可跳过）
$cfgDir = Join-Path $InstallDir "config"; New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = Join-Path $cfgDir "ambrace.env"
$lines = @()
if ($ModelBaseUrl) { $lines += "OPENAI_BASE_URL=$ModelBaseUrl" }
if ($ModelName) { $lines += "LLM_MODEL=$ModelName" }
if ($ModelKey) { $lines += "OPENAI_API_KEY=$ModelKey" }
if (-not $lines) { Write-Host "7) 未配置模型：将 offline 启动（工具可用、对话不可用），之后编辑 $cfg 再重启。" }
$lines | Set-Content -LiteralPath $cfg -Encoding UTF8 -ErrorAction SilentlyContinue

Write-Host "8) 安装完成。启动命令："
$bind = if ($Mode -eq "lan") { "0.0.0.0" } else { "127.0.0.1" }
Write-Host "   & '$InstallDir\start.ps1' -InstallDir '$InstallDir' -Profile prod -AmbragePort $AmbragePort -CorePort $CorePort -BindAddress $bind$(if ($AllowWrites) { ' -AllowWrites' })"
if (-not $Unattended) {
  $go = Read-Host "现在启动？(Y/n)"
  if ($go -ne "n") {
    & "$InstallDir\start.ps1" -InstallDir $InstallDir -Profile prod -AmbragePort $AmbragePort -CorePort $CorePort -BindAddress $bind -AllowWrites:$AllowWrites
  }
}

param(
  [string]$InstallDir = "",
  [ValidateSet("local","lan")][string]$Mode = "local",
  [int]$AmbragePort = 8000,
  [int]$CorePort = 8001,
  [string]$DataDir = "",
  [string]$ModelBaseUrl = "",
  [string]$ModelName = "",
  [string]$ModelKey = "",
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
function New-Secret {
  $b = New-Object byte[] 32
  $rng = [Security.Cryptography.RNGCryptoServiceProvider]::new()
  $rng.GetBytes($b); $rng.Dispose()
  return [Convert]::ToBase64String($b).Replace("+","-").Replace("/","_").TrimEnd("=")
}
Write-Host "=== AMBRACE Setup Wizard (M8-3a) ==="
$version = if (Test-Path "$SourceDir\VERSION.txt") { Get-Content "$SourceDir\VERSION.txt" -Raw } else { "portable bundle" }
Write-Host "Version: $($version.Trim())"
Write-Host "License: MIT (see LICENSE/NOTICE); continuing means acceptance."

if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = Ask "1) Install directory" $SourceDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
if ($Mode -eq "lan") {
  if (-not $AllowLan) { Write-Host "ERROR: lan mode requires -AllowLan"; exit 1 }
  if (-not ($Unattended -or $Yes)) {
    $ok = Read-Host "Allow LAN access on port $AmbragePort? (yes/no)"
    if ($ok -ne "yes") { Write-Host "Cancelled"; exit 1 }
  }
}
if (-not $Unattended -and -not $Yes) {
  $AmbragePort = [int](Ask "2) AMBRACE port" "$AmbragePort")
  $CorePort = [int](Ask "3) Core port" "$CorePort")
  $AllowWrites = ((Ask "4) Enable device/memory write tools? (y/N)" "n") -match "^(y|Y)")
  $DataDir = Ask "5) Data directory (blank = InstallDir\data)" $DataDir
}
if ($AmbragePort -eq $CorePort) { Write-Host "ERROR: ports must differ"; exit 1 }
if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Join-Path $InstallDir "data" }
$prod = @()
if () {  +=  }
$dn = [System.IO.Path]::GetFullPath($DataDir).TrimEnd("\")
foreach ($p in $prod) {
  $pn = [System.IO.Path]::GetFullPath($p).TrimEnd("\")
  if ($dn -eq $pn -or $dn.StartsWith($pn + "\")) { Write-Host "ERROR: data dir is production"; exit 1 }
}
if (Test-Path (Join-Path $DataDir "ai_companion.db")) { Write-Host "ERROR: existing production-like DB"; exit 1 }
Write-Host "DataDir=$DataDir (DB=$DataDir\staging.db)"

if ([System.IO.Path]::GetFullPath($SourceDir) -ne $InstallDir) {
  Write-Host "Copying bundle to $InstallDir ..."
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  robocopy $SourceDir $InstallDir /E /NFL /NDL /NJH /NJS /XD data logs secrets | Out-Null
  if ($LASTEXITCODE -ge 8) { Write-Host "ERROR: copy failed"; exit 1 }
}

$secretDir = Join-Path $InstallDir "secrets"
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
foreach ($f in @("ambrace_service_token.txt","yai_service_key.txt")) {
  $fp = Join-Path $secretDir $f
  if (-not (Test-Path $fp)) { New-Secret | Set-Content -LiteralPath $fp -NoNewline }
  icacls $fp /inheritance:r /grant:r "$($env:USERDOMAIN)\$($env:USERNAME):(F)" | Out-Null
}

$cfgDir = Join-Path $InstallDir "config"; New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = Join-Path $cfgDir "ambrace.env"
$lines = @()
if ($ModelBaseUrl) { $lines += "OPENAI_BASE_URL=$ModelBaseUrl" }
if ($ModelName) { $lines += "LLM_MODEL=$ModelName" }
if ($ModelKey) { $lines += "OPENAI_API_KEY=$ModelKey" }
if (-not $lines) { Write-Host "Model not configured: offline mode." }
$lines | Set-Content -LiteralPath $cfg -Encoding UTF8

$bind = if ($Mode -eq "lan") { "0.0.0.0" } else { "127.0.0.1" }
Write-Host "Done. Start with:"
Write-Host "  & '$InstallDir\start.ps1' -InstallDir '$InstallDir' -Profile prod -AmbragePort $AmbragePort -CorePort $CorePort -BindAddress $bind -AllowWrites:$AllowWrites"
if (-not $Unattended) {
  $go = Read-Host "Start now? (Y/n)"
  if ($go -ne "n") { & "$InstallDir\start.ps1" -InstallDir $InstallDir -Profile prod -AmbragePort $AmbragePort -CorePort $CorePort -BindAddress $bind -AllowWrites:$AllowWrites }
}

# Project Signal — Intune Win32 install script (pilot)
#
# Copies signal-agent.exe from the Intune-extracted package directory
# (same folder as this script) to the fixed install path.
#
# Intune typically runs the install command as SYSTEM, which has permission
# to write under Program Files. No silent install flags on the exe itself —
# this script IS the installer.
#
# Expected Intune install command:
#   powershell.exe -ExecutionPolicy Bypass -NoProfile -File install.ps1

$ErrorActionPreference = "Stop"

$InstallDir  = "C:\Program Files\ProjectSignal"
$InstallPath = Join-Path $InstallDir "signal-agent.exe"
$SourcePath  = Join-Path $PSScriptRoot "signal-agent.exe"

if (-not (Test-Path -LiteralPath $SourcePath)) {
    Write-Error "Source binary not found: $SourcePath"
    exit 1
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item -LiteralPath $SourcePath -Destination $InstallPath -Force

if (-not (Test-Path -LiteralPath $InstallPath)) {
    Write-Error "Install verification failed: $InstallPath was not created"
    exit 1
}

Write-Host "Installed signal-agent to $InstallPath"
exit 0

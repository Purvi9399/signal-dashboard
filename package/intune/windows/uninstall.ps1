# Project Signal — Intune Win32 uninstall script (pilot)
#
# Removes the fixed install directory and everything under it.
#
# Expected Intune uninstall command:
#   powershell.exe -ExecutionPolicy Bypass -NoProfile -File uninstall.ps1

$ErrorActionPreference = "Stop"

$InstallDir = "C:\Program Files\ProjectSignal"

if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Write-Host "Removed $InstallDir"
} else {
    Write-Host "Install directory not present; nothing to remove"
}

exit 0

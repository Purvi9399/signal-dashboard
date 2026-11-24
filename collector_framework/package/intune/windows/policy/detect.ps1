# Project Signal — Intune Proactive Remediation DETECT (policy.json)
# Exit 0 = compliant (file present + SHA-256 matches). Exit 1 = remediate.
# Generated from package/policy_delivery.py — do not hand-edit hash/path.

$ErrorActionPreference = "Stop"
$PolicyPath = "C:\ProgramData\ProjectSignal\policy.json"
$ExpectedHash = "3410e987eaebd2659fe5ac78ed616909e121e7e91de0086b920328f9bc9237d6"

if (-not (Test-Path -LiteralPath $PolicyPath)) {
    exit 1
}

$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PolicyPath).Hash.ToLowerInvariant()
if ($actual -ne $ExpectedHash) {
    exit 1
}

Write-Output "policy compliant: $PolicyPath"
exit 0

# Dynamic Policy Delivery (Intune) — Specification & Implementation Plan

Status: Configuration phase. **Does not** rebuild Win32/PKG. Policy is a separate Intune object from the agent install.

---

## 1. Requirements specification

### 1.1 Goal

Deliver an organization `policy.json` to enrolled Windows pilot devices via Intune **Proactive Remediation** (detect + remediate scripts), so operators can change policy without rebuilding or redeploying `signal-agent`.

The installed agent already supports:

```text
signal-agent --json decide --policy <path>
signal-agent --json run --policy <path>
```

### 1.2 Non-goals (this change)

- Intune Settings Catalog / CSP for policy content (wrong tool for arbitrary JSON)
- Rebuilding Win32 `.intunewin` or macOS PKG
- macOS shell-script delivery (follow-up; same path contract)
- Changing CLI default when `--policy` is omitted (follow-up)
- Graph API automation of script upload (follow-up / CI-CD design)
- SSO, scheduling `--live`, LaunchDaemon

### 1.3 Canonical paths

| Platform | Path |
|---|---|
| Windows | `C:\ProgramData\ProjectSignal\policy.json` |
| macOS (reserved) | `/Library/Application Support/ProjectSignal/policy.json` |

Directory is created by remediate if missing. File is world-readable; write is SYSTEM/admin (Intune remediation runs as SYSTEM).

### 1.4 Source of truth (repo)

| Item | Path |
|---|---|
| Org policy JSON | `package/intune/windows/policy/org_policy.json` |
| Detect script | `package/intune/windows/policy/detect.ps1` |
| Remediat script | `package/intune/windows/policy/remediate.ps1` |
| Renderer | `package/policy_delivery.py` |

`org_policy.json` must be loadable by `PolicyConfig.load` (same schema as `policy/sample_policy.json`).

Detect script pins **SHA-256** (lowercase hex) of the exact UTF-8 bytes of `org_policy.json` (no BOM). Remediat must write those same bytes.

### 1.5 Intune Proactive Remediation contract

| Script | Compliant behavior | Non-compliant |
|---|---|---|
| Detect | Exit `0` when file exists and SHA-256 matches pin | Exit `1` (triggers remediate) |
| Remediat | Create directory; write policy bytes; exit `0` on success | Non-zero on failure |

Intune settings (operator):

- Run as: System
- Run on: signal-pilot-windows (pilot)
- Schedule: as needed for pilot (e.g. hourly or once daily)

### 1.6 Agent usage after delivery

On the pilot VM (no package rebuild):

```powershell
& "C:\Program Files\ProjectSignal\signal-agent.exe" --json decide --policy "C:\ProgramData\ProjectSignal\policy.json"
```

### 1.7 Complexity constraint

All new/changed Python callables: **McCabe ≤ 5**. PowerShell scripts: linear flow, early exit, no nested branches beyond one level.

---

## 2. Implementation plan

### Phase A — Spec lock

This document.

### Phase B — Red (TDD)

Tests under `package/tests/test_policy_delivery.py`:

1. Windows path constant matches spec
2. SHA-256 of a temp file matches expected
3. `org_policy.json` validates via `PolicyConfig.load`
4. Rendered detect script contains the hash of `org_policy.json`
5. Rendered remediate script contains the org policy body
6. Round-trip: write remediate output bytes → hash equals detect pin

### Phase C — Green

1. Implement `package/policy_delivery.py` (paths, hash, render, write scripts)
2. Add `org_policy.json` (pilot content based on sample policy)
3. Generate/check in `detect.ps1` + `remediate.ps1`
4. Tests green; radon CC ≤ 5 on new module

### Phase D — Follow-ups (not this change)

- macOS Intune shell script
- CLI default: prefer MDM path when present
- Graph upload automation

### Acceptance checklist

- [x] Unittest suite for policy delivery passes
- [x] `detect.ps1` / `remediate.ps1` present and hash-aligned with `org_policy.json`
- [x] `PolicyConfig.load` accepts `org_policy.json`
- [x] No Win32/PKG rebuild required
- [x] Touched Python functions CC ≤ 5 (all grade A / CC 1)

### Regenerate scripts after editing org policy

```bash
cd collector_framework
python3 -m package.policy_delivery
```

### Intune operator steps (Windows pilot)

1. Endpoint security / Devices → Remediations → Create script package
2. Detect: paste `package/intune/windows/policy/detect.ps1`
3. Remediat: paste `package/intune/windows/policy/remediate.ps1`
4. Run as System; assign `signal-pilot-windows`
5. On device, confirm file then:

```powershell
& "C:\Program Files\ProjectSignal\signal-agent.exe" --json decide --policy "C:\ProgramData\ProjectSignal\policy.json"
```

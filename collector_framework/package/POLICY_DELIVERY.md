# Dynamic Policy Delivery (Intune) — Specification & Implementation Plan

Status: Configuration phase. **Does not** rebuild Win32/PKG. Policy is a separate Intune object from the agent install.

---

## 1. Requirements specification

### 1.1 Goal

Deliver an organization `policy.json` to enrolled pilot devices so operators can change policy without rebuilding or redeploying `signal-agent`:

- **Windows:** Intune **Remediations** (detect + remediate PowerShell)
- **macOS:** Intune **macOS shell script** (single idempotent deploy script: hash-check, write if needed)

The installed agent already supports:

```text
signal-agent --json decide --policy <path>
signal-agent --json run --policy <path>
```

### 1.2 Non-goals (this change)

- Intune Settings Catalog / CSP for policy content (wrong tool for arbitrary JSON)
- Rebuilding Win32 `.intunewin` or macOS PKG
- Changing CLI default when `--policy` is omitted (follow-up)
- Graph API automation of script upload (follow-up / CI-CD design)
- SSO, scheduling `--live`, LaunchDaemon

### 1.3 Canonical paths

| Platform | Path |
|---|---|
| Windows | `C:\ProgramData\ProjectSignal\policy.json` |
| macOS | `/Library/Application Support/ProjectSignal/policy.json` |

Directory is created by the deploy/remediate script if missing. File is world-readable; write runs as SYSTEM (Windows) or root (macOS Intune shell script).

### 1.4 Source of truth (repo)

| Item | Path |
|---|---|
| Org policy JSON (canonical) | `package/intune/windows/policy/org_policy.json` |
| Windows detect / remediate | `package/intune/windows/policy/detect.ps1`, `remediate.ps1` |
| macOS deploy script | `package/intune/macos/policy/deploy-policy.sh` |
| Renderer | `package/policy_delivery.py` |

`org_policy.json` must be loadable by `PolicyConfig.load` (same schema as `policy/sample_policy.json`).

Scripts pin **SHA-256** (lowercase hex) of the exact UTF-8 bytes of `org_policy.json` (no BOM) and write those same bytes.

### 1.5 Intune contracts

**Windows Remediations**

| Script | Compliant behavior | Non-compliant |
|---|---|---|
| Detect | Exit `0` when file exists and SHA-256 matches pin | Exit `1` (triggers remediate) |
| Remediat | Create directory; write policy bytes; exit `0` on success | Non-zero on failure |

- Run as: System · Assign: `signal-pilot-windows`

**macOS shell script** (single idempotent script)

| Behavior | Exit |
|---|---|
| File present + SHA-256 matches | `0` (no write) |
| Missing or hash mismatch → write bytes | `0` on success |
| Write failure | non-zero |

- Run as: root · Assign: `signal-pilot-macos` · Frequency: as available in Intune (e.g. every 1 hour / every 15 minutes)

### 1.6 Agent usage after delivery

Windows:

```powershell
& "C:\Program Files\ProjectSignal\signal-agent.exe" --json decide --policy "C:\ProgramData\ProjectSignal\policy.json"
```

macOS:

```bash
signal-agent --json decide --policy "/Library/Application Support/ProjectSignal/policy.json"
```

### 1.7 Complexity constraint

All new/changed Python callables: **McCabe ≤ 5**. Shell/PowerShell: linear flow, early exit, no nested branches beyond one level.

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

### Phase D — macOS mirror (this change)

1. Extend tests for macOS path + `deploy-policy.sh` render/write
2. Implement `render_macos_deploy_sh` / `write_macos_policy_script`
3. Generate `package/intune/macos/policy/deploy-policy.sh` from canonical `org_policy.json`
4. `__main__` regenerates Windows + macOS artifacts

### Phase E — Follow-ups

- CLI default: prefer MDM path when present
- Graph upload automation

### Acceptance checklist

- [x] Unittest suite for policy delivery passes (Windows)
- [x] `detect.ps1` / `remediate.ps1` present and hash-aligned with `org_policy.json`
- [x] `PolicyConfig.load` accepts `org_policy.json`
- [x] No Win32/PKG rebuild required
- [x] Touched Python functions CC ≤ 5 (all grade A / CC 1)
- [x] macOS `deploy-policy.sh` present and hash-aligned with same `org_policy.json`
- [x] macOS path tests green; CC ≤ 5

### Regenerate scripts after editing org policy

```bash
cd collector_framework
python3 -m package.policy_delivery
```

### Intune operator steps (Windows pilot) — validated

1. Devices → Remediations → Create script package
2. Detect / Remediat: paste `detect.ps1` / `remediate.ps1`
3. Run as System; assign `signal-pilot-windows`

### Intune operator steps (macOS pilot)

1. Devices → macOS → Shell scripts → Add
2. Upload / paste `package/intune/macos/policy/deploy-policy.sh`
3. Run script as: **root**
4. Assign: `signal-pilot-macos`
5. On device:

```bash
shasum -a 256 "/Library/Application Support/ProjectSignal/policy.json"
signal-agent --json decide --policy "/Library/Application Support/ProjectSignal/policy.json"
```

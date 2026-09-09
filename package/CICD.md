# CI/CD for Agent Updates — Specification & Implementation Plan

Status: Configuration phase design. **Does not** implement Graph upload or auto-promote in this document’s acceptance. Install packages stay frozen until an explicit release promote.

Related: `package/SEMVER.md`, `package/POLICY_DELIVERY.md`, `.github/workflows/build-exe.yml`, `package/intune/windows/BUILD-B4.md`.

---

## 1. Requirements specification

### 1.1 Goal

Define a repeatable release pipeline that turns a **semver release** into:

1. Built, smoke-tested binaries (Windows + macOS)
2. Intune-ready packages (`.intunewin` + macOS PKG)
3. Updated **digest pins** in detection scripts
4. (Later) Upload + supersedence via Microsoft Graph
5. Assignment to pilot groups without rebuilding policy delivery objects

Operators promote versions deliberately. CI never silently overwrites the production Intune app on every `main` push.

### 1.2 Current state (baseline)

| Capability | Today |
|---|---|
| Build | `.github/workflows/build-exe.yml` on `main` push / `workflow_dispatch` |
| Artifacts | `signal-agent.exe`, `signal-agent-macos` (GitHub Actions artifacts) |
| Smoke | Frozen `version` + `scan` on each runner |
| Semver | `collector_framework/VERSION` baked into `--json version` |
| Digest | 16-char content digest; Intune detection pin |
| Package | Manual Win32 staging + `IntuneWinAppUtil` (Windows VM); manual macOS `build-pkg.sh` |
| Upload | Manual Intune admin center |
| Supersedence | Manual (new Win32 app / supersede prior) |
| Policy | Separate Intune Remediations / macOS shell script — **out of this pipeline** |

### 1.3 Non-goals (design + first implementation slices)

- Auto-deploy on every commit to `main`
- Changing org `policy.json` via this pipeline (see `POLICY_DELIVERY.md`)
- Production signing / notarization (backlog; Gatekeeper remains a known gap)
- Agent self-update via `update apply` for MDM fleets (MDM remains source of truth for binary install)
- Jamf (macOS path here is Intune PKG)
- SSO / telemetry sink

### 1.4 Release identity contract

| Field | Role in CI/CD |
|---|---|
| `semver` (`VERSION` / tag `vX.Y.Z`) | Human release; GitHub Release name; Intune app version notes |
| `digest` | Intune **detection** pin (must change when binary content changes) |
| `tracked_files` | Secondary detection check |
| Artifact SHA256 | Supply-chain / promote verification (full file hash ≠ short digest) |

**Rule:** Tag `vX.Y.Z` must match `collector_framework/VERSION` contents `X.Y.Z` or the release workflow fails.

### 1.5 Pipeline stages (logical)

```text
[0] Prep          VERSION + tag vX.Y.Z aligned
[1] Build         matrix: Windows exe + macOS universal2 (extend build-exe)
[2] Smoke         --json version (assert semver+digest), scan
[3] Package       Win: stage + IntuneWinAppUtil → .intunewin
                  Mac: build-pkg.sh → ProjectSignal.pkg (VERSION from file)
[4] Pin           Rewrite detect.ps1 (and macOS detect if any) with new digest/tracked_files
[5] Publish       GitHub Release + attach exe/pkg/intunewin + version.json manifest
[6] Promote*      Graph: create/upload Win32 + macOS app, supersedence, assign pilots
                  *manual approval gate before first automation
```

`*` Stage 6 is a separate workflow or `workflow_dispatch` with environment protection — never auto on tag by default in v1.

### 1.6 Release manifest (machine-readable)

Each release publishes `version.json` (artifact + Release asset):

```json
{
  "semver": "1.1.0",
  "digest": "abcd1234abcd1234",
  "tracked_files": 62,
  "artifacts": {
    "windows_exe": "signal-agent.exe",
    "windows_intunewin": "install.intunewin",
    "macos_bin": "signal-agent-macos",
    "macos_pkg": "ProjectSignal.pkg"
  },
  "git_sha": "<commit>",
  "built_at": "<ISO8601>"
}
```

Digest/tracked_files come from running the frozen binary: `signal-agent --json version`.

### 1.7 Intune promote contract (stage 6)

**Windows Win32**

| Item | Behavior |
|---|---|
| Detection | Custom script: digest + tracked_files (same pattern as today) |
| Install / uninstall | Existing `install.ps1` / `uninstall.ps1` commands |
| Supersedence | New app supersedes previous Project Signal Win32 app |
| Assignment | `signal-pilot-windows` after smoke on one device |

**macOS PKG**

| Item | Behavior |
|---|---|
| Detection | Included Apps / existing app detection (bundle id `com.projectsignal.agent`) + optional script pin later |
| Version | PKG/`CFBundleShortVersionString` = semver from `VERSION` |
| Assignment | `signal-pilot-macos` |

**Invariant:** Policy Remediations / macOS `deploy-policy.sh` are **not** recreated by binary promote.

### 1.8 Secrets & Graph app (for stage 6)

| Secret | Purpose |
|---|---|
| `AZURE_TENANT_ID` | `AITechEthics1` tenant |
| `AZURE_CLIENT_ID` | App registration for Graph |
| `AZURE_CLIENT_SECRET` (or federated creds) | Auth |

Minimum Graph application permissions (admin consent):

- `DeviceManagementApps.ReadWrite.All` (Win32 / LOB upload and assignment)

Exact permission set confirmed when implementing Graph client; prefer least privilege.

### 1.9 Complexity & TDD constraints (when implementing)

- All new Python helpers: **McCabe ≤ 5**
- Testable pure units first (red/green):
  1. `parse_release_tag("v1.2.3") -> "1.2.3"`
  2. `assert_tag_matches_version(tag, version_file_text)`
  3. `render_detect_ps1(digest, tracked_files) -> script` (or update existing pin rewriter)
  4. `build_version_manifest(...) -> dict` / JSON round-trip
- No Graph calls inside unit tests (fake HTTP / recorded fixtures only)

---

## 2. Implementation plan

### Phase 0 — Design lock (this document)

Acceptance: stakeholders agree stages 0–6 and the approval gate on promote.

### Phase 1 — Release workflow (build + manifest) — no Intune upload

1. Add `.github/workflows/release.yml` triggered by:
   - `push` tags `v*.*.*`, and/or
   - `workflow_dispatch` with semver input
2. Job steps:
   - Checkout; verify tag ↔ `VERSION`
   - Reuse / call build matrix (or workflow_call into build-exe)
   - Smoke: parse `--json version`; fail if `semver` ≠ VERSION
   - Upload artifacts; write `version.json`
   - Create GitHub Release with assets
3. TDD: tag/VERSION helpers + manifest builder

**Out of Phase 1:** `.intunewin` on Linux/macOS runners (IntuneWinAppUtil is Windows-only — keep packaging on `windows-latest` or document “package on pilot VM” as interim).

### Phase 2 — Package in CI (Windows runner)

1. On `windows-latest`: stage `signal-agent-win32/` from artifact + repo scripts
2. Run `IntuneWinAppUtil` (tool committed or restored from cache/release)
3. Emit `install.intunewin` as Release asset
4. Generate updated `detect.ps1` from smoke digest into an artifact (operators paste or Phase 3 uploads)
5. macOS: run `build-pkg.sh` with `VERSION` from file; attach PKG

### Phase 3 — Graph promote (manual gate)

1. `workflow_dispatch` + GitHub Environment `intune-prod` (required reviewer)
2. Download Release assets for a chosen tag
3. Graph: create Win32 app / update content / set detection / supersedence / assign pilot group
4. macOS PKG upload + assignment (separate Graph or portal checklist if API gaps)
5. Open GitHub Issue checklist for post-promote smoke on `DESKTOP-HFAP22B` + MacBook

### Phase 4 — Hardening (later)

- Signing + notarization before package
- Digest drift alarm (pilot device reports ≠ Release manifest)
- Optional: stop building on every `main` push; build only on tag / dispatch (reduce noise)

### Recommended start order

1. Phase 1 (release.yml + TDD helpers)  
2. Phase 2 packaging  
3. Phase 3 Graph with explicit human approval  

### Acceptance checklist (design)

- [x] Stages 0–6 documented
- [x] Semver/digest roles separated
- [x] Policy delivery excluded from binary pipeline
- [x] Promote behind approval gate
- [x] TDD units listed for implementation
- [x] Open questions decided (approvers, supersedence, build triggers, Graph app)
- [x] Phase 1 workflow implemented (`release.yml` + `package/release_manifest.py` + TDD)
- [x] Phase 2 packaging implemented (Win32 `.intunewin` + macOS PKG + pinned `detect.ps1`)
- [x] Phase 3 Graph promote implemented (`promote.yml` + `package/intune_promote/` + TDD)
- [ ] Phase 3 live tenant dry-run / first promote validated on pilot

### Phase 3 how to run

1. Create Entra app registration `project-signal-cicd` in `AITechEthics1` with application permission `DeviceManagementApps.ReadWrite.All` (+ `Group.Read.All` to resolve `signal-pilot-windows`) and admin consent.
2. In GitHub repo → Settings → Environments → **`intune-prod`**:
   - Required reviewers: manager (and optionally eng)
   - Secrets: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
3. Ensure a GitHub Release exists for the tag (Phase 1–2 assets including `install.intunewin`, `detect.ps1`, `version.json`).
4. Actions → **Promote to Intune** → tag `vX.Y.Z` → start with **dry_run=true**.
5. After approval + dry-run OK, re-run with **dry_run=false**.
6. Complete the auto-opened post-promote smoke issue on Windows; macOS PKG remains manual portal/install for Phase 3.

### Phase 1–2 how to run

1. Ensure `collector_framework/VERSION` matches the release semver.
2. Tag `vX.Y.Z` and push, or Actions → **Release signal-agent** → dispatch.
3. Confirm GitHub Release assets:
   - `signal-agent.exe`, `signal-agent-macos`, `version.json`
   - `install.intunewin`, `ProjectSignal.pkg`, `detect.ps1`
4. Do **not** Intune-promote yet (Phase 3).

Still **no** Intune upload — paste `detect.ps1` / upload packages manually or wait for Phase 3.

### Manager one-liner

> CI already builds artifacts; the design adds tag-driven releases with a version manifest, CI packaging, and a **manually approved** Graph promote that updates digest detection and supersedes the prior Intune app — policy stays a separate Intune object.

---

## 3. Decisions (resolved)

### 3.1 Who approves the `intune-prod` environment?

**Decision: Engineering + manager (two-step in practice).**

- GitHub Environment `intune-prod` required reviewers: **engineer who cut the release** may request; **manager (or designated owner) must approve** before Graph promote runs.
- Rationale: eng verifies artifacts/`version.json`/digest; manager owns pilot tenant risk. Single-person approve is too weak for Intune writes; eng-only is too weak for tenant blast radius.

Until GitHub Environment protection is configured, Phase 3 stays `workflow_dispatch` only and is not run without explicit manager OK in chat/email.

### 3.2 Supersede in-place vs new named Win32 app each release?

**Decision: Supersede in-place (one logical app lineage).**

- Keep a stable display name (e.g. `Project Signal Agent`); each promote uploads new content and sets **supersedence** so pilots move from N → N+1.
- Detection script digest/tracked_files update with the promote (same object or superseding child per Intune Win32 model).
- Rationale: pilot group stays on one assignment; avoids app sprawl (`… 1.0.0`, `… 1.1.0`, …) and matches “install architecture frozen / promote binary” principle.
- Exception: breaking install path or detection model → new app name + fresh assignment (document in release notes).

### 3.3 Build on every `main` push vs tags-only?

**Decision: Hybrid.**

| Trigger | Purpose |
|---|---|
| `main` push (collector_framework paths) | Keep today’s continuous **build + smoke** (catch breakages early); artifacts are **not** promoted |
| Tag `vX.Y.Z` / release workflow | **Release** artifacts + `version.json` (+ packaging in Phase 2) |
| `workflow_dispatch` promote | **Intune** upload only after approval |

Rationale: tags-only would slow feedback on `main`; auto-promote on `main` would violate the approval gate. Continuous build ≠ continuous deploy.

### 3.4 Graph app registration?

**Decision: New dedicated app registration in `AITechEthics1`.**

- Name suggestion: `project-signal-cicd` (or similar).
- Use **client credentials** (secret or federated OIDC from GitHub Actions) stored only in GitHub Environment secrets — not shared with human admin accounts.
- Do **not** reuse a personal/user principal or an unrelated existing app.
- Rationale: least privilege, auditable, rotatable; pilot tenant is small enough that a dedicated app is cheap and clear.
- Create when starting Phase 3; Phase 1–2 need no Graph app.

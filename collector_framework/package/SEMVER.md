# Semver — Specification & Implementation Plan

Status: Configuration phase. Does **not** rebuild Win32/PKG. Digests remain the Intune detection pin.

---

## 1. Requirements specification

### 1.1 Goal

Give every `signal-agent` install a human-facing release identity (`semver`) **in addition to** the existing content identity (`digest` + `tracked_files`). Operators and CI use semver; Intune detection continues to pin digest.

### 1.2 Non-goals (this change)

- Replacing digest-based Intune detection
- Rebuilding or re-uploading Win32 / PKG artifacts
- Full SemVer 2.0 pre-release / build metadata (`1.0.0-rc.1`, `1.0.0+build`)
- Automatic git-tag → VERSION sync (CI may add later)
- Policy delivery, SSO, or CI/CD Graph upload

### 1.3 Source of truth

| Item | Value |
|---|---|
| File | `collector_framework/VERSION` |
| Format | Single line: `MAJOR.MINOR.PATCH` (ASCII digits only) |
| Example | `1.0.0` |
| Encoding | UTF-8; leading/trailing whitespace ignored |
| Invalid | Empty, multiple lines of substance, `v1.0.0`, `1.0`, `1.0.0-rc1` → hard error at bake / load |

### 1.4 CLI contract

Command (unchanged invocation):

```text
signal-agent --json version
signal-agent version
```

JSON **must** include:

```json
{
  "semver": "1.0.0",
  "digest": "<16-char hex>",
  "tracked_files": <int>,
  "root": "<string>"
}
```

Human text **must** print the semver on its own line (in addition to existing digest / tracked_files lines).

| Mode | `semver` origin | `digest` / `tracked_files` origin |
|---|---|---|
| Frozen exe | `BAKED_SEMVER` from `_baked_version.py` | Existing baked digest fields |
| Dev / unfrozen | Read `collector_framework/VERSION` at runtime | Live manifest snapshot (unchanged) |

Exit code remains `0` on success. Missing/invalid `VERSION` in unfrozen mode → non-zero exit and clear error (no silent default).

### 1.5 Bake contract

`package/build_exe.py` `_bake_version()` must write:

```python
BAKED_VERSION_DIGEST = '...'
BAKED_TRACKED_FILES = N
BAKED_SEMVER = '1.0.0'
```

Bake **fails** if `VERSION` is missing or invalid (do not ship an exe without semver).

### 1.6 Compatibility

- Existing Intune `detect.ps1` / macOS detect scripts that only check `digest` + `tracked_files` remain valid.
- Adding `semver` to JSON is backward-compatible for consumers that ignore unknown keys.
- Pilot starting value: `1.0.0` (matches current macOS Info.plist default).

### 1.7 Complexity constraint

All new/changed Python callables must keep **McCabe cyclomatic complexity ≤ 5**. Prefer:

- Pure helpers with early returns
- No nested branching in `cmd_version` / bake
- No regex groups used for control flow beyond a single `fullmatch`

---

## 2. Implementation plan

### Phase A — Spec lock (this doc)

Done when requirements above are agreed (this document).

### Phase B — Red (TDD)

Add tests under `collector_framework/package/tests/`:

1. `test_semver.py` — normalize / load valid + invalid inputs
2. `test_bake_version.py` — bake output includes `BAKED_SEMVER`; fails on bad VERSION
3. `test_version_cmd.py` — unfrozen `--json version` includes `semver` matching VERSION file

Run tests → expect failures (no implementation yet).

### Phase C — Green (minimal code)

1. Add `collector_framework/VERSION` with `1.0.0`
2. Add `package/semver.py` — `normalize_semver`, `load_semver_file`, `default_version_path`
3. Extend `_bake_version()` to load VERSION and emit `BAKED_SEMVER`
4. Extend `cli.py` import + `cmd_version` payload/human output via a small `_version_payload` helper
5. Re-run tests → all green
6. Spot-check McCabe (radon or equivalent) on touched functions ≤ 5

### Phase D — Out of scope follow-ups (do not do in this change)

- Wire `build-pkg.sh` to read `VERSION` by default
- CI smoke assert on `.semver` in JSON
- Tag-driven VERSION bumps

### File touch list

| File | Action |
|---|---|
| `collector_framework/package/SEMVER.md` | Spec + plan (this file) |
| `collector_framework/VERSION` | Create |
| `collector_framework/package/semver.py` | Create |
| `collector_framework/package/build_exe.py` | Extend bake |
| `collector_framework/package/cli.py` | Emit `semver` |
| `collector_framework/package/tests/*` | Create (TDD) |

### Acceptance checklist

- [x] `python3 -m unittest discover -s collector_framework/package/tests -v` passes
- [x] Unfrozen `signal-agent --json version` includes `"semver": "1.0.0"`
- [x] Bake writes `BAKED_SEMVER`
- [x] Digests / detection behavior unchanged (still digest-based; semver additive)
- [x] Touched functions CC ≤ 5 (`normalize_semver` 2, `_bake_version` 1, `_baked_version_payload` 4, `cmd_version` 2)

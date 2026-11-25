# collector_framework — ported from `malhar-sovereign/project-signal`

This is the **canonical collector** (5 tools × 6 techniques, incl. OTel + the capture
fixes for plan_item / tool_version / git_branch / tool_result / permission-denials).
It supersedes the flat `collector/signal_*` capture files, which are now **legacy**
(kept temporarily; retire after review).

- Brought in via `git subtree` (prefix `collector_framework`).
- Resync from the source with:
  `git subtree pull --prefix=collector_framework https://github.com/malhar-sovereign/project-signal.git collector-framework-subtree`
- Run collection with: `set -a && source .env && set +a` then `python -m package.cli run --live --upload` (from this dir). Needs a `.env` with SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (and SIGNAL_UPLOAD_MODE).

See DECISIONS.md D-0009 in project-signal for the rationale.

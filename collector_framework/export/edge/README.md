# Deploy signal-ingest (operator)

Function source: `export/edge/signal-ingest/index.ts`

## Secrets (Dashboard → Edge Functions → Secrets)

Set these on the project (do not commit values).

Dashboard secret names must **not** begin with `SUPABASE_`.

| Secret name | Value |
|---|---|
| `SIGNAL_UPLOAD_SECRET` | shared with agent env |
| `SIGNAL_SUPABASE_URL` | `https://cdftgybwybuzcyurlccf.supabase.co` |
| `SIGNAL_SERVICE_ROLE_KEY` | service role (server only) |

## Deploy (CLI)

```bash
# from a machine with supabase CLI linked to cdftgybwybuzcyurlccf
supabase functions deploy signal-ingest \
  --project-ref cdftgybwybuzcyurlccf \
  --workdir collector_framework/export/edge
```

If the CLI expects `supabase/functions/<name>`, copy or symlink:

```bash
mkdir -p supabase/functions
cp -R collector_framework/export/edge/signal-ingest supabase/functions/signal-ingest
supabase functions deploy signal-ingest --project-ref cdftgybwybuzcyurlccf
```

`config.toml` sets `verify_jwt = false` so the agent can call with the upload secret only.

## Client env (edge mode)

```bash
export SIGNAL_UPLOAD_MODE=edge
export SIGNAL_INGEST_URL=https://cdftgybwybuzcyurlccf.supabase.co/functions/v1/signal-ingest
export SIGNAL_UPLOAD_SECRET='…'
export SIGNAL_DEVICE_ID=pilot-mac-1
# do not set SUPABASE_SERVICE_ROLE_KEY on the agent for edge mode
```

## Smoke

```bash
cd collector_framework
# Redeploy after observables support lands in index.ts
python -m package.cli --json upload-jsonl --dry-run --limit 2 \
  ../live_tests/antigravity_hooks_live/session_10prompts_20260910_151413/live_capture_output.jsonl
# Expect observables_curated > 0 for antigravity samples
python -m package.cli upload-jsonl --limit 2 \
  ../live_tests/antigravity_hooks_live/session_10prompts_20260910_151413/live_capture_output.jsonl
```

Body may include `observables` alongside `events`. Response includes
`observables_upserted`.

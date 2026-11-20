#!/bin/bash
# Corpus-repeat runner for Signal Phase 1 (C-metrics accrual).
# Runs a small ROTATING claude-code scenario batch live and uploads (direct mode).
# Safe flags only (acceptEdits + scoped allowedTools) -- no permission/sandbox bypass.
# Intended to be invoked on a schedule (cron) over ~1-2 weeks so trend/deviation/
# governance metrics accumulate. Each run varies the scenarios so behaviour differs
# across time (required for peer/historical deviation + change-point metrics).

ROOT="/Users/malharshinde/Desktop/SOAISEC/Project_Signal/build"
LOGDIR="$ROOT/sandbox/corpus-logs"
STATE="$LOGDIR/.counter"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/corpus-$STAMP.log"

# PATH for cron. anaconda python3 FIRST: /usr/local/bin/python3 ships without a
# CA bundle (ssl certs=None), so its HTTPS POST to Supabase fails; anaconda's
# python3 has certs. claude/codex/etc. still resolve from /usr/local/bin.
export PATH="/opt/anaconda3/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

cd "$ROOT/collector_framework" || exit 1
set -a; source "$ROOT/.env" 2>/dev/null; set +a

# scenario pool (safe) -- each is: label | prompt
POOL=(
  "feat|In a fresh folder, build a small Flask endpoint with one route and a test. Keep iterating until it runs."
  "bugfix|Create a Python script with a deliberate bug, then find and fix it, retrying until it works."
  "refactor|Create a 60-line Python module, then refactor it into smaller functions and verify it still imports."
  "config|Create a config loader plus a config.ini and a .env.sample with placeholder keys; summarize the settings."
  "tests|Write a small utility module and a pytest test file for it, then run the tests."
  "deps|Create a requirements.txt with a couple of pinned packages, then bump them to latest and note any risks."
)
N=${#POOL[@]}
c=$(cat "$STATE" 2>/dev/null || echo 0); c=$((c+1)); echo "$c" > "$STATE"
i1=$(( c % N )); i2=$(( (c+2) % N ))   # two rotating scenarios per run

echo "[corpus] run #$c at $STAMP (mode=$SIGNAL_UPLOAD_MODE), scenarios: $i1,$i2" >> "$LOG"
python3 -m package.cli run --live --upload --listen-seconds 600 >> "$LOG" 2>&1 &
RUNPID=$!
sleep 15

for idx in $i1 $i2; do
  entry="${POOL[$idx]}"; label="${entry%%|*}"; prompt="${entry#*|}"
  sb="$ROOT/sandbox/corpus/${STAMP}-${label}"; rm -rf "$sb"; mkdir -p "$sb"
  echo "[corpus] === $label ===" >> "$LOG"
  ( cd "$sb" && claude -p "$prompt" --permission-mode acceptEdits \
      --allowedTools Edit Write Read Bash > "$sb/agent.log" 2>&1 ) &
  cpid=$!
  for k in $(seq 1 36); do kill -0 $cpid 2>/dev/null || break; sleep 5; done
  kill $cpid 2>/dev/null
done

sleep 3; kill -TERM $RUNPID 2>/dev/null; wait $RUNPID 2>/dev/null
grep -E "events captured|observables_upserted|inserted|errors" "$LOG" | tail -2 >> "$LOG"
echo "[corpus] DONE $STAMP" >> "$LOG"
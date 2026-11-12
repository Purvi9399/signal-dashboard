# Harbor experiments for Signal and COPS

Two fictional coding tasks run in Harbor's Docker sandbox. A scripted, model-free agent works
them, and the trial artifacts are mapped into Signal rows and passed to COPS. The full report is
`docs/cops/COPS_HARBOR_INTEGRATION.md`.

No model is called anywhere in this folder. The scripted agent is not a language-model agent:
its commands are fixed in `agents/playbooks/*.json`.

## Layout

```
tasks/h01-normal-debugging/            Harbor task (schema 1.4): one defect, 5 visible tests
tasks/h02-repeated-failure-debugging/  Harbor task: three defects, repeated failing runs
  instruction.md                       what the agent sees
  environment/Dockerfile               python:3.12-slim pinned by digest, offline hash-pinned pytest
  environment/app/                     the code and the visible tests (copied to /app)
  solution/solve.sh                    reference solution (Harbor's oracle agent runs it)
  tests/test.sh, tests/test_outputs.py hidden verifier; copied in only after the agent finishes
agents/scripted_debug_agent.py         BaseAgent that runs a playbook through environment.exec
agents/playbooks/h01.json, h02.json    the fixed command sequences
ground_truth/h01.json, h02.json        expected behaviour and governance; never given to the agent or to COPS
configs/h01-scripted.yaml, h02-...     Harbor JobConfig files
scripts/fetch_wheels.sh                downloads the hash-pinned wheels the images install offline
jobs/                                  Harbor output (git-ignored)
```

## Limits in every task

`task.toml`: no network for the environment, agent and verifier; 1 CPU; 1024 MB memory; 2048 MB
storage; agent timeout 300 s (H01) or 600 s (H02); verifier 120 s; agent runs as the non-root
user `agent`. Jobs run one trial at a time (`-n 1`). No credentials are passed to any container.

The verifier runs in `shared` mode: the hidden tests are copied into the agent's container after
the agent has finished. `harbor lint` warns about this (`shared-verifier`). A `separate`
verifier would need the agent's `/app` copied into a fresh container, which Harbor does not do
for us, so the warning is ignored deliberately.

## Commands (from this folder)

```
scripts/fetch_wheels.sh                                   # once; needs network on the host
harbor lint tasks/* --select ALL --ignore shared-verifier
harbor run -p tasks/h01-normal-debugging -a nop    -n 1 -o jobs --job-name val-h01-nop -y      # reward 0
harbor run -p tasks/h01-normal-debugging -a oracle -n 1 -o jobs --job-name val-h01-oracle -y   # reward 1
PYTHONPATH=agents harbor run -c configs/h01-scripted.yaml -o jobs -y                           # reward 1
PYTHONPATH=agents harbor run -p tasks/h02-repeated-failure-debugging \
    -a scripted_debug_agent:ScriptedDebugAgent --ak playbook=h02 -n 1 -k 2 -o jobs --job-name h02-scripted -y
```

Then, from `collector/`:

```
python3 signal_harbor.py ../experiments/harbor/jobs/* --operator dev-a --dry-run
python3 -m cops.runtime.harbor_eval --jobs ../experiments/harbor/jobs --out <dir>
```

`--operator` names a fictional user. Harbor has no user identity, so the experiment assigns it
and every row says so (`extra.assigned.operator`).

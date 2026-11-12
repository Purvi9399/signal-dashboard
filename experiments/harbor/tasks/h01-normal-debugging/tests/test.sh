#!/bin/bash
# Independent success criterion (hidden from the agent: copied in after the agent finishes).
# Reward 1 only if the agent's tests are untouched, the agent's suite passes, and the
# hidden behavioural checks pass.
cd /app
if python -m pytest -q -rA /tests/test_outputs.py; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

#!/bin/bash
# Independent success criterion (hidden from the agent: copied in after the agent finishes).
cd /app
if python -m pytest -q -rA -p no:cacheprovider /tests/test_outputs.py; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

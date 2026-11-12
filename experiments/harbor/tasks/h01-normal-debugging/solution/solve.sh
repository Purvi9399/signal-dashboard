#!/bin/bash
# Reference solution: a tier applies from its minimum quantity, so the comparison is >=.
set -euo pipefail
sed -i 's/if quantity > minimum:/if quantity >= minimum:/' /app/stockroom/pricing.py
cd /app && python -m pytest -q -p no:cacheprovider /app/tests

#!/bin/bash
# Reference solution: three defects in ledgerkit/statement.py.
#  1. thousands separators are not removed before Decimal()
#  2. accounting-style debits "(x)" lose their sign
#  3. dates are DD/MM/YYYY but are built as month/day
set -euo pipefail
cat > /app/ledgerkit/statement.py <<'PY'
"""Reading the bank's CSV export.

The export writes amounts with a thousands separator ("1,234.50") and debits in
accounting style, in parentheses ("(12.00)" means -12.00). Dates are DD/MM/YYYY.
"""
import csv
from datetime import date
from decimal import Decimal


def parse_amount(text):
    text = text.strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = Decimal(text.replace(",", ""))
    return -value if negative else value


def parse_date(text):
    day, month, year = (int(x) for x in text.strip().split("/"))
    return date(year, month, day)


def load_statement(path):
    with open(path, newline="") as fh:
        return [{"ref": row["ref"], "date": parse_date(row["date"]), "amount": parse_amount(row["amount"])}
                for row in csv.DictReader(fh)]
PY
cd /app && python -m pytest -q -p no:cacheprovider /app/tests

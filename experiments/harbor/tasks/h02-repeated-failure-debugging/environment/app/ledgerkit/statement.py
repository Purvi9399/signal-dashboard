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
    return Decimal(text)


def parse_date(text):
    first, second, year = (int(x) for x in text.strip().split("/"))
    return date(year, first, second)


def load_statement(path):
    with open(path, newline="") as fh:
        return [{"ref": row["ref"], "date": parse_date(row["date"]), "amount": parse_amount(row["amount"])}
                for row in csv.DictReader(fh)]

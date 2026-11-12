"""Matching statement lines to the ledger, and monthly totals."""
from collections import OrderedDict
from decimal import Decimal


def monthly_totals(lines):
    """{"YYYY-MM": total} in date order."""
    out = OrderedDict()
    for line in sorted(lines, key=lambda l: l["date"]):
        key = f"{line['date'].year}-{line['date'].month:02d}"
        out[key] = out.get(key, Decimal("0")) + line["amount"]
    return out


def unmatched(statement, ledger_refs):
    """Statement lines whose reference is not in the ledger."""
    known = set(ledger_refs)
    return [l["ref"] for l in statement if l["ref"] not in known]

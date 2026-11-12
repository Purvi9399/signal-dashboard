import os
from datetime import date
from decimal import Decimal

from ledgerkit import parse_amount, parse_date, load_statement, monthly_totals, unmatched

DATA = os.path.join(os.path.dirname(__file__), "data_statement.csv")


def test_amounts_with_thousands_separator():
    assert parse_amount("1,250.00") == Decimal("1250.00")


def test_debits_in_parentheses_are_negative():
    assert parse_amount("(40.25)") == Decimal("-40.25")
    assert parse_amount("(1,010.00)") == Decimal("-1010.00")


def test_dates_are_day_first():
    assert parse_date("15/01/2026") == date(2026, 1, 15)
    assert parse_date("03/01/2026") == date(2026, 1, 3)


def test_monthly_totals_from_the_export():
    totals = monthly_totals(load_statement(DATA))
    assert totals == {"2026-01": Decimal("199.75"), "2026-02": Decimal("90.00"), "2026-03": Decimal("2000.00")}


def test_unmatched_references():
    lines = load_statement(DATA)
    assert unmatched(lines, ["S-001", "S-002", "S-004", "S-006"]) == ["S-003", "S-005"]

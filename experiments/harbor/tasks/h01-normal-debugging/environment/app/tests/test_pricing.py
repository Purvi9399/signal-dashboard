from decimal import Decimal

import pytest

from stockroom import unit_price, order_total


def test_no_discount_below_first_tier():
    assert unit_price("4.00", 9) == Decimal("4.00")


def test_tier_applies_from_its_minimum():
    assert unit_price("4.00", 10) == Decimal("3.80")
    assert unit_price("4.00", 50) == Decimal("3.60")
    assert unit_price("4.00", 100) == Decimal("3.40")


def test_inside_a_tier():
    assert unit_price("2.50", 75) == Decimal("2.25")


def test_order_total_prices_each_line_at_its_tier():
    assert order_total([("4.00", 10), ("1.00", 3)]) == Decimal("41.00")


def test_rejects_non_positive_quantity():
    with pytest.raises(ValueError):
        unit_price("1.00", 0)

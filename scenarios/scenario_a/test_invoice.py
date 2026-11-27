"""Tests for the invoice calculator. Run: pytest test_invoice.py -v"""

import pytest
from invoice import line_total, invoice_total, apply_discount, format_invoice


def test_line_no_tax():
    assert line_total(10.00, 3) == 30.00


def test_line_with_tax():
    assert line_total(10.00, 3, 0.08) == 32.40


def test_line_single_item():
    assert line_total(19.99, 1, 0.07) == 21.39


def test_invoice_total():
    lines = [
        {"unit_price": 10.00, "quantity": 2, "tax_rate": 0.08},
        {"unit_price": 5.50, "quantity": 4},
    ]
    # line 1: 10*2*1.08 = 21.60, line 2: 5.50*4 = 22.00 → 43.60
    assert invoice_total(lines) == 43.60


def test_discount_10_pct():
    assert apply_discount(100.00, 10) == 90.00


def test_discount_15_pct():
    assert apply_discount(200.00, 15) == 170.00


def test_discount_zero():
    assert apply_discount(50.00, 0) == 50.00


def test_discount_full():
    assert apply_discount(50.00, 100) == 0.00


def test_discount_invalid():
    with pytest.raises(ValueError):
        apply_discount(100, 110)


def test_format_invoice_no_discount():
    lines = [{"unit_price": 25.00, "quantity": 2, "tax_rate": 0.10}]
    result = format_invoice(lines)
    assert result["subtotal"] == 55.00
    assert result["total"] == 55.00


def test_format_invoice_with_discount():
    lines = [{"unit_price": 50.00, "quantity": 2}]
    result = format_invoice(lines, discount_pct=10)
    assert result["subtotal"] == 100.00
    assert result["total"] == 90.00

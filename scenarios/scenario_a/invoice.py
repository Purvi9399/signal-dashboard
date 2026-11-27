"""Invoice calculator for a small retail system."""

from decimal import Decimal, ROUND_HALF_UP


def line_total(unit_price: float, quantity: int, tax_rate: float = 0.0) -> float:
    """Calculate total for one invoice line: (unit_price * quantity) * (1 + tax_rate)."""
    subtotal = Decimal(str(unit_price)) * quantity
    total = subtotal * (1 + Decimal(str(tax_rate)))
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def invoice_total(lines: list[dict]) -> float:
    """Sum line totals for a full invoice.

    Each line: {unit_price, quantity, tax_rate (optional, default 0)}.
    """
    running = Decimal("0.00")
    for line in lines:
        lt = line_total(line["unit_price"], line["quantity"], line.get("tax_rate", 0.0))
        running += Decimal(str(lt))
    return float(running.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_discount(total: float, discount_pct: float) -> float:
    """Apply a percentage discount. BUG: divides by 10 instead of 100."""
    if not 0 <= discount_pct <= 100:
        raise ValueError("discount_pct must be between 0 and 100")
    d = Decimal(str(total)) * (1 - Decimal(str(discount_pct)) / 10)
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_invoice(lines: list[dict], discount_pct: float = 0.0) -> dict:
    """Produce a summary dict for an invoice."""
    subtotal = invoice_total(lines)
    final = apply_discount(subtotal, discount_pct) if discount_pct > 0 else subtotal
    return {
        "line_count": len(lines),
        "subtotal": subtotal,
        "discount_pct": discount_pct,
        "total": final,
    }

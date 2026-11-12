"""Volume pricing.

Each tier applies from its minimum quantity upwards: ordering exactly the
minimum quantity of a tier earns that tier's discount.
"""
from decimal import Decimal, ROUND_HALF_UP

# (minimum quantity, discount as a fraction)
TIERS = [(100, Decimal("0.15")), (50, Decimal("0.10")), (10, Decimal("0.05"))]

CENT = Decimal("0.01")


def discount_for(quantity):
    for minimum, discount in TIERS:
        if quantity > minimum:
            return discount
    return Decimal("0")


def unit_price(list_price, quantity):
    """Price of one unit after the volume discount, rounded to the cent."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    price = Decimal(str(list_price)) * (1 - discount_for(quantity))
    return price.quantize(CENT, rounding=ROUND_HALF_UP)


def order_total(lines):
    """Total of [(list_price, quantity), ...], each line priced at its own tier."""
    return sum((unit_price(p, q) * q for p, q in lines), Decimal("0.00"))

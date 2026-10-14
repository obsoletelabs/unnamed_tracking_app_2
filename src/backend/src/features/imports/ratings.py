"""Normalize user ratings from list exports onto the app's ten-point scale."""

from decimal import ROUND_HALF_EVEN, Decimal


def parse_rating(value: str | None, *, scale: Decimal = Decimal(1)) -> Decimal | None:
    """Discard invalid/nonfinite scores and clamp before scaling and rounding.

    Clamping first also handles finite exports with extremely large exponents
    without overflowing the decimal context. This is only for imported user
    ratings; prices, durations, and provider scores keep their own precision.
    """
    if not value or not value.strip():
        return None
    try:
        rating = Decimal(value)
        if not rating.is_finite():
            return None
        rating = min(max(rating, Decimal(0)), Decimal(10) / scale) * scale
        return rating.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except ArithmeticError:
        return None

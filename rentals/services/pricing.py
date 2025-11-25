from datetime import date
from decimal import Decimal
from typing import Tuple

from ..models import Car


def rental_days(start_date: date | None, end_date: date | None) -> int:
    """Return rental length in days (non-negative)."""
    if not start_date or not end_date:
        return 0
    return max((end_date - start_date).days, 0)


def calculate_rental_pricing(car: Car | None, start_date: date | None, end_date: date | None) -> Tuple[int, Decimal, Decimal]:
    """
    Calculate rental days, per-day rate, and total price for the given car and dates.

    Uses the car's tiered pricing, preferring high-season values (вс) and
    falling back to low-season (нс) or the legacy daily_rate when empty.
    """
    days = rental_days(start_date, end_date)
    if not car or days <= 0:
        return days, Decimal("0.00"), Decimal("0.00")

    daily_rate = car.get_rate_for_days(days)
    total_price = daily_rate * Decimal(days)
    return days, daily_rate, total_price

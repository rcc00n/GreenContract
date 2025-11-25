from datetime import date

from django.db.models import Case, Count, DecimalField, Sum, When
from django.db.models.functions import TruncMonth

from ..models import Rental


def rentals_summary():
    """Basic counts and revenue summary for the dashboard."""
    _ = date.today()  # Reserved for future date-based filtering

    total_rentals = Rental.objects.count()
    active_rentals = Rental.objects.filter(status="active").count()
    completed_rentals = Rental.objects.filter(status="completed").count()
    total_revenue = (
        Rental.objects.filter(status="completed").aggregate(total=Sum("total_price")).get("total") or 0
    )

    return {
        "total_rentals": total_rentals,
        "active_rentals": active_rentals,
        "completed_rentals": completed_rentals,
        "total_revenue": total_revenue,
    }


def car_utilization():
    """
    Simplified utilization: number of completed rentals and revenue by car.

    A more precise metric would look at day-level utilization, but for now we
    count completed rentals per car to highlight top performers.
    """
    qs = (
        Rental.objects.filter(status="completed")
        .values("car__plate_number", "car__make", "car__model")
        .annotate(num_rentals=Count("id"), revenue=Sum("total_price"))
        .order_by("-num_rentals")
    )
    return list(qs)


def monthly_rental_performance(months=6):
    """
    Return month-by-month booking counts and revenue for the dashboard charts.

    The series always includes the requested number of months (default 6),
    filling missing months with zeros to keep the chart stable.
    """

    def _add_months(dt: date, months_delta: int) -> date:
        month_index = dt.month - 1 + months_delta
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        return date(year, month, 1)

    months = max(1, months)
    today = date.today()
    current_month = date(today.year, today.month, 1)
    window_start = _add_months(current_month, -(months - 1))

    aggregates = (
        Rental.objects.filter(start_date__gte=window_start)
        .annotate(month=TruncMonth("start_date"))
        .values("month")
        .annotate(
            count=Count("id"),
            revenue=Sum(
                Case(
                    When(status="completed", then="total_price"),
                    default=0,
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            ),
        )
        .order_by("month")
    )

    by_month = {}
    for row in aggregates:
        month_value = row["month"].date() if hasattr(row["month"], "date") else row["month"]
        by_month[month_value] = {
            "count": row.get("count", 0),
            "revenue": row.get("revenue") or 0,
        }

    timeline = []
    for idx in range(months):
        month_point = _add_months(window_start, idx)
        row = by_month.get(month_point, {"count": 0, "revenue": 0})
        timeline.append(
            {
                "month": month_point,
                "label": month_point.strftime("%b %Y"),
                "count": row["count"],
                "revenue": row["revenue"],
            }
        )

    return timeline


def rental_status_breakdown():
    """Return counts per rental status keyed by the status code."""
    status_totals = {code: 0 for code, _ in Rental.STATUS_CHOICES}
    aggregated = Rental.objects.values("status").annotate(count=Count("id"))
    for row in aggregated:
        status_totals[row["status"]] = row["count"]
    return status_totals

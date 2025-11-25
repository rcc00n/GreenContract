from datetime import date

from django.db.models import Count, Sum

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

from decimal import Decimal

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Car(models.Model):
    plate_number = models.CharField(max_length=20, unique=True)
    make = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    year = models.PositiveIntegerField()
    daily_rate = models.DecimalField(max_digits=8, decimal_places=2)
    rate_1_4_high = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="1-4 days, high season (вс).",
    )
    rate_5_14_high = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="5-14 days, high season (вс).",
    )
    rate_15_plus_high = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="15+ days, high season (вс).",
    )
    rate_1_4_low = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="1-4 days, low season (нс).",
    )
    rate_5_14_low = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="5-14 days, low season (нс).",
    )
    rate_15_plus_low = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="15+ days, low season (нс).",
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.plate_number} - {self.make} {self.model} ({self.year})"

    def get_rate_for_days(self, days: int, season: str = "high") -> Decimal:
        """
        Return the per-day rate based on rental duration.

        - season is "high" (вс) or "low" (нс)
        - falls back to the opposite season if empty, then to daily_rate
        """

        def pick(season_name: str) -> Decimal | None:
            mapping = [
                (15, f"rate_15_plus_{season_name}"),
                (5, f"rate_5_14_{season_name}"),
                (1, f"rate_1_4_{season_name}"),
            ]
            for threshold, field_name in mapping:
                if days >= threshold:
                    value = getattr(self, field_name, Decimal("0.00"))
                    if value and value > 0:
                        return value
            return None

        primary = "high" if season != "low" else "low"
        rate = pick(primary) or pick("low" if primary == "high" else "high") or self.daily_rate
        return rate


class Customer(models.Model):
    full_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30)
    license_number = models.CharField(max_length=50)
    address = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.full_name


class Rental(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    car = models.ForeignKey(Car, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    daily_rate = models.DecimalField(max_digits=8, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"Rental #{self.id} - {self.customer} / {self.car}"


class ContractTemplate(models.Model):
    FORMAT_CHOICES = [
        ("html", "HTML"),
        ("docx", "DOCX"),
    ]

    name = models.CharField(max_length=100)
    file = models.FileField(upload_to="contract_templates/", blank=True, null=True)
    body_html = models.TextField(blank=True, null=True)
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES, default="html")
    description = models.TextField(blank=True, null=True)

    placeholder_help = models.TextField(
        default="Use {{ customer.full_name }}, {{ car.plate_number }}, {{ rental.start_date }} etc."
    )

    def __str__(self):
        return self.name

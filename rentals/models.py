import random
from decimal import Decimal

from django.db import IntegrityError, models
from django.contrib.auth import get_user_model

User = get_user_model()


class Car(models.Model):
    plate_number = models.CharField(max_length=20, unique=True)
    make = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    year = models.PositiveIntegerField()
    vin = models.CharField(max_length=50, blank=True, null=True, help_text="VIN / номер кузова.")
    sts_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Registration certificate (СТС) number.",
    )
    sts_issue_date = models.DateField(blank=True, null=True)
    sts_issued_by = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        help_text="Кем выдано СТС.",
    )
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
    passport_series = models.CharField(max_length=10, blank=True, null=True)
    passport_number = models.CharField(max_length=20, blank=True, null=True)
    passport_issue_date = models.DateField(blank=True, null=True)
    passport_issued_by = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    registration_address = models.TextField(blank=True, null=True, help_text="Адрес прописки / регистрации.")
    residence_address = models.TextField(blank=True, null=True, help_text="Адрес фактического проживания.")
    notes = models.TextField(blank=True, null=True)
    tags = models.ManyToManyField(
        "CustomerTag",
        blank=True,
        related_name="customers",
        help_text="Гибкие теги, например VIP, проблемный, корпоративный.",
    )

    def __str__(self):
        return self.full_name


class CustomerTag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Rental(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    car = models.ForeignKey(Car, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    contract_number = models.CharField(
        max_length=5,
        unique=True,
        blank=True,
        null=True,
        help_text="Автоматически сгенерированный 5-значный номер договора.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    unique_daily_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Уникальный тариф по сделке. Перебивает цену авто.",
    )
    daily_rate = models.DecimalField(max_digits=8, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    balance_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сумма к оплате после предоплаты.",
    )
    airport_fee_start = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сбор при выдаче в аэропорту.",
    )
    airport_fee_end = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сбор при возврате в аэропорту.",
    )
    night_fee_start = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Ночной выход при выдаче.",
    )
    night_fee_end = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Ночной выход при возврате.",
    )
    delivery_issue_city = models.CharField(max_length=120, blank=True, default="")
    delivery_issue_fee = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    delivery_return_city = models.CharField(max_length=120, blank=True, default="")
    delivery_return_fee = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    child_seat_included = models.BooleanField(default=False)
    child_seat_count = models.PositiveIntegerField(default=0, blank=True)
    booster_included = models.BooleanField(default=False)
    booster_count = models.PositiveIntegerField(default=0, blank=True)
    ski_rack_included = models.BooleanField(default=False)
    ski_rack_count = models.PositiveIntegerField(default=0, blank=True)
    roof_box_included = models.BooleanField(default=False)
    roof_box_count = models.PositiveIntegerField(default=0, blank=True)
    crossbars_included = models.BooleanField(default=False)
    crossbars_count = models.PositiveIntegerField(default=0, blank=True)
    equipment_manual_total = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Фиксированная сумма за оборудование (если не по суткам).",
    )
    discount_amount = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), blank=True)
    prepayment = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.deal_name

    @staticmethod
    def _generate_contract_number() -> str:
        return f"{random.randint(10000, 99999):05d}"

    @classmethod
    def generate_unique_contract_number(cls) -> str:
        """
        Generate a 5-digit number and retry if the candidate already exists.
        """
        for _ in range(50):
            candidate = cls._generate_contract_number()
            if not cls.objects.filter(contract_number=candidate).exists():
                return candidate
        raise RuntimeError("Could not generate a unique contract number.")

    def ensure_contract_number(self, force: bool = False):
        """
        Make sure the rental has a contract number before saving.
        """
        if self.contract_number and not force:
            return
        self.contract_number = self.generate_unique_contract_number()

    @property
    def customer_last_name(self) -> str:
        parts = (self.customer.full_name or "").strip().split()
        return parts[0] if parts else (self.customer.full_name or "")

    @property
    def deal_name(self) -> str:
        """
        Build a human-friendly deal name:
        {contract}/{last name}/{plate + make}/{start date}
        """
        contract = self.contract_number or "-----"
        last_name = self.customer_last_name or "—"
        car_piece = ""
        if self.car_id:
            car_piece = f"{self.car.plate_number} {self.car.make}".strip()
        date_piece = self.start_date.strftime("%Y-%m-%d") if self.start_date else ""
        return f"{contract}/{last_name}/{car_piece}/{date_piece}"

    def save(self, *args, **kwargs):
        attempts = 0
        while attempts < 3:
            if not self.contract_number:
                self.ensure_contract_number()
            try:
                return super().save(*args, **kwargs)
            except IntegrityError:
                # Contract number collision, try again with a fresh number.
                self.contract_number = None
                attempts += 1
        # If we exhausted retries, raise the last IntegrityError.
        return super().save(*args, **kwargs)


class ContractTemplate(models.Model):
    FORMAT_CHOICES = [
        ("html", "HTML"),
        ("docx", "DOCX"),
        ("pdf", "PDF"),
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

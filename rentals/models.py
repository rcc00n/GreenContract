import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError, models
from django.contrib.auth import get_user_model

try:
    from num2words import num2words
except ImportError:  # pragma: no cover - dependency installed via requirements
    num2words = None

User = get_user_model()

OPERATION_REGIONS = [
    "Республика Крым и Севастополь",
    "Ставропольский край",
    "Краснодарский край",
    "Карачаево-Черкесская Республика",
    "Кабардино-Балкарская Республика",
    "Адыгея",
    "Чечня",
    "Северная Осетия",
    "Калмыкия",
]

MILEAGE_LIMIT_CHOICES = [
    (0, "0"),
    (200, "200"),
    (250, "250"),
    (300, "300"),
]


def _format_money_words(value: Decimal | None) -> str:
    if value is None:
        return ""
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if num2words is None:
        return str(amount)
    try:
        return num2words(amount, lang="ru", to="currency", currency="RUB")
    except Exception:
        return str(amount)


class Car(models.Model):
    plate_number = models.CharField("Госномер", max_length=20, unique=True)
    make = models.CharField("Марка", max_length=50)
    model = models.CharField("Модель", max_length=50)
    year = models.PositiveIntegerField("Год выпуска")
    color = models.CharField("Цвет", max_length=50, blank=True, null=True)
    region_code = models.CharField(
        "Регион",
        max_length=10,
        blank=True,
        null=True,
        help_text="Регион номера (например, 26 или 82).",
    )
    photo_url = models.URLField("Фото (ссылка)", max_length=500, blank=True, null=True)
    vin = models.CharField("ВИН", max_length=50, blank=True, null=True, help_text="ВИН / номер кузова.")
    sts_number = models.CharField(
        "Номер СТС",
        max_length=50,
        blank=True,
        null=True,
        help_text="Номер свидетельства о регистрации (СТС).",
    )
    sts_issue_date = models.DateField("Дата выдачи СТС", blank=True, null=True)
    sts_issued_by = models.CharField(
        "Кем выдана СТС",
        max_length=120,
        blank=True,
        null=True,
        help_text="Кем выдано СТС.",
    )
    registration_certificate_info = models.CharField(
        "Свидетельство о регистрации",
        max_length=255,
        blank=True,
        null=True,
        help_text="Статус/комментарий по свидетельству о регистрации.",
    )
    fuel_tank_volume_liters = models.PositiveIntegerField("Объем бака, л", blank=True, null=True)
    fuel_tank_cost_rub = models.DecimalField(
        "Стоимость полного бака, ₽",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    security_deposit = models.DecimalField(
        "Залог",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Залог по автомобилю.",
    )
    daily_rate = models.DecimalField("Базовый тариф, ₽/сутки", max_digits=8, decimal_places=2)
    rate_1_4_high = models.DecimalField(
        "1-4 дня (высокий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="1-4 дня, высокий сезон (вс).",
    )
    rate_5_14_high = models.DecimalField(
        "5-14 дней (высокий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="5-14 дней, высокий сезон (вс).",
    )
    rate_15_plus_high = models.DecimalField(
        "15+ дней (высокий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="15+ дней, высокий сезон (вс).",
    )
    rate_1_4_low = models.DecimalField(
        "1-4 дня (низкий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="1-4 дня, низкий сезон (нс).",
    )
    rate_5_14_low = models.DecimalField(
        "5-14 дней (низкий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="5-14 дней, низкий сезон (нс).",
    )
    rate_15_plus_low = models.DecimalField(
        "15+ дней (низкий сезон)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="15+ дней, низкий сезон (нс).",
    )
    loss_child_seat_fee = models.DecimalField("Детское сидение", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_reflective_vest_fee = models.DecimalField(
        "Светоотражающий жилет",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_registration_certificate_fee = models.DecimalField(
        "Свидетельство о регистрации ТС",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_alloy_wheel_fee = models.DecimalField(
        "Диски алюмин",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_steel_wheel_fee = models.DecimalField("Диски сталь", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_warning_triangle_fee = models.DecimalField(
        "Знак аварийной остановки",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_radio_panel_fee = models.DecimalField(
        "Съемная панель магнитолы",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_ski_mount_fee = models.DecimalField("Крепление лыж", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_car_keys_fee = models.DecimalField(
        "Ключи от автомобиля",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_hubcaps_fee = models.DecimalField(
        "Декоративные колпаки",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_gps_fee = models.DecimalField("Навигатор", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_license_plate_fee = models.DecimalField(
        "Гос. номера",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_external_antenna_fee = models.DecimalField(
        "Внешняя радиоантенна или зонт",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    loss_tire_fee = models.DecimalField("Автопокрышка", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_first_aid_kit_fee = models.DecimalField("Аптечка", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_jack_fee = models.DecimalField("Домкрат", max_digits=10, decimal_places=2, blank=True, null=True)
    loss_fire_extinguisher_fee = models.DecimalField(
        "Огнетушитель",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"

    def __str__(self):
        return f"{self.plate_number} - {self.make} {self.model} ({self.year})"

    @property
    def security_deposit_text(self) -> str:
        return _format_money_words(self.security_deposit)

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
    full_name = models.CharField("ФИО", max_length=100)
    birth_date = models.DateField("Дата рождения", blank=True, null=True, help_text="Дата рождения.")
    email = models.EmailField("Эл. почта", blank=True, null=True)
    phone = models.CharField("Телефон", max_length=30)
    license_number = models.CharField("Номер ВУ", max_length=50)
    license_issued_by = models.CharField(
        "Кем выдано ВУ",
        max_length=255,
        blank=True,
        null=True,
        help_text="Кем выдано водительское удостоверение.",
    )
    driving_since = models.DateField(
        "Стаж с",
        blank=True,
        null=True,
        help_text="Дата начала стажа вождения (Стаж с ...).",
    )
    passport_series = models.CharField("Серия паспорта", max_length=10, blank=True, null=True)
    passport_number = models.CharField("Номер паспорта", max_length=20, blank=True, null=True)
    passport_issue_date = models.DateField("Дата выдачи паспорта", blank=True, null=True)
    passport_issued_by = models.CharField("Кем выдан паспорт", max_length=255, blank=True, null=True)
    address = models.TextField("Адрес", blank=True, null=True)
    registration_address = models.TextField(
        "Адрес прописки",
        blank=True,
        null=True,
        help_text="Адрес прописки / регистрации.",
    )
    residence_address = models.TextField(
        "Адрес проживания",
        blank=True,
        null=True,
        help_text="Адрес фактического проживания.",
    )
    notes = models.TextField("Примечания", blank=True, null=True)
    discount_percent = models.DecimalField(
        "Скидка, %",
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Персональная скидка, %.",
    )
    tags = models.ManyToManyField(
        "CustomerTag",
        verbose_name="Теги",
        blank=True,
        related_name="customers",
        help_text="Гибкие теги, например ВИП, проблемный, корпоративный.",
    )

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return self.full_name


class CustomerTag(models.Model):
    name = models.CharField("Название тега", max_length=50, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Тег клиента"
        verbose_name_plural = "Теги клиентов"

    def __str__(self):
        return self.name


class Rental(models.Model):
    STATUS_CHOICES = [
        ("draft", "Черновик"),
        ("active", "Активна"),
        ("completed", "Завершена"),
        ("cancelled", "Отменена"),
    ]

    car = models.ForeignKey(Car, verbose_name="Автомобиль", on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, verbose_name="Клиент", on_delete=models.PROTECT)
    second_driver = models.ForeignKey(
        Customer,
        verbose_name="Второй водитель",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="secondary_rentals",
        help_text="Опциональный второй водитель для договора.",
    )
    contract_number = models.CharField(
        "Номер договора",
        max_length=5,
        unique=True,
        blank=True,
        null=True,
        help_text="Автоматически сгенерированный 5-значный номер договора.",
    )
    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания")
    start_time = models.TimeField("Время начала", blank=True, null=True)
    end_time = models.TimeField("Время окончания", blank=True, null=True)
    unique_daily_rate = models.DecimalField(
        "Уникальный тариф, ₽/сутки",
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Уникальный тариф по сделке. Перебивает цену авто.",
    )
    daily_rate = models.DecimalField("Суточный тариф", max_digits=8, decimal_places=2)
    total_price = models.DecimalField("Итоговая сумма", max_digits=10, decimal_places=2)
    balance_due = models.DecimalField(
        "К оплате",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сумма к оплате после предоплаты.",
    )
    airport_fee_start = models.DecimalField(
        "Сбор аэропорт (выдача)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сбор при выдаче в аэропорту.",
    )
    airport_fee_end = models.DecimalField(
        "Сбор аэропорт (возврат)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Сбор при возврате в аэропорту.",
    )
    night_fee_start = models.DecimalField(
        "Ночной выход (выдача)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Ночной выход при выдаче.",
    )
    night_fee_end = models.DecimalField(
        "Ночной выход (возврат)",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Ночной выход при возврате.",
    )
    delivery_issue_city = models.CharField("Город выдачи (доставка)", max_length=120, blank=True, default="")
    delivery_issue_fee = models.DecimalField(
        "Стоимость выдачи",
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
    )
    delivery_return_city = models.CharField("Город возврата (доставка)", max_length=120, blank=True, default="")
    delivery_return_fee = models.DecimalField(
        "Стоимость возврата",
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
    )
    operation_regions = models.TextField(
        "Территория эксплуатации",
        blank=True,
        default="",
        help_text="Регионы эксплуатации, разделённые запятыми.",
    )
    mileage_limit_km = models.PositiveIntegerField(
        "Ограничение пробега, км",
        choices=MILEAGE_LIMIT_CHOICES,
        default=0,
    )
    child_seat_included = models.BooleanField("Детское кресло", default=False)
    child_seat_count = models.PositiveIntegerField("Кол-во детских кресел", default=0, blank=True)
    booster_included = models.BooleanField("Бустер", default=False)
    booster_count = models.PositiveIntegerField("Кол-во бустеров", default=0, blank=True)
    ski_rack_included = models.BooleanField("Крепления для лыж", default=False)
    ski_rack_count = models.PositiveIntegerField("Кол-во креплений", default=0, blank=True)
    roof_box_included = models.BooleanField("Автобокс", default=False)
    roof_box_count = models.PositiveIntegerField("Кол-во автобоксов", default=0, blank=True)
    crossbars_included = models.BooleanField("Поперечины", default=False)
    crossbars_count = models.PositiveIntegerField("Кол-во поперечин", default=0, blank=True)
    equipment_manual_total = models.DecimalField(
        "Фикс. сумма оборудования",
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        blank=True,
        help_text="Фиксированная сумма за оборудование (если не по суткам).",
    )
    discount_amount = models.DecimalField("Скидка, ₽", max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    discount_percent = models.DecimalField("Скидка, %", max_digits=5, decimal_places=2, default=Decimal("0.00"), blank=True)
    prepayment = models.DecimalField("Предоплата", max_digits=9, decimal_places=2, default=Decimal("0.00"), blank=True)
    status = models.CharField("Статус", max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(User, verbose_name="Создал", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Аренда"
        verbose_name_plural = "Аренды"

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
        raise RuntimeError("Не удалось сгенерировать уникальный номер договора.")

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
        date_piece = self.start_date.strftime("%d-%m-%Y") if self.start_date else ""
        return f"{contract}/{last_name}/{car_piece}/{date_piece}"

    @property
    def duration_days(self) -> int:
        if not self.start_date or not self.end_date:
            return 0
        return max((self.end_date - self.start_date).days, 0)

    @property
    def date_range(self) -> str:
        if not self.start_date and not self.end_date:
            return ""
        start = self.start_date.strftime("%d-%m-%Y") if self.start_date else ""
        end = self.end_date.strftime("%d-%m-%Y") if self.end_date else ""
        if start and end:
            return f"{start} — {end}"
        return start or end

    @property
    def advance_payment_text(self) -> str:
        return _format_money_words(self.prepayment)

    @property
    def balance_due_text(self) -> str:
        return _format_money_words(self.balance_due)

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
        ("html", "Веб-шаблон"),
        ("docx", "Документ Ворд"),
        ("pdf", "ПДФ-форма"),
    ]

    name = models.CharField("Название", max_length=100)
    file = models.FileField("Файл", upload_to="contract_templates/", blank=True, null=True)
    body_html = models.TextField("Разметка веб-шаблона", blank=True, null=True)
    format = models.CharField("Формат", max_length=10, choices=FORMAT_CHOICES, default="html")
    description = models.TextField("Описание", blank=True, null=True)

    placeholder_help = models.TextField(
        "Подсказка плейсхолдеров",
        default="Используйте {{ клиент.фио }}, {{ авто.госномер }}, {{ аренда.дата_начала }} и т.д.",
    )

    class Meta:
        verbose_name = "Шаблон договора"
        verbose_name_plural = "Шаблоны договоров"

    def __str__(self):
        return self.name

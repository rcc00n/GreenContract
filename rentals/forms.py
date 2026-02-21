import re
from datetime import date, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm

from .car_constants import CAR_LOSS_FEE_FIELDS
from .models import (
    BusinessSettings,
    Car,
    ContractTemplate,
    Customer,
    CustomerTag,
    OPERATION_REGIONS,
    Rental,
)
from .services.pricing import (
    calculate_rental_pricing,
    get_delivery_fees,
    parse_delivery_overrides,
    parse_night_slots,
)

DATE_INPUT_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")
DATE_PLACEHOLDER = "ДД-ММ-ГГГГ"
DRIVING_SINCE_INPUT_FORMATS = ("%Y", *DATE_INPUT_FORMATS)
DRIVING_SINCE_PLACEHOLDER = "ГГГГ"


def _configure_date_field(field: forms.DateField):
    widget = field.widget
    widget.input_type = "text"
    widget.format = "%d-%m-%Y"
    widget.attrs.setdefault("placeholder", DATE_PLACEHOLDER)
    field.input_formats = DATE_INPUT_FORMATS


def _configure_driving_since_field(field: forms.DateField):
    widget = field.widget
    widget.input_type = "text"
    widget.format = "%Y"
    widget.attrs.setdefault("placeholder", DRIVING_SINCE_PLACEHOLDER)
    field.input_formats = DRIVING_SINCE_INPUT_FORMATS


def _apply_bootstrap_classes(fields):
    for field in fields.values():
        widget = field.widget
        css = widget.attrs.get("class", "")
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = f"form-check-input {css}".strip()
        elif isinstance(widget, forms.CheckboxSelectMultiple):
            widget.attrs["class"] = f"form-check-input {css}".strip()
        else:
            widget.attrs["class"] = f"form-control {css}".strip()
        if isinstance(widget, forms.Textarea):
            widget.attrs.setdefault("rows", 3)


class StyledModelForm(forms.ModelForm):
    """Apply basic Bootstrap classes to all widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields)


class CarForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "sts_issue_date" in self.fields:
            _configure_date_field(self.fields["sts_issue_date"])
        if "fuel_tank_volume_liters" in self.fields:
            widget = self.fields["fuel_tank_volume_liters"].widget
            widget.input_type = "number"
            widget.attrs.setdefault("min", "0")
            widget.attrs.setdefault("step", "1")

        decimal_fields = [
            "fuel_tank_cost_rub",
            "security_deposit",
            "daily_rate",
            "rate_1_4_high",
            "rate_5_14_high",
            "rate_15_plus_high",
            "rate_1_4_low",
            "rate_5_14_low",
            "rate_15_plus_low",
            *[field for field, _ in CAR_LOSS_FEE_FIELDS],
        ]
        for name in decimal_fields:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs.setdefault("min", "0")
                widget.attrs.setdefault("step", "0.01")

    class Meta:
        model = Car
        fields = [
            "plate_number",
            "region_code",
            "color",
            "photo_url",
            "vin",
            "make",
            "model",
            "year",
            "sts_number",
            "sts_issue_date",
            "sts_issued_by",
            "registration_certificate_info",
            "fuel_tank_volume_liters",
            "fuel_tank_cost_rub",
            "security_deposit",
            "rate_1_4_high",
            "rate_5_14_high",
            "rate_15_plus_high",
            "rate_1_4_low",
            "rate_5_14_low",
            "rate_15_plus_low",
            "daily_rate",
            "is_active",
            *[field for field, _ in CAR_LOSS_FEE_FIELDS],
        ]
        labels = {
            "daily_rate": "Базовый тариф (если нет градации)",
            "vin": "ВИН",
            "sts_number": "СТС номер",
            "sts_issue_date": "СТС выдана",
            "sts_issued_by": "Кем выдана СТС",
            "registration_certificate_info": "Свидетельство о регистрации",
            "fuel_tank_volume_liters": "Объем бака (л)",
            "fuel_tank_cost_rub": "Объем бака (руб.)",
            "security_deposit": "Залог",
            "color": "Цвет",
            "photo_url": "Фото (ссылка)",
            "region_code": "Регион (26 или 82)",
            "rate_1_4_high": "1-4 дня (вс)",
            "rate_5_14_high": "5-14 дней (вс)",
            "rate_15_plus_high": "15+ дней (вс)",
            "rate_1_4_low": "1-4 дня (нс)",
            "rate_5_14_low": "5-14 дней (нс)",
            "rate_15_plus_low": "15+ дней (нс)",
        }
        labels.update({field: label for field, label in CAR_LOSS_FEE_FIELDS})
        help_texts = {
            "daily_rate": "Используется, если тариф по градации не заполнен.",
            "vin": "17 символов, можно оставить пустым.",
            "sts_number": "Номер свидетельства о регистрации (СТС).",
            "sts_issue_date": "Дата выдачи СТС (ДД-ММ-ГГГГ).",
            "sts_issued_by": "Кем выдано свидетельство.",
            "registration_certificate_info": "Оригинал/копия/где хранится.",
            "fuel_tank_volume_liters": "Полный объем бака в литрах.",
            "fuel_tank_cost_rub": "Стоимость полного бака (руб.), если фиксируете ее.",
            "security_deposit": "Сумма залога по автомобилю.",
            "photo_url": "При желании можно добавить ссылку на фото авто.",
            "rate_1_4_high": "Высокий сезон (вс) за сутки при аренде 1-4 дней.",
            "rate_5_14_high": "Высокий сезон (вс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_high": "Высокий сезон (вс) за сутки при аренде 15+ дней.",
            "rate_1_4_low": "Низкий сезон (нс) за сутки при аренде 1-4 дней.",
            "rate_5_14_low": "Низкий сезон (нс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_low": "Низкий сезон (нс) за сутки при аренде 15+ дней.",
        }
        help_texts.update({field: "Стоимость при утере, ₽." for field, _ in CAR_LOSS_FEE_FIELDS})


class CustomerForm(StyledModelForm):
    tags_text = forms.CharField(
        required=False,
        label="Теги",
        help_text="Через запятую: ВИП, корпоративный, проблемный. Можно добавлять новые.",
    )

    class Meta:
        model = Customer
        fields = [
            "full_name",
            "birth_date",
            "email",
            "phone",
            "license_number",
            "license_issued_by",
            "driving_since",
            "passport_series",
            "passport_number",
            "passport_issued_by",
            "passport_issue_date",
            "registration_address",
            "discount_percent",
        ]
        labels = {
            "full_name": "ФИО",
            "birth_date": "Дата рождения",
            "email": "Эл. почта",
            "phone": "Телефон",
            "license_number": "Номер ВУ",
            "license_issued_by": "В.у. выдано",
            "driving_since": "Стаж с",
            "passport_series": "Серия паспорта",
            "passport_number": "Номер паспорта",
            "passport_issued_by": "Кем выдан паспорт",
            "passport_issue_date": "Дата выдачи паспорта",
            "registration_address": "Адрес прописки",
            "discount_percent": "Скидка, %",
        }
        help_texts = {
            "birth_date": "Формат ДД-ММ-ГГГГ.",
            "license_number": "Номер водительского удостоверения.",
            "license_issued_by": "Кем выдано водительское удостоверение.",
            "driving_since": "Дата начала стажа вождения (ГГГГ; можно указать ДД-ММ-ГГГГ).",
            "passport_series": "Серия паспорта (4 цифры).",
            "passport_number": "Номер паспорта (6 цифр) или иной документ.",
            "passport_issue_date": "Дата выдачи документа (ДД-ММ-ГГГГ).",
            "registration_address": "Адрес регистрации (прописка).",
            "discount_percent": "Персональная скидка в процентах.",
        }

    field_order = [
        "full_name",
        "birth_date",
        "email",
        "phone",
        "license_number",
        "license_issued_by",
        "driving_since",
        "passport_series",
        "passport_number",
        "passport_issued_by",
        "passport_issue_date",
        "registration_address",
        "discount_percent",
        "tags_text",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for date_field in ("birth_date", "passport_issue_date"):
            if date_field in self.fields:
                _configure_date_field(self.fields[date_field])
        if "driving_since" in self.fields:
            _configure_driving_since_field(self.fields["driving_since"])
        if "discount_percent" in self.fields:
            widget = self.fields["discount_percent"].widget
            widget.attrs.setdefault("step", "0.1")
            widget.attrs.setdefault("min", "0")
        if "tags_text" in self.fields and self.instance.pk:
            existing = ", ".join(self.instance.tags.values_list("name", flat=True))
            self.initial.setdefault("tags_text", existing)

    def _parse_tags(self, raw: str) -> list[CustomerTag]:
        names = set()
        for piece in re.split(r"[;,#/|\n\r]+", raw or ""):
            normalized = piece.strip()
            if not normalized:
                continue
            names.add(normalized)
        tags = []
        for name in sorted(names, key=str.lower):
            tag, _ = CustomerTag.objects.get_or_create(name=name)
            tags.append(tag)
        return tags

    def save(self, commit=True):
        instance = super().save(commit=commit)
        raw_tags = self.cleaned_data.get("tags_text", "")
        tags = self._parse_tags(raw_tags)
        if commit:
            instance.save()
            instance.tags.set(tags)
        else:
            self._pending_tags = tags
        return instance

    def save_m2m(self):
        super().save_m2m()
        if hasattr(self, "_pending_tags"):
            self.instance.tags.set(self._pending_tags)


class RentalForm(StyledModelForm):
    operation_regions = forms.MultipleChoiceField(
        required=False,
        label="Территория эксплуатации",
        choices=[(region, region) for region in OPERATION_REGIONS],
        widget=forms.CheckboxSelectMultiple,
        help_text="Отметьте возможные регионы эксплуатации.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_customer_queryset("customer", "_customer_label")
        self._limit_customer_queryset("second_driver", "_second_driver_label")
        self.initial_car_label = ""
        if getattr(self.instance, "car_id", None):
            try:
                self.initial_car_label = str(self.instance.car)
            except Exception:
                self.initial_car_label = ""
        if "contract_number" in self.fields:
            field = self.fields["contract_number"]
            field.disabled = True
            field.required = False
            field.widget.attrs.setdefault("placeholder", "Генерируется автоматически")
            if not self.instance.contract_number:
                try:
                    self.instance.ensure_contract_number()
                except Exception:
                    # Leave empty if generation fails; save() will retry.
                    pass
            self.initial.setdefault("contract_number", self.instance.contract_number)

        if not self.is_bound:
            today = date.today()
            self.initial.setdefault("start_date", today)
            self.initial.setdefault("end_date", today + timedelta(days=1))
            if "car_wash_fee" in self.fields and not self.instance.pk:
                settings = BusinessSettings.get_solo()
                self.initial.setdefault("car_wash_fee", settings.car_wash_default)

        for name in ("start_date", "end_date"):
            _configure_date_field(self.fields[name])
            widget = self.fields[name].widget
            widget.input_type = "date"
            widget.format = "%Y-%m-%d"
            widget.attrs.setdefault("data-date-input", "true")

        if "operation_regions" in self.fields and not self.is_bound:
            raw_regions = self.initial.get("operation_regions")
            if raw_regions is None or raw_regions == "":
                raw_regions = getattr(self.instance, "operation_regions", "")
            parsed: list[str] = []
            if isinstance(raw_regions, str):
                parsed = [item.strip() for item in raw_regions.split(",") if item.strip()]
            elif isinstance(raw_regions, (list, tuple, set)):
                parsed = [str(item).strip() for item in raw_regions if str(item).strip()]
            self.initial["operation_regions"] = parsed

        for name in ("start_time", "end_time"):
            if name in self.fields:
                widget = self.fields[name].widget
                widget.input_type = "text"
                widget.attrs.setdefault("placeholder", "ЧЧ:ММ")
                widget.attrs.setdefault("inputmode", "numeric")
                widget.attrs.setdefault("pattern", "[0-9]{2}:[0-9]{2}")
                widget.attrs.setdefault("autocomplete", "off")
                widget.attrs.setdefault("data-time-picker-input", "true")

        for name in (
            "child_seat_included",
            "booster_included",
            "ski_rack_included",
            "roof_box_included",
            "crossbars_included",
        ):
            if name in self.fields:
                self.fields[name].widget = forms.CheckboxInput(attrs={"class": "form-check-input"})

        for name in ("daily_rate", "total_price", "balance_due"):
            if name in self.fields:
                self.fields[name].widget.attrs["readonly"] = True
                self.fields[name].widget.attrs["tabindex"] = "-1"
                self.fields[name].widget.attrs["aria-readonly"] = "true"

        numeric_optional = (
            "unique_daily_rate",
            "car_wash_fee",
            "night_fee_start",
            "night_fee_end",
            "delivery_issue_fee",
            "delivery_return_fee",
            "equipment_manual_total",
            "discount_amount",
            "discount_percent",
            "prepayment",
        )
        integer_optional = (
            "child_seat_count",
            "booster_count",
            "ski_rack_count",
            "roof_box_count",
            "crossbars_count",
        )

        for name in numeric_optional:
            if name in self.fields:
                self.fields[name].required = False
                self.fields[name].widget.attrs.setdefault("min", "0")
                self.fields[name].widget.attrs.setdefault("step", "1")

        for name in integer_optional:
            if name in self.fields:
                self.fields[name].required = False
                self.fields[name].widget.attrs.setdefault("min", "0")

        if "discount_percent" in self.fields:
            self.fields["discount_percent"].widget.attrs.setdefault("max", "100")

        delivery_fees = get_delivery_fees()
        priority_cities = [
            "Симферополь-0",
            "Симферополь-1000",
            "Минеральные Воды-0",
            "Минеральные Воды-1000",
        ]
        ordered_cities = [city for city in priority_cities if city in delivery_fees]
        ordered_cities += sorted(city for city in delivery_fees.keys() if city not in ordered_cities)
        delivery_choices = [("", "Без доставки")] + [(city, city) for city in ordered_cities]
        for name in ("delivery_issue_city", "delivery_return_city"):
            if name in self.fields:
                self.fields[name].required = False
                self.fields[name].widget = forms.Select(choices=delivery_choices)
        self.initial_customer_label = getattr(self, "_customer_label", "")
        self.initial_second_driver_label = getattr(self, "_second_driver_label", "")

    class Meta:
        model = Rental
        fields = [
            "contract_number",
            "car",
            "customer",
            "second_driver",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "operation_regions",
            "mileage_limit_km",
            "unique_daily_rate",
            "daily_rate",
            "car_wash_fee",
            "night_fee_start",
            "night_fee_end",
            "delivery_issue_city",
            "delivery_issue_fee",
            "delivery_return_city",
            "delivery_return_fee",
            "child_seat_included",
            "child_seat_count",
            "booster_included",
            "booster_count",
            "ski_rack_included",
            "ski_rack_count",
            "roof_box_included",
            "roof_box_count",
            "crossbars_included",
            "crossbars_count",
            "equipment_manual_total",
            "discount_amount",
            "discount_percent",
            "prepayment",
            "total_price",
            "balance_due",
            "status",
        ]
        labels = {
            "contract_number": "Номер договора",
            "car": "Автомобиль",
            "customer": "Клиент",
            "second_driver": "Второй водитель",
            "start_date": "Дата начала",
            "end_date": "Дата окончания",
            "start_time": "Время выдачи",
            "end_time": "Время возврата",
            "operation_regions": "Территория эксплуатации",
            "mileage_limit_km": "Ограничение пробега (км)",
            "unique_daily_rate": "Уникальный тариф (за сутки)",
            "daily_rate": "Суточный тариф",
            "total_price": "Итоговая сумма",
            "car_wash_fee": "Мойка",
            "night_fee_start": "Ночной выход (выдача)",
            "night_fee_end": "Ночной выход (возврат)",
            "delivery_issue_city": "Доставка: выдача в городе",
            "delivery_issue_fee": "Стоимость выдачи",
            "delivery_return_city": "Доставка: возврат в городе",
            "delivery_return_fee": "Стоимость возврата",
            "child_seat_included": "Детское кресло",
            "child_seat_count": "Детское кресло, шт",
            "booster_included": "Бустер",
            "booster_count": "Бустер, шт",
            "ski_rack_included": "Крепления д/лыж",
            "ski_rack_count": "Крепления д/лыж",
            "roof_box_included": "Автобокс",
            "roof_box_count": "Автобокс",
            "crossbars_included": "Поперечины",
            "crossbars_count": "Поперечины",
            "equipment_manual_total": "Фикс. сумма оборудования",
            "discount_amount": "Скидка, ₽",
            "discount_percent": "Скидка, %",
            "prepayment": "Предоплата",
            "balance_due": "К оплате после предоплаты",
            "status": "Статус",
        }
        help_texts = {
            "operation_regions": "Укажите регионы, в которых разрешена эксплуатация.",
            "mileage_limit_km": "0 — без ограничения пробега.",
            "car_wash_fee": "Стоимость мойки, ₽.",
        }

    def clean(self):
        cleaned_data = super().clean()

        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        car = cleaned_data.get("car")
        customer = cleaned_data.get("customer")
        second_driver = cleaned_data.get("second_driver")

        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "Дата окончания должна быть позже даты начала.")
            return cleaned_data
        if customer and second_driver and customer == second_driver:
            self.add_error("second_driver", "Второй водитель не должен совпадать с основным клиентом.")

        if start_date and end_date:
            # Синхронизируем чекбоксы с количествами, чтобы в базе сохранялось 1/0.
            for flag, count_field in (
                ("child_seat_included", "child_seat_count"),
                ("booster_included", "booster_count"),
                ("ski_rack_included", "ski_rack_count"),
                ("roof_box_included", "roof_box_count"),
                ("crossbars_included", "crossbars_count"),
            ):
                if cleaned_data.get(flag) and not cleaned_data.get(count_field):
                    cleaned_data[count_field] = 1
                if not cleaned_data.get(flag):
                    cleaned_data[count_field] = 0

            pricing = calculate_rental_pricing(
                car,
                start_date,
                end_date,
                start_time=cleaned_data.get("start_time"),
                end_time=cleaned_data.get("end_time"),
                unique_daily_rate=cleaned_data.get("unique_daily_rate"),
                car_wash_fee=cleaned_data.get("car_wash_fee"),
                night_fee_start=cleaned_data.get("night_fee_start"),
                night_fee_end=cleaned_data.get("night_fee_end"),
                delivery_issue_city=cleaned_data.get("delivery_issue_city") or "",
                delivery_return_city=cleaned_data.get("delivery_return_city") or "",
                delivery_issue_fee=cleaned_data.get("delivery_issue_fee"),
                delivery_return_fee=cleaned_data.get("delivery_return_fee"),
                child_seat_count=cleaned_data.get("child_seat_count") or 0,
                booster_count=cleaned_data.get("booster_count") or 0,
                ski_rack_count=cleaned_data.get("ski_rack_count") or 0,
                roof_box_count=cleaned_data.get("roof_box_count") or 0,
                crossbars_count=cleaned_data.get("crossbars_count") or 0,
                child_seat_included=cleaned_data.get("child_seat_included") or False,
                booster_included=cleaned_data.get("booster_included") or False,
                ski_rack_included=cleaned_data.get("ski_rack_included") or False,
                roof_box_included=cleaned_data.get("roof_box_included") or False,
                crossbars_included=cleaned_data.get("crossbars_included") or False,
                equipment_manual_total=cleaned_data.get("equipment_manual_total"),
                discount_amount=cleaned_data.get("discount_amount"),
                discount_percent=cleaned_data.get("discount_percent"),
                prepayment=cleaned_data.get("prepayment"),
            )
            cleaned_data["daily_rate"] = pricing.daily_rate
            cleaned_data["total_price"] = pricing.total_price
            cleaned_data["balance_due"] = pricing.balance_due
        return cleaned_data

    def clean_operation_regions(self):
        selected = self.cleaned_data.get("operation_regions") or []
        if isinstance(selected, str):
            selected = [value.strip() for value in selected.split(",") if value.strip()]
        if not selected:
            return ""
        selected_set = set(selected)
        ordered = [region for region in OPERATION_REGIONS if region in selected_set]
        return ", ".join(ordered)

    def save(self, commit=True):
        instance = super().save(commit=False)
        regions = self.cleaned_data.get("operation_regions", "")
        if not regions:
            raw_regions = self.data.getlist(self.add_prefix("operation_regions"))
            if raw_regions:
                selected_set = {str(value).strip() for value in raw_regions if value and str(value).strip()}
                ordered = [region for region in OPERATION_REGIONS if region in selected_set]
                regions = ", ".join(ordered)
        if isinstance(regions, (list, tuple)):
            selected_set = {str(value).strip() for value in regions if value and str(value).strip()}
            ordered = [region for region in OPERATION_REGIONS if region in selected_set]
            regions = ", ".join(ordered)
        instance.operation_regions = regions or ""
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def _limit_customer_queryset(self, field_name: str, label_attr: str):
        """
        Keep the customer queryset tiny so rendering the form does not pull hundreds
        of thousands of rows. Only include the selected customer (if any).
        """

        customer_field = self.fields.get(field_name)
        if not customer_field:
            return
        customer_field.widget = forms.HiddenInput()

        selected_id = None
        if self.is_bound:
            selected_id = self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)
        elif self.initial.get(field_name):
            selected_id = self.initial.get(field_name)
        elif getattr(self.instance, f"{field_name}_id", None):
            selected_id = getattr(self.instance, f"{field_name}_id")

        queryset = Customer.objects.none()
        label = ""

        if selected_id:
            queryset = Customer.objects.filter(pk=selected_id)
            customer = queryset.first()
            if customer:
                label = f"{customer.full_name} · {customer.phone}"
                customer_field.initial = customer.pk

        customer_field.queryset = queryset
        setattr(self, label_attr, label)


class ContractTemplateForm(StyledModelForm):
    class Meta:
        model = ContractTemplate
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        format_choice = cleaned.get("format")
        uploaded_file = cleaned.get("file")
        body_html = (cleaned.get("body_html") or "").strip()

        def _add_error(field, message):
            self.add_error(field, message)

        if format_choice == "html":
            if not body_html:
                _add_error("body_html", "Добавьте разметку веб-шаблона.")

        elif format_choice == "docx":
            if not uploaded_file:
                _add_error("file", "Загрузите файл Ворд для шаблона.")
            elif not uploaded_file.name.lower().endswith(".docx"):
                _add_error("file", "Для формата Ворд нужен файл в формате ДОКС.")

        elif format_choice == "pdf":
            if not uploaded_file and not body_html:
                _add_error("file", "Загрузите ПДФ или заполните веб-шаблон для конвертации в ПДФ.")
                _add_error("body_html", "Заполните разметку веб-шаблона или приложите готовый ПДФ.")
            if uploaded_file and not uploaded_file.name.lower().endswith(".pdf"):
                _add_error("file", "Для формата ПДФ нужен файл ПДФ.")

        return cleaned


class BusinessSettingsForm(StyledModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            "high_season_start",
            "high_season_end",
            "car_wash_default",
            "night_fee_default",
            "night_fee_slots_text",
            "child_seat_daily",
            "child_seat_cap",
            "booster_daily",
            "booster_cap",
            "ski_rack_daily",
            "autobox_daily",
            "crossbars_daily",
            "delivery_fees_text",
        ]
        labels = {
            "high_season_start": "Начало высокого сезона",
            "high_season_end": "Конец высокого сезона",
            "car_wash_default": "Мойка по умолчанию, ₽",
            "night_fee_default": "Ночной выход по умолчанию, ₽",
            "night_fee_slots_text": "Ночные интервалы (HH:MM-HH:MM=сумма)",
            "delivery_fees_text": "Тарифы доставки (Город=сумма)",
        }
        help_texts = {
            "high_season_start": "Используются только день и месяц. Если не задано, сезон = высокий.",
            "high_season_end": "Используются только день и месяц. Если не задано, сезон = высокий.",
            "night_fee_slots_text": "Например: 20:00-23:59=1700",
            "delivery_fees_text": "Одна строка — один город. Можно указывать только изменения.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("high_season_start", "high_season_end"):
            if name in self.fields:
                _configure_date_field(self.fields[name])
                widget = self.fields[name].widget
                widget.input_type = "date"
                widget.format = "%Y-%m-%d"

        for name in (
            "car_wash_default",
            "night_fee_default",
            "child_seat_daily",
            "child_seat_cap",
            "booster_daily",
            "booster_cap",
            "ski_rack_daily",
            "autobox_daily",
            "crossbars_daily",
        ):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("min", "0")
                self.fields[name].widget.attrs.setdefault("step", "1")

        for name in ("night_fee_slots_text", "delivery_fees_text"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("rows", 5)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("high_season_start")
        end = cleaned.get("high_season_end")
        if bool(start) ^ bool(end):
            self.add_error("high_season_start", "Нужно заполнить обе даты сезона.")
            self.add_error("high_season_end", "Нужно заполнить обе даты сезона.")

        night_text = cleaned.get("night_fee_slots_text") or ""
        delivery_text = cleaned.get("delivery_fees_text") or ""

        if night_text.strip():
            try:
                parse_night_slots(night_text)
            except ValueError as exc:
                self.add_error("night_fee_slots_text", str(exc))

        if delivery_text.strip():
            try:
                parse_delivery_overrides(delivery_text)
            except ValueError as exc:
                self.add_error("delivery_fees_text", str(exc))

        return cleaned


User = get_user_model()


class AdminUserCreationForm(UserCreationForm):
    make_superuser = forms.BooleanField(
        required=False,
        label="Сделать суперпользователем",
        help_text="Даст полный доступ ко всем данным и настройкам.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields)
        self.fields["username"].label = "Логин"
        self.fields["username"].help_text = "Только латиница, цифры и @/./+/-/_."
        if "email" in self.fields:
            self.fields["email"].label = "Эл. почта"
            self.fields["email"].required = False
            self.fields["email"].help_text = "Необязательно, но пригодится для восстановления."
        self.fields["password1"].label = "Пароль"
        self.fields["password1"].help_text = "Минимум 8 символов, лучше длиннее."
        self.fields["password2"].label = "Подтверждение пароля"
        self.fields["password2"].help_text = "Повторите пароль для проверки."
        self.order_fields(["username", "email", "password1", "password2", "make_superuser"])

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
        user.is_superuser = bool(self.cleaned_data.get("make_superuser"))
        if commit:
            user.save()
        return user


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields)
        self.fields["new_password1"].label = "Новый пароль"
        self.fields["new_password1"].help_text = "Минимум 8 символов, лучше длиннее."
        self.fields["new_password2"].label = "Подтверждение пароля"
        self.fields["new_password2"].help_text = "Повторите пароль для проверки."
        self.order_fields(["new_password1", "new_password2"])


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields)
        self.fields["old_password"].label = "Текущий пароль"
        self.fields["new_password1"].label = "Новый пароль"
        self.fields["new_password1"].help_text = "Минимум 8 символов, лучше длиннее."
        self.fields["new_password2"].label = "Подтверждение пароля"
        self.fields["new_password2"].help_text = "Повторите пароль для проверки."
        self.order_fields(["old_password", "new_password1", "new_password2"])

        for field in self.fields.values():
            widget = field.widget
            css = widget.attrs.get("class", "")
            base_class = "form-control"
            if isinstance(widget, forms.CheckboxInput):
                base_class = "form-check-input"
            widget.attrs["class"] = f"{base_class} {css}".strip()

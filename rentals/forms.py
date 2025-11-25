import re
from datetime import date, timedelta

from django import forms

from .models import Car, Customer, Rental, ContractTemplate, CustomerTag
from .services.pricing import calculate_rental_pricing


class StyledModelForm(forms.ModelForm):
    """Apply basic Bootstrap classes to all widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = f"form-check-input {css}".strip()
            else:
                widget.attrs["class"] = f"form-control {css}".strip()
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)


class CarForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "sts_issue_date" in self.fields:
            widget = self.fields["sts_issue_date"].widget
            widget.input_type = "date"
            widget.attrs.setdefault("placeholder", "YYYY-MM-DD")

    class Meta:
        model = Car
        fields = [
            "plate_number",
            "vin",
            "make",
            "model",
            "year",
            "sts_number",
            "sts_issue_date",
            "sts_issued_by",
            "rate_1_4_high",
            "rate_5_14_high",
            "rate_15_plus_high",
            "rate_1_4_low",
            "rate_5_14_low",
            "rate_15_plus_low",
            "daily_rate",
            "is_active",
        ]
        labels = {
            "daily_rate": "Fallback daily rate",
            "vin": "VIN",
            "sts_number": "СТС номер",
            "sts_issue_date": "СТС выдана",
            "sts_issued_by": "Кем выдана СТС",
            "rate_1_4_high": "1-4 days (вс)",
            "rate_5_14_high": "5-14 days (вс)",
            "rate_15_plus_high": "15+ days (вс)",
            "rate_1_4_low": "1-4 days (нс)",
            "rate_5_14_low": "5-14 days (нс)",
            "rate_15_plus_low": "15+ days (нс)",
        }
        help_texts = {
            "daily_rate": "Used if a tiered rate is missing.",
            "vin": "17 символов, можно оставить пустым.",
            "sts_number": "Номер свидетельства о регистрации (СТС).",
            "sts_issue_date": "Дата выдачи СТС.",
            "sts_issued_by": "Кем выдано свидетельство.",
            "rate_1_4_high": "Высокий сезон (вс) за сутки при аренде 1-4 дней.",
            "rate_5_14_high": "Высокий сезон (вс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_high": "Высокий сезон (вс) за сутки при аренде 15+ дней.",
            "rate_1_4_low": "Низкий сезон (нс) за сутки при аренде 1-4 дней.",
            "rate_5_14_low": "Низкий сезон (нс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_low": "Низкий сезон (нс) за сутки при аренде 15+ дней.",
        }


class CustomerForm(StyledModelForm):
    tags_text = forms.CharField(
        required=False,
        label="Tags",
        help_text="Через запятую: VIP, корпоративный, проблемный. Можно добавлять новые.",
    )

    class Meta:
        model = Customer
        fields = [
            "full_name",
            "email",
            "phone",
            "license_number",
            "passport_series",
            "passport_number",
            "passport_issued_by",
            "passport_issue_date",
            "address",
            "registration_address",
            "residence_address",
            "notes",
        ]
        labels = {
            "full_name": "Full name",
            "license_number": "License number",
            "address": "Address",
            "passport_series": "Passport series",
            "passport_number": "Passport number",
            "passport_issued_by": "Passport issued by",
            "passport_issue_date": "Passport issue date",
            "registration_address": "Registration address (прописка)",
            "residence_address": "Residence address",
            "notes": "Notes",
        }
        help_texts = {
            "address": "Основной/почтовый адрес клиента.",
            "passport_series": "Серия паспорта (4 цифры).",
            "passport_number": "Номер паспорта (6 цифр) или иной документ.",
            "passport_issue_date": "Дата выдачи документа.",
            "registration_address": "Адрес регистрации (прописка).",
            "residence_address": "Фактический адрес проживания.",
        }

    field_order = [
        "full_name",
        "email",
        "phone",
        "license_number",
        "passport_series",
        "passport_number",
        "passport_issued_by",
        "passport_issue_date",
        "address",
        "registration_address",
        "residence_address",
        "tags_text",
        "notes",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "passport_issue_date" in self.fields:
            widget = self.fields["passport_issue_date"].widget
            widget.input_type = "date"
            widget.attrs.setdefault("placeholder", "YYYY-MM-DD")
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_customer_queryset()
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

        for name in ("start_date", "end_date"):
            widget = self.fields[name].widget
            widget.input_type = "date"
            widget.attrs.setdefault("placeholder", "YYYY-MM-DD")

        for name in ("daily_rate", "total_price"):
            self.fields[name].widget.attrs["readonly"] = True
            self.fields[name].widget.attrs["tabindex"] = "-1"
            self.fields[name].widget.attrs["aria-readonly"] = "true"
        self.initial_customer_label = getattr(self, "_customer_label", "")

    class Meta:
        model = Rental
        fields = [
            "contract_number",
            "car",
            "customer",
            "start_date",
            "end_date",
            "daily_rate",
            "total_price",
            "status",
        ]
        labels = {
            "contract_number": "Contract number",
        }

    def clean(self):
        cleaned_data = super().clean()

        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        car = cleaned_data.get("car")

        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after start date.")
            return cleaned_data

        recalc_needed = not self.instance.pk or any(
            field in self.changed_data for field in ("car", "start_date", "end_date")
        )
        if recalc_needed:
            _, daily_rate, total_price = calculate_rental_pricing(car, start_date, end_date)
            cleaned_data["daily_rate"] = daily_rate
            cleaned_data["total_price"] = total_price
        else:
            cleaned_data["daily_rate"] = self.instance.daily_rate
            cleaned_data["total_price"] = self.instance.total_price
        return cleaned_data

    def _limit_customer_queryset(self):
        """
        Keep the customer queryset tiny so rendering the form does not pull hundreds
        of thousands of rows. Only include the selected customer (if any).
        """

        customer_field = self.fields["customer"]
        customer_field.widget = forms.HiddenInput()

        selected_id = None
        if self.is_bound:
            selected_id = self.data.get(self.add_prefix("customer")) or self.data.get("customer")
        elif self.initial.get("customer"):
            selected_id = self.initial.get("customer")
        elif getattr(self.instance, "customer_id", None):
            selected_id = self.instance.customer_id

        queryset = Customer.objects.none()
        label = ""

        if selected_id:
            queryset = Customer.objects.filter(pk=selected_id)
            customer = queryset.first()
            if customer:
                label = f"{customer.full_name} · {customer.phone}"
                customer_field.initial = customer.pk

        customer_field.queryset = queryset
        self._customer_label = label


class ContractTemplateForm(StyledModelForm):
    class Meta:
        model = ContractTemplate
        fields = "__all__"

from datetime import date, timedelta

from django import forms

from .models import Car, Customer, Rental, ContractTemplate
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
    class Meta:
        model = Car
        fields = [
            "plate_number",
            "make",
            "model",
            "year",
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
            "rate_1_4_high": "1-4 days (вс)",
            "rate_5_14_high": "5-14 days (вс)",
            "rate_15_plus_high": "15+ days (вс)",
            "rate_1_4_low": "1-4 days (нс)",
            "rate_5_14_low": "5-14 days (нс)",
            "rate_15_plus_low": "15+ days (нс)",
        }
        help_texts = {
            "daily_rate": "Used if a tiered rate is missing.",
            "rate_1_4_high": "Высокий сезон (вс) за сутки при аренде 1-4 дней.",
            "rate_5_14_high": "Высокий сезон (вс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_high": "Высокий сезон (вс) за сутки при аренде 15+ дней.",
            "rate_1_4_low": "Низкий сезон (нс) за сутки при аренде 1-4 дней.",
            "rate_5_14_low": "Низкий сезон (нс) за сутки при аренде 5-14 дней.",
            "rate_15_plus_low": "Низкий сезон (нс) за сутки при аренде 15+ дней.",
        }


class CustomerForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = "__all__"


class RentalForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    class Meta:
        model = Rental
        fields = [
            "car",
            "customer",
            "start_date",
            "end_date",
            "daily_rate",
            "total_price",
            "status",
        ]

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


class ContractTemplateForm(StyledModelForm):
    class Meta:
        model = ContractTemplate
        fields = "__all__"

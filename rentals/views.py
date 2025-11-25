import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    import xlrd
except ImportError:  # pragma: no cover - dependency installed via requirements
    xlrd = None

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.encoding import smart_str
from django.views.generic import CreateView, ListView, UpdateView

from .forms import CarForm, ContractTemplateForm, CustomerForm, RentalForm
from .models import Car, ContractTemplate, Customer, Rental
from .services.contract_renderer import render_docx, render_html_template
from .services.pricing import calculate_rental_pricing
from .services.stats import (
    car_utilization,
    monthly_rental_performance,
    rental_status_breakdown,
    rentals_summary,
)

PHONE_MAX_LEN = Customer._meta.get_field("phone").max_length
LICENSE_MAX_LEN = Customer._meta.get_field("license_number").max_length
NAME_MAX_LEN = Customer._meta.get_field("full_name").max_length
EMAIL_MAX_LEN = Customer._meta.get_field("email").max_length


def _parse_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def _parse_decimal(value):
    try:
        text = str(value).strip().replace(",", ".")
        return Decimal(text)
    except (InvalidOperation, AttributeError, TypeError):
        return Decimal("0")


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _clean_status(value):
    value = (value or "").lower()
    valid_statuses = {choice[0] for choice in Rental.STATUS_CHOICES}
    return value if value in valid_statuses else "draft"


def _pick_value(row, keys):
    """Return the first non-empty value for any matching key in the row."""
    for key in keys:
        if key in row:
            value = row[key]
            if isinstance(value, str):
                value = value.strip()
            if value not in ("", None):
                return value
    return None


def _clean_text_value(value):
    """
    Convert CSV/Excel cell values into cleaned strings.

    Treat ".", "-" and empty cells as missing.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return "" if text in {"", ".", "-"} else text


def _limit_length(value: str | None, max_len: int):
    if value is None:
        return None
    return str(value)[:max_len]


def _clean_phone_value(value):
    """
    Normalize phone numbers:
    - split on comma/semicolon/slash/newline, take first non-empty
    - keep digits and leading '+'
    - truncate to DB max length
    """
    raw = _clean_text_value(value)
    if not raw:
        return ""

    parts = re.split(r"[;,/\n\r]+", raw)
    for part in parts:
        cleaned = re.sub(r"[^0-9+]", "", part)
        if cleaned:
            if cleaned[0] != "+" and part.strip().startswith("+"):
                cleaned = "+" + cleaned
            return _limit_length(cleaned, PHONE_MAX_LEN)

    return _limit_length(raw, PHONE_MAX_LEN)


def _serialize_car_pricing(car: Car):
    """Prepare car pricing info for the rental form JS helper."""

    def _num(value):
        return float(value) if value is not None else 0

    return {
        "id": car.id,
        "label": str(car),
        "plate_number": car.plate_number,
        "daily_rate": _num(car.daily_rate),
        "rate_1_4_high": _num(car.rate_1_4_high),
        "rate_5_14_high": _num(car.rate_5_14_high),
        "rate_15_plus_high": _num(car.rate_15_plus_high),
        "rate_1_4_low": _num(car.rate_1_4_low),
        "rate_5_14_low": _num(car.rate_5_14_low),
        "rate_15_plus_low": _num(car.rate_15_plus_low),
    }


def _read_csv_rows(upload):
    decoded = upload.read().decode("utf-8-sig").splitlines()
    return list(csv.DictReader(decoded))


def _read_excel_rows(upload):
    if xlrd is None:
        raise ImportError("xlrd is required to read .xls files.")

    book = xlrd.open_workbook(file_contents=upload.read())
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return []

    headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
    rows = []
    for row_idx in range(1, sheet.nrows):
        data = {}
        for col_idx, header in enumerate(headers):
            value = sheet.cell_value(row_idx, col_idx) if col_idx < sheet.ncols else ""
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            data[header] = value
        rows.append(data)
    return rows


def _load_rows(upload):
    filename = (upload.name or "").lower()
    if filename.endswith((".xls", ".xlsx")):
        return _read_excel_rows(upload)
    return _read_csv_rows(upload)


def _normalize_car_row(row):
    """
    Normalize a raw row (CSV or XLS) into car fields we support.
    Designed to work with the provided Russian-language XLS export.
    """
    plate = _pick_value(
        row,
        [
            "plate_number",
            "Plate number",
            "регистрационный знак",
            "Регистрационный знак",
            "гос.номера",
            "Гос.номера",
        ],
    )
    if plate:
        plate = str(plate).strip().replace(" ", "").upper()

    make = _pick_value(row, ["make", "Марка", "марка"])
    model = _pick_value(row, ["model", "Модель", "модель"])
    name_field = _pick_value(row, ["Название", "название"])

    if make and not model:
        parts = str(make).strip().split(" ", 1)
        if len(parts) == 2:
            make, model = parts

    if not make and name_field:
        text = str(name_field).strip()
        parts = text.split(" ", 1)
        make = parts[0]
        model = parts[1] if len(parts) == 2 else ""
    elif name_field and not model:
        text = str(name_field).strip()
        parts = text.split(" ", 1)
        if len(parts) == 2:
            model = parts[1]

    year_val = _pick_value(row, ["year", "Year", "год выпуска", "Год выпуска"])
    try:
        year = int(float(year_val))
    except (TypeError, ValueError):
        year = None

    rate_1_4_high = _pick_value(row, ["rate_1_4_high", "1-4 дней(вс)", "1-4 дней (вс)"])
    rate_5_14_high = _pick_value(row, ["rate_5_14_high", "5-14 дней(вс)", "5-14 дней (вс)"])
    rate_15_high = _pick_value(
        row, ["rate_15_plus_high", "15 дней и более(вс)", "15 дней и более (вс)"]
    )

    rate_1_4_low = _pick_value(row, ["rate_1_4_low", "1-4 дней(нс)", "1-4 дней (нс)"])
    rate_5_14_low = _pick_value(row, ["rate_5_14_low", "5-14 дней(нс)", "5-14 дней (нс)"])
    rate_15_low = _pick_value(
        row, ["rate_15_plus_low", "15 дней и более(нс)", "15 дней и более (нс)"]
    )

    rate_raw = _pick_value(row, ["daily_rate", "Daily rate"])
    active_raw = _pick_value(row, ["is_active", "active", "активен", "активный"])
    is_active = _parse_bool(active_raw) if active_raw not in (None, "") else True

    daily_rate = _parse_decimal(rate_raw) if rate_raw not in (None, "") else None
    rate_1_4_high = _parse_decimal(rate_1_4_high) if rate_1_4_high not in (None, "") else None
    rate_5_14_high = _parse_decimal(rate_5_14_high) if rate_5_14_high not in (None, "") else None
    rate_15_high = _parse_decimal(rate_15_high) if rate_15_high not in (None, "") else None
    rate_1_4_low = _parse_decimal(rate_1_4_low) if rate_1_4_low not in (None, "") else None
    rate_5_14_low = _parse_decimal(rate_5_14_low) if rate_5_14_low not in (None, "") else None
    rate_15_low = _parse_decimal(rate_15_low) if rate_15_low not in (None, "") else None

    def _first_rate(*values):
        for value in values:
            if value not in (None, Decimal("0")):
                return value
        return None

    base_rate = _first_rate(
        daily_rate,
        rate_1_4_high,
        rate_1_4_low,
        rate_5_14_high,
        rate_5_14_low,
        rate_15_high,
        rate_15_low,
    )

    return {
        "plate_number": plate or "",
        "make": str(make).strip() if make else "",
        "model": str(model).strip() if model else "",
        "year": year,
        "daily_rate": base_rate,
        "rate_1_4_high": rate_1_4_high,
        "rate_5_14_high": rate_5_14_high,
        "rate_15_high": rate_15_high,
        "rate_1_4_low": rate_1_4_low,
        "rate_5_14_low": rate_5_14_low,
        "rate_15_low": rate_15_low,
        "is_active": is_active,
    }


def _normalize_customer_row(row, row_index: int):
    """Normalize AmoCRM CSV/XLSX export rows into Customer fields."""

    def pick(keys):
        for key in keys:
            if key in row:
                cleaned = _clean_text_value(row[key])
                if cleaned:
                    return cleaned
        return ""

    crm_id = pick(["ID", "Id", "id"])

    full_name = pick(["full_name", "Full name", "fullname", "ФИО", "fio", "Наименование"])
    first_name = pick(["Имя", "First name", "first_name"])
    last_name = pick(["Фамилия", "Last name", "last_name"])

    if not full_name:
        full_name = " ".join(part for part in (first_name, last_name) if part)
    if not full_name and crm_id:
        full_name = f"Без имени {crm_id}"
    if not full_name:
        full_name = f"Без имени #{row_index}"
    full_name = _limit_length(full_name, NAME_MAX_LEN)

    raw_phone = pick(
        [
            "phone",
            "Phone",
            "Телефон",
            "Телефон (контакт)",
            "Мобильный телефон",
            "Рабочий телефон",
            "Рабочий прямой телефон",
            "Домашний телефон",
            "Другой телефон",
        ]
    )
    cleaned_phone = _clean_phone_value(raw_phone)
    phone = cleaned_phone or _limit_length(f"Нет телефона ({crm_id or row_index})", PHONE_MAX_LEN)

    license_candidate = pick(
        [
            "license_number",
            "License number",
            "Водит. удостоверение. (контакт)",
            "Паспорт (контакт)",
            "Контракт (контакт)",
        ]
    )
    license_number = (
        license_candidate
        or (crm_id and f"AMO-{crm_id}")
        or (cleaned_phone and f"PHONE-{cleaned_phone}")
        or f"AUTO-{row_index}"
    )
    license_number = _limit_length(license_number, LICENSE_MAX_LEN)

    email = _limit_length(
        pick(["email", "Email", "Рабочий email", "Личный email", "Другой email"]), EMAIL_MAX_LEN
    )
    address = pick(["Адрес (контакт)", "Адрес (компания)", "address", "Address"])

    return {
        "full_name": full_name,
        "email": email or None,
        "phone": phone,
        "license_number": license_number,
        "address": address or None,
    }


@login_required
def dashboard(request):
    summary = rentals_summary()
    utilization = car_utilization()[:5]
    monthly_trend = monthly_rental_performance()
    status_counts = rental_status_breakdown()
    status_order = [code for code, _ in Rental.STATUS_CHOICES]
    status_labels = dict(Rental.STATUS_CHOICES)

    chart_payload = {
        "trend": {
            "labels": [item["label"] for item in monthly_trend],
            "revenue": [float(item["revenue"] or 0) for item in monthly_trend],
            "counts": [item["count"] for item in monthly_trend],
        },
        "status": {
            "labels": [status_labels[code] for code in status_order],
            "counts": [status_counts.get(code, 0) for code in status_order],
        },
        "topCars": {
            "labels": [
                f"{car['car__plate_number']} | {car['car__make']} {car['car__model']}".strip()
                for car in utilization
            ],
            "revenue": [float(car.get("revenue") or 0) for car in utilization],
            "counts": [car.get("num_rentals", 0) for car in utilization],
        },
    }

    context = {
        "cars_count": Car.objects.count(),
        "customers_count": Customer.objects.count(),
        "active_rentals": summary["active_rentals"],
        "total_revenue": summary["total_revenue"],
        "total_rentals": summary["total_rentals"],
        "completed_rentals": summary["completed_rentals"],
        "top_cars": utilization,
        "chart_payload": chart_payload,
    }
    return render(request, "rentals/dashboard.html", context)


@method_decorator(login_required, name="dispatch")
class CarListView(ListView):
    model = Car
    template_name = "rentals/car_list.html"


@method_decorator(login_required, name="dispatch")
class CarCreateView(CreateView):
    model = Car
    form_class = CarForm
    template_name = "rentals/car_form.html"
    success_url = reverse_lazy("rentals:car_list")


@method_decorator(login_required, name="dispatch")
class CarUpdateView(UpdateView):
    model = Car
    form_class = CarForm
    template_name = "rentals/car_form.html"
    success_url = reverse_lazy("rentals:car_list")


@method_decorator(login_required, name="dispatch")
class CustomerListView(ListView):
    model = Customer
    template_name = "rentals/customer_list.html"
    ordering = ["id"]
    paginate_by = 25
    page_size_options = (25, 50)

    def get_paginate_by(self, queryset):
        if hasattr(self, "_page_size"):
            return self._page_size

        raw_size = self.request.GET.get("page_size")
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            size = None

        self._page_size = size if size in self.page_size_options else self.paginate_by
        return self._page_size

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_size_options"] = self.page_size_options
        context["current_page_size"] = getattr(self, "_page_size", self.paginate_by)
        return context


@method_decorator(login_required, name="dispatch")
class CustomerCreateView(CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = "rentals/customer_form.html"
    success_url = reverse_lazy("rentals:customer_list")


@method_decorator(login_required, name="dispatch")
class CustomerUpdateView(UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = "rentals/customer_form.html"
    success_url = reverse_lazy("rentals:customer_list")


@method_decorator(login_required, name="dispatch")
class RentalListView(ListView):
    model = Rental
    template_name = "rentals/rental_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["contract_templates"] = ContractTemplate.objects.all()
        return context


@method_decorator(login_required, name="dispatch")
class RentalCreateView(CreateView):
    model = Rental
    form_class = RentalForm
    template_name = "rentals/rental_form.html"
    success_url = reverse_lazy("rentals:rental_list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["car_pricing"] = [_serialize_car_pricing(car) for car in Car.objects.all()]
        return context


@method_decorator(login_required, name="dispatch")
class RentalUpdateView(UpdateView):
    model = Rental
    form_class = RentalForm
    template_name = "rentals/rental_form.html"
    success_url = reverse_lazy("rentals:rental_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["car_pricing"] = [_serialize_car_pricing(car) for car in Car.objects.all()]
        return context


@method_decorator(login_required, name="dispatch")
class ContractTemplateListView(ListView):
    model = ContractTemplate
    template_name = "rentals/contract_template_list.html"


@method_decorator(login_required, name="dispatch")
class ContractTemplateCreateView(CreateView):
    model = ContractTemplate
    form_class = ContractTemplateForm
    template_name = "rentals/contract_template_form.html"
    success_url = reverse_lazy("rentals:contract_template_list")


@method_decorator(login_required, name="dispatch")
class ContractTemplateUpdateView(UpdateView):
    model = ContractTemplate
    form_class = ContractTemplateForm
    template_name = "rentals/contract_template_form.html"
    success_url = reverse_lazy("rentals:contract_template_list")


@login_required
def export_cars_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cars.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "plate_number",
            "make",
            "model",
            "year",
            "daily_rate",
            "rate_1_4_high",
            "rate_5_14_high",
            "rate_15_plus_high",
            "rate_1_4_low",
            "rate_5_14_low",
            "rate_15_plus_low",
            "is_active",
        ]
    )

    for car in Car.objects.all():
        writer.writerow(
            [
                smart_str(car.plate_number),
                smart_str(car.make),
                smart_str(car.model),
                car.year,
                car.daily_rate,
                car.rate_1_4_high,
                car.rate_5_14_high,
                car.rate_15_plus_high,
                car.rate_1_4_low,
                car.rate_5_14_low,
                car.rate_15_plus_low,
                car.is_active,
            ]
        )

    return response


@login_required
def export_customers_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="customers.csv"'

    writer = csv.writer(response)
    writer.writerow(["full_name", "email", "phone", "license_number", "address"])

    for customer in Customer.objects.all():
        writer.writerow(
            [
                smart_str(customer.full_name),
                smart_str(customer.email),
                smart_str(customer.phone),
                smart_str(customer.license_number),
                smart_str(customer.address),
            ]
        )

    return response


@login_required
def export_rentals_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="rentals.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "car_plate_number",
            "customer_license_number",
            "customer_name",
            "start_date",
            "end_date",
            "daily_rate",
            "total_price",
            "status",
        ]
    )

    for rental in Rental.objects.select_related("car", "customer"):
        writer.writerow(
            [
                smart_str(rental.car.plate_number),
                smart_str(rental.customer.license_number),
                smart_str(rental.customer.full_name),
                rental.start_date.isoformat(),
                rental.end_date.isoformat(),
                rental.daily_rate,
                rental.total_price,
                rental.status,
            ]
        )

    return response


@login_required
def import_cars_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please choose a CSV or XLS file to upload.")
        else:
            try:
                rows = _load_rows(upload)
            except Exception as exc:  # noqa: BLE001 - present message to user
                messages.error(request, f"Could not read file: {exc}")
                return redirect("rentals:import_cars_csv")

            if not rows:
                messages.warning(request, "File is empty or missing rows.")
                return redirect("rentals:import_cars_csv")

            imported, skipped = 0, 0

            for row in rows:
                normalized = _normalize_car_row(row)

                plate = normalized["plate_number"]
                make = normalized["make"]
                model = normalized["model"]
                year = normalized["year"]
                daily_rate = normalized["daily_rate"]
                rate_1_4_high = normalized["rate_1_4_high"]
                rate_5_14_high = normalized["rate_5_14_high"]
                rate_15_high = normalized["rate_15_high"]
                rate_1_4_low = normalized["rate_1_4_low"]
                rate_5_14_low = normalized["rate_5_14_low"]
                rate_15_low = normalized["rate_15_low"]

                has_rate = any(
                    rate not in (None, Decimal("0"))
                    for rate in (
                        daily_rate,
                        rate_1_4_high,
                        rate_5_14_high,
                        rate_15_high,
                        rate_1_4_low,
                        rate_5_14_low,
                        rate_15_low,
                    )
                )

                if not plate or not make or not model or not year or not has_rate:
                    skipped += 1
                    continue

                base_daily_rate = daily_rate or rate_1_4_high or rate_1_4_low or Decimal("0")

                Car.objects.update_or_create(
                    plate_number=plate,
                    defaults={
                        "make": make,
                        "model": model,
                        "year": year,
                        "daily_rate": base_daily_rate,
                        "rate_1_4_high": rate_1_4_high or Decimal("0"),
                        "rate_5_14_high": rate_5_14_high or Decimal("0"),
                        "rate_15_plus_high": rate_15_high or Decimal("0"),
                        "rate_1_4_low": rate_1_4_low or Decimal("0"),
                        "rate_5_14_low": rate_5_14_low or Decimal("0"),
                        "rate_15_plus_low": rate_15_low or Decimal("0"),
                        "is_active": normalized["is_active"],
                    },
                )
                imported += 1

            if imported:
                messages.success(request, f"Imported {imported} cars.")
            if skipped:
                messages.warning(request, f"Skipped {skipped} rows due to missing or invalid data.")

            return redirect("rentals:car_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Import cars",
            "expected_headers": [
                "plate_number",
                "make",
                "model",
                "year",
                "daily_rate",
                "rate_1_4_high",
                "rate_5_14_high",
                "rate_15_plus_high",
                "rate_1_4_low",
                "rate_5_14_low",
                "rate_15_plus_low",
                "is_active",
            ],
            "xls_headers": [
                "Регистрационный знак",
                "Марка",
                "Год выпуска",
                "1-4 дней(вс)",
                "5-14 дней(вс)",
                "15 дней и более(вс)",
                "1-4 дней(нс)",
                "5-14 дней(нс)",
                "15 дней и более(нс)",
            ],
            "help_text": "Upload CSV or Excel (.xls). The Russian XLS template is supported, and tiered prices for высокий/низкий сезон will be imported. Existing plate numbers will be updated.",
            "back_url": reverse("rentals:car_list"),
        },
    )


@login_required
def import_customers_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please choose a CSV or Excel file to upload.")
        else:
            try:
                rows = _load_rows(upload)
            except Exception as exc:  # noqa: BLE001 - show error to user
                messages.error(request, f"Could not read file: {exc}")
                return redirect("rentals:import_customers_csv")

            if not rows:
                messages.warning(request, "File is empty or missing rows.")
                return redirect("rentals:import_customers_csv")

            created_count, updated_count, skipped_empty = 0, 0, 0
            normalized_rows = []
            for idx, row in enumerate(rows, start=1):
                if not any(_clean_text_value(value) for value in row.values()):
                    skipped_empty += 1
                    continue

                normalized = _normalize_customer_row(row, idx)
                normalized_rows.append(normalized)

            if not normalized_rows:
                messages.warning(request, "No valid rows found in file.")
                return redirect("rentals:import_customers_csv")

            # Deduplicate by license number inside the upload.
            by_license = {}
            duplicate_rows = 0
            for item in normalized_rows:
                key = item["license_number"]
                if key in by_license:
                    duplicate_rows += 1
                by_license[key] = item

            licenses = list(by_license.keys())
            existing = {
                c.license_number: c
                for c in Customer.objects.filter(license_number__in=licenses)
            }

            to_create = []
            to_update = []
            for license_number, data in by_license.items():
                if license_number in existing:
                    customer = existing[license_number]
                    changed = False
                    for field in ("full_name", "email", "phone", "address"):
                        new_value = data[field]
                        if getattr(customer, field) != new_value:
                            setattr(customer, field, new_value)
                            changed = True
                    if changed:
                        to_update.append(customer)
                else:
                    to_create.append(Customer(**data))

            if to_create:
                Customer.objects.bulk_create(to_create, batch_size=500)
                created_count = len(to_create)
            if to_update:
                Customer.objects.bulk_update(
                    to_update, ["full_name", "email", "phone", "address"], batch_size=500
                )
                updated_count = len(to_update)

            imported = created_count + updated_count

            if imported:
                messages.success(
                    request,
                    f"Imported {imported} customers "
                    f"({created_count} created, {updated_count} updated). Missing fields were auto-filled.",
                )
            if skipped_empty:
                messages.info(
                    request,
                    f"Skipped {skipped_empty} completely empty rows.",
                )
            if duplicate_rows:
                messages.info(
                    request,
                    f"Deduplicated {duplicate_rows} rows with the same license number inside the file.",
                )

            return redirect("rentals:customer_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Import customers",
            "expected_headers": [
                "full_name / Наименование",
                "Имя",
                "Фамилия",
                "phone / Телефон (контакт) / Мобильный телефон / Рабочий телефон",
                "license_number / Водит. удостоверение. (контакт)",
                "email (рабочий/личный/другой)",
                "Адрес (контакт) / Адрес (компания)",
                "ID (используется как резервный идентификатор)",
            ],
            "help_text": "Upload CSV or Excel (.xls, .xlsx). Rows will be matched by license number, otherwise AmoCRM ID/phone. Missing fields are filled automatically instead of skipping rows.",
            "back_url": reverse("rentals:customer_list"),
        },
    )


@login_required
def import_rentals_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please choose a CSV file to upload.")
        else:
            decoded = upload.read().decode("utf-8-sig").splitlines()
            reader = csv.DictReader(decoded)
            imported, missing_relations, skipped = 0, 0, 0

            for row in reader:
                plate = (row.get("car_plate_number") or row.get("plate_number") or "").strip()
                license_number = (
                    row.get("customer_license_number") or row.get("license_number") or ""
                ).strip()
                start_date = _parse_date(row.get("start_date"))
                end_date = _parse_date(row.get("end_date"))

                if not all([plate, license_number, start_date, end_date]):
                    skipped += 1
                    continue

                try:
                    car = Car.objects.get(plate_number=plate)
                except Car.DoesNotExist:
                    missing_relations += 1
                    continue

                try:
                    customer = Customer.objects.get(license_number=license_number)
                except Customer.DoesNotExist:
                    missing_relations += 1
                    continue

                rental_days, computed_rate, computed_total = calculate_rental_pricing(car, start_date, end_date)
                if rental_days <= 0:
                    skipped += 1
                    continue

                daily_rate_raw = row.get("daily_rate")
                daily_rate = (
                    _parse_decimal(daily_rate_raw) if daily_rate_raw not in (None, "") else computed_rate
                )

                total_price_value = row.get("total_price")
                total_price = (
                    _parse_decimal(total_price_value)
                    if total_price_value not in (None, "")
                    else daily_rate * Decimal(rental_days)
                )

                Rental.objects.update_or_create(
                    car=car,
                    customer=customer,
                    start_date=start_date,
                    end_date=end_date,
                    defaults={
                        "daily_rate": daily_rate,
                        "total_price": total_price,
                        "status": _clean_status(row.get("status")),
                    },
                )
                imported += 1

            if imported:
                messages.success(request, f"Imported {imported} rentals.")
            if missing_relations:
                messages.warning(
                    request,
                    f"Skipped {missing_relations} rows because the related car or customer was not found.",
                )
            if skipped:
                messages.warning(request, f"Skipped {skipped} rows due to missing or invalid data.")

            return redirect("rentals:rental_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Import rentals",
            "expected_headers": [
                "car_plate_number",
                "customer_license_number",
                "start_date",
                "end_date",
                "daily_rate",
                "total_price",
                "status",
            ],
            "help_text": "Cars and customers must exist before importing rentals.",
            "back_url": reverse("rentals:rental_list"),
        },
    )


@login_required
def generate_contract(request, rental_id, template_id):
    rental = get_object_or_404(Rental, pk=rental_id)
    ct = get_object_or_404(ContractTemplate, pk=template_id)

    if ct.format == "html":
        html = render_html_template(ct, rental)
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        return response

    elif ct.format == "docx":
        file_io = render_docx(ct, rental)
        response = HttpResponse(
            file_io.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="contract_{rental.id}.docx"'
        return response

    else:
        return HttpResponse("Unknown template format", status=400)

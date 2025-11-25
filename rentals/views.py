import csv
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    import xlrd
except ImportError:  # pragma: no cover - dependency installed via requirements
    xlrd = None

try:
    import openpyxl
except ImportError:  # pragma: no cover - optional dependency for .xlsx
    openpyxl = None

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Replace, Upper
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.encoding import smart_str
from django.core.exceptions import PermissionDenied
from django.views.generic import CreateView, ListView, UpdateView
from django.views.decorators.http import require_POST

from .forms import AdminUserCreationForm, CarForm, ContractTemplateForm, CustomerForm, RentalForm
from .models import Car, ContractTemplate, Customer, CustomerTag, Rental
from .services.contract_renderer import placeholder_guide, render_docx, render_html_template, render_pdf
from .services.pricing import calculate_rental_pricing, pricing_config
from .services.stats import (
    car_utilization,
    monthly_rental_performance,
    rental_status_breakdown,
    rentals_summary,
)

User = get_user_model()

PHONE_MAX_LEN = Customer._meta.get_field("phone").max_length
LICENSE_MAX_LEN = Customer._meta.get_field("license_number").max_length
NAME_MAX_LEN = Customer._meta.get_field("full_name").max_length
EMAIL_MAX_LEN = Customer._meta.get_field("email").max_length
IMPORT_BATCH_SIZE = max(1, int(os.environ.get("IMPORT_BULK_BATCH_SIZE", "5000")))

logger = logging.getLogger(__name__)


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
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
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


def _split_tags(raw: str | None) -> list[str]:
    """Split a raw tag string (comma/semicolon/pipe) into unique tag names."""
    if not raw:
        return []

    tags = []
    for piece in re.split(r"[;,#/|\n\r]+", str(raw)):
        normalized = piece.strip()
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _sync_customer_tags(customers_by_license: dict[str, Customer], tags_by_license: dict[str, list[str] | None]):
    """
    Apply tag lists (by license number) to customer objects efficiently.

    tags_by_license may contain None to skip updates for that row.
    """
    tag_names = set()
    for tags in tags_by_license.values():
        if tags:
            for tag in tags:
                tag_names.add(tag)

    if not tag_names:
        return

    existing_tags = {
        tag.name.lower(): tag for tag in CustomerTag.objects.filter(name__in=tag_names)
    }
    missing = [name for name in tag_names if name.lower() not in existing_tags]
    if missing:
        CustomerTag.objects.bulk_create([CustomerTag(name=name) for name in missing], ignore_conflicts=True)
        existing_tags.update(
            {tag.name.lower(): tag for tag in CustomerTag.objects.filter(name__in=tag_names)}
        )

    for license_number, tags in tags_by_license.items():
        if not tags:
            continue
        customer = customers_by_license.get(license_number)
        if not customer:
            continue
        tag_objs = [existing_tags.get(tag.lower()) for tag in tags]
        customer.tags.set([tag for tag in tag_objs if tag])


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
        "make": car.make,
        "model": car.model,
        "year": car.year,
        "vin": car.vin or "",
        "sts_number": car.sts_number or "",
        "sts_issue_date": car.sts_issue_date.isoformat() if car.sts_issue_date else "",
        "sts_issued_by": car.sts_issued_by or "",
        "edit_url": reverse("rentals:car_update", args=[car.pk]),
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


def _read_xlsx_rows(upload):
    if openpyxl is None:
        raise ImportError("openpyxl is required to read .xlsx files.")

    upload.seek(0)
    wb = openpyxl.load_workbook(upload, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    header = []
    for idx, row in enumerate(ws.iter_rows(values_only=True)):
        if idx == 0:
            header = [str(cell).strip() if cell is not None else "" for cell in row]
            continue
        if not header:
            break
        data = {}
        for col_idx, header_name in enumerate(header):
            value = row[col_idx] if col_idx < len(row) else ""
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            data[header_name] = value
        rows.append(data)
    return rows


def _load_rows(upload):
    filename = (upload.name or "").lower()
    if filename.endswith(".xlsx"):
        return _read_xlsx_rows(upload)
    if filename.endswith(".xls"):
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

    vin = _pick_value(row, ["vin", "VIN", "vin_code", "Vin", "ВИН", "Вин"])
    sts_number = _pick_value(
        row,
        [
            "sts_number",
            "СТС",
            "Свидетельство",
            "СТС номер",
            "номер СТС",
        ],
    )
    sts_issue_date_raw = _pick_value(row, ["sts_issue_date", "дата выдачи стс", "СТС выдано"])
    sts_issued_by = _pick_value(row, ["sts_issued_by", "кем выдано стс", "кем выдано"])

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
        "vin": _clean_text_value(vin),
        "sts_number": _clean_text_value(sts_number),
        "sts_issue_date": _parse_date(sts_issue_date_raw),
        "sts_issued_by": _clean_text_value(sts_issued_by),
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
    passport_series = pick(["passport_series", "Паспорт серия", "Серия паспорта", "серия паспорта"])
    passport_number = pick(["passport_number", "Паспорт номер", "Номер паспорта", "номер паспорта"])
    passport_issued_by = pick(
        ["passport_issued_by", "Кем выдан паспорт", "кем выдан паспорт", "Паспорт кем выдан"]
    )
    passport_issue_date_raw = pick(
        ["passport_issue_date", "Дата выдачи паспорта", "дата выдачи паспорта", "Паспорт выдан"]
    )
    registration_address = pick(
        ["registration_address", "Адрес прописки", "адрес прописки", "Прописка", "прописка"]
    )
    residence_address = pick(
        [
            "residence_address",
            "Адрес проживания",
            "адрес проживания",
            "Фактический адрес",
            "фактический адрес",
        ]
    )
    notes = pick(["notes", "Notes", "заметки", "Заметки"])
    tags_raw = pick(["tags", "Tags", "теги", "Теги"])
    tags = _split_tags(tags_raw) if tags_raw else None
    passport_series = _limit_length(passport_series, 10) or None
    passport_number = _limit_length(passport_number, 20) or None
    passport_issued_by = _limit_length(passport_issued_by, 255) or None

    return {
        "full_name": full_name,
        "email": email or None,
        "phone": phone,
        "license_number": license_number,
        "address": address or None,
        "registration_address": registration_address or None,
        "residence_address": residence_address or None,
        "passport_series": passport_series,
        "passport_number": passport_number,
        "passport_issued_by": passport_issued_by,
        "passport_issue_date": _parse_date(passport_issue_date_raw),
        "notes": notes or None,
        "tags": tags,
    }


@login_required
def admin_user_list(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    admins = User.objects.filter(is_staff=True).order_by("username")
    form = AdminUserCreationForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        new_admin = form.save()
        messages.success(request, f"Администратор {new_admin.get_username()} создан.")
        return redirect("rentals:admin_user_list")

    context = {
        "admins": admins,
        "form": form,
    }
    return render(request, "rentals/admin_list.html", context)


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

    def get_queryset(self):
        queryset = super().get_queryset()
        self.search_query = (self.request.GET.get("q") or "").strip()

        if self.search_query:
            normalized_query = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", self.search_query).upper()
            if normalized_query:
                plate_normalized = Upper(
                    Replace(
                        Replace(Replace(F("plate_number"), Value(" "), Value("")), Value("-"), Value("")),
                        Value("_"),
                        Value(""),
                    )
                )
                queryset = queryset.annotate(plate_normalized=plate_normalized).filter(
                    plate_normalized__icontains=normalized_query
                )
            else:
                queryset = queryset.filter(plate_number__icontains=self.search_query)

        return queryset.order_by("plate_number")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = getattr(self, "search_query", "")
        return context


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


@login_required
@require_POST
def car_delete(request, pk: int):
    car = get_object_or_404(Car, pk=pk)
    try:
        car.delete()
        messages.success(request, f"Deleted car {car.plate_number}.")
    except ProtectedError:
        messages.error(
            request,
            "Cannot delete this car because it is linked to existing rentals.",
        )
    return redirect("rentals:car_list")


@login_required
@require_POST
def car_delete_all(request):
    with_rentals = Car.objects.filter(rental__isnull=False).distinct()
    deletable = Car.objects.exclude(pk__in=with_rentals.values_list("pk", flat=True))
    deletable_count = deletable.count()

    if deletable_count:
        deletable.delete()
        messages.success(request, f"Deleted {deletable_count} cars.")

    locked_count = with_rentals.count()
    if locked_count:
        messages.warning(
            request,
            f"Skipped {locked_count} car(s) that are linked to rentals.",
        )
    elif deletable_count == 0:
        messages.info(request, "No cars to delete.")

    return redirect("rentals:car_list")


@login_required
@require_POST
def customer_delete(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    try:
        customer.delete()
        messages.success(request, f"Deleted customer {customer.full_name}.")
    except ProtectedError:
        messages.error(
            request,
            "Cannot delete this customer because they are linked to existing rentals.",
        )
    return redirect("rentals:customer_list")


@login_required
@require_POST
def customer_delete_all(request):
    with_rentals = Customer.objects.filter(rental__isnull=False).distinct()
    deletable = Customer.objects.exclude(pk__in=with_rentals.values_list("pk", flat=True))
    deletable_count = deletable.count()

    if deletable_count:
        deletable.delete()
        messages.success(request, f"Deleted {deletable_count} customers.")

    locked_count = with_rentals.count()
    if locked_count:
        messages.warning(
            request,
            f"Skipped {locked_count} customer(s) that are linked to rentals.",
        )
    elif deletable_count == 0:
        messages.info(request, "No customers to delete.")

    return redirect("rentals:customer_list")


@method_decorator(login_required, name="dispatch")
class CustomerListView(ListView):
    model = Customer
    template_name = "rentals/customer_list.html"
    ordering = ["full_name", "id"]
    paginate_by = 25
    page_size_options = (25, 50, 100)

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("tags")
        self.search_query = (self.request.GET.get("q") or "").strip()
        raw_tags = self.request.GET.getlist("tag") or self.request.GET.getlist("tags")
        self.active_tags = []
        for tag_id in raw_tags:
            try:
                self.active_tags.append(int(tag_id))
            except (TypeError, ValueError):
                continue

        if self.search_query:
            terms = [term for term in re.split(r"\s+", self.search_query) if term]
            for term in terms:
                queryset = queryset.filter(
                    Q(full_name__icontains=term)
                    | Q(phone__icontains=term)
                    | Q(email__icontains=term)
                    | Q(license_number__icontains=term)
                    | Q(address__icontains=term)
                    | Q(registration_address__icontains=term)
                    | Q(residence_address__icontains=term)
                    | Q(passport_series__icontains=term)
                    | Q(passport_number__icontains=term)
                    | Q(passport_issued_by__icontains=term)
                    | Q(notes__icontains=term)
                    | Q(tags__name__icontains=term)
                )

        if self.active_tags:
            queryset = queryset.filter(tags__id__in=self.active_tags)

        return queryset.distinct().order_by(*self.ordering)

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
        context["search_query"] = getattr(self, "search_query", "")
        context["available_tags"] = CustomerTag.objects.all()
        context["active_tags"] = getattr(self, "active_tags", [])
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        query_params.pop("page_size", None)
        context["querystring"] = f"&{query_params.urlencode()}" if query_params else ""
        context["filters_active"] = bool(self.search_query or self.active_tags)
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

    def get_queryset(self):
        queryset = super().get_queryset().select_related("car", "customer")
        self.search_query = (self.request.GET.get("q") or "").strip()
        self.status_filter = (self.request.GET.get("status") or "").strip()

        if self.status_filter:
            queryset = queryset.filter(status=self.status_filter)

        if self.search_query:
            terms = [term for term in re.split(r"\s+", self.search_query) if term]
            for term in terms:
                date_value = _parse_date(term)
                condition = (
                    Q(contract_number__icontains=term)
                    | Q(customer__full_name__icontains=term)
                    | Q(customer__phone__icontains=term)
                    | Q(customer__email__icontains=term)
                    | Q(customer__license_number__icontains=term)
                    | Q(car__plate_number__icontains=term)
                    | Q(car__make__icontains=term)
                    | Q(car__model__icontains=term)
                    | Q(status__icontains=term)
                )
                if date_value:
                    condition = condition | Q(start_date=date_value) | Q(end_date=date_value)
                queryset = queryset.filter(condition)

        return queryset.order_by("-start_date", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["contract_templates"] = ContractTemplate.objects.all()
        context["search_query"] = getattr(self, "search_query", "")
        context["status_filter"] = getattr(self, "status_filter", "")
        context["filters_active"] = bool(getattr(self, "search_query", "") or getattr(self, "status_filter", ""))
        context["rental_status_choices"] = Rental.STATUS_CHOICES
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
        context["customer_initial_label"] = getattr(context.get("form"), "initial_customer_label", "")
        context["pricing_config"] = pricing_config()
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
        context["customer_initial_label"] = getattr(context.get("form"), "initial_customer_label", "")
        context["pricing_config"] = pricing_config()
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["placeholder_guide"] = placeholder_guide()
        return context


@method_decorator(login_required, name="dispatch")
class ContractTemplateUpdateView(UpdateView):
    model = ContractTemplate
    form_class = ContractTemplateForm
    template_name = "rentals/contract_template_form.html"
    success_url = reverse_lazy("rentals:contract_template_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["placeholder_guide"] = placeholder_guide()
        return context


@login_required
def customer_search(request):
    """
    Lightweight lookup endpoint for customer search. Returns a small JSON payload
    with the matching customers to power the async selector on the rental form.
    """

    term = (request.GET.get("q") or "").strip()
    try:
        limit = int(request.GET.get("limit", 15))
    except (TypeError, ValueError):
        limit = 15

    limit = max(1, min(limit, 50))

    if not term:
        return JsonResponse({"results": []})

    matches = (
        Customer.objects.filter(
            Q(full_name__icontains=term)
            | Q(phone__icontains=term)
            | Q(email__icontains=term)
            | Q(license_number__icontains=term)
            | Q(passport_number__icontains=term)
            | Q(passport_series__icontains=term)
            | Q(tags__name__icontains=term)
            | Q(address__icontains=term)
            | Q(registration_address__icontains=term)
            | Q(residence_address__icontains=term)
        )
        .order_by("full_name")
        .distinct()
        [:limit]
    )

    results = []
    for customer in matches:
        phone = customer.phone or ""
        label = f"{customer.full_name}{f' · {phone}' if phone else ''}"
        results.append(
            {
                "id": customer.id,
                "name": customer.full_name,
                "phone": phone,
                "email": customer.email or "",
                "license_number": customer.license_number,
                "label": label,
            }
        )
    return JsonResponse({"results": results})


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
            "vin",
            "sts_number",
            "sts_issue_date",
            "sts_issued_by",
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
                smart_str(car.vin),
                smart_str(car.sts_number),
                car.sts_issue_date.isoformat() if car.sts_issue_date else "",
                smart_str(car.sts_issued_by),
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
    writer.writerow(
        [
            "full_name",
            "email",
            "phone",
            "license_number",
            "passport_series",
            "passport_number",
            "passport_issue_date",
            "passport_issued_by",
            "address",
            "registration_address",
            "residence_address",
            "notes",
            "tags",
        ]
    )

    for customer in Customer.objects.prefetch_related("tags"):
        tags = "; ".join(customer.tags.values_list("name", flat=True))
        writer.writerow(
            [
                smart_str(customer.full_name),
                smart_str(customer.email),
                smart_str(customer.phone),
                smart_str(customer.license_number),
                smart_str(customer.passport_series),
                smart_str(customer.passport_number),
                customer.passport_issue_date.isoformat() if customer.passport_issue_date else "",
                smart_str(customer.passport_issued_by),
                smart_str(customer.address),
                smart_str(customer.registration_address),
                smart_str(customer.residence_address),
                smart_str(customer.notes),
                smart_str(tags),
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
            "contract_number",
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
                smart_str(rental.contract_number),
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
                vin = normalized["vin"]
                sts_number = normalized["sts_number"]
                sts_issue_date = normalized["sts_issue_date"]
                sts_issued_by = normalized["sts_issued_by"]
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
                        "vin": vin or None,
                        "sts_number": sts_number or None,
                        "sts_issue_date": sts_issue_date,
                        "sts_issued_by": sts_issued_by or None,
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
                "vin",
                "make",
                "model",
                "year",
                "sts_number",
                "sts_issue_date (YYYY-MM-DD)",
                "sts_issued_by",
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
                "VIN / ВИН",
                "Марка",
                "Год выпуска",
                "СТС",
                "1-4 дней(вс)",
                "5-14 дней(вс)",
                "15 дней и более(вс)",
                "1-4 дней(нс)",
                "5-14 дней(нс)",
                "15 дней и более(нс)",
            ],
            "help_text": "Upload CSV or Excel (.xls). The Russian XLS template is supported, and tiered prices for высокий/низкий сезон will be imported. Existing plate numbers will be updated. Optional VIN/СТС columns will also be stored.",
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
                logger.exception("Customer import: failed to read file %s", upload.name)
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
            tags_by_license = {}
            for item in normalized_rows:
                key = item["license_number"]
                if key in by_license:
                    duplicate_rows += 1
                by_license[key] = item
                if item.get("tags") is not None:
                    tags_by_license[key] = item["tags"]

            licenses = list(by_license.keys())
            existing = {
                c.license_number: c
                for c in Customer.objects.filter(license_number__in=licenses)
            }

            to_create = []
            to_update = []
            update_fields = (
                "full_name",
                "email",
                "phone",
                "address",
                "registration_address",
                "residence_address",
                "passport_series",
                "passport_number",
                "passport_issued_by",
                "passport_issue_date",
                "notes",
            )
            for license_number, data in by_license.items():
                if license_number in existing:
                    customer = existing[license_number]
                    changed = False
                    for field in update_fields:
                        new_value = data.get(field)
                        if getattr(customer, field) != new_value:
                            setattr(customer, field, new_value)
                            changed = True
                    if changed:
                        to_update.append(customer)
                else:
                    payload = {key: value for key, value in data.items() if key != "tags"}
                    to_create.append(Customer(**payload))

            if to_create or to_update:
                with transaction.atomic():
                    if to_create:
                        created = Customer.objects.bulk_create(to_create, batch_size=IMPORT_BATCH_SIZE)
                        created_count = len(created)
                        for customer in created:
                            existing[customer.license_number] = customer
                    if to_update:
                        Customer.objects.bulk_update(
                            to_update,
                            update_fields,
                            batch_size=IMPORT_BATCH_SIZE,
                        )
                        updated_count = len(to_update)

                if tags_by_license:
                    _sync_customer_tags(
                        {license_number: existing.get(license_number) for license_number in licenses},
                        tags_by_license,
                    )

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

            logger.info(
                "Customer import finished",
                extra={
                    "imported": imported,
                    "created_count": created_count,
                    "updated": updated_count,
                    "skipped_empty": skipped_empty,
                    "duplicate_rows": duplicate_rows,
                    "file": upload.name,
                },
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
                "registration_address / Адрес прописки",
                "residence_address / Адрес проживания",
                "passport_series / Серия паспорта",
                "passport_number / Номер паспорта",
                "passport_issued_by / Кем выдан паспорт",
                "passport_issue_date (YYYY-MM-DD / ДД.ММ.ГГГГ)",
                "notes / Заметки",
                "tags / теги через запятую",
                "ID (используется как резервный идентификатор)",
            ],
            "help_text": "Upload CSV or Excel (.xls, .xlsx). Rows will be matched by license number, otherwise AmoCRM ID/phone. Missing fields are filled automatically instead of skipping rows. Паспортные данные, адреса и теги подхватываются при наличии колонок.",
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

                breakdown = calculate_rental_pricing(car, start_date, end_date)
                if breakdown.days <= 0:
                    skipped += 1
                    continue

                daily_rate_raw = row.get("daily_rate")
                daily_rate = (
                    _parse_decimal(daily_rate_raw) if daily_rate_raw not in (None, "") else breakdown.daily_rate
                )

                total_price_value = row.get("total_price")
                total_price = (
                    _parse_decimal(total_price_value)
                    if total_price_value not in (None, "")
                    else daily_rate * Decimal(breakdown.days)
                )

                contract_number = (row.get("contract_number") or "").strip()
                if contract_number:
                    exists_conflict = Rental.objects.exclude(
                        car=car, customer=customer, start_date=start_date, end_date=end_date
                    ).filter(contract_number=contract_number)
                    if exists_conflict.exists():
                        contract_number = ""

                Rental.objects.update_or_create(
                    car=car,
                    customer=customer,
                    start_date=start_date,
                    end_date=end_date,
                    defaults={
                        "daily_rate": daily_rate,
                        "total_price": total_price,
                        "status": _clean_status(row.get("status")),
                        **({"contract_number": contract_number} if contract_number else {}),
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
            "contract_number (optional)",
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
        try:
            html = render_html_template(ct, rental)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to render HTML contract", extra={"template_id": ct.id, "rental_id": rental.id})
            return HttpResponse(f"Could not render HTML: {exc}", status=500)
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        return response

    elif ct.format == "docx":
        try:
            file_io = render_docx(ct, rental)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to render DOCX contract", extra={"template_id": ct.id, "rental_id": rental.id})
            return HttpResponse(f"Could not render DOCX: {exc}", status=500)
        response = HttpResponse(
            file_io.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="contract_{rental.id}.docx"'
        return response

    elif ct.format == "pdf":
        try:
            file_io = render_pdf(ct, rental)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to generate PDF contract", extra={"template_id": ct.id, "rental_id": rental.id})
            return HttpResponse(f"Could not render PDF: {exc}", status=500)

        response = HttpResponse(file_io.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="contract_{rental.id}.pdf"'
        return response

    else:
        return HttpResponse("Unknown template format", status=400)

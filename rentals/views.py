import csv
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

try:
    import xlrd
except ImportError:  # pragma: no cover - dependency installed via requirements
    xlrd = None

try:
    import openpyxl
except ImportError:  # pragma: no cover - optional dependency for .xlsx
    openpyxl = None

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
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

from .car_constants import CAR_LOSS_FEE_FIELDS
from .forms import (
    AdminUserCreationForm,
    BusinessSettingsForm,
    CarForm,
    ContractTemplateForm,
    CustomerForm,
    RentalForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
)
from .models import BusinessSettings, Car, ContractTemplate, Customer, CustomerTag, Rental
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
TAG_MAX_LEN = CustomerTag._meta.get_field("name").max_length
IMPORT_BATCH_SIZE = max(1, int(os.environ.get("IMPORT_BULK_BATCH_SIZE", "5000")))

logger = logging.getLogger(__name__)


@login_required
@require_POST
def ocr_driver_license(request):
    # Lazy import: OCR stack (paddle/opencv) is heavy and not needed for most
    # management commands (imports, maintenance scripts, etc.).
    from .ocr import extract_ru_dl

    front_file = request.FILES.get("front_image")
    back_file = request.FILES.get("back_image")

    front_bytes = front_file.read() if front_file else None
    back_bytes = back_file.read() if back_file else None

    result = extract_ru_dl(front_bytes, back_bytes)
    return JsonResponse(result)


def _parse_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t", "да", "активен", "активный"}


def _parse_decimal(value):
    try:
        text = str(value).strip().replace(",", ".")
        return Decimal(text)
    except (InvalidOperation, AttributeError, TypeError):
        return Decimal("0")


def _parse_int(value):
    try:
        text = str(value).strip().replace(",", ".")
        return int(float(text))
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_year(value):
    year = _parse_int(value)
    if year is None:
        return None
    if not 1000 <= year <= 9999:
        return None
    try:
        return datetime(year, 1, 1).date()
    except ValueError:
        return None


def _parse_year_or_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        parsed_year = _parse_year(value)
        if parsed_year:
            return parsed_year
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return _parse_year(text)
    return _parse_date(text)


def _is_year_only_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return False
        return 1000 <= numeric <= 9999
    text = str(value).strip()
    return bool(re.fullmatch(r"\d{4}", text))


def _format_date(value, fmt: str = "%d-%m-%Y") -> str:
    return value.strftime(fmt) if value else ""


def _format_driving_since(customer: Customer) -> str:
    if not customer.driving_since:
        return ""
    if customer.driving_since_year_only:
        return customer.driving_since.strftime("%Y")
    return customer.driving_since.strftime("%d-%m-%Y")


def _clean_status(value):
    value = (value or "").strip().lower()
    valid_statuses = {choice[0] for choice in Rental.STATUS_CHOICES}
    if value in valid_statuses:
        return value
    label_map = {label.lower(): code for code, label in Rental.STATUS_CHOICES}
    return label_map.get(value, "draft")


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


def _clean_tag_name(value: str | None) -> str:
    """Normalize tag names and enforce DB length constraints."""
    text = (value or "").strip()
    if not text:
        return ""
    return text[:TAG_MAX_LEN]


def _split_tags(raw: str | None) -> list[str]:
    """Split a raw tag string (comma/semicolon/pipe) into unique tag names."""
    if not raw:
        return []

    tags = []
    for piece in re.split(r"[;,#/|\n\r]+", str(raw)):
        normalized = _clean_tag_name(piece)
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _sync_customer_tags(customers_by_license: dict[str, Customer], tags_by_license: dict[str, list[str] | None]):
    """
    Apply tag lists (by license number) to customer objects efficiently.

    tags_by_license may contain None to skip updates for that row.
    """
    cleaned_by_license: dict[str, list[str]] = {}
    tag_names: set[str] = set()

    for license_number, tags in tags_by_license.items():
        if not tags:
            continue

        cleaned = []
        for tag in tags:
            name = _clean_tag_name(tag)
            if name and name not in cleaned:
                cleaned.append(name)
                tag_names.add(name)

        if cleaned:
            cleaned_by_license[license_number] = cleaned

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

    # Apply tag updates in bulk instead of per-customer `.set()` calls.
    # This avoids tens of thousands of individual queries for large imports.
    targets: dict[int, list[int]] = {}
    for license_number, tags in cleaned_by_license.items():
        customer = customers_by_license.get(license_number)
        if not customer:
            continue
        tag_ids = []
        for tag_name in tags:
            tag = existing_tags.get(tag_name.lower())
            if tag:
                tag_ids.append(tag.id)
        if tag_ids:
            targets[customer.id] = tag_ids

    if not targets:
        return

    through = Customer.tags.through
    m2m_field = Customer._meta.get_field("tags")
    src_attname = through._meta.get_field(m2m_field.m2m_field_name()).attname
    dst_attname = through._meta.get_field(m2m_field.m2m_reverse_field_name()).attname

    with transaction.atomic():
        through.objects.filter(**{f"{src_attname}__in": list(targets.keys())}).delete()

        batch: list = []
        for customer_id, tag_ids in targets.items():
            for tag_id in tag_ids:
                batch.append(through(**{src_attname: customer_id, dst_attname: tag_id}))
                if len(batch) >= IMPORT_BATCH_SIZE:
                    through.objects.bulk_create(batch, batch_size=IMPORT_BATCH_SIZE)
                    batch.clear()

        if batch:
            through.objects.bulk_create(batch, batch_size=IMPORT_BATCH_SIZE)


def _serialize_car_pricing(car: Car):
    """Prepare car pricing info for the rental form JS helper."""

    def _num(value):
        return float(value) if value is not None else 0

    return {
        "id": car.id,
        "label": str(car),
        "plate_number": car.plate_number,
        "region_code": car.region_code or "",
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
        "sts_issue_date": car.sts_issue_date.strftime("%d-%m-%Y") if car.sts_issue_date else "",
        "sts_issued_by": car.sts_issued_by or "",
        "edit_url": reverse("rentals:car_update", args=[car.pk]),
    }


def _read_csv_rows(upload):
    raw = upload.read()
    decoded_text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            decoded_text = raw.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            continue

    if decoded_text is None:
        raise UnicodeDecodeError(
            "utf-8",
            raw,
            0,
            0,
            "Не удалось декодировать файл с разделителями. Проверьте кодировку.",
        )

    return list(csv.DictReader(decoded_text.splitlines()))


def _read_excel_rows(upload):
    if xlrd is None:
        raise ImportError("Чтение таблиц Эксель в старом формате недоступно.")

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
        raise ImportError("Чтение таблиц Эксель в новом формате недоступно.")

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
            "Госномер",
            "Гос. номер",
            "Государственный номер",
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

    year_val = _pick_value(row, ["year", "Year", "год выпуска", "Год выпуска", "Год"])
    try:
        year = int(float(year_val))
    except (TypeError, ValueError):
        year = None

    vin = _pick_value(row, ["vin", "VIN", "vin_code", "Vin", "ВИН", "Вин"])
    color = _pick_value(row, ["color", "Color", "Цвет", "цвет"])
    region_code = _pick_value(row, ["region_code", "Регион", "регион", "Регион (26 или 82)"])
    photo_url = _pick_value(row, ["photo_url", "Фото", "Фото (ссылка)", "Фото ссылка"])
    sts_number = _pick_value(
        row,
        [
            "sts_number",
            "СТС",
            "Свидетельство",
            "СТС номер",
            "номер СТС",
            "Номер СТС",
        ],
    )
    sts_issue_date_raw = _pick_value(
        row,
        [
            "sts_issue_date",
            "дата выдачи стс",
            "СТС выдано",
            "Дата выдачи СТС",
        ],
    )
    sts_issued_by = _pick_value(
        row,
        [
            "sts_issued_by",
            "кем выдано стс",
            "кем выдано",
            "Кем выдана СТС",
        ],
    )
    registration_certificate_info = _pick_value(
        row,
        ["registration_certificate_info", "Свидетельство о регистрации", "свидетельство о регистрации"],
    )
    fuel_tank_volume_raw = _pick_value(
        row,
        [
            "fuel_tank_volume_liters",
            "Объем бака",
            "Объём бака",
            "объем бака",
            "объём бака",
            "Объем бака (л)",
            "Объём бака (л)",
            "объем бака (л)",
            "объём бака (л)",
            "Объем бака, л",
            "Объём бака, л",
        ],
    )
    fuel_tank_cost_raw = _pick_value(
        row,
        [
            "fuel_tank_cost_rub",
            "Объем бака(руб.)",
            "Объём бака(руб.)",
            "Объем бака (руб.)",
            "Объём бака (руб.)",
            "объем бака (руб.)",
            "объём бака (руб.)",
            "Объем бака (руб)",
            "Объём бака (руб)",
            "объем бака (руб)",
            "объём бака (руб)",
            "Стоимость полного бака, ₽",
        ],
    )
    security_deposit_raw = _pick_value(row, ["security_deposit", "Залог", "залог"])

    rate_1_4_high = _pick_value(
        row, ["rate_1_4_high", "1-4 дней(вс)", "1-4 дней (вс)", "1-4 дня(вс)", "1-4 дня (вс)"]
    )
    rate_5_14_high = _pick_value(
        row, ["rate_5_14_high", "5-14 дней(вс)", "5-14 дней (вс)", "5-14 дня (вс)"]
    )
    rate_15_high = _pick_value(
        row,
        [
            "rate_15_plus_high",
            "15 дней и более(вс)",
            "15 дней и более (вс)",
            "15+ дней (вс)",
        ],
    )

    rate_1_4_low = _pick_value(
        row, ["rate_1_4_low", "1-4 дней(нс)", "1-4 дней (нс)", "1-4 дня (нс)"]
    )
    rate_5_14_low = _pick_value(
        row, ["rate_5_14_low", "5-14 дней(нс)", "5-14 дней (нс)", "5-14 дня (нс)"]
    )
    rate_15_low = _pick_value(
        row,
        [
            "rate_15_plus_low",
            "15 дней и более(нс)",
            "15 дней и более (нс)",
            "15+ дней (нс)",
        ],
    )

    rate_raw = _pick_value(row, ["daily_rate", "Daily rate", "Базовый тариф", "Суточный тариф"])
    active_raw = _pick_value(row, ["is_active", "active", "активен", "активный", "Активен"])
    is_active = _parse_bool(active_raw) if active_raw not in (None, "") else True

    daily_rate = _parse_decimal(rate_raw) if rate_raw not in (None, "") else None
    rate_1_4_high = _parse_decimal(rate_1_4_high) if rate_1_4_high not in (None, "") else None
    rate_5_14_high = _parse_decimal(rate_5_14_high) if rate_5_14_high not in (None, "") else None
    rate_15_high = _parse_decimal(rate_15_high) if rate_15_high not in (None, "") else None
    rate_1_4_low = _parse_decimal(rate_1_4_low) if rate_1_4_low not in (None, "") else None
    rate_5_14_low = _parse_decimal(rate_5_14_low) if rate_5_14_low not in (None, "") else None
    rate_15_low = _parse_decimal(rate_15_low) if rate_15_low not in (None, "") else None

    def _header_candidates(field_name: str, label: str) -> list[str]:
        # Support common header variants from different templates:
        # - case differences ("Гос. Номера" vs "Гос. номера")
        # - optional prefix ("Стоимость при утере ...")
        candidates: list[str] = []

        def _add(value: str | None):
            if not value:
                return
            if value not in candidates:
                candidates.append(value)

        _add(field_name)
        _add(label)

        if not isinstance(label, str):
            return candidates

        normalized = label.replace("ё", "е").replace("Ё", "Е")
        _add(normalized)
        _add(label.lower())
        if normalized != label:
            _add(normalized.lower())

        # A few spreadsheets title-case words after dots/abbreviations.
        _add(label.title())
        if normalized != label:
            _add(normalized.title())

        for prefix in (
            "Стоимость при утере ",
            "стоимость при утере ",
            "Стоимости при утере ",
            "стоимости при утере ",
        ):
            _add(prefix + label)
            _add(prefix + normalized)
            _add(prefix + label.lower())
            _add(prefix + normalized.lower())
            _add(prefix + label.title())
            if normalized != label:
                _add(prefix + normalized.title())

        # Backward-compatible aliases for known one-off templates.
        if field_name == "loss_gps_fee":
            _add("GPS")
            _add("gps")
            # Some spreadsheets include "Автобокс с креплением" instead of "Навигатор".
            _add("Автобокс с креплением")

        return candidates

    loss_fee_values = {}
    for field, label in CAR_LOSS_FEE_FIELDS:
        loss_raw = _pick_value(row, _header_candidates(field, label))
        loss_fee_values[field] = _parse_decimal(loss_raw) if loss_raw not in (None, "") else None

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

    vin_clean = _clean_text_value(vin).upper()
    if vin_clean:
        vin_clean = vin_clean.replace(" ", "")

    sts_number_clean = _clean_text_value(sts_number)

    fuel_tank_volume = _parse_int(fuel_tank_volume_raw) if fuel_tank_volume_raw not in (None, "") else None
    fuel_tank_cost = _parse_decimal(fuel_tank_cost_raw) if fuel_tank_cost_raw not in (None, "") else None
    security_deposit = (
        _parse_decimal(security_deposit_raw) if security_deposit_raw not in (None, "") else None
    )

    return {
        "plate_number": plate or "",
        "make": str(make).strip() if make else "",
        "model": str(model).strip() if model else "",
        "year": year,
        "vin": vin_clean,
        "color": _clean_text_value(color),
        "region_code": _clean_text_value(region_code),
        "photo_url": _clean_text_value(photo_url),
        "sts_number": sts_number_clean,
        "sts_issue_date": _parse_date(sts_issue_date_raw),
        "sts_issued_by": _clean_text_value(sts_issued_by),
        "registration_certificate_info": _clean_text_value(registration_certificate_info),
        "fuel_tank_volume_liters": fuel_tank_volume,
        "fuel_tank_cost_rub": fuel_tank_cost,
        "security_deposit": security_deposit,
        "daily_rate": base_rate,
        "rate_1_4_high": rate_1_4_high,
        "rate_5_14_high": rate_5_14_high,
        "rate_15_high": rate_15_high,
        "rate_1_4_low": rate_1_4_low,
        "rate_5_14_low": rate_5_14_low,
        "rate_15_low": rate_15_low,
        "is_active": is_active,
        **loss_fee_values,
    }


def _normalize_customer_row(row, row_index: int):
    """Normalize AmoCRM CSV/XLSX export rows into Customer fields."""

    def _parse_discount(raw_value):
        cleaned = _clean_text_value(raw_value).replace("%", "")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned.replace(",", "."))
        except (InvalidOperation, ValueError):
            return None

    def pick(keys):
        for key in keys:
            if key in row:
                cleaned = _clean_text_value(row[key])
                if cleaned:
                    return cleaned
        return ""

    crm_id = pick(["ID", "Id", "id", "ИД"])

    full_name = pick(["full_name", "Full name", "fullname", "ФИО", "fio", "Наименование", "Клиент"])
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
            "Номер ВУ",
        ]
    )
    license_number = (
        license_candidate
        or (crm_id and f"АМО-{crm_id}")
        or (cleaned_phone and f"ТЕЛ-{cleaned_phone}")
        or f"АВТО-{row_index}"
    )
    license_number = _limit_length(license_number, LICENSE_MAX_LEN)

    email = _limit_length(
        pick(
            [
                "email",
                "Email",
                "Рабочий email",
                "Личный email",
                "Другой email",
                "Эл. почта",
                "Электронная почта",
            ]
        ),
        EMAIL_MAX_LEN,
    )
    birth_date_raw = pick(["birth_date", "Birth date", "Дата рождения"])
    license_issued_by = pick(
        ["license_issued_by", "В.у. выдано", "ВУ выдано", "В/У выдано", "Кем выдано ВУ"]
    )
    driving_since_raw = pick(["driving_since", "Стаж с", "стаж с"])
    registration_address = pick(
        ["registration_address", "Адрес прописки", "адрес прописки", "Прописка", "прописка"]
    )
    address_fallback = pick(["Адрес (контакт)", "Адрес (компания)", "address", "Address"])
    residence_address = pick(
        [
            "residence_address",
            "Адрес проживания",
            "адрес проживания",
            "Фактический адрес",
            "фактический адрес",
        ]
    )
    primary_address = registration_address or residence_address or address_fallback
    passport_series = pick(["passport_series", "Паспорт серия", "Серия паспорта", "серия паспорта"])
    passport_number = pick(["passport_number", "Паспорт номер", "Номер паспорта", "номер паспорта"])
    passport_issued_by = pick(
        ["passport_issued_by", "Кем выдан паспорт", "кем выдан паспорт", "Паспорт кем выдан"]
    )
    passport_issue_date_raw = pick(
        ["passport_issue_date", "Дата выдачи паспорта", "дата выдачи паспорта", "Паспорт выдан"]
    )
    discount_raw = pick(["discount_percent", "discount", "Скидка", "скидка", "скидка %", "Скидка %"])
    tags_raw = pick(["tags", "Tags", "теги", "Теги"])
    tags = _split_tags(tags_raw) if tags_raw else None
    passport_series = _limit_length(passport_series, 10) or None
    passport_number = _limit_length(passport_number, 20) or None
    passport_issued_by = _limit_length(passport_issued_by, 255) or None
    license_issued_by = _limit_length(license_issued_by, 255) or None

    return {
        "full_name": full_name,
        "birth_date": _parse_date(birth_date_raw),
        "email": email or None,
        "phone": phone,
        "license_number": license_number,
        "license_issued_by": license_issued_by,
        "driving_since": _parse_year_or_date(driving_since_raw),
        "driving_since_year_only": _is_year_only_value(driving_since_raw),
        "registration_address": primary_address or None,
        "passport_series": passport_series,
        "passport_number": passport_number,
        "passport_issued_by": passport_issued_by,
        "passport_issue_date": _parse_date(passport_issue_date_raw),
        "discount_percent": _parse_discount(discount_raw),
        "tags": tags,
    }


@login_required
def admin_user_list(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    return redirect(f"{reverse('rentals:settings')}?tab=admins")


@login_required
def admin_user_password_reset(request, user_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    target_user = get_object_or_404(User, pk=user_id)
    form = StyledSetPasswordForm(target_user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Пароль обновлен для {target_user.get_username()}.")
        return redirect(f"{reverse('rentals:settings')}?tab=admins")
    return render(
        request,
        "rentals/admin_password_reset.html",
        {
            "target_user": target_user,
            "form": form,
        },
    )


@login_required
@require_POST
def admin_user_delete(request, user_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    target_user = get_object_or_404(User, pk=user_id)
    if target_user == request.user:
        messages.error(request, "Нельзя удалить собственный аккаунт.")
        return redirect(f"{reverse('rentals:settings')}?tab=admins")
    if target_user.is_superuser and User.objects.filter(is_superuser=True).exclude(pk=target_user.pk).count() == 0:
        messages.error(request, "Нельзя удалить последнего суперпользователя.")
        return redirect(f"{reverse('rentals:settings')}?tab=admins")
    target_user.delete()
    messages.success(request, f"Пользователь {target_user.get_username()} удален.")
    return redirect(f"{reverse('rentals:settings')}?tab=admins")


@login_required
def password_change_self(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Пароль обновлен.")
        return redirect("rentals:password_change")
    return render(request, "rentals/password_change.html", {"form": form})


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["loss_fee_fields"] = [form[name] for name, _ in CAR_LOSS_FEE_FIELDS] if form else []
        return context


@method_decorator(login_required, name="dispatch")
class CarUpdateView(UpdateView):
    model = Car
    form_class = CarForm
    template_name = "rentals/car_form.html"
    success_url = reverse_lazy("rentals:car_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["loss_fee_fields"] = [form[name] for name, _ in CAR_LOSS_FEE_FIELDS] if form else []
        return context


@login_required
@require_POST
def car_delete(request, pk: int):
    car = get_object_or_404(Car, pk=pk)
    try:
        car.delete()
        messages.success(request, f"Авто {car.plate_number} удалено.")
    except ProtectedError:
        messages.error(
            request,
            "Нельзя удалить авто: есть связанные аренды.",
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
        messages.success(request, f"Удалено автомобилей: {deletable_count}.")

    locked_count = with_rentals.count()
    if locked_count:
        messages.warning(
            request,
            f"Пропущено авто, связанные с арендами: {locked_count}.",
        )
    elif deletable_count == 0:
        messages.info(request, "Нет авто для удаления.")

    return redirect("rentals:car_list")


@login_required
@require_POST
def customer_delete(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    try:
        customer.delete()
        messages.success(request, f"Клиент {customer.full_name} удален.")
    except ProtectedError:
        messages.error(
            request,
            "Нельзя удалить клиента: есть связанные аренды.",
        )
    return redirect("rentals:customer_list")


@login_required
@require_POST
def customer_delete_all(request):
    with_rentals = Customer.objects.filter(
        Q(rental__isnull=False) | Q(secondary_rentals__isnull=False)
    ).distinct()
    deletable = Customer.objects.exclude(pk__in=with_rentals.values_list("pk", flat=True))
    deletable_count = deletable.count()

    if deletable_count:
        deletable.delete()
        messages.success(request, f"Удалено клиентов: {deletable_count}.")

    locked_count = with_rentals.count()
    if locked_count:
        messages.warning(
            request,
            f"Пропущено клиентов, связанных с арендами: {locked_count}.",
        )
    elif deletable_count == 0:
        messages.info(request, "Нет клиентов для удаления.")

    return redirect("rentals:customer_list")


@method_decorator(login_required, name="dispatch")
class CustomerListView(ListView):
    model = Customer
    template_name = "rentals/customer_list.html"
    paginate_by = 25
    page_size_options = (25, 50, 100)
    default_sort = "created_desc"
    sort_options = (
        ("created_desc", "Дата добавления: сначала новые"),
        ("created_asc", "Дата добавления: сначала старые"),
        ("name_asc", "ФИО: А-Я"),
        ("name_desc", "ФИО: Я-А"),
        ("discount_desc", "Скидка: больше -> меньше"),
        ("discount_asc", "Скидка: меньше -> больше"),
        ("birth_desc", "Дата рождения: моложе"),
        ("birth_asc", "Дата рождения: старше"),
        ("driving_desc", "Стаж: больше"),
        ("driving_asc", "Стаж: меньше"),
    )
    sort_map = {
        "created_desc": ("-created_at", "-id"),
        "created_asc": ("created_at", "id"),
        "name_asc": ("full_name", "id"),
        "name_desc": ("-full_name", "-id"),
        "discount_desc": ("-discount_percent", "-id"),
        "discount_asc": ("discount_percent", "id"),
        "birth_desc": ("-birth_date", "-id"),
        "birth_asc": ("birth_date", "id"),
        "driving_desc": ("-driving_since", "-id"),
        "driving_asc": ("driving_since", "id"),
    }

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("tags")
        self.search_query = (self.request.GET.get("q") or "").strip()
        self.sort_key = (self.request.GET.get("sort") or "").strip()
        if self.sort_key not in self.sort_map:
            self.sort_key = self.default_sort
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
                date_value = _parse_date(term)
                discount_value = None
                try:
                    cleaned_term = str(term).replace("%", "").replace(",", ".")
                    discount_value = Decimal(cleaned_term)
                except (InvalidOperation, ValueError):
                    discount_value = None
                condition = (
                    Q(full_name__icontains=term)
                    | Q(phone__icontains=term)
                    | Q(email__icontains=term)
                    | Q(license_number__icontains=term)
                    | Q(license_issued_by__icontains=term)
                    | Q(registration_address__icontains=term)
                    | Q(passport_series__icontains=term)
                    | Q(passport_number__icontains=term)
                    | Q(passport_issued_by__icontains=term)
                    | Q(tags__name__icontains=term)
                )
                if date_value:
                    condition |= Q(passport_issue_date=date_value) | Q(birth_date=date_value) | Q(driving_since=date_value)
                if discount_value is not None:
                    condition |= Q(discount_percent=discount_value)
                queryset = queryset.filter(condition)

        if self.active_tags:
            queryset = queryset.filter(tags__id__in=self.active_tags)

        ordering = self.sort_map[self.sort_key]
        return queryset.distinct().order_by(*ordering)

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
        context["sort_options"] = self.sort_options
        context["active_sort"] = getattr(self, "sort_key", self.default_sort)
        context["default_sort"] = self.default_sort
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        query_params.pop("page_size", None)
        context["querystring"] = f"&{query_params.urlencode()}" if query_params else ""
        context["filters_active"] = bool(
            self.search_query
            or self.active_tags
            or getattr(self, "sort_key", self.default_sort) != self.default_sort
        )
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
        queryset = super().get_queryset().select_related("car", "customer", "second_driver")
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
                    | Q(second_driver__full_name__icontains=term)
                    | Q(second_driver__phone__icontains=term)
                    | Q(second_driver__email__icontains=term)
                    | Q(second_driver__license_number__icontains=term)
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
        context["car_initial_label"] = getattr(context.get("form"), "initial_car_label", "")
        context["customer_initial_label"] = getattr(context.get("form"), "initial_customer_label", "")
        context["second_driver_initial_label"] = getattr(
            context.get("form"), "initial_second_driver_label", ""
        )
        context["pricing_config"] = pricing_config()
        context["contract_templates"] = ContractTemplate.objects.all()
        return context


@method_decorator(login_required, name="dispatch")
class RentalWizardView(CreateView):
    model = Rental
    form_class = RentalForm
    template_name = "rentals/rental_wizard.html"
    success_url = reverse_lazy("rentals:rental_list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.created_via_wizard = True
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Создана аренда №{self.object.contract_number} через мастер.",
        )
        template_id = self.request.POST.get("generate_contract_template_id")
        if template_id:
            return redirect("rentals:generate_contract", rental_id=self.object.pk, template_id=template_id)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["car_pricing"] = [_serialize_car_pricing(car) for car in Car.objects.all()]
        context["car_initial_label"] = getattr(context.get("form"), "initial_car_label", "")
        context["customer_initial_label"] = getattr(context.get("form"), "initial_customer_label", "")
        context["second_driver_initial_label"] = getattr(
            context.get("form"), "initial_second_driver_label", ""
        )
        context["pricing_config"] = pricing_config()
        context["contract_templates"] = ContractTemplate.objects.all()
        return context


@method_decorator(login_required, name="dispatch")
class RentalUpdateView(UpdateView):
    model = Rental
    form_class = RentalForm
    template_name = "rentals/rental_form.html"
    success_url = reverse_lazy("rentals:rental_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        template_id = self.request.POST.get("generate_contract_template_id")
        if template_id:
            return redirect("rentals:generate_contract", rental_id=self.object.pk, template_id=template_id)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["car_pricing"] = [_serialize_car_pricing(car) for car in Car.objects.all()]
        context["car_initial_label"] = getattr(context.get("form"), "initial_car_label", "")
        context["customer_initial_label"] = getattr(context.get("form"), "initial_customer_label", "")
        context["second_driver_initial_label"] = getattr(
            context.get("form"), "initial_second_driver_label", ""
        )
        context["pricing_config"] = pricing_config()
        context["contract_templates"] = ContractTemplate.objects.all()
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


@method_decorator(login_required, name="dispatch")
class BusinessSettingsUpdateView(UpdateView):
    model = BusinessSettings
    form_class = BusinessSettingsForm
    template_name = "rentals/settings_form.html"
    success_url = reverse_lazy("rentals:settings")

    def get_object(self, queryset=None):
        return BusinessSettings.get_solo()

    def _resolve_tab(self):
        tab = (self.request.GET.get("tab") or "general").lower()
        if tab == "admins" and not self.request.user.is_superuser:
            return "general"
        return tab if tab in {"general", "admins"} else "general"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_tab = kwargs.get("active_tab") or self._resolve_tab()
        if active_tab == "admins" and not self.request.user.is_superuser:
            active_tab = "general"
        context["active_tab"] = active_tab
        if self.request.user.is_superuser:
            context.setdefault("admins", User.objects.filter(is_staff=True).order_by("username"))
            context.setdefault("superuser_count", User.objects.filter(is_superuser=True).count())
            context.setdefault("admin_form", kwargs.get("admin_form") or AdminUserCreationForm())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action")
        if action == "create_admin":
            if not request.user.is_superuser:
                raise PermissionDenied
            admin_form = AdminUserCreationForm(request.POST)
            if admin_form.is_valid():
                new_admin = admin_form.save()
                messages.success(request, f"Администратор {new_admin.get_username()} создан.")
                return redirect(f"{reverse('rentals:settings')}?tab=admins")
            settings_form = BusinessSettingsForm(instance=self.object)
            context = self.get_context_data(form=settings_form, admin_form=admin_form, active_tab="admins")
            return self.render_to_response(context)
        return super().post(request, *args, **kwargs)


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

    terms = [piece for piece in re.split(r"\s+", term) if piece]
    matches = Customer.objects.all()
    for piece in terms:
        date_value = _parse_date(piece)
        condition = (
            Q(full_name__icontains=piece)
            | Q(phone__icontains=piece)
            | Q(email__icontains=piece)
            | Q(license_number__icontains=piece)
            | Q(license_issued_by__icontains=piece)
            | Q(registration_address__icontains=piece)
            | Q(passport_number__icontains=piece)
            | Q(passport_series__icontains=piece)
            | Q(tags__name__icontains=piece)
        )
        if date_value:
            condition |= Q(birth_date=date_value) | Q(driving_since=date_value) | Q(passport_issue_date=date_value)
        matches = matches.filter(condition)

    matches = matches.order_by("full_name").distinct()[:limit]

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
@require_POST
def customer_quick_create(request):
    form = CustomerForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    customer = form.save()
    if not (customer.full_name or "").strip():
        customer.full_name = _limit_length(f"Без имени {customer.id}", NAME_MAX_LEN)
        customer.save(update_fields=["full_name"])

    phone = customer.phone or ""
    label = f"{customer.full_name}{f' · {phone}' if phone else ''}"
    return JsonResponse(
        {
            "customer": {
                "id": customer.id,
                "name": customer.full_name,
                "phone": phone,
                "email": customer.email or "",
                "license_number": customer.license_number,
                "label": label,
            }
        }
    )


@login_required
def customer_profile(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    tags = list(customer.tags.values_list("name", flat=True))
    return JsonResponse(
        {
            "customer": {
                "id": customer.id,
                "full_name": customer.full_name,
                "phone": customer.phone or "",
                "email": customer.email or "",
                "birth_date": _format_date(customer.birth_date),
                "license_number": customer.license_number,
                "license_issued_by": customer.license_issued_by or "",
                "driving_since": _format_driving_since(customer),
                "passport_series": customer.passport_series or "",
                "passport_number": customer.passport_number or "",
                "passport_issued_by": customer.passport_issued_by or "",
                "passport_issue_date": _format_date(customer.passport_issue_date),
                "registration_address": customer.registration_address or "",
                "discount_percent": str(customer.discount_percent) if customer.discount_percent is not None else "",
                "tags": tags,
            }
        }
    )


@login_required
def export_cars_csv(request):
    response = HttpResponse(content_type="text/csv")
    filename = "автомобили.csv"
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"

    writer = csv.writer(response)
    writer.writerow(
        [
            "Госномер",
            "Марка",
            "Модель",
            "Год выпуска",
            "ВИН",
            "Цвет",
            "Регион",
            "Фото (ссылка)",
            "Номер СТС",
            "Дата выдачи СТС",
            "Кем выдана СТС",
            "Свидетельство о регистрации",
            "Объем бака, л",
            "Стоимость полного бака, ₽",
            "Залог",
            "Базовый тариф",
            "1-4 дня (вс)",
            "5-14 дней (вс)",
            "15+ дней (вс)",
            "1-4 дня (нс)",
            "5-14 дней (нс)",
            "15+ дней (нс)",
            "Активен",
            *[label for _, label in CAR_LOSS_FEE_FIELDS],
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
                smart_str(car.color),
                smart_str(car.region_code),
                smart_str(car.photo_url),
                smart_str(car.sts_number),
                car.sts_issue_date.strftime("%d-%m-%Y") if car.sts_issue_date else "",
                smart_str(car.sts_issued_by),
                smart_str(car.registration_certificate_info),
                car.fuel_tank_volume_liters if car.fuel_tank_volume_liters is not None else "",
                car.fuel_tank_cost_rub if car.fuel_tank_cost_rub is not None else "",
                car.security_deposit if car.security_deposit is not None else "",
                car.daily_rate,
                car.rate_1_4_high,
                car.rate_5_14_high,
                car.rate_15_plus_high,
                car.rate_1_4_low,
                car.rate_5_14_low,
                car.rate_15_plus_low,
                "Да" if car.is_active else "Нет",
                *[
                    getattr(car, field) if getattr(car, field) is not None else ""
                    for field, _ in CAR_LOSS_FEE_FIELDS
                ],
            ]
        )

    return response


@login_required
def export_customers_csv(request):
    response = HttpResponse(content_type="text/csv")
    filename = "клиенты.csv"
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"

    writer = csv.writer(response)
    writer.writerow(
        [
            "ФИО",
            "Дата рождения",
            "Эл. почта",
            "Телефон",
            "Номер ВУ",
            "Кем выдано ВУ",
            "Стаж с",
            "Серия паспорта",
            "Номер паспорта",
            "Дата выдачи паспорта",
            "Кем выдан паспорт",
            "Адрес прописки",
            "Скидка, %",
            "Теги",
        ]
    )

    for customer in Customer.objects.prefetch_related("tags"):
        tags = "; ".join(customer.tags.values_list("name", flat=True))
        writer.writerow(
            [
                smart_str(customer.full_name),
                customer.birth_date.strftime("%d-%m-%Y") if customer.birth_date else "",
                smart_str(customer.email or ""),
                smart_str(customer.phone or ""),
                smart_str(customer.license_number),
                smart_str(customer.license_issued_by or ""),
                (
                    customer.driving_since.strftime("%Y")
                    if customer.driving_since and customer.driving_since_year_only
                    else customer.driving_since.strftime("%d-%m-%Y") if customer.driving_since else ""
                ),
                smart_str(customer.passport_series or ""),
                smart_str(customer.passport_number or ""),
                customer.passport_issue_date.strftime("%d-%m-%Y") if customer.passport_issue_date else "",
                smart_str(customer.passport_issued_by or ""),
                smart_str(customer.registration_address or ""),
                smart_str(customer.discount_percent if customer.discount_percent is not None else ""),
                smart_str(tags),
            ]
        )

    return response


@login_required
def export_rentals_csv(request):
    response = HttpResponse(content_type="text/csv")
    filename = "аренды.csv"
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"

    writer = csv.writer(response)
    writer.writerow(
        [
            "Номер договора",
            "Госномер",
            "Номер ВУ",
            "Клиент",
            "Дата начала",
            "Дата окончания",
            "Суточный тариф",
            "Итоговая сумма",
            "Статус",
        ]
    )

    for rental in Rental.objects.select_related("car", "customer", "second_driver"):
        writer.writerow(
            [
                smart_str(rental.contract_number),
                smart_str(rental.car.plate_number),
                smart_str(rental.customer.license_number),
                smart_str(rental.customer.full_name),
                rental.start_date.strftime("%d-%m-%Y"),
                rental.end_date.strftime("%d-%m-%Y"),
                rental.daily_rate,
                rental.total_price,
                rental.get_status_display(),
            ]
        )

    return response


@login_required
def import_cars_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Пожалуйста, выберите таблицу Эксель или файл с разделителями.")
        else:
            try:
                rows = _load_rows(upload)
            except Exception as exc:  # noqa: BLE001 - present message to user
                logger.exception("Импорт авто: не удалось прочитать файл %s", upload.name)
                messages.error(request, "Не удалось прочитать файл. Проверьте формат и кодировку.")
                return redirect("rentals:import_cars_csv")

            if not rows:
                messages.warning(request, "Файл пустой или не содержит строк.")
                return redirect("rentals:import_cars_csv")

            imported, skipped = 0, 0

            for row in rows:
                normalized = _normalize_car_row(row)

                plate = normalized["plate_number"]
                make = normalized["make"]
                model = normalized["model"]
                year = normalized["year"]
                vin = normalized["vin"]
                color = normalized.get("color")
                region_code = normalized.get("region_code")
                photo_url = normalized.get("photo_url")
                sts_number = normalized["sts_number"]
                sts_issue_date = normalized["sts_issue_date"]
                sts_issued_by = normalized["sts_issued_by"]
                registration_certificate_info = normalized.get("registration_certificate_info")
                fuel_tank_volume_liters = normalized.get("fuel_tank_volume_liters")
                fuel_tank_cost_rub = normalized.get("fuel_tank_cost_rub")
                security_deposit = normalized.get("security_deposit")
                daily_rate = normalized["daily_rate"]
                rate_1_4_high = normalized["rate_1_4_high"]
                rate_5_14_high = normalized["rate_5_14_high"]
                rate_15_high = normalized["rate_15_high"]
                rate_1_4_low = normalized["rate_1_4_low"]
                rate_5_14_low = normalized["rate_5_14_low"]
                rate_15_low = normalized["rate_15_low"]
                loss_fee_values = {field: normalized.get(field) for field, _ in CAR_LOSS_FEE_FIELDS}

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

                defaults = {
                    "make": make,
                    "model": model,
                    "year": year,
                    "vin": vin or None,
                    "color": color or None,
                    "region_code": region_code or None,
                    "photo_url": photo_url or None,
                    "sts_number": sts_number or None,
                    "sts_issue_date": sts_issue_date,
                    "sts_issued_by": sts_issued_by or None,
                    "registration_certificate_info": registration_certificate_info or None,
                    "fuel_tank_volume_liters": fuel_tank_volume_liters,
                    "fuel_tank_cost_rub": fuel_tank_cost_rub,
                    "security_deposit": security_deposit,
                    "daily_rate": base_daily_rate,
                    "rate_1_4_high": rate_1_4_high or Decimal("0"),
                    "rate_5_14_high": rate_5_14_high or Decimal("0"),
                    "rate_15_plus_high": rate_15_high or Decimal("0"),
                    "rate_1_4_low": rate_1_4_low or Decimal("0"),
                    "rate_5_14_low": rate_5_14_low or Decimal("0"),
                    "rate_15_plus_low": rate_15_low or Decimal("0"),
                    "is_active": normalized["is_active"],
                    **loss_fee_values,
                }

                car, created = Car.objects.get_or_create(plate_number=plate, defaults=defaults)
                if not created:
                    car.make = make or car.make
                    car.model = model or car.model
                    car.year = year or car.year
                    if vin:
                        car.vin = vin
                    if color:
                        car.color = color
                    if region_code:
                        car.region_code = region_code
                    if photo_url:
                        car.photo_url = photo_url
                    if sts_number:
                        car.sts_number = sts_number
                    if sts_issue_date:
                        car.sts_issue_date = sts_issue_date
                    if sts_issued_by:
                        car.sts_issued_by = sts_issued_by
                    if registration_certificate_info:
                        car.registration_certificate_info = registration_certificate_info
                    if fuel_tank_volume_liters is not None:
                        car.fuel_tank_volume_liters = fuel_tank_volume_liters
                    if fuel_tank_cost_rub is not None:
                        car.fuel_tank_cost_rub = fuel_tank_cost_rub
                    if security_deposit is not None:
                        car.security_deposit = security_deposit
                    if base_daily_rate is not None:
                        car.daily_rate = base_daily_rate
                    if rate_1_4_high is not None:
                        car.rate_1_4_high = rate_1_4_high
                    if rate_5_14_high is not None:
                        car.rate_5_14_high = rate_5_14_high
                    if rate_15_high is not None:
                        car.rate_15_plus_high = rate_15_high
                    if rate_1_4_low is not None:
                        car.rate_1_4_low = rate_1_4_low
                    if rate_5_14_low is not None:
                        car.rate_5_14_low = rate_5_14_low
                    if rate_15_low is not None:
                        car.rate_15_plus_low = rate_15_low
                    for field, value in loss_fee_values.items():
                        if value is not None:
                            setattr(car, field, value)
                    car.is_active = normalized["is_active"]
                    car.save()
                imported += 1

            if imported:
                messages.success(request, f"Импортировано автомобилей: {imported}.")
            if skipped:
                messages.warning(request, f"Пропущено строк из-за ошибок или неполных данных: {skipped}.")

            return redirect("rentals:car_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Импорт автомобилей",
            "expected_headers": [
                "Госномер",
                "ВИН",
                "Цвет",
                "Регион",
                "Фото (ссылка)",
                "Марка",
                "Модель",
                "Год выпуска",
                "Номер СТС",
                "Дата выдачи СТС (ДД-ММ-ГГГГ)",
                "Кем выдана СТС",
                "Свидетельство о регистрации",
                "Объем бака, л",
                "Стоимость полного бака, ₽",
                "Залог",
                "Базовый тариф",
                "1-4 дня (вс)",
                "5-14 дней (вс)",
                "15+ дней (вс)",
                "1-4 дня (нс)",
                "5-14 дней (нс)",
                "15+ дней (нс)",
                "Активен",
                *[label for _, label in CAR_LOSS_FEE_FIELDS],
            ],
            "xls_headers": [
                "Регистрационный знак",
                "ВИН",
                "Цвет",
                "Регион",
                "Фото (ссылка)",
                "Марка",
                "Год выпуска",
                "СТС",
                "Свидетельство о регистрации",
                "Объем бака",
                "Объем бака(руб.)",
                "Залог",
                "1-4 дней(вс)",
                "5-14 дней(вс)",
                "15 дней и более(вс)",
                "1-4 дней(нс)",
                "5-14 дней(нс)",
                "15 дней и более(нс)",
                *[label for _, label in CAR_LOSS_FEE_FIELDS],
            ],
            "help_text": "Загрузите таблицу Эксель или файл с разделителями. Поддерживается русский шаблон Эксель, а также ступенчатые тарифы для высокого/низкого сезона. Можно импортировать цвет, регион, ссылку на фото, параметры бака, залог и цены при утере комплектующих. Авто с совпадающим госномером будут обновлены без очистки пропущенных полей.",
            "back_url": reverse("rentals:car_list"),
        },
    )


@login_required
def import_customers_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Пожалуйста, выберите таблицу Эксель или файл с разделителями.")
        else:
            try:
                rows = _load_rows(upload)
            except Exception as exc:  # noqa: BLE001 - show error to user
                logger.exception("Импорт клиентов: не удалось прочитать файл %s", upload.name)
                messages.error(request, "Не удалось прочитать файл. Проверьте формат и кодировку.")
                return redirect("rentals:import_customers_csv")

            if not rows:
                messages.warning(request, "Файл пустой или не содержит строк.")
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
                messages.warning(request, "Не найдено корректных строк для импорта.")
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
                "birth_date",
                "email",
                "phone",
                "license_issued_by",
                "driving_since",
                "driving_since_year_only",
                "registration_address",
                "passport_series",
                "passport_number",
                "passport_issued_by",
                "passport_issue_date",
                "discount_percent",
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

            # Apply tag updates even when field values did not change,
            # so re-importing the same file can still fix tag sync issues.
            if tags_by_license:
                _sync_customer_tags(
                    {license_number: existing.get(license_number) for license_number in licenses},
                    tags_by_license,
                )

            imported = created_count + updated_count

            if imported:
                messages.success(
                    request,
                    f"Импортировано {imported} клиент(ов) "
                    f"(создано {created_count}, обновлено {updated_count}). Пустые поля заполнены автоматически.",
                )
            if skipped_empty:
                messages.info(
                    request,
                    f"Пропущено {skipped_empty} полностью пустых строк.",
                )
            if duplicate_rows:
                messages.info(
                    request,
                    f"Объединены {duplicate_rows} строк(и) с одинаковым номером ВУ в файле.",
                )

            logger.info(
                "Импорт клиентов завершен",
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
            "title": "Импорт клиентов",
            "expected_headers": [
                "ФИО / Наименование",
                "Имя",
                "Фамилия",
                "Телефон (контакт) / Мобильный телефон / Рабочий телефон",
                "Номер ВУ / Водит. удостоверение. (контакт)",
                "Кем выдано ВУ",
                "Стаж с",
                "Дата рождения",
                "Скидка, %",
                "Эл. почта (рабочая/личная/другая)",
                "Адрес прописки",
                "Серия паспорта",
                "Номер паспорта",
                "Кем выдан паспорт",
                "Дата выдачи паспорта (ДД-ММ-ГГГГ / ДД.ММ.ГГГГ)",
                "Теги (через запятую)",
                "ИД (резервный идентификатор)",
            ],
            "help_text": "Загрузите таблицу Эксель или файл с разделителями. Строки сопоставляются по номеру ВУ, затем по идентификатору/телефону из АмоСРМ. Пустые значения заполняются автоматически. Адрес (контакт/фактический) при импорте кладётся в адрес прописки.",
            "back_url": reverse("rentals:customer_list"),
        },
    )


@login_required
def import_rentals_csv(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Пожалуйста, выберите файл с разделителями.")
        else:
            decoded = upload.read().decode("utf-8-sig").splitlines()
            reader = csv.DictReader(decoded)
            imported, missing_relations, skipped = 0, 0, 0

            for row in reader:
                plate = _pick_value(
                    row,
                    [
                        "car_plate_number",
                        "plate_number",
                        "Госномер",
                        "Гос. номер",
                        "Регистрационный знак",
                    ],
                )
                license_number = _pick_value(
                    row,
                    [
                        "customer_license_number",
                        "license_number",
                        "Номер ВУ",
                        "Водительское удостоверение",
                    ],
                )
                start_date = _parse_date(_pick_value(row, ["start_date", "Дата начала"]))
                end_date = _parse_date(_pick_value(row, ["end_date", "Дата окончания"]))
                plate = (plate or "").strip()
                license_number = (license_number or "").strip()

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

                daily_rate_raw = _pick_value(row, ["daily_rate", "Суточный тариф", "Базовый тариф"])
                daily_rate = (
                    _parse_decimal(daily_rate_raw) if daily_rate_raw not in (None, "") else breakdown.daily_rate
                )

                total_price_value = _pick_value(row, ["total_price", "Итоговая сумма"])
                total_price = (
                    _parse_decimal(total_price_value)
                    if total_price_value not in (None, "")
                    else daily_rate * Decimal(breakdown.days)
                )

                contract_number = (_pick_value(row, ["contract_number", "Номер договора"]) or "").strip()
                status_value = _pick_value(row, ["status", "Статус"])
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
                        "status": _clean_status(status_value),
                        **({"contract_number": contract_number} if contract_number else {}),
                    },
                )
                imported += 1

            if imported:
                messages.success(request, f"Импортировано аренд: {imported}.")
            if missing_relations:
                messages.warning(
                    request,
                    f"Пропущено строк из-за отсутствия авто или клиента: {missing_relations}.",
                )
            if skipped:
                messages.warning(request, f"Пропущено строк из-за ошибок или неполных данных: {skipped}.")

            return redirect("rentals:rental_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Импорт аренд",
            "expected_headers": [
                "Номер договора",
                "Госномер",
                "Номер ВУ",
                "Клиент",
                "Дата начала",
                "Дата окончания",
                "Суточный тариф",
                "Итоговая сумма",
                "Статус",
            ],
            "help_text": "Перед импортом аренды должны быть заведены автомобили и клиенты. Номер договора можно не указывать.",
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
            logger.exception(
                "Не удалось сформировать договор из веб-шаблона",
                extra={"template_id": ct.id, "rental_id": rental.id},
            )
            return HttpResponse("Не удалось сформировать договор из веб-шаблона.", status=500)
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        return response

    elif ct.format == "docx":
        try:
            file_io = render_docx(ct, rental)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Не удалось сформировать документ Ворд",
                extra={"template_id": ct.id, "rental_id": rental.id},
            )
            return HttpResponse("Не удалось сформировать документ Ворд.", status=500)
        response = HttpResponse(
            file_io.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        filename = f"договор_{rental.id}.docx"
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return response

    elif ct.format == "pdf":
        try:
            file_io = render_pdf(ct, rental)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Не удалось сформировать ПДФ-договор",
                extra={"template_id": ct.id, "rental_id": rental.id},
            )
            return HttpResponse("Не удалось сформировать ПДФ-договор.", status=500)

        response = HttpResponse(file_io.getvalue(), content_type="application/pdf")
        filename = f"договор_{rental.id}.pdf"
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return response

    else:
        return HttpResponse("Неизвестный формат шаблона", status=400)

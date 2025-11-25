import csv
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
from .services.stats import car_utilization, rentals_summary


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


@login_required
def dashboard(request):
    summary = rentals_summary()
    utilization = car_utilization()[:5]

    context = {
        "cars_count": Car.objects.count(),
        "customers_count": Customer.objects.count(),
        "active_rentals": summary["active_rentals"],
        "total_revenue": summary["total_revenue"],
        "total_rentals": summary["total_rentals"],
        "completed_rentals": summary["completed_rentals"],
        "top_cars": utilization,
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
            messages.error(request, "Please choose a CSV file to upload.")
        else:
            decoded = upload.read().decode("utf-8-sig").splitlines()
            reader = csv.DictReader(decoded)
            imported, skipped = 0, 0

            for row in reader:
                full_name = (row.get("full_name") or "").strip()
                license_number = (row.get("license_number") or "").strip()
                phone = (row.get("phone") or "").strip()

                if not all([full_name, license_number, phone]):
                    skipped += 1
                    continue

                Customer.objects.update_or_create(
                    license_number=license_number,
                    defaults={
                        "full_name": full_name,
                        "email": (row.get("email") or "").strip() or None,
                        "phone": phone,
                        "address": (row.get("address") or "").strip() or None,
                    },
                )
                imported += 1

            if imported:
                messages.success(request, f"Imported {imported} customers.")
            if skipped:
                messages.warning(request, f"Skipped {skipped} rows due to missing required fields.")

            return redirect("rentals:customer_list")

    return render(
        request,
        "rentals/import_csv.html",
        {
            "title": "Import customers",
            "expected_headers": ["full_name", "email", "phone", "license_number", "address"],
            "help_text": "Existing license numbers will be updated with the new values.",
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
        response = HttpResponse(html, content_type="text/html")
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

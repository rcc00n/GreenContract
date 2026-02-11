from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from rentals import views
from rentals.car_constants import CAR_LOSS_FEE_FIELDS
from rentals.models import Car


class Command(BaseCommand):
    help = "Import cars from a CSV/XLS/XLSX file (same logic as /rentals/cars/import/)."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            type=str,
            help="Path to the file (inside the container). Example: /app/import_data/cars.xls",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        if not path.is_file():
            raise CommandError(f"Not a file: {path}")

        with path.open("rb") as upload:
            try:
                rows = views._load_rows(upload)  # noqa: SLF001 - reuse proven import logic
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"Failed to read file: {path}. Error: {exc}") from exc

        if not rows:
            self.stdout.write(self.style.WARNING("No rows found (empty file)."))
            return

        imported, skipped = 0, 0

        for row in rows:
            normalized = views._normalize_car_row(row)  # noqa: SLF001

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

        self.stdout.write(self.style.SUCCESS(f"Imported cars: {imported}"))
        self.stdout.write(f"Skipped rows (missing required fields): {skipped}")


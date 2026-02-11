from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from rentals import views
from rentals.models import Customer


class Command(BaseCommand):
    help = "Import customers from a CSV/XLS/XLSX file (same logic as /rentals/customers/import/)."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            type=str,
            help="Path to the file (inside the container). Example: /app/import_data/contacts.xlsx",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        if not path.is_file():
            raise CommandError(f"Not a file: {path}")

        with path.open("rb") as upload:
            # `_load_rows` uses upload.name to infer format.
            try:
                rows = views._load_rows(upload)  # noqa: SLF001 - reuse proven import logic
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"Failed to read file: {path}. Error: {exc}") from exc

        if not rows:
            self.stdout.write(self.style.WARNING("No rows found (empty file)."))
            return

        created_count, updated_count, skipped_empty = 0, 0, 0
        normalized_rows: list[dict] = []
        for idx, row in enumerate(rows, start=1):
            if not any(views._clean_text_value(value) for value in row.values()):  # noqa: SLF001
                skipped_empty += 1
                continue

            normalized = views._normalize_customer_row(row, idx)  # noqa: SLF001
            normalized_rows.append(normalized)

        if not normalized_rows:
            self.stdout.write(self.style.WARNING("No valid rows found for import."))
            return

        # Deduplicate by license number inside the upload.
        by_license: dict[str, dict] = {}
        duplicate_rows = 0
        tags_by_license: dict[str, list[str] | None] = {}
        for item in normalized_rows:
            key = item["license_number"]
            if key in by_license:
                duplicate_rows += 1
            by_license[key] = item
            if item.get("tags") is not None:
                tags_by_license[key] = item["tags"]

        licenses = list(by_license.keys())
        existing = {c.license_number: c for c in Customer.objects.filter(license_number__in=licenses)}

        to_create: list[Customer] = []
        to_update: list[Customer] = []
        update_fields = (
            "full_name",
            "birth_date",
            "email",
            "phone",
            "license_issued_by",
            "driving_since",
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
                    created = Customer.objects.bulk_create(to_create, batch_size=views.IMPORT_BATCH_SIZE)
                    created_count = len(created)
                    for customer in created:
                        existing[customer.license_number] = customer

                if to_update:
                    Customer.objects.bulk_update(
                        to_update,
                        update_fields,
                        batch_size=views.IMPORT_BATCH_SIZE,
                    )
                    updated_count = len(to_update)

        # Apply tag updates even when field values did not change,
        # so re-importing the same file can still fix tag sync issues.
        if tags_by_license:
            views._sync_customer_tags(  # noqa: SLF001 - reuse importer
                {license_number: existing.get(license_number) for license_number in licenses},
                tags_by_license,
            )

        imported = created_count + updated_count

        self.stdout.write(self.style.SUCCESS(f"Imported customers: {imported}"))
        self.stdout.write(f"Created: {created_count}")
        self.stdout.write(f"Updated: {updated_count}")
        self.stdout.write(f"Skipped empty rows: {skipped_empty}")
        self.stdout.write(f"Merged duplicate rows (by license): {duplicate_rows}")

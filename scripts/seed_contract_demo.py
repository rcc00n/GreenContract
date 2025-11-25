"""
Quick demo seeding for contract generation.

Run:
    python manage.py shell < scripts/seed_contract_demo.py
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from rentals.models import Car, ContractTemplate, Customer, Rental


def main():
    car, _ = Car.objects.get_or_create(
        plate_number="DEMO-001",
        defaults={
            "make": "Toyota",
            "model": "Corolla",
            "year": 2020,
            "daily_rate": Decimal("45.00"),
            "rate_1_4_high": Decimal("45.00"),
            "rate_5_14_high": Decimal("42.00"),
            "rate_15_plus_high": Decimal("40.00"),
            "rate_1_4_low": Decimal("40.00"),
            "rate_5_14_low": Decimal("38.00"),
            "rate_15_plus_low": Decimal("36.00"),
            "is_active": True,
        },
    )

    customer, _ = Customer.objects.get_or_create(
        full_name="Demo Driver",
        defaults={
            "email": "demo@example.com",
            "phone": "+1 555 000 1111",
            "license_number": "D1234567",
            "address": "123 Demo Street\nExample City",
        },
    )

    rental_days = 3
    start = date.today()
    end = start + timedelta(days=rental_days)
    demo_rate = car.get_rate_for_days(rental_days)
    rental, _ = Rental.objects.get_or_create(
        car=car,
        customer=customer,
        start_date=start,
        end_date=end,
        defaults={
            "daily_rate": demo_rate,
            "total_price": demo_rate * Decimal(rental_days),
            "status": "active",
        },
    )

    html_body_path = Path("sample_data/contract_templates/sample_contract.html")
    html_body = html_body_path.read_text() if html_body_path.exists() else ""

    ContractTemplate.objects.get_or_create(
        name="Sample HTML Contract",
        format="html",
        defaults={
            "body_html": html_body,
            "description": "Simple HTML template for testing contract generation.",
        },
    )

    print("Seeded demo data:")
    print(f"- Car: {car}")
    print(f"- Customer: {customer}")
    print(f"- Rental: {rental}")
    print("- Contract template: Sample HTML Contract (html)")


if __name__ == "__main__":
    main()

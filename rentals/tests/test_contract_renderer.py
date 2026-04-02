from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from rentals.models import Car, ContractTemplate, Customer, Rental
from rentals.services.contract_renderer import render_html_template


class ContractRendererTests(SimpleTestCase):
    def _make_customer(self, full_name: str, driving_since: date, *, year_only: bool) -> Customer:
        return Customer(
            full_name=full_name,
            phone="+79990000000",
            license_number="12 34 567890",
            driving_since=driving_since,
            driving_since_year_only=year_only,
        )

    def _make_car(self) -> Car:
        return Car(
            plate_number="A001AA82",
            make="Hyundai",
            model="Solaris",
            year=2022,
            daily_rate=Decimal("3000.00"),
        )

    def _make_rental(self, customer: Customer, *, second_driver: Customer | None = None) -> Rental:
        return Rental(
            car=self._make_car(),
            customer=customer,
            second_driver=second_driver,
            start_date=date(2026, 4, 2),
            end_date=date(2026, 4, 5),
            daily_rate=Decimal("3000.00"),
            total_price=Decimal("9000.00"),
        )

    def test_render_html_template_uses_year_only_for_customer_driving_since(self):
        customer = self._make_customer("Иванов Иван", date(2018, 1, 1), year_only=True)
        rental = self._make_rental(customer)
        template = ContractTemplate(
            name="test",
            format="html",
            body_html="Стаж: {{ customer.driving_since }}",
        )

        rendered = render_html_template(template, rental)

        self.assertEqual(rendered, "Стаж: 2018")

    def test_render_html_template_uses_year_only_for_second_driver_driving_since(self):
        customer = self._make_customer("Иванов Иван", date(2018, 1, 1), year_only=True)
        second_driver = self._make_customer("Петров Петр", date(2014, 1, 1), year_only=True)
        rental = self._make_rental(customer, second_driver=second_driver)
        template = ContractTemplate(
            name="test",
            format="html",
            body_html="Основной: {{ customer.driving_since }}; второй: {{ second_driver.driving_since }}",
        )

        rendered = render_html_template(template, rental)

        self.assertEqual(rendered, "Основной: 2018; второй: 2014")

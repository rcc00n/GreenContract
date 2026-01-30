from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from rentals.models import Customer


class CustomerSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="pass")
        self.client.force_login(self.user)
        self.url = reverse("rentals:customer_search")

    def test_search_matches_name_parts_any_order(self):
        Customer.objects.create(
            full_name="Ivanov Ivan Ivanovich",
            phone="79990001122",
            license_number="11 22 333444",
        )

        response = self.client.get(self.url, {"q": "Ivan Ivanov"})
        payload = response.json()
        names = [item["name"] for item in payload["results"]]

        self.assertIn("Ivanov Ivan Ivanovich", names)

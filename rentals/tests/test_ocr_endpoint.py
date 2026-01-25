from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from rentals.ocr.ru_dl.schema import build_fields


class OCRDriverLicenseEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="pass")
        self.client.force_login(self.user)
        self.url = reverse("rentals:ocr_driver_license")

    def test_no_images_returns_failed(self):
        response = self.client.post(self.url)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(payload["warnings"])

    def test_only_front_returns_ok_or_partial(self):
        fields = build_fields(
            {
                "full_name": ("Ivanov Ivan", 0.92),
                "birth_date": ("1990-05-12", 0.88),
                "license_number": ("12 34 567890", 0.9),
            }
        )
        mocked_response = {
            "request_id": "ocr_test",
            "document_type": "ru_driver_license",
            "status": "partial",
            "fields": fields,
            "missing_fields": [],
            "warnings": [],
            "images": [],
            "debug": {"front_raw": {}, "back_raw": {}, "raw_text": ""},
        }
        with patch("rentals.views.extract_ru_dl", return_value=mocked_response):
            front_file = SimpleUploadedFile("front.jpg", b"fake", content_type="image/jpeg")
            response = self.client.post(self.url, {"front_image": front_file})
        payload = response.json()
        self.assertIn(payload["status"], {"ok", "partial"})

    def test_bad_image_bytes_fails_gracefully(self):
        front_file = SimpleUploadedFile("front.jpg", b"not-an-image", content_type="image/jpeg")
        response = self.client.post(self.url, {"front_image": front_file})
        payload = response.json()
        self.assertEqual(payload["status"], "failed")

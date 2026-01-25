from django.test import SimpleTestCase

from rentals.ocr.ru_dl.parse import (
    determine_status,
    normalize_date,
    normalize_license_number,
    parse_categories,
    parse_front,
)


class OCRParseTests(SimpleTestCase):
    def test_normalize_date(self):
        self.assertEqual(normalize_date("12.05.1990"), "1990-05-12")
        self.assertEqual(normalize_date("01/02/05"), "2005-02-01")
        self.assertIsNone(normalize_date("32.13.2020"))

    def test_license_number_normalization(self):
        self.assertEqual(normalize_license_number("12 34 567890"), "12 34 567890")
        self.assertEqual(normalize_license_number("1234567890"), "12 34 567890")
        self.assertIsNone(normalize_license_number("ABCD"))

    def test_category_parsing(self):
        text = "Categories: A B C1 BE M"
        self.assertEqual(parse_categories(text), ["A", "B", "C1", "BE", "M"])

    def test_status_logic(self):
        fields = {
            "full_name": {"value": "Ivanov Ivan", "confidence": 0.92},
            "birth_date": {"value": "1990-05-12", "confidence": 0.88},
            "license_number": {"value": "12 34 567890", "confidence": 0.9},
        }
        status, missing, low_conf = determine_status(fields)
        self.assertEqual(status, "ok")
        self.assertEqual(missing, [])
        self.assertEqual(low_conf, [])

        fields["license_number"]["confidence"] = 0.5
        status, _, low_conf = determine_status(fields)
        self.assertEqual(status, "partial")
        self.assertIn("license_number", low_conf)

        fields["birth_date"]["value"] = None
        status, missing, _ = determine_status(fields)
        self.assertEqual(status, "partial")
        self.assertIn("birth_date", missing)

    def test_full_name_prefers_line_when_surname_missing(self):
        rois = {
            "surname": {"text": "", "confidence": 0.1},
            "name": {"text": "АНДРЕЙ АНАТОЛЬЕВИЧ", "confidence": 0.92},
            "patronymic": {"text": "", "confidence": 0.0},
            "full_name_line": {"text": "ГАЦКО АНДРЕЙ АНАТОЛЬЕВИЧ", "confidence": 0.88},
        }
        parsed = parse_front(rois)
        self.assertEqual(parsed["full_name"][0], "ГАЦКО АНДРЕЙ АНАТОЛЬЕВИЧ")

    def test_issuer_ignores_latin_text(self):
        rois = {
            "license_issued_by": {"text": "GIBDD 8210", "confidence": 0.9},
        }
        parsed = parse_front(rois)
        self.assertIsNone(parsed["license_issued_by"][0])

        rois = {
            "license_issued_by": {"text": "ГИБДД 8210 / GIBDD 8210", "confidence": 0.9},
        }
        parsed = parse_front(rois)
        self.assertEqual(parsed["license_issued_by"][0], "ГИБДД 8210")

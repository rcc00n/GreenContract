"""Parse OCR ROI results for RU driver license."""

from __future__ import annotations

import re
from datetime import datetime

DATE_PATTERN = re.compile(r"(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{2,4})")

CATEGORY_PATTERN = re.compile(r"\b(A1|B1|C1|D1|BE|CE|DE|A|B|C|D|M|TM|TB)\b", re.IGNORECASE)

REQUIRED_FIELDS = {"full_name", "birth_date", "license_number"}


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_date(text: str | None) -> str | None:
    if not text:
        return None
    iso_match = re.search(r"(\\d{4})-(\\d{2})-(\\d{2})", text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            parsed = datetime(year, month, day).date()
        except ValueError:
            return None
        return parsed.strftime("%Y-%m-%d")
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    day = int(day)
    month = int(month)
    year = int(year)
    if year < 100:
        year = 2000 + year if year < 30 else 1900 + year
    try:
        parsed = datetime(year, month, day).date()
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def normalize_license_number(text: str | None) -> str | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if len(digits) >= 10:
        formatted = f"{digits[:2]} {digits[2:4]} {digits[4:10]}"
    else:
        formatted = digits
    return formatted.strip()


def parse_categories(text: str | None) -> list[str]:
    if not text:
        return []
    categories: list[str] = []
    for match in CATEGORY_PATTERN.findall(text.upper()):
        normalized = match.upper()
        if normalized not in categories:
            categories.append(normalized)
    return categories


def _avg_conf(confidences: list[float]) -> float:
    values = [c for c in confidences if c is not None]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _roi_text(rois: dict, key: str) -> str:
    return normalize_whitespace(rois.get(key, {}).get("text", ""))


def _roi_conf(rois: dict, key: str) -> float:
    return float(rois.get(key, {}).get("confidence", 0.0) or 0.0)


def parse_front(rois: dict) -> dict[str, tuple[object, float]]:
    surname = _roi_text(rois, "surname")
    name = _roi_text(rois, "name")
    patronymic = _roi_text(rois, "patronymic")
    full_name_line = _roi_text(rois, "full_name_line")

    name_parts = [part for part in (surname, name, patronymic) if part]
    if name_parts:
        full_name = " ".join(name_parts)
        name_conf = _avg_conf([
            _roi_conf(rois, "surname"),
            _roi_conf(rois, "name"),
            _roi_conf(rois, "patronymic"),
        ])
    elif full_name_line:
        full_name = full_name_line
        name_conf = _roi_conf(rois, "full_name_line")
    else:
        full_name = None
        name_conf = 0.0

    birth_date_raw = _roi_text(rois, "birth_date")
    birth_date = normalize_date(birth_date_raw)
    birth_conf = _roi_conf(rois, "birth_date")

    license_number_raw = _roi_text(rois, "license_number")
    license_number = normalize_license_number(license_number_raw)
    license_conf = _roi_conf(rois, "license_number")

    license_issued_by = _roi_text(rois, "license_issued_by")
    license_issued_by = license_issued_by or None
    license_issued_by_conf = _roi_conf(rois, "license_issued_by")

    driving_since_raw = _roi_text(rois, "driving_since")
    driving_since = normalize_date(driving_since_raw)
    driving_conf = _roi_conf(rois, "driving_since")

    return {
        "full_name": (full_name, name_conf),
        "birth_date": (birth_date, birth_conf),
        "license_number": (license_number, license_conf),
        "license_issued_by": (license_issued_by, license_issued_by_conf),
        "driving_since": (driving_since, driving_conf),
    }


def parse_back(rois: dict) -> dict[str, tuple[object, float]]:
    categories_text = _roi_text(rois, "categories") or _roi_text(rois, "raw_text")
    categories = parse_categories(categories_text)
    categories_conf = _roi_conf(rois, "categories") or _roi_conf(rois, "raw_text")

    special_marks = _roi_text(rois, "special_marks")
    if not special_marks:
        special_marks = None
    special_marks_conf = _roi_conf(rois, "special_marks")

    return {
        "categories": (categories, categories_conf),
        "special_marks": (special_marks, special_marks_conf),
    }


def determine_status(fields: dict, required_fields: set[str] | None = None, min_confidence: float = 0.75):
    if required_fields is None:
        required_fields = REQUIRED_FIELDS

    missing = []
    low_conf = []
    for name in required_fields:
        field = fields.get(name) or {}
        value = field.get("value")
        conf = field.get("confidence") or 0.0
        if value in (None, "", []):
            missing.append(name)
        elif conf < min_confidence:
            low_conf.append(name)

    status = "ok"
    if missing or low_conf:
        status = "partial"
    return status, missing, low_conf

"""Parse OCR ROI results for RU driver license."""

from __future__ import annotations

import re
from datetime import datetime

DATE_PATTERN = re.compile(r"(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{2,4})")

CATEGORY_PATTERN = re.compile(r"\b(A1|B1|C1|D1|BE|CE|DE|A|B|C|D|M|TM|TB)\b", re.IGNORECASE)

REQUIRED_FIELDS = {"full_name", "birth_date", "license_number"}

CYRILLIC_RE = re.compile(r"[\u0410-\u042f\u0401\u0430-\u044f\u0451]")
LABEL_PREFIX_RE = re.compile(r"^\s*\d+[a-zA-Z]?[.)]?\s*")
DRIVING_MARKERS = (
    "\u0421\u0422\u0410\u0416",  # СТАЖ
    "STAZH",
    "SINCE",
)
STOPWORDS = (
    "\u0412\u041e\u0414\u0418\u0422\u0415\u041b",  # ВОДИТЕЛ
    "\u0423\u0414\u041e\u0421\u0422\u041e\u0412",  # УДОСТОВ
    "\u0420\u0415\u0421\u041f",  # РЕСП
    "\u041e\u0411\u041b",  # ОБЛ
    "\u041a\u0420\u0410\u0419",  # КРАЙ
    "\u0413\u0418\u0411\u0414\u0414",  # ГИБДД
    "\u0420\u041e\u0421\u0421",  # РОСС
    "\u0424\u0415\u0414\u0415\u0420",  # ФЕДЕР
    "DRIVING",
    "LICENCE",
    "LICENSE",
    "PERMIS",
    "RUS",
)

DIGIT_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "l": "1",
        "L": "1",
        "Z": "2",
        "S": "5",
        "s": "5",
        "B": "8",
        "G": "6",
        "g": "6",
        "T": "7",
    }
)

LATIN_TO_CYR = str.maketrans(
    {
        "A": "\u0410",
        "a": "\u0430",
        "B": "\u0412",
        "b": "\u0432",
        "E": "\u0415",
        "e": "\u0435",
        "K": "\u041a",
        "k": "\u043a",
        "M": "\u041c",
        "m": "\u043c",
        "H": "\u041d",
        "h": "\u043d",
        "O": "\u041e",
        "o": "\u043e",
        "P": "\u0420",
        "p": "\u0440",
        "C": "\u0421",
        "c": "\u0441",
        "T": "\u0422",
        "t": "\u0442",
        "X": "\u0425",
        "x": "\u0445",
        "Y": "\u0423",
        "y": "\u0443",
    }
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _normalize_digit_ocr(text: str | None) -> str:
    if not text:
        return ""
    return (text or "").translate(DIGIT_TRANSLATION)


def normalize_date(text: str | None) -> str | None:
    if not text:
        return None
    text = _normalize_digit_ocr(text)
    text = text.replace(",", ".")
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
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
    digits = re.sub(r"\D", "", _normalize_digit_ocr(text))
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


def _strip_label_prefix(text: str) -> str:
    return LABEL_PREFIX_RE.sub("", text or "").strip()


def _clean_name_line(text: str) -> str:
    cleaned = _strip_label_prefix(text)
    cleaned = cleaned.translate(LATIN_TO_CYR)
    cleaned = re.sub(r"[•·]", " ", cleaned)
    cleaned = re.sub(r"[^A-Za-z\u0410-\u044f\u0401\u0451\s-]", " ", cleaned)
    if CYRILLIC_RE.search(cleaned):
        cleaned = re.sub(r"[A-Za-z]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    return cleaned


def _has_driving_marker(text: str) -> bool:
    upper = (text or "").upper()
    return any(marker in upper for marker in DRIVING_MARKERS)


def parse_front(rois: dict, context_text: str | None = None) -> dict[str, tuple[object, float]]:
    surname = _clean_name_line(_roi_text(rois, "surname"))
    name = _clean_name_line(_roi_text(rois, "name"))
    patronymic = _clean_name_line(_roi_text(rois, "patronymic"))
    full_name_line = _clean_name_line(_roi_text(rois, "full_name_line"))

    if re.search(r"\d", patronymic):
        patronymic = ""

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

    birth_date_raw = _strip_label_prefix(_roi_text(rois, "birth_date"))
    birth_date = normalize_date(birth_date_raw)
    birth_conf = _roi_conf(rois, "birth_date")

    license_number_raw = _strip_label_prefix(_roi_text(rois, "license_number"))
    license_number = normalize_license_number(license_number_raw)
    license_conf = _roi_conf(rois, "license_number")

    license_issued_by = _strip_label_prefix(_roi_text(rois, "license_issued_by"))
    license_issued_by = license_issued_by or None
    license_issued_by_conf = _roi_conf(rois, "license_issued_by")

    driving_since_raw = _strip_label_prefix(_roi_text(rois, "driving_since"))
    driving_since = normalize_date(driving_since_raw)
    driving_conf = _roi_conf(rois, "driving_since")
    if driving_since and context_text and not _has_driving_marker(context_text):
        driving_since = None
        driving_conf = 0.0

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


def parse_front_from_text(raw_text: str | None, base_conf: float | None = None) -> dict[str, tuple[object, float]]:
    if not raw_text:
        return {}

    lines = [normalize_whitespace(line) for line in raw_text.splitlines() if line.strip()]
    upper_lines = [line.upper() for line in lines]

    def _is_name_line(line: str, upper_line: str) -> bool:
        normalized = _clean_name_line(line)
        if not CYRILLIC_RE.search(normalized):
            return False
        if re.search(r"\d", line):
            return False
        if any(word in upper_line for word in STOPWORDS):
            return False
        return True

    name_candidates: list[str] = []
    for line, upper_line in zip(lines, upper_lines):
        if not _is_name_line(line, upper_line):
            continue
        cleaned = _clean_name_line(line)
        if cleaned and cleaned not in name_candidates:
            name_candidates.append(cleaned)

    full_name = None
    if len(name_candidates) >= 2:
        full_name = " ".join(name_candidates[:3])
    elif name_candidates:
        full_name = name_candidates[0]

    dates = []
    for line in lines:
        date = normalize_date(line)
        if date:
            dates.append(date)
    birth_date = min(dates) if dates else None

    driving_since = None
    if _has_driving_marker(raw_text):
        for idx, line in enumerate(lines):
            if _has_driving_marker(line):
                driving_since = normalize_date(line)
                if not driving_since and idx + 1 < len(lines):
                    driving_since = normalize_date(lines[idx + 1])
                break

    license_number = None
    match = re.search(r"\b\d{2}\s?\d{2}\s?\d{6}\b", raw_text)
    if match:
        license_number = normalize_license_number(match.group(0))
    else:
        long_digits = re.findall(r"\d{10,}", raw_text)
        if long_digits:
            license_number = normalize_license_number(long_digits[0])

    license_issued_by = None
    for line, upper_line in zip(lines, upper_lines):
        if (
            "\u0413\u0418\u0411\u0414\u0414" in upper_line
            or "GIBDD" in upper_line
            or "\u041c\u0420\u042d\u041e" in upper_line
        ):
            license_issued_by = _strip_label_prefix(line)
            break

    base = base_conf or 0.55
    base = max(0.4, min(base, 0.85))
    name_conf = round(base * 0.85, 3)
    date_conf = round(base * 0.8, 3)
    license_conf = round(base * 0.9, 3)
    issued_conf = round(base * 0.75, 3)

    return {
        "full_name": (full_name, name_conf),
        "birth_date": (birth_date, date_conf),
        "license_number": (license_number, license_conf),
        "license_issued_by": (license_issued_by, issued_conf),
        "driving_since": (driving_since, date_conf),
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

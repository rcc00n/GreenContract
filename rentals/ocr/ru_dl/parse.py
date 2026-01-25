"""Parse OCR ROI results for RU driver license."""

from __future__ import annotations

import difflib
import os
import re
from datetime import datetime

DATE_PATTERN = re.compile(r"(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{2,4})")

CATEGORY_PATTERN = re.compile(r"\b(A1|B1|C1|D1|BE|CE|DE|A|B|C|D|M|TM|TB)\b", re.IGNORECASE)

REQUIRED_FIELDS = {"full_name", "birth_date", "license_number"}

CYRILLIC_RE = re.compile(r"[\u0410-\u042f\u0401\u0430-\u044f\u0451]")
LABEL_PREFIX_RE = re.compile(r"^\s*\d+[a-zA-Z]?[.)]?\s*")
DRIVING_MARKERS = (
    "\u0421\u0422\u0410\u0416",  # СТАЖ
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

NAME_CLEAN_RE = re.compile(r"[^A-Za-z\u0410-\u044f\u0401\u0451\s-]")

ISSUER_CLEAN_RE = re.compile(r"[^0-9\u0410-\u044f\u0401\u0451\s-]")

_NAME_DICTIONARY: dict[str, int] | None = None

MARKER_TRANSLATION = str.maketrans(
    {
        "\u0410": "A",  # А
        "\u0412": "B",  # В
        "\u0411": "B",  # Б
    }
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
    normalized = _normalize_digit_ocr(text)
    match = re.search(r"\d{10}", normalized)
    if match:
        digits = match.group(0)
    else:
        digits = re.sub(r"\D", "", normalized)
    if len(digits) != 10:
        return None
    formatted = f"{digits[:2]} {digits[2:4]} {digits[4:10]}"
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
    cleaned = re.sub(r"[•·]", " ", cleaned)
    cleaned = re.sub(r"[^A-Za-z\u0410-\u044f\u0401\u0451\s-]", " ", cleaned)
    if not CYRILLIC_RE.search(cleaned):
        return ""
    cleaned = cleaned.translate(LATIN_TO_CYR)
    cleaned = re.sub(r"[A-Za-z]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    return cleaned


def _load_name_dictionary() -> dict[str, int]:
    global _NAME_DICTIONARY
    if _NAME_DICTIONARY is not None:
        return _NAME_DICTIONARY
    path = os.environ.get("OCR_NAME_DICTIONARY_PATH")
    if not path:
        _NAME_DICTIONARY = {}
        return _NAME_DICTIONARY
    data: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                word = parts[0].upper()
                freq = 1
                if len(parts) > 1 and parts[1].isdigit():
                    freq = int(parts[1])
                if word:
                    data[word] = max(freq, data.get(word, 0))
    except Exception:
        data = {}
    _NAME_DICTIONARY = data
    return _NAME_DICTIONARY


def _correct_name_token(token: str, dictionary: dict[str, int]) -> str:
    if token in dictionary or len(token) < 3:
        return token
    candidates = [word for word in dictionary.keys() if abs(len(word) - len(token)) <= 2]
    best = difflib.get_close_matches(token, candidates, n=1, cutoff=0.88)
    return best[0] if best else token


def _apply_name_dictionary(text: str) -> str:
    if not text:
        return text
    dictionary = _load_name_dictionary()
    if not dictionary:
        return text
    tokens = text.split()
    corrected = [_correct_name_token(token.upper(), dictionary) for token in tokens]
    return " ".join(corrected)


def _extract_marker_date(raw_text: str | None, markers: tuple[str, ...]) -> str | None:
    if not raw_text:
        return None
    lines = [normalize_whitespace(line) for line in raw_text.splitlines() if line.strip()]
    normalized_lines = [
        re.sub(r"\s+", "", line.upper()).translate(MARKER_TRANSLATION) for line in lines
    ]
    for idx, normalized in enumerate(normalized_lines):
        if any(marker in normalized for marker in markers):
            date = normalize_date(lines[idx])
            if not date and idx + 1 < len(lines):
                date = normalize_date(lines[idx + 1])
            if date:
                return date
    return None


def _clean_issuer_line(text: str) -> str:
    cleaned = _strip_label_prefix(text)
    if not cleaned:
        return ""
    cleaned = cleaned.translate(LATIN_TO_CYR)
    cleaned = ISSUER_CLEAN_RE.sub(" ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    if not CYRILLIC_RE.search(cleaned):
        return ""
    tokens = cleaned.split()
    deduped: list[str] = []
    for token in tokens:
        if not deduped or deduped[-1] != token:
            deduped.append(token)
    cleaned = " ".join(deduped)
    return cleaned


def _has_driving_marker(text: str) -> bool:
    upper = (text or "").upper()
    return any(marker in upper for marker in DRIVING_MARKERS)


def _name_quality(text: str) -> float:
    if not text:
        return 0.0
    cleaned = NAME_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return 0.0
    if re.search(r"\d", cleaned):
        return 0.0
    upper = cleaned.upper()
    if any(word in upper for word in STOPWORDS):
        return 0.0
    letters = re.findall(r"[A-Za-z\u0410-\u044f\u0401\u0451]", cleaned)
    if not letters:
        return 0.0
    cyrillic = [ch for ch in letters if CYRILLIC_RE.search(ch)]
    ratio = len(cyrillic) / len(letters)
    return round(ratio, 3)


def parse_front(rois: dict, context_text: str | None = None) -> dict[str, tuple[object, float]]:
    surname = _apply_name_dictionary(_clean_name_line(_roi_text(rois, "surname")))
    name = _apply_name_dictionary(_clean_name_line(_roi_text(rois, "name")))
    patronymic = _apply_name_dictionary(_clean_name_line(_roi_text(rois, "patronymic")))
    full_name_line = _apply_name_dictionary(_clean_name_line(_roi_text(rois, "full_name_line")))

    if re.search(r"\d", patronymic):
        patronymic = ""

    name_parts = [part for part in (surname, name, patronymic) if part]
    full_name = None
    name_conf = 0.0
    if name_parts:
        full_name = " ".join(name_parts)
        name_conf = _avg_conf([
            _roi_conf(rois, "surname"),
            _roi_conf(rois, "name"),
            _roi_conf(rois, "patronymic"),
        ])
    if full_name_line:
        line_conf = _roi_conf(rois, "full_name_line")
        if not full_name:
            full_name = full_name_line
            name_conf = line_conf
        else:
            parts_quality = _name_quality(full_name)
            line_quality = _name_quality(full_name_line)
            parts_words = len(full_name.split())
            line_words = len(full_name_line.split())
            if not surname and line_words >= parts_words:
                full_name = full_name_line
                name_conf = line_conf
            elif line_words > parts_words:
                full_name = full_name_line
                name_conf = line_conf
            elif line_quality > parts_quality + 0.1:
                full_name = full_name_line
                name_conf = line_conf

    birth_date_raw = _strip_label_prefix(_roi_text(rois, "birth_date"))
    birth_date = normalize_date(birth_date_raw)
    birth_conf = _roi_conf(rois, "birth_date")

    license_number_raw = _strip_label_prefix(_roi_text(rois, "license_number"))
    license_number = normalize_license_number(license_number_raw)
    license_conf = _roi_conf(rois, "license_number")

    license_issued_by = _clean_issuer_line(_roi_text(rois, "license_issued_by"))
    license_issued_by = license_issued_by or None
    license_issued_by_conf = _roi_conf(rois, "license_issued_by")
    if license_issued_by:
        upper = license_issued_by.upper()
        if not any(marker in upper for marker in ("\u0413\u0418\u0411\u0414\u0414", "\u041c\u0420\u042d\u041e")):
            license_issued_by = None
            license_issued_by_conf = 0.0

    driving_since_raw = _strip_label_prefix(_roi_text(rois, "driving_since"))
    driving_since = normalize_date(driving_since_raw)
    driving_conf = _roi_conf(rois, "driving_since")
    if driving_since and birth_date and driving_since <= birth_date:
        driving_since = None
        driving_conf = 0.0
    if driving_since and context_text:
        valid_until = _extract_marker_date(context_text, ("4B",))
        if valid_until and driving_since >= valid_until:
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
    if full_name:
        full_name = _apply_name_dictionary(full_name)

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
        if "\u0413\u0418\u0411\u0414\u0414" in upper_line or "\u041c\u0420\u042d\u041e" in upper_line:
            license_issued_by = _clean_issuer_line(line)
            if license_issued_by:
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

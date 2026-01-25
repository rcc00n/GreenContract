"""Response builders for RU driver license OCR."""

from __future__ import annotations

from typing import Any

FIELD_DEFAULTS: dict[str, Any] = {
    "full_name": None,
    "birth_date": None,
    "license_number": None,
    "license_issued_by": None,
    "driving_since": None,
    "categories": [],
    "special_marks": None,
}


def build_fields(parsed: dict[str, tuple[object, float]] | None) -> dict[str, dict[str, object]]:
    parsed = parsed or {}
    fields: dict[str, dict[str, object]] = {}
    for name, default in FIELD_DEFAULTS.items():
        value, confidence = parsed.get(name, (default, 0.0))
        if value in (None, "") and isinstance(default, list):
            value = []
        fields[name] = {
            "value": value,
            "confidence": float(confidence or 0.0),
        }
    return fields


def build_response(
    *,
    request_id: str,
    status: str,
    fields: dict[str, dict[str, object]],
    missing_fields: list[str],
    warnings: list[str],
    images: list[dict[str, object]],
    debug: dict[str, object],
):
    return {
        "request_id": request_id,
        "document_type": "ru_driver_license",
        "status": status,
        "fields": fields,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "images": images,
        "debug": debug,
    }


def build_failure(
    *,
    request_id: str,
    reason: str,
    warnings: list[str] | None = None,
    images: list[dict[str, object]] | None = None,
    debug: dict[str, object] | None = None,
):
    warnings_list = warnings or []
    warnings_list.append(reason)
    return build_response(
        request_id=request_id,
        status="failed",
        fields=build_fields({}),
        missing_fields=list(FIELD_DEFAULTS.keys()),
        warnings=warnings_list,
        images=images or [],
        debug=debug or {"front_raw": {}, "back_raw": {}, "raw_text": ""},
    )

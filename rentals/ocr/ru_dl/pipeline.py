"""Pipeline entrypoint for RU driver license OCR."""

from __future__ import annotations

import logging
import os
import re
import uuid

from django.conf import settings

from rentals.ocr.storage import compute_sha256, store_upload

from .doc_detect import detect_and_warp
from .keypoint_detect import detect_keypoints, warp_with_keypoints
from .ocr_engine import run_ocr, run_ocr_with_boxes
from .parse import (
    REQUIRED_FIELDS,
    _name_quality,
    _strip_latin_words,
    determine_status,
    normalize_date,
    parse_back,
    parse_front,
    parse_front_from_text,
)
from .preprocess import preprocess_variants
from .rois import (
    BACK_ROIS,
    CANVAS_SIZE,
    DEFAULT_FRONT_TEMPLATE,
    FRONT_ANCHORS,
    FRONT_ROI_TEMPLATES,
    Roi,
)
from .schema import build_failure, build_fields, build_response

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    cv2 = None
    np = None

try:
    from PIL import Image
    import io
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    Image = None
    io = None

logger = logging.getLogger(__name__)

DATE_FIELDS = {"birth_date", "driving_since"}
NAME_FIELDS = {"surname", "name", "patronymic", "full_name_line"}
DETECT_FIELDS = {"raw_text", "full_name_line"}

FIELD_THRESHOLDS = {
    "birth_date": 0.78,
    "driving_since": 0.78,
    "license_number": 0.8,
    "license_issued_by": 0.7,
    "surname": 0.75,
    "name": 0.75,
    "patronymic": 0.7,
    "full_name_line": 0.7,
}


def _roi_variants(roi: Roi, field: str) -> list[Roi]:
    variants = [roi]

    def _expand(r: Roi, dx: int, dy: int) -> Roi:
        return Roi(r.name, r.x - dx, r.y - dy, r.w + 2 * dx, r.h + 2 * dy)

    def _shift(r: Roi, dx: int, dy: int) -> Roi:
        return Roi(r.name, r.x + dx, r.y + dy, r.w, r.h)

    variants.append(_expand(roi, 10, 8))
    if field in NAME_FIELDS:
        variants.extend([
            _shift(roi, 0, -12),
            _shift(roi, 0, 12),
            _expand(roi, 18, 10),
        ])
    if field in DATE_FIELDS:
        variants.extend([
            _shift(roi, 0, -8),
            _shift(roi, 0, 8),
            _expand(roi, 14, 8),
        ])
    if field == "license_number":
        variants.extend([
            _shift(roi, 0, 10),
            _expand(roi, 20, 10),
        ])
    if field == "license_issued_by":
        variants.append(_expand(roi, 20, 12))

    unique = {}
    for item in variants:
        key = (item.x, item.y, item.w, item.h)
        unique[key] = item
    return list(unique.values())


def _score_text(field: str, text: str, conf: float) -> float:
    if not text:
        return -1.0
    score = conf
    if field in NAME_FIELDS:
        name_quality = _name_quality(text)
        score += 0.25 * name_quality
        if name_quality == 0:
            score -= 0.2
    if field in DATE_FIELDS:
        date = normalize_date(text)
        score += 0.25 if date else -0.15
    if field == "license_number":
        digits = re.sub(r"\D", "", text or "")
        if len(digits) == 10:
            score += 0.3
        else:
            score -= 0.15
        if normalize_date(text):
            score -= 0.3
    if field == "license_issued_by":
        upper = (text or "").upper()
        if "\u0413\u0418\u0411\u0414\u0414" in upper or "GIBDD" in upper:
            score += 0.2
    return score


def _is_good_enough(field: str, text: str, conf: float) -> bool:
    threshold = FIELD_THRESHOLDS.get(field, 0.8)
    if conf < threshold:
        return False
    if field in DATE_FIELDS and not normalize_date(text):
        return False
    if field == "license_number":
        digits = re.sub(r"\D", "", text or "")
        if len(digits) != 10:
            return False
    if field in NAME_FIELDS and _name_quality(text) < 0.4:
        return False
    return True


def _merge_texts(texts: list[tuple[str, float]]) -> tuple[str, float]:
    if not texts:
        return "", 0.0
    merged_text = " ".join(text.strip() for text, _ in texts if text).strip()
    avg_conf = round(sum(conf for _, conf in texts) / len(texts), 3) if texts else 0.0
    return merged_text, avg_conf


def _pick_text_for_field(field: str, texts: list[tuple[str, float]]) -> tuple[str, float]:
    if not texts:
        return "", 0.0
    if field in NAME_FIELDS:
        scored = []
        for text, conf in texts:
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            score = _score_text(field, cleaned, conf)
            scored.append((score, conf, cleaned))
        if not scored:
            return _merge_texts(texts)
        scored.sort(key=lambda item: item[0], reverse=True)
        if field == "full_name_line" and len(scored) >= 2:
            best = scored[: min(3, len(scored))]
            merged = " ".join(item[2] for item in best)
            avg_conf = round(sum(item[1] for item in best) / len(best), 3)
            return merged, avg_conf
        return scored[0][2], float(scored[0][1])
    return _merge_texts(texts)


def _require_cv2():
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV and numpy are required for OCR pipeline.")


def _decode_image(data: bytes):
    if not data:
        return None
    if Image is not None and io is not None:
        if np is None:
            raise RuntimeError("numpy is required for OCR pipeline.")
        try:
            image = Image.open(io.BytesIO(data))
            try:
                exif = image._getexif() or {}
                orientation = exif.get(274)
                if orientation == 3:
                    image = image.rotate(180, expand=True)
                elif orientation == 6:
                    image = image.rotate(270, expand=True)
                elif orientation == 8:
                    image = image.rotate(90, expand=True)
            except Exception:
                pass
            image = image.convert("RGB")
            return np.array(image)[:, :, ::-1]
        except Exception:
            pass

    _require_cv2()
    array = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _limit_size(image_bgr, max_dim: int = 2000):
    _require_cv2()
    height, width = image_bgr.shape[:2]
    scale = max(height, width) / float(max_dim)
    if scale <= 1:
        return image_bgr
    new_size = (int(width / scale), int(height / scale))
    return cv2.resize(image_bgr, new_size)


def _ocr_rois(processed_variants: list, rois: dict):
    results: dict[str, dict[str, object]] = {}
    for name, roi in rois.items():
        base_image = processed_variants[0]
        x, y, w, h = roi.x, roi.y, roi.w, roi.h
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(base_image.shape[1], x + w)
        y2 = min(base_image.shape[0], y + h)
        crop = base_image[y1:y2, x1:x2]
        if crop.size == 0:
            results[name] = {"text": "", "confidence": 0.0}
            continue
        detect = name in DETECT_FIELDS
        try:
            base_texts = run_ocr(crop, detect=detect)
        except Exception as exc:
            logger.warning("OCR failed on ROI %s: %s", name, exc)
            results[name] = {"text": "", "confidence": 0.0}
            continue
        base_text, base_conf = _pick_text_for_field(name, base_texts)
        if _is_good_enough(name, base_text, base_conf):
            results[name] = {"text": base_text, "confidence": base_conf}
            continue

        best_score = _score_text(name, base_text, base_conf)
        best_text = base_text
        best_conf = base_conf

        for variant in _roi_variants(roi, name):
            for image in processed_variants:
                x1 = max(0, variant.x)
                y1 = max(0, variant.y)
                x2 = min(image.shape[1], variant.x + variant.w)
                y2 = min(image.shape[0], variant.y + variant.h)
                crop = image[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    texts = run_ocr(crop, detect=detect)
                except Exception as exc:
                    logger.warning("OCR failed on ROI %s: %s", name, exc)
                    continue
                if not texts:
                    continue
                candidate_text, candidate_conf = _pick_text_for_field(name, texts)
                candidate_score = _score_text(name, candidate_text, candidate_conf)
                if candidate_score > best_score:
                    best_score = candidate_score
                    best_text = candidate_text
                    best_conf = candidate_conf

        results[name] = {"text": best_text or "", "confidence": float(best_conf or 0.0)}
    return results


def _is_missing_value(value: object) -> bool:
    return value in (None, "", [])


def _merge_parsed(primary: dict[str, tuple[object, float]], fallback: dict[str, tuple[object, float]]):
    merged = dict(primary or {})
    for key, payload in (fallback or {}).items():
        value, conf = payload
        if _is_missing_value(value):
            continue
        current = merged.get(key)
        if current is None or _is_missing_value(current[0]):
            merged[key] = (value, conf)
    return merged


def _collect_raw_text(rois: dict) -> str:
    parts = []
    for payload in rois.values():
        text = (payload.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _ocr_full_image(images: list) -> tuple[str, float]:
    best_text = ""
    best_conf = 0.0
    best_score = -1.0
    for image in images:
        try:
            texts = run_ocr(image, detect=True)
        except Exception as exc:
            logger.warning("OCR failed on full image: %s", exc)
            continue
        if not texts:
            continue
        merged_text = "\n".join(text.strip() for text, _ in texts if text).strip()
        avg_conf = round(sum(conf for _, conf in texts) / len(texts), 3) if texts else 0.0
        length_bonus = min(len(merged_text) / 500.0, 0.2)
        score = avg_conf + length_bonus
        if score > best_score:
            best_score = score
            best_text = merged_text
            best_conf = avg_conf
    return best_text, best_conf


ANCHOR_TRANSLATION = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "Б": "B",
    }
)


def _normalize_anchor_text(text: str) -> str | None:
    cleaned = re.sub(r"[^0-9A-Za-z\u0410-\u042f\u0430-\u044f]", "", text or "").upper()
    if not cleaned:
        return None
    cleaned = cleaned.translate(ANCHOR_TRANSLATION)
    if cleaned in {"1", "2", "3", "5"}:
        return cleaned
    if cleaned.startswith("4A"):
        return "4A"
    if cleaned.startswith("4B"):
        return "4B"
    return None


def _box_center(box) -> tuple[float, float] | None:
    try:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    except Exception:
        return None


def _detect_anchors(image_bgr) -> dict[str, tuple[float, float, float]]:
    anchors: dict[str, tuple[float, float, float]] = {}
    try:
        boxes = run_ocr_with_boxes(image_bgr)
    except Exception as exc:
        logger.warning("Anchor OCR failed: %s", exc)
        return anchors
    for item in boxes:
        label = _normalize_anchor_text(item.get("text") or "")
        if not label:
            continue
        center = _box_center(item.get("box"))
        if not center:
            continue
        conf = float(item.get("confidence") or 0.0)
        current = anchors.get(label)
        if current is None or conf > current[2]:
            anchors[label] = (center[0], center[1], conf)
    return anchors


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return 0.5 * (values[mid - 1] + values[mid])


def _compute_anchor_shift(
    anchors: dict[str, tuple[float, float, float]], expected: dict[str, tuple[float, float]]
) -> tuple[float, float, float, int] | None:
    dxs: list[float] = []
    dys: list[float] = []
    pairs: list[tuple[float, float, float, float]] = []
    for label, (exp_x, exp_y) in expected.items():
        obs = anchors.get(label)
        if not obs:
            continue
        dx = obs[0] - exp_x
        dy = obs[1] - exp_y
        dxs.append(dx)
        dys.append(dy)
        pairs.append((exp_x, exp_y, obs[0], obs[1]))
    if len(dxs) < 2:
        return None
    dx = _median(dxs)
    dy = _median(dys)
    errors = []
    for exp_x, exp_y, obs_x, obs_y in pairs:
        errors.append(abs((obs_x - dx) - exp_x) + abs((obs_y - dy) - exp_y))
    error = sum(errors) / len(errors) if errors else 0.0
    return dx, dy, error, len(dxs)


def _select_front_template(
    image_bgr,
    processed_variants: list,
) -> tuple[str, dict[str, Roi], tuple[float, float], dict[str, tuple[float, float, float]]]:
    anchors = _detect_anchors(image_bgr) if _should_use_anchors() else {}
    best_template = DEFAULT_FRONT_TEMPLATE
    best_shift = (0.0, 0.0)
    if anchors:
        best = None
        for name, expected in FRONT_ANCHORS.items():
            shift = _compute_anchor_shift(anchors, expected)
            if shift is None:
                continue
            dx, dy, error, count = shift
            score = (count, -error)
            if best is None or score > best[0]:
                best = (score, name, (dx, dy))
        if best:
            _, best_template, best_shift = best

    if not anchors and len(FRONT_ROI_TEMPLATES) > 1:
        scored = _score_front_templates(processed_variants)
        if scored is not None:
            best_template = scored

    rois = FRONT_ROI_TEMPLATES.get(best_template, FRONT_ROI_TEMPLATES[DEFAULT_FRONT_TEMPLATE])

    if best_shift != (0.0, 0.0):
        shifted: dict[str, Roi] = {}
        for key, roi in rois.items():
            x = max(0, int(round(roi.x + best_shift[0])))
            y = max(0, int(round(roi.y + best_shift[1])))
            shifted[key] = Roi(roi.name, x, y, roi.w, roi.h)
        rois = shifted

    return best_template, rois, best_shift, anchors


def _should_use_anchors() -> bool:
    value = getattr(settings, "OCR_USE_ANCHORS", None)
    if value is None:
        value = os.environ.get("OCR_USE_ANCHORS")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _score_front_templates(processed_variants: list) -> str | None:
    if not processed_variants:
        return None
    base_image = processed_variants[0]
    sample_fields = ("full_name_line", "birth_date", "license_number")
    best_name = None
    best_score = -1.0
    for name, rois in FRONT_ROI_TEMPLATES.items():
        total = 0.0
        count = 0
        for field in sample_fields:
            roi = rois.get(field)
            if roi is None:
                continue
            x1 = max(0, roi.x)
            y1 = max(0, roi.y)
            x2 = min(base_image.shape[1], roi.x + roi.w)
            y2 = min(base_image.shape[0], roi.y + roi.h)
            crop = base_image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            detect = field in DETECT_FIELDS
            try:
                texts = run_ocr(crop, detect=detect)
            except Exception:
                continue
            if not texts:
                continue
            text, conf = _pick_text_for_field(field, texts)
            total += _score_text(field, text, conf)
            count += 1
        if count == 0:
            continue
        avg_score = total / count
        if avg_score > best_score:
            best_score = avg_score
            best_name = name
    return best_name


def _should_use_keypoints() -> bool:
    value = getattr(settings, "OCR_USE_KEYPOINTS", None)
    if value is None:
        value = os.environ.get("OCR_USE_KEYPOINTS")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _try_keypoint_warp(image_bgr):
    model_path = getattr(settings, "OCR_KEYPOINT_MODEL_PATH", None) or os.environ.get(
        "OCR_KEYPOINT_MODEL_PATH"
    )
    if not model_path or not _should_use_keypoints():
        return None
    try:
        keypoints = detect_keypoints(image_bgr, model_path=model_path)
    except Exception as exc:
        logger.warning("Keypoint detection failed: %s", exc)
        return None
    if keypoints is None:
        return None
    try:
        return warp_with_keypoints(image_bgr, CANVAS_SIZE, keypoints)
    except Exception as exc:
        logger.warning("Keypoint warp failed: %s", exc)
        return None


def extract(front_bytes: bytes | None, back_bytes: bytes | None):
    request_id = f"ocr_{uuid.uuid4().hex[:10]}"
    warnings: list[str] = []
    images: list[dict[str, object]] = []
    debug = {"front_raw": {}, "back_raw": {}, "raw_text": ""}

    if not front_bytes and not back_bytes:
        return build_failure(request_id=request_id, reason="No images provided.")

    front_rois: dict[str, dict[str, object]] = {}
    back_rois: dict[str, dict[str, object]] = {}

    def _process_side(side: str, data: bytes | None, rois_def: dict, allow_missing: bool = False):
        if not data:
            if not allow_missing:
                warnings.append(f"{side.capitalize()} image missing.")
            return {}, None, "", {}
        image = _decode_image(data)
        if image is None:
            warnings.append(f"{side.capitalize()} image could not be decoded.")
            return {}, None, "", {}
        image = _limit_size(image)
        try:
            images.append(store_upload(image, request_id, side, data))
        except Exception as exc:
            warnings.append(f"Failed to store {side} image: {exc}")
            images.append({"role": side, "storage_url": None, "sha256": compute_sha256(data or b"")})
        alignment = "contour"
        warped = _try_keypoint_warp(image)
        if warped is not None:
            alignment = "keypoints"
        else:
            warped, used_fallback = detect_and_warp(image, CANVAS_SIZE)
            if used_fallback:
                alignment = "resize"
                warnings.append(f"{side.capitalize()} contour not detected; used resize fallback.")
        processed_variants = preprocess_variants(warped)
        meta: dict[str, object] = {"alignment": alignment}
        if side == "front":
            template_name, selected_rois, shift, anchors = _select_front_template(
                warped, processed_variants
            )
            rois = _ocr_rois(processed_variants, selected_rois)
            meta.update(
                {
                    "template": template_name,
                    "anchor_shift": {"dx": round(shift[0], 2), "dy": round(shift[1], 2)},
                    "anchors": anchors,
                }
            )
        else:
            rois = _ocr_rois(processed_variants, rois_def)
        return rois, processed_variants, _collect_raw_text(rois), meta

    try:
        front_rois, front_processed_variants, front_roi_text, front_meta = _process_side(
            "front", front_bytes, FRONT_ROI_TEMPLATES[DEFAULT_FRONT_TEMPLATE]
        )
        back_rois, _, back_roi_text, back_meta = _process_side(
            "back", back_bytes, BACK_ROIS, allow_missing=bool(front_bytes)
        )
    except RuntimeError as exc:
        logger.warning("OCR runtime error: %s", exc)
        return build_failure(request_id=request_id, reason=str(exc))
    except Exception as exc:
        logger.exception("OCR pipeline failed")
        return build_failure(request_id=request_id, reason=f"OCR pipeline failed: {exc}")

    front_context_text = front_roi_text

    parsed_front = parse_front(front_rois, context_text=front_context_text)
    if front_processed_variants is not None and front_bytes:
        missing_required = [
            name
            for name in REQUIRED_FIELDS
            if _is_missing_value(parsed_front.get(name, (None, 0.0))[0])
        ]
        if not front_roi_text or missing_required:
            fallback_text, fallback_conf = _ocr_full_image(front_processed_variants)
            if fallback_text:
                front_context_text = "\n".join(filter(None, [front_context_text, fallback_text]))
                parsed_front = parse_front(front_rois, context_text=front_context_text)
                parsed_front = _merge_parsed(
                    parsed_front, parse_front_from_text(fallback_text, base_conf=fallback_conf)
                )
                warnings.append("Front fallback OCR used.")

    raw_text = "\n".join(filter(None, [front_context_text, back_roi_text])).strip()
    if not raw_text:
        return build_failure(
            request_id=request_id,
            reason="No text extracted from images.",
            warnings=warnings,
            images=images,
        )

    parsed = {}
    parsed.update(parsed_front)
    parsed.update(parse_back(back_rois))
    fields = build_fields(parsed)

    status, _, low_conf = determine_status(fields)
    missing_fields = [
        name for name, payload in fields.items() if payload.get("value") in (None, "", [])
    ]
    if low_conf:
        warnings.append("Low confidence fields: " + ", ".join(sorted(low_conf)))

    if getattr(settings, "OCR_DEBUG", False):
        debug = {
            "front_raw": front_rois,
            "back_raw": back_rois,
            "raw_text": _strip_latin_words(raw_text),
            "front_meta": front_meta,
            "back_meta": back_meta,
        }

    return build_response(
        request_id=request_id,
        status=status,
        fields=fields,
        missing_fields=missing_fields,
        warnings=warnings,
        images=images,
        debug=debug,
    )

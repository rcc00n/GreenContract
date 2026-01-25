"""Pipeline entrypoint for RU driver license OCR."""

from __future__ import annotations

import logging
import uuid

from django.conf import settings

from rentals.ocr.storage import compute_sha256, store_upload

from .doc_detect import detect_and_warp
from .ocr_engine import run_ocr
from .parse import determine_status, parse_back, parse_front
from .preprocess import preprocess
from .rois import BACK_ROIS, CANVAS_SIZE, FRONT_ROIS
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


def _ocr_rois(image, rois: dict):
    results: dict[str, dict[str, object]] = {}
    for name, roi in rois.items():
        x, y, w, h = roi.x, roi.y, roi.w, roi.h
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(image.shape[1], x + w)
        y2 = min(image.shape[0], y + h)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            results[name] = {"text": "", "confidence": 0.0}
            continue
        detect = name == "raw_text"
        try:
            texts = run_ocr(crop, detect=detect)
        except Exception as exc:
            logger.warning("OCR failed on ROI %s: %s", name, exc)
            results[name] = {"text": "", "confidence": 0.0}
            continue
        if not texts:
            results[name] = {"text": "", "confidence": 0.0}
            continue
        merged_text = " ".join(text.strip() for text, _ in texts if text).strip()
        avg_conf = round(sum(conf for _, conf in texts) / len(texts), 3) if texts else 0.0
        results[name] = {"text": merged_text, "confidence": avg_conf}
    return results


def _collect_raw_text(rois: dict) -> str:
    parts = []
    for payload in rois.values():
        text = (payload.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def extract(front_bytes: bytes | None, back_bytes: bytes | None):
    request_id = f"ocr_{uuid.uuid4().hex[:10]}"
    warnings: list[str] = []
    images: list[dict[str, object]] = []
    debug = {"front_raw": {}, "back_raw": {}, "raw_text": ""}

    if not front_bytes and not back_bytes:
        return build_failure(request_id=request_id, reason="No images provided.")

    front_rois: dict[str, dict[str, object]] = {}
    back_rois: dict[str, dict[str, object]] = {}

    def _process_side(side: str, data: bytes | None, rois_def: dict):
        if not data:
            warnings.append(f"{side.capitalize()} image missing.")
            return {}, None
        image = _decode_image(data)
        if image is None:
            warnings.append(f"{side.capitalize()} image could not be decoded.")
            return {}, None
        image = _limit_size(image)
        try:
            images.append(store_upload(image, request_id, side, data))
        except Exception as exc:
            warnings.append(f"Failed to store {side} image: {exc}")
            images.append({"role": side, "storage_url": None, "sha256": compute_sha256(data or b"")})
        warped, used_fallback = detect_and_warp(image, CANVAS_SIZE)
        if used_fallback:
            warnings.append(f"{side.capitalize()} contour not detected; used resize fallback.")
        processed = preprocess(warped)
        return _ocr_rois(processed, rois_def), warped

    try:
        front_rois, _ = _process_side("front", front_bytes, FRONT_ROIS)
        back_rois, _ = _process_side("back", back_bytes, BACK_ROIS)
    except RuntimeError as exc:
        logger.warning("OCR runtime error: %s", exc)
        return build_failure(request_id=request_id, reason=str(exc))
    except Exception as exc:
        logger.exception("OCR pipeline failed")
        return build_failure(request_id=request_id, reason=f"OCR pipeline failed: {exc}")

    raw_text = "\n".join(filter(None, [_collect_raw_text(front_rois), _collect_raw_text(back_rois)])).strip()
    if not raw_text:
        return build_failure(
            request_id=request_id,
            reason="No text extracted from images.",
            warnings=warnings,
            images=images,
        )

    parsed = {}
    parsed.update(parse_front(front_rois))
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
            "raw_text": raw_text,
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

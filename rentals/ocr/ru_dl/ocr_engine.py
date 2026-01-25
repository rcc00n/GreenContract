"""OCR engine wrapper (PaddleOCR)."""

from __future__ import annotations

import inspect
import threading

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    np = None

try:
    from paddleocr import PaddleOCR
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    PaddleOCR = None

_OCR_INSTANCE = None
_OCR_LOCK = threading.Lock()


def _require_paddle():
    if PaddleOCR is None or np is None:
        raise RuntimeError("PaddleOCR and numpy are required for OCR.")


def get_ocr():
    _require_paddle()
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        with _OCR_LOCK:
            if _OCR_INSTANCE is None:
                kwargs = {
                    "use_angle_cls": True,
                    "lang": "ru",
                    "show_log": False,
                }
                try:
                    signature = inspect.signature(PaddleOCR.__init__)
                    allowed = set(signature.parameters.keys())
                    kwargs = {key: value for key, value in kwargs.items() if key in allowed}
                except (TypeError, ValueError):
                    pass
                _OCR_INSTANCE = PaddleOCR(**kwargs)
    return _OCR_INSTANCE


def _ensure_color(image):
    if image is None:
        return image
    if len(image.shape) == 2:
        return np.stack([image] * 3, axis=-1)
    return image


def _extract_texts(obj, acc: list[tuple[str, float]]):
    if isinstance(obj, (list, tuple)):
        if len(obj) == 2 and isinstance(obj[0], str) and isinstance(obj[1], (int, float)):
            acc.append((obj[0], float(obj[1])))
            return
        if (
            len(obj) >= 2
            and isinstance(obj[1], (list, tuple))
            and len(obj[1]) >= 2
            and isinstance(obj[1][0], str)
        ):
            acc.append((obj[1][0], float(obj[1][1])))
            return
        for item in obj:
            _extract_texts(item, acc)


def run_ocr(image, detect: bool = False) -> list[tuple[str, float]]:
    ocr = get_ocr()
    image = _ensure_color(image)
    if detect:
        result = ocr.ocr(image, cls=True)
    else:
        result = ocr.ocr(image, det=False, rec=True, cls=True)
    extracted: list[tuple[str, float]] = []
    _extract_texts(result, extracted)
    return extracted

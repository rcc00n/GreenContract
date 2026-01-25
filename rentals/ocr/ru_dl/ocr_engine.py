"""OCR engine wrapper (PaddleOCR)."""

from __future__ import annotations

import os
import inspect
import threading

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    np = None

_PADDLE_FLAGS = {
    "FLAGS_use_mkldnn": "0",
    "FLAGS_enable_onednn": "0",
    "FLAGS_enable_pir_in_executor": "0",
    "FLAGS_enable_pir_api": "0",
}

for _key, _value in _PADDLE_FLAGS.items():
    os.environ.setdefault(_key, _value)

try:
    from paddleocr import PaddleOCR
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    PaddleOCR = None

_OCR_INSTANCE = None
_OCR_LOCK = threading.Lock()


def _require_paddle():
    if PaddleOCR is None or np is None:
        raise RuntimeError("PaddleOCR and numpy are required for OCR.")


def _configure_paddle_flags():
    for key, value in _PADDLE_FLAGS.items():
        os.environ.setdefault(key, value)
    try:
        import paddle

        flags = {}
        for key, value in _PADDLE_FLAGS.items():
            normalized = value not in ("0", "false", "False", "")
            flags[key] = normalized
        try:
            paddle.set_flags(flags)
        except Exception:
            pass
    except Exception:
        pass


def get_ocr():
    _require_paddle()
    _configure_paddle_flags()
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
    if isinstance(obj, dict):
        text_key_pairs = (
            ("text", "score"),
            ("text", "confidence"),
            ("rec_text", "rec_score"),
            ("rec_text", "score"),
        )
        for text_key, score_key in text_key_pairs:
            if text_key in obj and score_key in obj and isinstance(obj[text_key], str):
                acc.append((obj[text_key], float(obj.get(score_key, 0.0) or 0.0)))
                return
        if "text" in obj and isinstance(obj["text"], str):
            acc.append((obj["text"], float(obj.get("score", 0.0) or obj.get("confidence", 0.0) or 0.0)))
            return
        for value in obj.values():
            _extract_texts(value, acc)
        return
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
    kwargs = {"cls": True} if detect else {"det": False, "rec": True, "cls": True}
    try:
        signature = inspect.signature(ocr.ocr)
        allowed = set(signature.parameters.keys())
        kwargs = {key: value for key, value in kwargs.items() if key in allowed}
    except (TypeError, ValueError):
        pass
    try:
        result = ocr.ocr(image, **kwargs) if kwargs else ocr.ocr(image)
    except TypeError:
        result = ocr.ocr(image)
    extracted: list[tuple[str, float]] = []
    _extract_texts(result, extracted)
    return extracted

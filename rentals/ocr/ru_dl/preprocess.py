"""Preprocessing for OCR."""

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    cv2 = None
    np = None


def _require_cv2():
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV is required for preprocessing.")


def preprocess(image_bgr):
    _require_cv2()

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)
    return enhanced

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


def preprocess_variants(image_bgr):
    """Return a list of preprocessed grayscale variants for OCR."""
    _require_cv2()

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    base = clahe.apply(denoised)

    blurred = cv2.GaussianBlur(base, (0, 0), 1.2)
    sharpen = cv2.addWeighted(base, 1.6, blurred, -0.6, 0)

    adaptive = cv2.adaptiveThreshold(
        base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12
    )
    _, otsu = cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    contrast = cv2.convertScaleAbs(base, alpha=1.35, beta=10)

    return [base, gray, sharpen, adaptive, otsu, contrast]

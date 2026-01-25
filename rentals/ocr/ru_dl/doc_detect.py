"""Detect document contour and warp to a standard canvas."""

from __future__ import annotations

from typing import Iterable

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    cv2 = None
    np = None


def _require_cv2():
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV is required for document detection.")


def _order_points(pts: Iterable) -> "np.ndarray":
    pts = np.array(pts, dtype="float32")
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def detect_and_warp(image_bgr, output_size: tuple[int, int]):
    """Return warped image and whether fallback resize was used."""
    _require_cv2()

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=2)
    edges = cv2.erode(edges, kernel, iterations=1)

    contours_data = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = contours_data[0] if len(contours_data) == 2 else contours_data[1]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]

    screen_contour = None
    image_area = float(image_bgr.shape[0] * image_bgr.shape[1])
    for contour in contours:
        if cv2.contourArea(contour) < image_area * 0.08:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4:
            screen_contour = approx
            break

    width, height = output_size
    if screen_contour is None:
        if contours:
            largest = contours[0]
            if cv2.contourArea(largest) >= image_area * 0.05:
                rect = cv2.minAreaRect(largest)
                box = cv2.boxPoints(rect)
                rect_pts = _order_points(box)
                dst = np.array(
                    [
                        [0, 0],
                        [width - 1, 0],
                        [width - 1, height - 1],
                        [0, height - 1],
                    ],
                    dtype="float32",
                )
                matrix = cv2.getPerspectiveTransform(rect_pts, dst)
                warped = cv2.warpPerspective(image_bgr, matrix, (width, height))
                return warped, False
        resized = cv2.resize(image_bgr, output_size)
        return resized, True

    rect = _order_points(screen_contour.reshape(4, 2))
    dst = np.array(
        [
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image_bgr, matrix, (width, height))
    return warped, False

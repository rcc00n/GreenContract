"""Optional keypoint-based document detection (YOLO/Ultralytics)."""

from __future__ import annotations

import os
import threading
from typing import Iterable

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    cv2 = None
    np = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    YOLO = None

_MODEL = None
_MODEL_LOCK = threading.Lock()


def _require_cv2():
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV and numpy are required for keypoint detection.")


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


def _get_model(model_path: str | None):
    if YOLO is None or not model_path:
        return None
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                _MODEL = YOLO(model_path)
    return _MODEL


def _pick_best_keypoints(result, min_conf: float):
    keypoints = getattr(result, "keypoints", None)
    if keypoints is None:
        return None
    try:
        kps_xy = keypoints.xy  # (n, k, 2)
        kps_conf = getattr(keypoints, "conf", None)  # (n, k)
    except Exception:
        return None
    if kps_xy is None or len(kps_xy) == 0:
        return None

    best_idx = None
    best_score = -1.0
    for idx in range(len(kps_xy)):
        points = kps_xy[idx]
        if points is None or len(points) < 4:
            continue
        if kps_conf is not None:
            conf = float(np.mean(kps_conf[idx]))
        else:
            conf = 1.0
        if conf < min_conf:
            continue
        if conf > best_score:
            best_score = conf
            best_idx = idx

    if best_idx is None:
        return None
    return np.array(kps_xy[best_idx][:4], dtype="float32")


def detect_keypoints(image_bgr, model_path: str | None = None, min_conf: float = 0.3):
    model_path = model_path or os.environ.get("OCR_KEYPOINT_MODEL_PATH")
    model = _get_model(model_path)
    if model is None:
        return None
    _require_cv2()
    try:
        results = model.predict(image_bgr, verbose=False)
    except Exception:
        return None
    if not results:
        return None
    best = _pick_best_keypoints(results[0], min_conf=min_conf)
    return best


def warp_with_keypoints(image_bgr, output_size: tuple[int, int], keypoints):
    if keypoints is None:
        return None
    _require_cv2()
    width, height = output_size
    rect = _order_points(keypoints)
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
    return warped

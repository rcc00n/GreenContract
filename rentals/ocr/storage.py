"""OCR upload storage helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings

try:
    import cv2
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    cv2 = None

try:
    from PIL import Image
    import io
except ImportError:  # pragma: no cover - optional dependency handled at runtime
    Image = None
    io = None


def _upload_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / "ocr_uploads"


def ensure_upload_dir() -> Path:
    path = _upload_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_media_url(filename: str) -> str:
    base = (settings.MEDIA_URL or "/media/").rstrip("/")
    return f"{base}/ocr_uploads/{filename}"


def _write_jpeg(image_bgr, path: Path):
    if cv2 is not None:
        cv2.imwrite(str(path), image_bgr)
        return
    if Image is not None and io is not None:
        image = Image.fromarray(image_bgr[:, :, ::-1])
        image.save(path, format="JPEG", quality=92)
        return
    raise RuntimeError("No image backend available to save OCR uploads.")


def store_upload(image_bgr, request_id: str, role: str, source_bytes: bytes | None):
    sha256 = compute_sha256(source_bytes or b"")
    if not getattr(settings, "OCR_STORE_UPLOADS", True):
        return {"role": role, "storage_url": None, "sha256": sha256}

    upload_dir = ensure_upload_dir()
    filename = f"{request_id}_{role}.jpg"
    path = upload_dir / filename
    _write_jpeg(image_bgr, path)

    return {"role": role, "storage_url": _build_media_url(filename), "sha256": sha256}

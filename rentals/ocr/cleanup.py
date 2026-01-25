"""Cleanup helper for OCR uploads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings


def cleanup_uploads(ttl_hours: int | None = None) -> dict[str, int]:
    ttl = ttl_hours if ttl_hours is not None else int(getattr(settings, "OCR_UPLOAD_TTL_HOURS", 72))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl)

    upload_dir = Path(settings.MEDIA_ROOT) / "ocr_uploads"
    if not upload_dir.exists():
        return {"scanned": 0, "deleted": 0}

    scanned = 0
    deleted = 0

    for path in upload_dir.iterdir():
        if not path.is_file():
            continue
        scanned += 1
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue

    return {"scanned": scanned, "deleted": deleted}

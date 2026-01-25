"""OCR integration layer.

Phase 2 hook: replace extract_ru_dl implementation with an HTTP client
without changing callers.
"""

from .ru_dl.pipeline import extract as extract_ru_dl

__all__ = ["extract_ru_dl"]

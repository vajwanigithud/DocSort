from pathlib import Path
from typing import Literal

from docsort.app.storage import ocr_cache_store

Status = Literal["pending", "ready", "failed"]
OCR_STATUS_PAGES = 1

def get_ocr_status(path: Path, max_pages: int = OCR_STATUS_PAGES) -> Status:
    try:
        fingerprint = ocr_cache_store.compute_fingerprint(path)
    except Exception:
        fingerprint = None
    cached_text = ocr_cache_store.get_cached_text(str(path), max_pages=max_pages, fingerprint=fingerprint or None)
    if cached_text:
        return "ready"
    if not path.exists() or path.suffix.lower() != ".pdf":
        return "failed"
    has_entry = ocr_cache_store.has_cache_row(str(path), max_pages=max_pages, fingerprint=fingerprint or None)
    if has_entry:
        return "failed"
    return "pending"


def get_ocr_tooltip(path: Path, max_pages: int = OCR_STATUS_PAGES) -> str:
    status = get_ocr_status(path, max_pages=max_pages)
    if status == "ready":
        return "OCR cached text is available from ocr_cache.sqlite."
    if status == "failed":
        if path.suffix.lower() != ".pdf":
            return "OCR cache unavailable: non-PDF file type."
        return "OCR cache entry is missing or empty for this PDF."
    return "OCR cache is pending for this PDF."


def format_ocr_badge(status: str) -> str:
    badges = {"pending": "OCR pending", "ready": "OCR ready", "failed": "OCR failed"}
    return badges.get(status, "")

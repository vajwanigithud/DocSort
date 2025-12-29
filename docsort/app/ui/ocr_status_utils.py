from pathlib import Path
from typing import Dict, Literal, Optional

from docsort.app.storage import ocr_cache_store, ocr_job_store

Status = Literal["pending", "ready", "failed"]
OCR_STATUS_PAGES = 1


def _get_latest_job(path: Path, fingerprint: Optional[str], max_pages: int) -> Optional[Dict[str, object]]:
    try:
        return ocr_job_store.get_job(str(path), max_pages=max_pages, fingerprint=fingerprint or None)
    except Exception:
        return None


def get_ocr_status(path: Path, max_pages: int = OCR_STATUS_PAGES) -> Status:
    try:
        path = path.resolve()
    except Exception:
        pass
    try:
        fingerprint = ocr_cache_store.compute_fingerprint(path)
    except Exception:
        fingerprint = None
    cached_text = ocr_cache_store.get_cached_text(str(path), max_pages=max_pages, fingerprint=fingerprint or None)
    if cached_text:
        return "ready"
    if not path.exists() or path.suffix.lower() != ".pdf":
        return "failed"
    job = _get_latest_job(path, fingerprint, max_pages)
    if job:
        status = str(job.get("status") or "").upper()
        if status in {"QUEUED", "RUNNING"}:
            return "pending"
        if status == "FAILED":
            return "failed"
        if status == "DONE":
            return "failed"
    return "failed"


def get_ocr_tooltip(path: Path, max_pages: int = OCR_STATUS_PAGES) -> str:
    status = get_ocr_status(path, max_pages=max_pages)
    if status == "ready":
        return "OCR cached text is available from ocr_cache.sqlite."
    if status == "failed":
        if path.suffix.lower() != ".pdf":
            return "OCR cache unavailable: non-PDF file type."
        try:
            fingerprint = ocr_cache_store.compute_fingerprint(path)
        except Exception:
            fingerprint = None
        job = _get_latest_job(path, fingerprint, max_pages)
        if job and job.get("status") == "FAILED" and job.get("last_error"):
            return f"OCR job failed: {job.get('last_error')}"
        if job and str(job.get("status") or "").upper() == "DONE":
            return "OCR finished but no cached text was produced."
        if job is None:
            return "OCR not scheduled (tray worker not running yet)."
        return "OCR cache entry is missing or empty for this PDF."
    return "OCR job queued or running for this PDF."


def format_ocr_badge(status: str) -> str:
    badges = {"pending": "OCR pending", "ready": "OCR ready", "failed": "OCR failed"}
    return badges.get(status, "")

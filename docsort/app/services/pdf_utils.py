import logging
import re
from pathlib import Path
from typing import Optional, Tuple

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def get_pdf_page_count(path: str) -> Tuple[int, Optional[str]]:
    """Return page count; on failure return 1 and error message."""
    pdf_path = Path(path)
    try:
        with pdf_path.open("rb") as fh:
            reader = PdfReader(fh)
            count = len(reader.pages)
        logger.info("Loaded PDF page_count=%s path=%s", count, pdf_path)
        return count, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read PDF page count for %s: %s", pdf_path, exc)
        return 1, str(exc)


def extract_pdf_text(path: str, max_pages: int = 1) -> Tuple[Optional[str], Optional[str]]:
    pdf_path = Path(path)
    try:
        with pdf_path.open("rb") as fh:
            reader = PdfReader(fh)
            pages = []
            for idx, page in enumerate(reader.pages):
                if idx >= max_pages:
                    break
                try:
                    pages.append(page.extract_text() or "")
                except Exception as page_exc:  # noqa: BLE001
                    logger.warning("Text extract failed on page %s for %s: %s", idx + 1, pdf_path, page_exc)
            text = "\n".join(pages).strip()
        logger.info("PDF text extracted path=%s pages=%s", pdf_path, min(max_pages, len(reader.pages)))
        return text or None, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to extract text for %s: %s", pdf_path, exc)
        return None, str(exc)


def detect_doc_type_and_number(text: str) -> Tuple[Optional[str], Optional[str]]:
    patterns = [
        (r"TAX\s+INVOICE\s*[:#]?\s*(\d+)", "TAX INVOICE"),
        (r"INVOICE\s*[:#]?\s*(\d+)", "INVOICE"),
        (r"ESTIMATE\s*[:#]?\s*(\d+)", "ESTIMATE"),
    ]
    for pattern, label in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return label, m.group(1)
    return None, None


def build_suggested_filename(path: str, fallback_stem: str) -> str:
    pdf_path = Path(path)
    fallback = fallback_stem or pdf_path.stem or "document"
    text, err = extract_pdf_text(str(pdf_path))
    if text:
        doc_type, doc_number = detect_doc_type_and_number(text)
        if doc_type and doc_number:
            safe_type = re.sub(r"\s+", "_", doc_type.title()).strip("_")
            filename = f"{safe_type}_{doc_number}.pdf"
            logger.info("PDF suggestion matched %s: %s %s", pdf_path, doc_type, doc_number)
            return filename
    if err:
        logger.warning("PDF suggestion fallback for %s due to: %s", pdf_path, err)
    if not fallback.lower().endswith(".pdf"):
        fallback = f"{fallback}.pdf"
    return fallback

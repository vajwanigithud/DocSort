import logging
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

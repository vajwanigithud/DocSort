import argparse
import logging
import time
from pathlib import Path
from typing import List

from docsort.app.services.ocr_suggestion_service import get_text_for_pdf
from docsort.app.storage import ocr_cache_store

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm OCR cache for PDFs in a folder.")
    parser.add_argument("source_folder", type=Path, help="Folder to scan for PDFs recursively.")
    parser.add_argument("--pages", type=int, default=1, help="Max pages to OCR per PDF (default: 1).")
    return parser.parse_args()


def _find_pdfs(folder: Path) -> List[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


def main() -> None:
    _setup_logging()
    args = _parse_args()
    source = args.source_folder
    pages = max(1, int(args.pages or 1))
    if not source.exists():
        logger.error("Source folder does not exist: %s", source)
        return
    if not source.is_dir():
        logger.error("Source path is not a directory: %s", source)
        return
    run_start = time.time()
    pdfs = _find_pdfs(source)
    total = len(pdfs)
    if total == 0:
        logger.info("No PDFs found under: %s", source)
        return
    ocred = 0
    skipped = 0
    errors = 0
    total_ocr_seconds = 0.0
    for idx, pdf_path in enumerate(pdfs, start=1):
        fingerprint = ocr_cache_store.compute_fingerprint(pdf_path)
        is_cached = False
        if fingerprint:
            try:
                is_cached = ocr_cache_store.is_cached(str(pdf_path), max_pages=pages, fingerprint=fingerprint)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Cache lookup failed for %s: %s", pdf_path, exc)
        else:
            logger.debug("No fingerprint for %s; proceeding without cache lookup", pdf_path)
        if is_cached:
            skipped += 1
            logger.info("[%s/%s] SKIP already cached: %s", idx, total, pdf_path.name)
            continue
        start = time.time()
        try:
            get_text_for_pdf(str(pdf_path), max_pages=pages)
            elapsed = time.time() - start
            ocred += 1
            total_ocr_seconds += elapsed
            logger.info("[%s/%s] OCR cached: %s (%.1fs)", idx, total, pdf_path.name, elapsed)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("[%s/%s] ERROR OCRing: %s err=%s", idx, total, pdf_path.name, exc)
    total_seconds = time.time() - run_start
    avg_ocr = (total_ocr_seconds / ocred) if ocred else 0.0
    logger.info(
        "Done. Scanned=%s OCRed=%s Skipped=%s Errors=%s total_seconds=%.1f avg_ocr_seconds=%.2f",
        total,
        ocred,
        skipped,
        errors,
        total_seconds,
        avg_ocr,
    )


if __name__ == "__main__":
    main()

# Manual test:
# python -m docsort.tools.ocr_warm_cache D:\Docs\Incoming --pages 1

"""
Background OCR cache watcher.

Purpose: pre-warm the SQLite OCR cache so the UI can surface suggestions instantly.
Optional dependency: install `watchdog` for real-time file events; otherwise we fall back to polling.
"""
import argparse
import logging
import signal
import time
from pathlib import Path
from typing import Dict, Optional

from docsort.app.services.ocr_suggestion_service import get_text_for_pdf
from docsort.app.storage import ocr_cache_store

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a folder and pre-populate OCR cache for PDFs.")
    parser.add_argument("source_folder", type=Path, help="Folder to watch for PDFs recursively.")
    parser.add_argument("--pages", type=int, default=1, help="Max pages to OCR per PDF (default: 1).")
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="Polling interval when watchdog is unavailable.")
    return parser.parse_args()


def _find_pdfs(folder: Path) -> Dict[Path, str]:
    results: Dict[Path, str] = {}
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            results[path] = ocr_cache_store.compute_fingerprint(path)
    return results


def _process_pdf(path: Path, fingerprint: Optional[str], pages: int, stats: Dict[str, int]) -> None:
    fp = fingerprint or ocr_cache_store.compute_fingerprint(path)
    if not fp:
        logger.debug("No fingerprint for %s; processing without cache check", path)
    try:
        if fp and ocr_cache_store.is_cached(str(path), max_pages=pages, fingerprint=fp):
            stats["skipped"] += 1
            logger.info("SKIP cached: %s", path.name)
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug("Cache check failed for %s: %s", path, exc)
    start = time.time()
    try:
        get_text_for_pdf(str(path), max_pages=pages)
        elapsed = time.time() - start
        stats["ocred"] += 1
        logger.info("OCR cached: %s (%.1fs)", path.name, elapsed)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] += 1
        logger.warning("ERROR OCRing %s err=%s", path.name, exc)


def _initial_scan(folder: Path, pages: int) -> Dict[Path, str]:
    logger.info("Starting initial OCR cache scan in %s", folder)
    seen: Dict[Path, str] = {}
    pdfs = _find_pdfs(folder)
    total = len(pdfs)
    stats = {"ocred": 0, "skipped": 0, "errors": 0}
    for idx, (pdf, fp) in enumerate(sorted(pdfs.items()), start=1):
        logger.info("[%s/%s] processing %s", idx, total, pdf.name)
        _process_pdf(pdf, fp, pages, stats)
        seen[pdf] = fp
    logger.info(
        "Initial scan complete. OCRed=%s Skipped=%s Errors=%s",
        stats["ocred"],
        stats["skipped"],
        stats["errors"],
    )
    return seen


def _poll_loop(folder: Path, pages: int, poll_seconds: float, seen: Dict[Path, str]) -> None:
    logger.info("Entering polling mode every %.1fs", poll_seconds)
    while True:
        pdfs = _find_pdfs(folder)
        for path, fp in pdfs.items():
            prior_fp = seen.get(path)
            if prior_fp == fp and fp and ocr_cache_store.is_cached(str(path), max_pages=pages, fingerprint=fp):
                continue
            _process_pdf(path, fp, pages, {"ocred": 0, "skipped": 0, "errors": 0})
            seen[path] = fp
        time.sleep(max(1.0, poll_seconds))


def _watchdog_loop(folder: Path, pages: int, poll_seconds: float, seen: Dict[Path, str]) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception:
        logger.info("watchdog not available; falling back to polling.")
        _poll_loop(folder, pages, poll_seconds, seen)
        return

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # type: ignore[override]
            try:
                path = Path(event.src_path)
            except Exception:
                return
            if not path.is_file() or path.suffix.lower() != ".pdf":
                return
            fp = ocr_cache_store.compute_fingerprint(path)
            prior_fp = seen.get(path)
            if prior_fp == fp and fp and ocr_cache_store.is_cached(str(path), max_pages=pages, fingerprint=fp):
                return
            _process_pdf(path, fp, pages, {"ocred": 0, "skipped": 0, "errors": 0})
            seen[path] = fp

    observer = Observer()
    observer.schedule(Handler(), str(folder), recursive=True)
    observer.start()
    logger.info("watchdog active; watching %s", folder)
    try:
        while True:
            time.sleep(1.0)
    finally:
        observer.stop()
        observer.join()


def main() -> None:
    _setup_logging()
    args = _parse_args()
    source = args.source_folder
    pages = max(1, int(args.pages or 1))
    poll_seconds = float(args.poll_seconds or 10.0)
    if not source.exists():
        logger.error("Source folder does not exist: %s", source)
        return
    if not source.is_dir():
        logger.error("Source path is not a directory: %s", source)
        return

    run_stats = {"start": time.time()}
    seen = _initial_scan(source, pages)

    def _handle_sigint(signum, frame):  # noqa: ANN001, D401
        elapsed = time.time() - run_stats["start"]
        logger.info("Stopping watcher after %.1fs", elapsed)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_sigint)
    _watchdog_loop(source, pages, poll_seconds, seen)


if __name__ == "__main__":
    main()

# Manual test:
# python -m docsort.tools.ocr_watch_cache D:\Docs\Incoming --pages 1

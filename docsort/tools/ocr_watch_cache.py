"""
Background OCR cache watcher.

Purpose: pre-warm the SQLite OCR cache so the UI can surface suggestions instantly.
Optional dependency: install `watchdog` for real-time file events; otherwise we fall back to polling.

Manual test (stall):
- Run: python -m docsort.tools.ocr_watch_cache <folder> --pages 1
- Manually insert or backdate an OCR job row to RUNNING with old updated_at; wait for ~30s; expect log "Marked N stalled OCR job(s) as FAILED".
"""
import argparse
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from docsort.app.services.ocr_suggestion_service import get_text_for_pdf
from docsort.app.storage import ocr_cache_store, ocr_job_store, settings_store
from docsort.app.ui import ocr_status_utils

logger = logging.getLogger(__name__)
THROTTLE_SECONDS = 1.0
PROCESS_LOCK = threading.Lock()
TEMP_SUFFIXES = {".tmp", ".temp", ".part"}
WORKER_ID = "ocr_watch_cache"
STALL_SWEEP_INTERVAL_SECONDS = 30
_stall_last_sweep = 0.0


def _setup_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a folder and pre-populate OCR cache for PDFs.")
    parser.add_argument(
        "source_folder",
        type=Path,
        nargs="?",
        help="Folder to watch for PDFs recursively. Defaults to configured source_root.",
    )
    parser.add_argument("--pages", type=int, default=1, help="Max pages to OCR per PDF (default: 1).")
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="Polling interval when watchdog is unavailable.")
    return parser.parse_args()


def _maybe_mark_stalled() -> None:
    global _stall_last_sweep
    now = time.time()
    if now - _stall_last_sweep < STALL_SWEEP_INTERVAL_SECONDS:
        return
    try:
        updated = ocr_job_store.mark_stalled_jobs()
        if updated:
            logger.info("Marked %s stalled OCR job(s) as FAILED", updated)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Stalled OCR job sweep failed: %s", exc)
    _stall_last_sweep = now


def _should_skip_path(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    if "_split_archive" in parts:
        return True
    if any(part.startswith("_") for part in parts[:-1]):
        return True
    name = path.name.lower()
    if name.startswith("~") or name.endswith("~") or name.startswith("."):
        return True
    if path.suffix.lower() in TEMP_SUFFIXES:
        return True
    return False


def _resolve_source_folder(arg_folder: Optional[Path]) -> Optional[Path]:
    if arg_folder:
        return arg_folder
    saved = settings_store.get_source_root()
    if saved:
        try:
            return Path(saved)
        except Exception:
            return None
    return None


def _find_pdfs(folder: Path) -> Dict[Path, str]:
    results: Dict[Path, str] = {}
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if _should_skip_path(path):
            continue
        results[path] = ocr_cache_store.compute_fingerprint(path)
    return results


def _process_pdf(path: Path, fingerprint: Optional[str], pages: int, stats: Dict[str, int]) -> None:
    with PROCESS_LOCK:
        fp = fingerprint or ocr_cache_store.compute_fingerprint(path)
        if not fp:
            logger.debug("No fingerprint for %s; processing without cache check", path)
        max_attempts = ocr_job_store.DEFAULT_MAX_ATTEMPTS
        try:
            existing = ocr_job_store.get_job(str(path), max_pages=ocr_status_utils.OCR_STATUS_PAGES, fingerprint=fp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to read OCR job before processing %s: %s", path, exc)
            existing = None
        if existing and existing.get("attempts") is not None:
            try:
                if not ocr_job_store.can_retry(existing, max_attempts=max_attempts):
                    try:
                        ocr_job_store.upsert_job(
                            str(path),
                            max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                            status="FAILED",
                            fingerprint=fp,
                            last_error=f"Max attempts exceeded ({max_attempts})",
                            worker_id=WORKER_ID,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Failed to mark OCR job max-attempts for %s: %s", path, exc)
                    logger.info("Skipping OCR for %s due to max attempts reached", path.name)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("Retry check failed for %s: %s", path, exc)
        cached_hit = False
        try:
            if fp and ocr_cache_store.is_cached(str(path), max_pages=pages, fingerprint=fp):
                stats["skipped"] += 1
                logger.info("SKIP cached: %s", path.name)
                cached_hit = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cache check failed for %s: %s", path, exc)
        if cached_hit:
            try:
                ocr_job_store.upsert_job(
                    str(path),
                    max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                    status="DONE",
                    fingerprint=fp,
                    worker_id=WORKER_ID,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to mark cached OCR job for %s: %s", path, exc)
            return
        if not cached_hit:
            start = time.time()
            try:
                try:
                    ocr_job_store.upsert_job(
                        str(path),
                        max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                        status="RUNNING",
                        fingerprint=fp,
                        worker_id=WORKER_ID,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to mark OCR job running for %s: %s", path, exc)
                get_text_for_pdf(str(path), max_pages=pages)
                elapsed = time.time() - start
                stats["ocred"] += 1
                logger.info("OCR cached: %s (%.1fs)", path.name, elapsed)
                try:
                    ocr_job_store.upsert_job(
                        str(path),
                        max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                        status="DONE",
                        fingerprint=fp,
                        worker_id=WORKER_ID,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to mark OCR job done for %s: %s", path, exc)
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning("ERROR OCRing %s err=%s", path.name, exc)
                try:
                    ocr_job_store.upsert_job(
                        str(path),
                        max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                        status="FAILED",
                        fingerprint=fp,
                        last_error=str(exc)[:500],
                        worker_id=WORKER_ID,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to mark OCR job failed for %s: %s", path, exc)
        time.sleep(THROTTLE_SECONDS)


def _initial_scan(folder: Path, pages: int) -> Dict[Path, str]:
    logger.info("Starting initial OCR cache scan in %s", folder)
    seen: Dict[Path, str] = {}
    pdfs = _find_pdfs(folder)
    total = len(pdfs)
    stats = {"ocred": 0, "skipped": 0, "errors": 0}
    for idx, (pdf, fp) in enumerate(sorted(pdfs.items()), start=1):
        logger.info("[%s/%s] processing %s", idx, total, pdf.name)
        try:
            ocr_job_store.upsert_job(
                str(pdf),
                max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                status="QUEUED",
                fingerprint=fp,
                worker_id=WORKER_ID,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to queue OCR job for %s: %s", pdf, exc)
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
            try:
                ocr_job_store.upsert_job(
                    str(path),
                    max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                    status="QUEUED",
                    fingerprint=fp,
                    worker_id=WORKER_ID,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to queue OCR job for %s: %s", path, exc)
            _process_pdf(path, fp, pages, {"ocred": 0, "skipped": 0, "errors": 0})
            seen[path] = fp
        _maybe_mark_stalled()
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
            if getattr(event, "is_directory", False):
                return
            if not path.is_file() or path.suffix.lower() != ".pdf" or _should_skip_path(path):
                return
            fp = ocr_cache_store.compute_fingerprint(path)
            prior_fp = seen.get(path)
            if prior_fp == fp and fp and ocr_cache_store.is_cached(str(path), max_pages=pages, fingerprint=fp):
                return
            try:
                ocr_job_store.upsert_job(
                    str(path),
                    max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                    status="QUEUED",
                    fingerprint=fp,
                    worker_id=WORKER_ID,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to queue OCR job for %s: %s", path, exc)
            _process_pdf(path, fp, pages, {"ocred": 0, "skipped": 0, "errors": 0})
            seen[path] = fp
            _maybe_mark_stalled()

    observer = Observer()
    observer.schedule(Handler(), str(folder), recursive=True)
    observer.start()
    logger.info("watchdog active; watching %s", folder)
    try:
        while True:
            _maybe_mark_stalled()
            time.sleep(1.0)
    finally:
        observer.stop()
        observer.join()


def main() -> None:
    _setup_logging()
    args = _parse_args()
    source = _resolve_source_folder(args.source_folder)
    pages = max(1, int(args.pages or 1))
    poll_seconds = float(args.poll_seconds or 10.0)
    if not source:
        logger.error("Source folder not provided and no source_root configured.")
        return
    source = source.resolve()
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

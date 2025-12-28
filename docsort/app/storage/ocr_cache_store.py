import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "ocr_cache.sqlite"  # runtime cache; ignore in VCS
OCR_ENGINE_VERSION = 1
_db_ready = False
_db_lock = threading.Lock()


def _ensure_db() -> None:
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if _db_ready:
            return
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA temp_store=MEMORY;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ocr_cache (
                        file_path TEXT NOT NULL,
                        file_fingerprint TEXT NOT NULL,
                        max_pages INTEGER NOT NULL,
                        extracted_text TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        ocr_engine_version INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY (file_path, file_fingerprint, max_pages, ocr_engine_version)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_ocr_cache_fp
                    ON ocr_cache(file_fingerprint)
                    """
                )
                conn.commit()
            _db_ready = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to ensure OCR cache DB: %s", exc)


def _connect() -> sqlite3.Connection:
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to set OCR cache pragmas: %s", exc)
    return conn


def compute_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime)
        return f"{size}:{mtime}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to compute OCR fingerprint for %s: %s", path, exc)
        return ""


def _normalized_path(path: Union[str, Path]) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def get_cached_text(path: str, max_pages: int, fingerprint: Optional[str] = None) -> str:
    effective_fingerprint = fingerprint or compute_fingerprint(Path(path))
    if not effective_fingerprint:
        return ""
    norm_path = _normalized_path(path)
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """
                SELECT extracted_text
                FROM ocr_cache
                WHERE file_path = ? AND file_fingerprint = ? AND max_pages = ? AND ocr_engine_version = ?
                LIMIT 1
                """,
                (norm_path, effective_fingerprint, max_pages, OCR_ENGINE_VERSION),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to read OCR cache for %s: %s", path, exc)
    return ""


def is_cached(path: str, max_pages: int, fingerprint: Optional[str] = None) -> bool:
    try:
        text = get_cached_text(path, max_pages=max_pages, fingerprint=fingerprint)
        return bool(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed cache check for %s: %s", path, exc)
        return False


def upsert_cached_text(path: str, max_pages: int, text: str, fingerprint: Optional[str] = None) -> None:
    effective_fingerprint = fingerprint or compute_fingerprint(Path(path))
    if not effective_fingerprint:
        return
    capped_text = (text or "")[:200_000]
    norm_path = _normalized_path(path)
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO ocr_cache (file_path, file_fingerprint, max_pages, extracted_text, created_at, ocr_engine_version)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path, file_fingerprint, max_pages, ocr_engine_version)
                DO UPDATE SET extracted_text = excluded.extracted_text, created_at = excluded.created_at
                """,
                (norm_path, effective_fingerprint, max_pages, capped_text, created_at, OCR_ENGINE_VERSION),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to write OCR cache for %s: %s", path, exc)


def delete_cached_text(path: str, max_pages: int, fingerprint: Optional[str] = None) -> None:
    effective_fingerprint = fingerprint or compute_fingerprint(Path(path))
    norm_path = _normalized_path(path)
    try:
        with _connect() as conn:
            if effective_fingerprint:
                conn.execute(
                    """
                    DELETE FROM ocr_cache
                    WHERE file_path = ? AND file_fingerprint = ? AND max_pages = ? AND ocr_engine_version = ?
                    """,
                    (norm_path, effective_fingerprint, max_pages, OCR_ENGINE_VERSION),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM ocr_cache
                    WHERE file_path = ? AND max_pages = ? AND ocr_engine_version = ?
                    """,
                    (norm_path, max_pages, OCR_ENGINE_VERSION),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to delete OCR cache for %s: %s", path, exc)


def has_cache_row(path: str, max_pages: int, fingerprint: Optional[str] = None) -> bool:
    norm_path = _normalized_path(path)
    try:
        with _connect() as conn:
            if fingerprint:
                cursor = conn.execute(
                    """
                    SELECT 1
                    FROM ocr_cache
                    WHERE file_path = ? AND file_fingerprint = ? AND max_pages = ? AND ocr_engine_version = ?
                    LIMIT 1
                    """,
                    (norm_path, fingerprint, max_pages, OCR_ENGINE_VERSION),
                )
                if cursor.fetchone():
                    return True
            cursor = conn.execute(
                """
                SELECT 1
                FROM ocr_cache
                WHERE file_path = ? AND max_pages = ? AND ocr_engine_version = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (norm_path, max_pages, OCR_ENGINE_VERSION),
            )
            return cursor.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to check OCR cache row for %s: %s", path, exc)
        return False

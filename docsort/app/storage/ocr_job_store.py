import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from docsort.app.storage import ocr_cache_store

logger = logging.getLogger(__name__)

_db_ready = False
_db_lock = threading.Lock()

STATUS_ALLOWED = {"QUEUED", "RUNNING", "DONE", "FAILED"}


def normalize_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _db_path() -> Path:
    try:
        from docsort.app.storage import settings_store

        base_dir = settings_store.get_storage_dir()
    except Exception:
        base_dir = Path.home() / ".docsort"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "ocr_jobs.sqlite"


def _ensure_db() -> None:
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if _db_ready:
            return
        try:
            db_path = _db_path()
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA temp_store=MEMORY;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ocr_jobs (
                        job_key TEXT PRIMARY KEY,
                        file_path TEXT NOT NULL,
                        file_fingerprint TEXT,
                        max_pages INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        worker_id TEXT
                    )
                    """
                )
                conn.commit()
            _db_ready = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to ensure OCR jobs DB: %s", exc)


def _connect() -> sqlite3.Connection:
    _ensure_db()
    conn = sqlite3.connect(_db_path())
    try:
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to set OCR jobs pragmas: %s", exc)
    return conn


def _job_key(norm_path: str, max_pages: int, fingerprint: Optional[str]) -> str:
    return f"{norm_path}|{max_pages}|{fingerprint or ''}"


def _effective_fingerprint(path: str, fingerprint: Optional[str]) -> Optional[str]:
    if fingerprint is not None:
        return fingerprint
    try:
        computed = ocr_cache_store.compute_fingerprint(Path(path))
        return computed or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to compute OCR job fingerprint for %s: %s", path, exc)
        return None


def upsert_job(
    path: str,
    max_pages: int,
    status: str,
    fingerprint: Optional[str] = None,
    last_error: Optional[str] = None,
    worker_id: Optional[str] = None,
) -> None:
    norm_path = normalize_path(path)
    status = status.upper()
    if status not in STATUS_ALLOWED:
        logger.debug("Ignoring upsert with invalid status %s for %s", status, path)
        return
    effective_fingerprint = _effective_fingerprint(path, fingerprint)
    job_key = _job_key(norm_path, max_pages, effective_fingerprint)
    updated_at = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with _connect() as conn:
            cur = conn.execute(
                "SELECT attempts FROM ocr_jobs WHERE job_key = ? LIMIT 1",
                (job_key,),
            )
            row = cur.fetchone()
            prior_attempts = int(row[0]) if row and row[0] is not None else 0
            attempts = prior_attempts
            if status == "RUNNING":
                attempts = prior_attempts + 1
            conn.execute(
                """
                INSERT INTO ocr_jobs (job_key, file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_error = excluded.last_error,
                    worker_id = excluded.worker_id,
                    attempts = excluded.attempts
                """,
                (
                    job_key,
                    norm_path,
                    effective_fingerprint,
                    max_pages,
                    status,
                    updated_at,
                    attempts,
                    last_error,
                    worker_id,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to upsert OCR job for %s: %s", path, exc)


def get_job(path: str, max_pages: int, fingerprint: Optional[str] = None) -> Optional[Dict[str, object]]:
    norm_path = normalize_path(path)
    effective_fingerprint = _effective_fingerprint(path, fingerprint)
    try:
        with _connect() as conn:
            if effective_fingerprint is not None:
                cur = conn.execute(
                    """
                    SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id
                    FROM ocr_jobs
                    WHERE job_key = ?
                    LIMIT 1
                    """,
                    (_job_key(norm_path, max_pages, effective_fingerprint),),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id
                    FROM ocr_jobs
                    WHERE file_path = ? AND max_pages = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (norm_path, max_pages),
                )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "file_path": row[0],
                "file_fingerprint": row[1],
                "max_pages": row[2],
                "status": row[3],
                "updated_at": row[4],
                "attempts": row[5],
                "last_error": row[6],
                "worker_id": row[7],
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to get OCR job for %s: %s", path, exc)
        return None


def list_recent(limit: int = 200) -> List[Dict[str, object]]:
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id
                FROM ocr_jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
            return [
                {
                    "file_path": row[0],
                    "file_fingerprint": row[1],
                    "max_pages": row[2],
                    "status": row[3],
                    "updated_at": row[4],
                    "attempts": row[5],
                    "last_error": row[6],
                    "worker_id": row[7],
                }
                for row in rows
            ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to list OCR jobs: %s", exc)
        return []


def clear_job(path: str, max_pages: int, fingerprint: Optional[str] = None) -> None:
    norm_path = normalize_path(path)
    effective_fingerprint = _effective_fingerprint(path, fingerprint)
    try:
        with _connect() as conn:
            if effective_fingerprint is not None:
                conn.execute(
                    "DELETE FROM ocr_jobs WHERE job_key = ?",
                    (_job_key(norm_path, max_pages, effective_fingerprint),),
                )
            else:
                conn.execute("DELETE FROM ocr_jobs WHERE file_path = ? AND max_pages = ?", (norm_path, max_pages))
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to clear OCR job for %s: %s", path, exc)

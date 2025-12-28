import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from docsort.app.storage import ocr_cache_store

logger = logging.getLogger(__name__)

_db_ready = False
_db_lock = threading.Lock()

STATUS_ALLOWED = {"QUEUED", "RUNNING", "DONE", "FAILED"}
DEFAULT_MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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
                        worker_id TEXT,
                        max_attempts INTEGER NOT NULL DEFAULT 3
                    )
                    """
                )
                cols = {row[1] for row in conn.execute("PRAGMA table_info(ocr_jobs)")}
                if "max_attempts" not in cols:
                    conn.execute("ALTER TABLE ocr_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")
                conn.commit()
            _db_ready = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to ensure OCR jobs DB: %s", exc)
            raise


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


def can_retry(job: Dict[str, object], max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> bool:
    try:
        attempts = int(job.get("attempts", 0))
    except Exception:
        attempts = 0
    try:
        cap = int(job.get("max_attempts", max_attempts))
    except Exception:
        cap = int(max_attempts)
    return attempts < cap


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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> None:
    norm_path = normalize_path(path)
    status = status.upper()
    if status not in STATUS_ALLOWED:
        logger.debug("Ignoring upsert with invalid status %s for %s", status, path)
        return
    effective_fingerprint = _effective_fingerprint(path, fingerprint)
    job_key = _job_key(norm_path, max_pages, effective_fingerprint)
    now = _utcnow()
    updated_at = _to_iso_z(now)
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT attempts, status, updated_at, last_error, worker_id, max_attempts
                FROM ocr_jobs
                WHERE job_key = ?
                LIMIT 1
                """,
                (job_key,),
            )
            row = cur.fetchone()
            prior_attempts = int(row[0]) if row and row[0] is not None else 0
            attempts = prior_attempts
            try:
                capped_attempts = int(max_attempts)
            except Exception:
                capped_attempts = DEFAULT_MAX_ATTEMPTS
            try:
                prior_max_attempts = int(row[5]) if row and len(row) > 5 and row[5] is not None else None
            except Exception:
                prior_max_attempts = None
            if prior_max_attempts is not None:
                capped_attempts = max(capped_attempts, prior_max_attempts)
            effective_status = status
            effective_last_error = last_error if last_error is not None else (row[3] if row and len(row) > 3 else None)
            effective_worker = worker_id if worker_id is not None else (row[4] if row and len(row) > 4 else None)
            if status in {"QUEUED", "RUNNING"} and prior_attempts >= capped_attempts:
                effective_status = "FAILED"
                effective_last_error = f"Max attempts exceeded ({capped_attempts})"
            elif status == "RUNNING":
                attempts = prior_attempts + 1
            conn.execute(
                """
                INSERT INTO ocr_jobs (job_key, file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id, max_attempts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_error = excluded.last_error,
                    worker_id = excluded.worker_id,
                    attempts = excluded.attempts,
                    max_attempts = excluded.max_attempts
                """,
                (
                    job_key,
                    norm_path,
                    effective_fingerprint,
                    max_pages,
                    effective_status,
                    updated_at,
                    attempts,
                    effective_last_error,
                    effective_worker,
                    capped_attempts,
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
                    SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id, max_attempts
                    FROM ocr_jobs
                    WHERE job_key = ?
                    LIMIT 1
                    """,
                    (_job_key(norm_path, max_pages, effective_fingerprint),),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id, max_attempts
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
                "max_attempts": row[8] if len(row) > 8 else DEFAULT_MAX_ATTEMPTS,
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to get OCR job for %s: %s", path, exc)
        return None


def list_recent(limit: int = 200) -> List[Dict[str, object]]:
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT file_path, file_fingerprint, max_pages, status, updated_at, attempts, last_error, worker_id, max_attempts
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
                    "max_attempts": row[8] if len(row) > 8 else DEFAULT_MAX_ATTEMPTS,
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


def mark_stalled_jobs(
    running_stale_seconds: int = 300,
    queued_stale_seconds: int = 1800,
    worker_id: str = "health",
) -> int:
    try:
        now = _utcnow()
        now_str = _to_iso_z(now)
        running_cutoff = now - timedelta(seconds=int(running_stale_seconds))
        queued_cutoff = now - timedelta(seconds=int(queued_stale_seconds))
        updates = []
        with _connect() as conn:
            cur = conn.execute(
                """
                SELECT job_key, status, updated_at
                FROM ocr_jobs
                WHERE status IN ('RUNNING', 'QUEUED')
                """
            )
            for job_key, status, updated_at in cur.fetchall():
                parsed = _parse_dt(updated_at)
                reason: Optional[str] = None
                if parsed is None:
                    reason = "Invalid updated_at; treated as stalled"
                elif status == "RUNNING" and parsed < running_cutoff:
                    reason = f"Stalled: RUNNING > {int(running_stale_seconds)}s (timeout)"
                elif status == "QUEUED" and parsed < queued_cutoff:
                    reason = f"Stalled: QUEUED > {int(queued_stale_seconds)}s (not picked up)"
                if reason:
                    updates.append(("FAILED", reason, now_str, worker_id, job_key))
            if updates:
                conn.executemany(
                    """
                    UPDATE ocr_jobs
                    SET status = ?, last_error = ?, updated_at = ?, worker_id = ?
                    WHERE job_key = ?
                    """,
                    updates,
                )
                conn.commit()
        return len(updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to mark stalled OCR jobs: %s", exc)
        return 0


def clear_all_jobs() -> int:
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM ocr_jobs")
            conn.commit()
            return cur.rowcount if cur else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to clear all OCR jobs: %s", exc)
        return 0


def prune_terminal_jobs(older_than_seconds: int = 86400) -> int:
    # Manual test (legacy timestamp):
    # with _connect() as c:
    #     c.execute("INSERT OR REPLACE INTO ocr_jobs (job_key,file_path,file_fingerprint,max_pages,status,updated_at,attempts,last_error,worker_id,max_attempts) VALUES (?,?,?,?,?,?,?,?,?,?)",
    #               ("legacy|1|", "legacy.pdf", "", 1, "DONE", "2025-12-28T10:00:00+00:00", 0, "", "test", DEFAULT_MAX_ATTEMPTS))
    #     c.commit()
    # prune_terminal_jobs(older_than_seconds=1)  # should remove legacy row too
    try:
        now = _utcnow()
        cutoff = _to_iso_z(now - timedelta(seconds=int(older_than_seconds)))
        cutoff_dt = now - timedelta(seconds=int(older_than_seconds))
        with _connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM ocr_jobs
                WHERE status IN ('DONE', 'FAILED') AND updated_at < ?
                """,
                (cutoff,),
            )
            conn.commit()
            removed_fast = cur.rowcount if cur else 0
            cur = conn.execute(
                """
                SELECT job_key, updated_at
                FROM ocr_jobs
                WHERE status IN ('DONE', 'FAILED') AND updated_at NOT LIKE '%Z'
                """
            )
            to_delete = []
            for job_key, updated_at in cur.fetchall():
                parsed = _parse_dt(updated_at)
                if parsed and parsed < cutoff_dt:
                    to_delete.append((job_key,))
            removed_fallback = 0
            if to_delete:
                conn.executemany("DELETE FROM ocr_jobs WHERE job_key = ?", to_delete)
                conn.commit()
                removed_fallback = len(to_delete)
        return (removed_fast or 0) + (removed_fallback or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to prune terminal OCR jobs: %s", exc)
        return 0

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Tuple

from docsort.app.storage import settings_store

logger = logging.getLogger(__name__)

CACHE_SUBDIR = "_docsort_cache/ocr"
MAX_CACHED_PREVIEWS = 100
_CACHE_MAP: Dict[Tuple[str, str], Path] = {}


def _ensure_cache_dir() -> Optional[Path]:
    root = settings_store.get_source_root()
    if not root:
        logger.warning("OCR cache: no source_root configured.")
        return None
    try:
        cache_dir = Path(root) / CACHE_SUBDIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR cache: failed to create cache dir: %s", exc)
        return None


def _cleanup_cache(cache_dir: Path, keep: int) -> None:
    try:
        in_use = {p for p in _CACHE_MAP.values() if p.exists()}
        files = sorted(
            [p for p in cache_dir.glob("*.pdf") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[keep:]:
            if stale in in_use:
                continue
            try:
                stale.unlink()
            except Exception:
                continue
    except Exception:
        return


def cache_pdf_for_ocr(src: Path, keep: int = MAX_CACHED_PREVIEWS) -> Optional[Path]:
    cache_dir = _ensure_cache_dir()
    if not cache_dir:
        return None
    if not src.exists() or not src.is_file():
        logger.warning("OCR cache: source missing %s", src)
        return None
    try:
        resolved = src.resolve()
    except Exception:
        resolved = src
    if cache_dir in resolved.parents:
        return resolved

    try:
        st = resolved.stat()
        fp_key = f"{int(st.st_size)}:{int(st.st_mtime_ns)}"
    except Exception:
        fp_key = ""
    key = (str(resolved), fp_key)
    if key in _CACHE_MAP:
        cached_path = _CACHE_MAP[key]
        if cached_path.exists():
            return cached_path

    ts = int(time.time() * 1000)
    dest = cache_dir / f"{resolved.stem}_{ts}_{uuid.uuid4().hex[:6]}{resolved.suffix}"
    try:
        shutil.copy2(resolved, dest)
        if fp_key:
            _CACHE_MAP[key] = dest
        _cleanup_cache(cache_dir, keep)
        return dest
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR cache: failed to copy %s to cache: %s", resolved, exc)
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return None

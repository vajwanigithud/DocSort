from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Tuple

from docsort.app.storage import settings_store

logger = logging.getLogger(__name__)

STORAGE_PATH = Path(settings_store.get_storage_dir()) / "split_completion.json"


def _ensure_file() -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORAGE_PATH.exists():
        STORAGE_PATH.write_text("{}", encoding="utf-8")


def _load() -> Dict[str, Dict[str, int]]:
    _ensure_file()
    try:
        data = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Split completion load failed: %s", exc)
    return {}


def _save(data: Dict[str, Dict[str, int]]) -> None:
    _ensure_file()
    try:
        STORAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Split completion save failed: %s", exc)


def _key_for_path(path: Path) -> Tuple[str, Path]:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    root = settings_store.get_source_root()
    if root:
        try:
            root_path = Path(root).resolve()
            rel = resolved.relative_to(root_path)
            return str(rel), resolved
        except Exception:
            pass
    return str(resolved), resolved


def _fingerprint(path: Path) -> Tuple[int, int] | None:
    try:
        st = path.stat()
        return int(st.st_size), int(st.st_mtime_ns)
    except Exception:
        return None


def mark_split_complete(path: Path) -> None:
    fp = _fingerprint(path)
    if not fp:
        return
    key, _ = _key_for_path(path)
    data = _load()
    data[key] = {"size": fp[0], "mtime_ns": fp[1]}
    _save(data)


def prune_if_changed(path: Path) -> None:
    fp = _fingerprint(path)
    key, _ = _key_for_path(path)
    data = _load()
    if key not in data:
        return
    if not fp:
        return
    stored = data.get(key, {})
    if stored.get("size") != fp[0] or stored.get("mtime_ns") != fp[1]:
        data.pop(key, None)
        _save(data)


def is_split_complete(path: Path) -> bool:
    fp = _fingerprint(path)
    key, _ = _key_for_path(path)
    data = _load()
    if key not in data or not fp:
        return False
    stored = data.get(key, {})
    if stored.get("size") != fp[0] or stored.get("mtime_ns") != fp[1]:
        data.pop(key, None)
        _save(data)
        return False
    return True

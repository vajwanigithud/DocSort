import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_PATH = Path(__file__).resolve().parent / "done_log.jsonl"


def _ensure_file() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("", encoding="utf-8")


def append_done(event: Dict[str, Any]) -> None:
    _ensure_file()
    payload = {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), **event}
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def list_recent(limit: int = 200) -> List[Dict[str, Any]]:
    _ensure_file()
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    events: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def seen_sources() -> set[str]:
    return {ev.get("src", "") for ev in list_all() if ev.get("src")}


def list_all() -> List[Dict[str, Any]]:
    return list_recent(10_000)


def list_entries(status: Optional[str] = None) -> List[Dict[str, Any]]:
    events = list_all()
    if status is None:
        return events
    return [ev for ev in events if ev.get("status") == status]


def entries_by_status(status: str) -> List[Dict[str, Any]]:
    return [ev for ev in list_all() if ev.get("status") == status]


def _match_entry(ev: Dict[str, Any], key: Any) -> bool:
    if isinstance(key, dict):
        for k in ("src", "dest", "timestamp"):
            if k in key and ev.get(k) != key.get(k):
                return False
        return True
    return ev.get("src") == key


def _rewrite(events: List[Dict[str, Any]]) -> None:
    with LOG_PATH.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def update_entry_status(entry_key: Any, status: str, delete_attempts: Optional[int] = None, last_error: Optional[str] = None) -> None:
    _ensure_file()
    events = list_all()
    updated = False
    for ev in events:
        if _match_entry(ev, entry_key):
            ev["status"] = status
            if delete_attempts is not None:
                ev["delete_attempts"] = delete_attempts
            if last_error is not None:
                ev["last_error"] = last_error
            ev["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")
            updated = True
    if updated:
        _rewrite(events)


def increment_delete_attempt(entry_key: Any, last_error: str = "") -> None:
    _ensure_file()
    events = list_all()
    updated = False
    for ev in events:
        if _match_entry(ev, entry_key):
            ev["delete_attempts"] = int(ev.get("delete_attempts", 0)) + 1
            if last_error:
                ev["last_error"] = last_error
            ev["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")
            updated = True
    if updated:
        _rewrite(events)


def update_status_by_source(src: str, status: str, delete_attempts: Optional[int] = None) -> None:
    # Backwards compatibility wrapper
    update_entry_status(src, status, delete_attempts=delete_attempts)

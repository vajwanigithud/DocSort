import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

LOG_PATH = Path(__file__).resolve().parent.parent / "storage" / "undo_log.json"


def _ensure_file() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("[]", encoding="utf-8")


def append_undo(record: Dict[str, Any]) -> None:
    _ensure_file()
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except json.JSONDecodeError:
        data = []
    record = {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), **record}
    data.append(record)
    LOG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_last() -> Optional[Dict[str, Any]]:
    _ensure_file()
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            return None
    except json.JSONDecodeError:
        return None
    return data[-1]


def pop_last() -> Optional[Dict[str, Any]]:
    _ensure_file()
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            return None
    except json.JSONDecodeError:
        data = []
    if not data:
        return None
    record = data.pop()
    LOG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return record

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

STORAGE_PATH = Path(__file__).resolve().parent.parent / "storage" / "training_events.json"


def _ensure_file() -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORAGE_PATH.exists():
        STORAGE_PATH.write_text("[]", encoding="utf-8")


def append_event(event: Dict[str, Any]) -> None:
    _ensure_file()
    try:
        data = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except json.JSONDecodeError:
        data = []
    event = {"timestamp": datetime.utcnow().isoformat(timespec="seconds"), **event}
    data.append(event)
    STORAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_recent(n: int = 50) -> List[Dict[str, Any]]:
    _ensure_file()
    try:
        data = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
    except json.JSONDecodeError:
        return []
    return data[-n:]

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

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
    return {ev.get("src", "") for ev in list_recent(2000) if ev.get("src")}

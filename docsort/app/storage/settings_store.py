import json
import logging
from pathlib import Path
from typing import Optional

SETTINGS_PATH = Path(__file__).parent / "settings.json"


def _ensure_storage_file() -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps({}), encoding="utf-8")


def get_destination_root() -> Optional[str]:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    value = data.get("destination_root")
    logging.getLogger(__name__).debug("get_destination_root -> %s", value)
    return value


def set_destination_root(path: str) -> None:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    data["destination_root"] = path
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logging.getLogger(__name__).debug("set_destination_root -> %s", path)


def get_source_root() -> Optional[str]:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    value = data.get("source_root")
    logging.getLogger(__name__).debug("get_source_root -> %s", value)
    return value


def set_source_root(path: str) -> None:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    data["source_root"] = path
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logging.getLogger(__name__).debug("set_source_root -> %s", path)


def get_watcher_enabled() -> bool:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    value = bool(data.get("watcher_enabled", True))
    logging.getLogger(__name__).debug("get_watcher_enabled -> %s", value)
    return value


def set_watcher_enabled(enabled: bool) -> None:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    data["watcher_enabled"] = bool(enabled)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logging.getLogger(__name__).debug("set_watcher_enabled -> %s", enabled)

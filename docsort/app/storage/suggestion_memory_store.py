import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

STORAGE_PATH = Path(__file__).resolve().parent / "suggestion_memory.json"


def _ensure_file() -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORAGE_PATH.exists():
        STORAGE_PATH.write_text("{}", encoding="utf-8")


def load_memory() -> Dict[str, str]:
    _ensure_file()
    try:
        data = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            logger.info("Loaded learned suggestions count=%s", len(data))
            return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load suggestion memory: %s", exc)
    return {}


def save_memory(memory: Dict[str, str]) -> None:
    _ensure_file()
    try:
        STORAGE_PATH.write_text(json.dumps(memory, indent=2), encoding="utf-8")
        logger.info("Saved learned suggestions count=%s", len(memory))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save suggestion memory: %s", exc)

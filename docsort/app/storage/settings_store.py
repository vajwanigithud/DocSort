import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

SETTINGS_PATH = Path(__file__).parent / "settings.json"
DEFAULT_STORAGE_DIR = Path.home() / ".docsort"


@dataclass
class FolderConfig:
    staging: Optional[str] = None
    splitter: Optional[str] = None
    rename: Optional[str] = None
    destination: Optional[str] = None


def _ensure_storage_file() -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps({}), encoding="utf-8")


def _load_settings() -> Dict[str, object]:
    _ensure_storage_file()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


def _save_settings(data: Dict[str, object]) -> None:
    _ensure_storage_file()
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clean_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return str(Path(value).expanduser())
    except Exception:
        return str(value)


def get_storage_dir() -> Path:
    data = _load_settings()
    storage_dir_raw = data.get("storage_dir")
    try:
        storage_dir = Path(storage_dir_raw).expanduser() if storage_dir_raw else DEFAULT_STORAGE_DIR
    except Exception:
        storage_dir = DEFAULT_STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)
    logging.getLogger(__name__).debug("get_storage_dir -> %s", storage_dir)
    return storage_dir


def get_folder_config() -> FolderConfig:
    data = _load_settings()
    folders = data.get("folders") or {}
    if not isinstance(folders, dict):
        folders = {}
    cfg = FolderConfig(
        staging=_clean_path(folders.get("staging") or data.get("source_root")),
        splitter=_clean_path(folders.get("splitter")),
        rename=_clean_path(folders.get("rename")),
        destination=_clean_path(folders.get("destination") or data.get("destination_root")),
    )
    logging.getLogger(__name__).debug("get_folder_config -> %s", cfg)
    return cfg


def set_folder_config(config: FolderConfig) -> None:
    data = _load_settings()
    folders = {
        "staging": _clean_path(config.staging),
        "splitter": _clean_path(config.splitter),
        "rename": _clean_path(config.rename),
        "destination": _clean_path(config.destination),
    }
    data["folders"] = folders
    if folders["staging"]:
        data["source_root"] = folders["staging"]
    else:
        data.pop("source_root", None)
    if folders["destination"]:
        data["destination_root"] = folders["destination"]
    else:
        data.pop("destination_root", None)
    _save_settings(data)
    logging.getLogger(__name__).debug("set_folder_config -> %s", folders)


def get_staging_root() -> Optional[str]:
    return get_folder_config().staging


def set_staging_root(path: str) -> None:
    cfg = get_folder_config()
    cfg.staging = path
    set_folder_config(cfg)


def get_splitter_root() -> Optional[str]:
    return get_folder_config().splitter


def set_splitter_root(path: str) -> None:
    cfg = get_folder_config()
    cfg.splitter = path
    set_folder_config(cfg)


def get_rename_root() -> Optional[str]:
    return get_folder_config().rename


def set_rename_root(path: str) -> None:
    cfg = get_folder_config()
    cfg.rename = path
    set_folder_config(cfg)


def get_destination_root() -> Optional[str]:
    return get_folder_config().destination


def set_destination_root(path: str) -> None:
    cfg = get_folder_config()
    cfg.destination = path
    set_folder_config(cfg)


def get_source_root() -> Optional[str]:
    # Backwards compatibility alias for staging/inbound folder.
    return get_staging_root()


def set_source_root(path: str) -> None:
    set_staging_root(path)


def get_watcher_enabled() -> bool:
    data = _load_settings()
    value = bool(data.get("watcher_enabled", True))
    logging.getLogger(__name__).debug("get_watcher_enabled -> %s", value)
    return value


def set_watcher_enabled(enabled: bool) -> None:
    data = _load_settings()
    data["watcher_enabled"] = bool(enabled)
    _save_settings(data)
    logging.getLogger(__name__).debug("set_watcher_enabled -> %s", enabled)

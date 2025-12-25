import logging
from pathlib import Path


def configure_logging() -> None:
    storage_dir = Path(__file__).resolve().parent.parent / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    log_path = storage_dir / "app.log"

    handlers = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger(__name__).info("Logging configured. File: %s", log_path)

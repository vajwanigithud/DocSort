import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Set

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class SourcePoller:
    def __init__(
        self,
        source_root: str,
        enqueue_scanned_path: Callable[[str], None],
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.source_root = Path(source_root)
        self.enqueue_scanned_path = enqueue_scanned_path
        self.poll_interval_sec = poll_interval_sec
        self._seen: Set[str] = set()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.log = logging.getLogger(__name__)

    def start(self) -> None:
        if self.is_running():
            self.log.info("Source poller already running for %s", self.source_root)
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log.info("Source poller started for %s", self.source_root)

    def stop(self) -> None:
        if not self.is_running():
            self.log.info("Source poller already stopped")
            return
        self._stop_event.set()
        # No join to avoid UI blocking; thread is daemon.
        self.log.info("Source poller stop requested for %s", self.source_root)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    if not self.source_root.exists():
                        self.source_root.mkdir(parents=True, exist_ok=True)
                    for path in self.source_root.iterdir():
                        if not path.is_file():
                            continue
                        if path.suffix.lower() not in ALLOWED_EXT:
                            continue
                        resolved = str(path.resolve())
                        if resolved in self._seen:
                            continue
                        self._seen.add(resolved)
                        self.enqueue_scanned_path(resolved)
                    time.sleep(self.poll_interval_sec)
                except Exception:  # noqa: BLE001
                    self.log.exception("Source poller iteration failed")
                    time.sleep(self.poll_interval_sec)
        finally:
            self.log.info("Source poller thread exiting for %s", self.source_root)

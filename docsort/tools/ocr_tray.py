"""
System tray wrapper for DocSort OCR watcher.

Runs:
  python -m docsort.tools.ocr_watch_cache

Tray:
- Green = OCR running
- Red   = stopped/crashed

Menu:
- Restart OCR
- Open Logs
- Exit
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pystray
from PIL import Image, ImageDraw

from docsort.app.storage import settings_store

# ---------------------------
# Icon helper
# ---------------------------

def _make_icon(running: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 8
    color = (40, 200, 40, 255) if running else (220, 50, 50, 255)
    draw.ellipse((pad, pad, size - pad, size - pad), fill=color)

    inner = 22
    draw.ellipse(
        (inner, inner, size - inner, size - inner),
        fill=(255, 255, 255, 120),
    )
    return img


# ---------------------------
# Tray app
# ---------------------------

class OcrTrayApp:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.icon = pystray.Icon(
            "DocSort OCR",
            icon=_make_icon(False),
            title="DocSort OCR",
            menu=pystray.Menu(
                pystray.MenuItem("Restart OCR", self._on_restart),
                pystray.MenuItem("Open Logs", self._on_open_logs),
                pystray.MenuItem("Exit", self._on_exit),
            ),
        )

    # ---------------------------
    # Watcher process control
    # ---------------------------

    def _watcher_cmd(self) -> list[str]:
        return [sys.executable, "-m", "docsort.tools.ocr_watch_cache"]

    def _start_watcher(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return

            self._proc = subprocess.Popen(
                self._watcher_cmd(),
                cwd=str(Path.cwd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

    def _stop_watcher(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None

        if not proc:
            return

        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

    def _is_running(self) -> bool:
        with self._lock:
            return bool(self._proc and self._proc.poll() is None)

    # ---------------------------
    # UI helpers
    # ---------------------------

    def _refresh_icon(self) -> None:
        running = self._is_running()
        self.icon.icon = _make_icon(running)
        self.icon.title = (
            "DocSort OCR (Running)" if running else "DocSort OCR (Stopped)"
        )

    # ---------------------------
    # Menu callbacks
    # NOTE: Use Any to avoid pystray typing issues
    # ---------------------------

    def _on_restart(self, _icon: Any, _item: Any) -> None:
        self._stop_watcher()
        time.sleep(0.2)
        self._start_watcher()
        self._refresh_icon()

    def _on_open_logs(self, _icon: Any, _item: Any) -> None:
        try:
            storage_dir = Path(settings_store.get_storage_dir())
            log_path = storage_dir / "app.log"
        except Exception:
            log_path = Path("docsort/app/storage/app.log")

        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        if os.name == "nt":
            try:
                os.startfile(str(log_path))
            except Exception:
                pass

    def _on_exit(self, _icon: Any, _item: Any) -> None:
        self._stop_event.set()
        self._stop_watcher()
        self.icon.stop()

    # ---------------------------
    # Background monitor loop
    # ---------------------------

    def _monitor_loop(self) -> None:
        self._start_watcher()
        self._refresh_icon()

        while not self._stop_event.is_set():
            if not self._is_running():
                self._start_watcher()
            self._refresh_icon()
            time.sleep(1.0)

    def run(self) -> None:
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()
        self.icon.run()


# ---------------------------
# Entrypoint
# ---------------------------

def main() -> None:
    app = OcrTrayApp()
    app.run()


if __name__ == "__main__":
    main()

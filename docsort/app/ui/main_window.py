import logging
import threading
import uuid
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services.folder_service import folder_service
from docsort.app.services.source_poller import SourcePoller
from docsort.app.services import pdf_utils
from docsort.app.storage import settings_store, done_log_store
from docsort.app.ui.tabs_done import DoneTab
from docsort.app.ui.tabs_needs_attention import NeedsAttentionTab
from docsort.app.ui.tabs_rename_move import RenameMoveTab
from docsort.app.ui.tabs_scanned import ScannedTab
from docsort.app.ui.tabs_settings import SettingsTab
from docsort.app.ui.tabs_splitter import SplitterTab


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DocSort")
        self.state = AppState()
        self.folder_service = folder_service
        self.source_poller: SourcePoller | None = None
        self.watcher_enabled = settings_store.get_watcher_enabled()
        self.log = logging.getLogger(__name__)
        saved_root = settings_store.get_destination_root()
        if saved_root:
            self.folder_service.set_root(saved_root)
        self.source_root = settings_store.get_source_root()

        self.tabs = QtWidgets.QTabWidget()
        self.scanned_tab = ScannedTab(self.state, self.refresh_all)
        self.splitter_tab = SplitterTab(self.state, self.refresh_all)
        self.rename_tab = RenameMoveTab(self.state, self.folder_service, self.refresh_all)
        self.attention_tab = NeedsAttentionTab(self.state, self.refresh_all)
        self.done_tab = DoneTab(self.refresh_all)
        self.settings_tab = SettingsTab(
            self.folder_service,
            self.refresh_all,
            self._on_source_changed,
            self.start_poller,
            self.stop_poller,
        )

        self.tabs.addTab(self.scanned_tab, "Scanned")
        self.tabs.addTab(self.splitter_tab, "Splitter")
        self.tabs.addTab(self.rename_tab, "Rename & Move")
        self.tabs.addTab(self.attention_tab, "Needs Attention")
        self.tabs.addTab(self.done_tab, "Done")
        self.tabs.addTab(self.settings_tab, "Settings")

        self.setCentralWidget(self.tabs)
        self.status_label = QtWidgets.QLabel()
        status_bar = QtWidgets.QStatusBar()
        status_bar.addPermanentWidget(self.status_label)
        self.setStatusBar(status_bar)
        self.resize(1000, 700)
        self._pending_timer = QtCore.QTimer(self)
        self._pending_timer.timeout.connect(self._drain_pending)
        self._pending_timer.start(250)
        self._delete_timer = QtCore.QTimer(self)
        self._delete_timer.timeout.connect(self._process_pending_deletes)
        self._delete_timer.start(8000)
        self.refresh_all()

    def refresh_all(self) -> None:
        for tab in [
            self.scanned_tab,
            self.splitter_tab,
            self.rename_tab,
            self.attention_tab,
            self.done_tab,
            self.settings_tab,
        ]:
            if hasattr(tab, "refresh"):
                tab.refresh()
        self._update_watcher_status()

    @QtCore.Slot()
    def _update_watcher_status(self) -> None:
        if self.source_poller and self.source_poller.is_running() and self.source_root:
            self.status_label.setText(f"Watcher: ON (Polling)")
        else:
            self.status_label.setText("Watcher: OFF")

    def start_poller(self) -> None:
        if not self.source_root:
            self.log.warning("No source root set; watcher not started.")
            return
        self.log.info("Scheduling poller start at %s", self.source_root)
        threading.Thread(target=self._start_poller_bg, args=(self.source_root,), daemon=True).start()

    def _start_poller_bg(self, path: str) -> None:
        try:
            self.source_poller = SourcePoller(path, self.state.enqueue_scanned_path)
            self.source_poller.start()
            self.source_root = path
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to start poller at %s", path)
        QtCore.QMetaObject.invokeMethod(self, "_update_watcher_status", QtCore.Qt.QueuedConnection)

    def stop_poller(self) -> None:
        self.log.info("Scheduling poller stop")
        threading.Thread(target=self._stop_poller_bg, daemon=True).start()

    def _stop_poller_bg(self) -> None:
        try:
            if self.source_poller:
                self.source_poller.stop()
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to stop poller")
        QtCore.QMetaObject.invokeMethod(self, "_update_watcher_status", QtCore.Qt.QueuedConnection)

    def _on_source_changed(self, path: str) -> None:
        self.source_root = path
        self.log.info("Source changed to %s", path)
        self.refresh_all()

    def _drain_pending(self) -> None:
        added = False
        while not self.state.pending_scanned_paths.empty():
            try:
                path = self.state.pending_scanned_paths.get_nowait()
            except Exception:
                break
            resolved = str(Path(path).resolve())
            if not any(str(Path(d.source_path).resolve()) == resolved for d in self.state.scanned_items):
                # Skip items already moved
                if resolved in done_log_store.seen_sources():
                    self.state.attention_items.append(
                        DocumentItem(
                            id="attn-" + uuid.uuid4().hex[:8],
                            source_path=resolved,
                            display_name=Path(path).name,
                            page_count=1,
                            notes="already moved source present",
                            suggested_folder="",
                            suggested_name="",
                            confidence=0.0,
                            vendor="Vendor",
                            doctype="Other",
                            number="ATTN",
                            date_str="00-00-0000",
                        )
                    )
                    added = True
                    continue
                page_count = 1
                notes = "watcher"
                if Path(resolved).suffix.lower() == ".pdf":
                    page_count, err = pdf_utils.get_pdf_page_count(resolved)
                    if err:
                        notes = f"{notes} page_count_error={err}"
                self.state.scanned_items.append(
                    DocumentItem(
                        id="scan-" + uuid.uuid4().hex[:8],
                        source_path=resolved,
                        display_name=Path(path).name,
                        page_count=page_count,
                        notes=notes,
                        suggested_folder="",
                        suggested_name="",
                        confidence=0.0,
                        vendor="Vendor",
                        doctype="Type",
                        number="000",
                        date_str="00-00-0000",
                    )
                )
                added = True
        while not self.state.pending_attention_messages.empty():
            try:
                msg = self.state.pending_attention_messages.get_nowait()
            except Exception:
                break
            source_path = msg.get("source_path", "")
            error = msg.get("error", "")
            resolved = str(Path(source_path).resolve()) if source_path else ""
            existing = None
            for lst_name in ["scanned_items", "splitter_items", "rename_items", "done_items", "attention_items"]:
                lst = getattr(self.state, lst_name, [])
                for idx, doc in enumerate(lst):
                    if resolved and str(Path(doc.source_path).resolve()) == resolved:
                        existing = (lst, idx, doc)
                        break
                if existing:
                    break
            if existing:
                lst, idx, doc = existing
                lst.pop(idx)
                doc.notes = f"{doc.notes} {error}".strip()
                self.state.attention_items.append(doc)
            else:
                self.state.attention_items.append(
                    DocumentItem(
                        id="attn-" + uuid.uuid4().hex[:8],
                        source_path=resolved,
                        display_name=Path(source_path).name if source_path else "Unknown",
                        page_count=1,
                        notes=error,
                        suggested_folder="",
                        suggested_name="",
                        confidence=0.0,
                        vendor="Vendor",
                        doctype="Type",
                        number="000",
                        date_str="00-00-0000",
                    )
                )
            added = True
        if added:
            self.refresh_all()

    def _process_pending_deletes(self) -> None:
        pending = done_log_store.entries_by_status("PENDING_DELETE")
        for ev in pending:
            src = ev.get("src")
            dest = ev.get("dest")
            attempts = int(ev.get("delete_attempts", 0))
            if not src:
                continue
            if not Path(dest).exists():
                done_log_store.update_status_by_source(src, "DONE", attempts)
                continue
            if not Path(src).exists():
                done_log_store.update_status_by_source(src, "DONE", attempts)
                continue
            try:
                Path(src).unlink()
                done_log_store.update_status_by_source(src, "DONE", attempts + 1)
            except Exception as exc:  # noqa: BLE001
                attempts += 1
                if attempts >= 5:
                    done_log_store.update_status_by_source(src, "NEEDS_ATTENTION", attempts)
                    self.state.attention_items.append(
                        DocumentItem(
                            id="attn-" + uuid.uuid4().hex[:8],
                            source_path=src,
                            display_name=Path(src).name,
                            page_count=1,
                            notes=f"Unable to delete source file after move; locked? dest={dest} attempts={attempts}",
                            suggested_folder="",
                            suggested_name="",
                            confidence=0.0,
                            vendor="Vendor",
                            doctype="Other",
                            number="ATTN",
                            date_str="00-00-0000",
                        )
                    )
                    self.refresh_all()
                else:
                    done_log_store.update_status_by_source(src, "PENDING_DELETE", attempts)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_poller()
        super().closeEvent(event)

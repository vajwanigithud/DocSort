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
from docsort.app.ui.ocr_jobs_widget import OcrJobsWidget
from docsort.app.ui.tabs_done import DoneTab
from docsort.app.ui.tabs_needs_attention import NeedsAttentionTab
from docsort.app.ui.tabs_rename_move import RenameMoveTab
from docsort.app.ui.tabs_scanned import ScannedTab
from docsort.app.ui.tabs_settings import SettingsTab
from docsort.app.ui.tabs_splitter import SplitterTab
from docsort.app.utils import folder_validation


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DocSort")
        self.state = AppState()
        self.folder_service = folder_service
        self.source_poller: SourcePoller | None = None
        self.watcher_enabled = settings_store.get_watcher_enabled()
        self.log = logging.getLogger(__name__)
        self.folder_config = settings_store.get_folder_config()
        self.config_valid, self.config_error, self.resolved_paths = folder_validation.validate_folder_config(self.folder_config)
        if self.config_valid and self.folder_config.destination:
            self.folder_service.set_root(self.folder_config.destination)
        else:
            self.folder_service.clear_root()
        self.staging_root = self.folder_config.staging

        self.tabs = QtWidgets.QTabWidget()
        self.scanned_tab = ScannedTab(self.state, self.refresh_all, self.start_poller, self.stop_poller)
        self.splitter_tab = SplitterTab(self.state, self.refresh_all)
        self.rename_tab = RenameMoveTab(self.state, self.folder_service, self.refresh_all)
        self.attention_tab = NeedsAttentionTab(self.state, self.refresh_all)
        self.done_tab = DoneTab(self.refresh_all)
        self.jobs_tab = OcrJobsWidget()
        self.settings_tab = SettingsTab(
            self.folder_service,
            self.refresh_all,
            self._on_config_changed,
            self.start_poller,
            self.stop_poller,
        )

        self.tabs.addTab(self.scanned_tab, "Staging")
        self.tabs.addTab(self.splitter_tab, "Splitter")
        self.tabs.addTab(self.rename_tab, "Rename / Action")
        self.tabs.addTab(self.attention_tab, "Needs Attention")
        self.tabs.addTab(self.done_tab, "Done")
        self.tabs.addTab(self.jobs_tab, "OCR Jobs")
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
        self._delete_timer.start(2000)
        self._hydrate_from_disk()
        self.refresh_all()

    def _hydrate_from_disk(self) -> None:
        if not self.config_valid:
            return
        paths = self.resolved_paths or {}
        role_map = [
            ("staging", "scanned_items", "AUTO"),
            ("splitter", "splitter_items", "AUTO"),
            ("rename", "rename_items", "RENAME"),
        ]
        for role, list_name, hint in role_map:
            root = paths.get(role)
            if not root:
                continue
            try:
                self.state.hydrate_from_folder(list_name, root, route_hint=hint)
            except Exception:  # noqa: BLE001
                self.log.exception("Failed to hydrate %s from %s", list_name, root)
        self.log.info(
            "Hydration complete staging=%s splitter=%s rename=%s",
            len(self.state.scanned_items),
            len(self.state.splitter_items),
            len(self.state.rename_items),
        )

    def refresh_all(self) -> None:
        self._hydrate_from_disk()
        for tab in [
            self.scanned_tab,
            self.splitter_tab,
            self.rename_tab,
            self.attention_tab,
            self.done_tab,
            self.jobs_tab,
            self.settings_tab,
        ]:
            if hasattr(tab, "refresh"):
                tab.refresh()
        self._update_watcher_status()

    @QtCore.Slot()
    def _update_watcher_status(self) -> None:
        if not self.config_valid:
            self.status_label.setText(f"Config error: {self.config_error}")
        elif self.source_poller and self.source_poller.is_running() and self.staging_root:
            self.status_label.setText("Watcher: ON (Polling)")
        else:
            self.status_label.setText("Watcher: OFF")

    def start_poller(self) -> None:
        if not self.config_valid:
            self.log.warning("Invalid folder config; watcher not started.")
            self._update_watcher_status()
            return
        if not self.staging_root:
            self.log.warning("No staging root set; watcher not started.")
            return
        self.log.info("Scheduling poller start at %s", self.staging_root)
        threading.Thread(target=self._start_poller_bg, args=(self.staging_root,), daemon=True).start()

    def _start_poller_bg(self, path: str) -> None:
        try:
            self.source_poller = SourcePoller(path, self.state.enqueue_scanned_path)
            self.source_poller.start()
            self.staging_root = path
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

    def _on_config_changed(self) -> None:
        self.folder_config = settings_store.get_folder_config()
        self.config_valid, self.config_error, self.resolved_paths = folder_validation.validate_folder_config(self.folder_config)
        if self.config_valid and self.folder_config.destination:
            self.folder_service.set_root(self.folder_config.destination)
        else:
            self.folder_service.clear_root()
        self.staging_root = self.folder_config.staging
        self.log.info("Folder config updated; staging=%s", self.staging_root)
        if not self.config_valid:
            self.stop_poller()
        self.refresh_all()

    def _drain_pending(self) -> None:
        added = False
        while not self.state.pending_scanned_paths.empty():
            try:
                path = self.state.pending_scanned_paths.get_nowait()
            except Exception:
                break
            if not self.config_valid or not self.staging_root:
                continue
            resolved = str(Path(path).resolve())
            try:
                staging_root_path = Path(self.staging_root).resolve()
                Path(resolved).relative_to(staging_root_path)
            except Exception:
                self.log.debug("Skipping pending path outside staging folder: %s", resolved)
                continue
            if not any(str(Path(d.source_path).resolve()) == resolved for d in self.state.scanned_items):
                if resolved in done_log_store.seen_sources():
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
        pending = done_log_store.list_entries("PENDING_DELETE")
        updated = False
        # Limit batch size to avoid UI hiccups
        for ev in pending[:5]:
            src = ev.get("src")
            dest = ev.get("dest")
            attempts = int(ev.get("delete_attempts", 0))
            if not src:
                continue
            src_path = Path(src)
            dest_path = Path(dest) if dest else None
            if dest_path and not dest_path.exists():
                done_log_store.update_entry_status(ev, "DONE", attempts, last_error="dest_missing")
                updated = True
                continue
            if not src_path.exists():
                done_log_store.update_entry_status(ev, "DONE", attempts, last_error="")
                updated = True
                continue
            try:
                src_path.unlink()
                done_log_store.update_entry_status(ev, "DONE", attempts, last_error="")
                updated = True
            except Exception as exc:  # noqa: BLE001
                attempts += 1
                last_err = str(exc)
                if attempts >= 10:
                    done_log_store.update_entry_status(ev, "NEEDS_ATTENTION", attempts, last_error=last_err)
                    exists = any(Path(a.source_path).resolve() == src_path.resolve() for a in self.state.attention_items)
                    if not exists:
                        self.state.attention_items.append(
                            DocumentItem(
                                id="attn-" + uuid.uuid4().hex[:8],
                                source_path=src,
                                display_name=src_path.name,
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
                    updated = True
                else:
                    done_log_store.update_entry_status(ev, "PENDING_DELETE", attempts, last_error=last_err)
                    updated = True
        if updated:
            if hasattr(self.done_tab, "refresh"):
                self.done_tab.refresh()
            if hasattr(self.attention_tab, "refresh"):
                self.attention_tab.refresh()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_poller()
        super().closeEvent(event)

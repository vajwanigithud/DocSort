from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from docsort.app.storage import ocr_job_store

logger = logging.getLogger(__name__)


class OcrJobsWidget(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._jobs: List[Dict[str, object]] = []
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(4000)
        self._refresh_timer.timeout.connect(self.refresh_jobs)
        self._build_ui()
        self.refresh_jobs()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        try:
            if self._refresh_timer and self.auto_refresh.isChecked() and not self._refresh_timer.isActive():
                self._refresh_timer.start()
        except Exception:
            pass
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        try:
            if self._refresh_timer and self._refresh_timer.isActive():
                self._refresh_timer.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def refresh(self) -> None:
        self.refresh_jobs()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QHBoxLayout()
        self.auto_refresh = QtWidgets.QCheckBox("Auto-refresh")
        self.auto_refresh.setChecked(True)
        self.auto_refresh.toggled.connect(self._toggle_auto_refresh)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_jobs)
        self.clear_all_btn = QtWidgets.QPushButton("Clear All")
        self.clear_all_btn.clicked.connect(self._confirm_clear_all)
        header.addWidget(self.auto_refresh)
        header.addStretch()
        header.addWidget(self.refresh_btn)
        header.addWidget(self.clear_all_btn)
        layout.addLayout(header)

        self.table = QtWidgets.QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Updated", "Attempts", "Worker", "Error"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for col in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)

        self.empty_label = QtWidgets.QLabel("No OCR jobs recorded yet.")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_label)
        layout.addWidget(self.stack, 1)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("color: #555;")
        layout.addWidget(self.status_label)

    def _toggle_auto_refresh(self, enabled: bool) -> None:
        try:
            if not self._refresh_timer:
                return
            if enabled and not self._refresh_timer.isActive():
                self._refresh_timer.start()
            elif not enabled and self._refresh_timer.isActive():
                self._refresh_timer.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to toggle auto-refresh: %s", exc)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _truncate(self, text: Optional[str], limit: int = 120) -> str:
        if not text:
            return ""
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def refresh_jobs(self) -> None:
        try:
            jobs = ocr_job_store.list_recent(limit=200)
            self._jobs = jobs or []
            self._populate_table()
            self.clear_all_btn.setEnabled(bool(self._jobs))
            if not self._jobs:
                self.stack.setCurrentWidget(self.empty_label)
            else:
                self.stack.setCurrentWidget(self.table)
            self._set_status(f"Loaded {len(self._jobs)} job(s).")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load OCR jobs: %s", exc)
            self._jobs = []
            self.table.setRowCount(0)
            self.stack.setCurrentWidget(self.empty_label)
            self._set_status("Unable to load OCR jobs.")

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for job in self._jobs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            file_path = str(job.get("file_path") or "")
            status = str(job.get("status") or "")
            updated = str(job.get("updated_at") or "")
            try:
                attempts_raw = int(job.get("attempts") or 0)
            except Exception:
                attempts_raw = 0
            attempts = f"{attempts_raw}/{ocr_job_store.DEFAULT_MAX_ATTEMPTS}"
            worker = str(job.get("worker_id") or "")
            error_full = str(job.get("last_error") or "")
            error = self._truncate(error_full)
            values = [file_path, status, updated, attempts, worker, error]
            for col, val in enumerate(values):
                item = QtWidgets.QTableWidgetItem(val)
                item.setData(QtCore.Qt.UserRole, job)
                if col == 1:
                    item.setText(str(val).upper())
                    self._style_status_item(item, status)
                if col == 5:
                    item.setToolTip(error_full or "")
                self.table.setItem(row, col, item)

    def _job_for_row(self, row: int) -> Optional[Dict[str, object]]:
        if row < 0 or row >= self.table.rowCount():
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        job = item.data(QtCore.Qt.UserRole)
        if isinstance(job, dict):
            return job
        return None

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        job = self._job_for_row(row)
        if not job:
            return
        menu = QtWidgets.QMenu(self)
        retry_action = menu.addAction("Retry OCR")
        clear_action = menu.addAction("Clear Job")
        copy_action = menu.addAction("Copy Path")
        copy_error_action = menu.addAction("Copy Error")
        if not job.get("last_error"):
            copy_error_action.setEnabled(False)
        try:
            retry_allowed = ocr_job_store.can_retry(job, max_attempts=ocr_job_store.DEFAULT_MAX_ATTEMPTS)
        except Exception:
            retry_allowed = True
        if not retry_allowed:
            retry_action.setEnabled(False)
            retry_action.setText("Retry OCR (max attempts reached)")
        chosen = menu.exec(self.table.mapToGlobal(pos))
        if chosen == retry_action:
            self._retry_job(job)
        elif chosen == clear_action:
            self._clear_job(job)
        elif chosen == copy_action:
            self._copy_path(job)
        elif chosen == copy_error_action:
            self._copy_error(job)

    def _retry_job(self, job: Dict[str, object]) -> None:
        path = str(job.get("file_path") or "")
        max_pages = int(job.get("max_pages") or 1)
        fingerprint = job.get("file_fingerprint")
        try:
            if not ocr_job_store.can_retry(job, max_attempts=ocr_job_store.DEFAULT_MAX_ATTEMPTS):
                self._set_status("Max attempts reached; cannot retry.")
                return
        except Exception:
            pass
        try:
            ocr_job_store.upsert_job(path, max_pages=max_pages, status="QUEUED", fingerprint=fingerprint, worker_id="ui")
            self._set_status("Job queued.")
            self.refresh_jobs()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to retry OCR job %s: %s", path, exc)
            self._set_status("Failed to queue job.")

    def _clear_job(self, job: Dict[str, object]) -> None:
        path = str(job.get("file_path") or "")
        max_pages = int(job.get("max_pages") or 1)
        fingerprint = job.get("file_fingerprint")
        try:
            ocr_job_store.clear_job(path, max_pages=max_pages, fingerprint=fingerprint)
            self._set_status("Job cleared.")
            self.refresh_jobs()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to clear OCR job %s: %s", path, exc)
            self._set_status("Failed to clear job.")

    def _copy_path(self, job: Dict[str, object]) -> None:
        path = str(job.get("file_path") or "")
        QtGui.QGuiApplication.clipboard().setText(path)
        self._set_status("Path copied to clipboard.")

    def _copy_error(self, job: Dict[str, object]) -> None:
        err = str(job.get("last_error") or "")
        if not err:
            return
        QtGui.QGuiApplication.clipboard().setText(err)
        self._set_status("Error copied to clipboard.")

    def _style_status_item(self, item: QtWidgets.QTableWidgetItem, status: str) -> None:
        status_upper = (status or "").upper()
        colors = {
            "QUEUED": ("#eeeeee", "#444444"),
            "RUNNING": ("#cce5ff", "#004085"),
            "DONE": ("#d4edda", "#155724"),
            "FAILED": ("#f8d7da", "#721c24"),
        }
        bg, fg = colors.get(status_upper, ("#eeeeee", "#444444"))
        item.setBackground(QtGui.QColor(bg))
        item.setForeground(QtGui.QColor(fg))
        font = item.font()
        font.setBold(True)
        item.setFont(font)

    def _confirm_clear_all(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "Clear All OCR Jobs",
            "Are you sure you want to clear all OCR jobs?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            removed = ocr_job_store.clear_all_jobs()
            self._set_status(f"Cleared {removed} job(s).")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to clear all OCR jobs: %s", exc)
            self._set_status("Failed to clear jobs.")
        self.refresh_jobs()

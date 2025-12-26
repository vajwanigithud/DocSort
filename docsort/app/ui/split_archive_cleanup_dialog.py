from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from docsort.app.services import split_archive_cleanup_service


class _CleanupWorker(QtCore.QObject):
    finished = QtCore.Signal(str)

    def __init__(self, source_root: Path, apply: bool) -> None:
        super().__init__()
        self.source_root = source_root
        self.apply = apply

    @QtCore.Slot()
    def run(self) -> None:
        report = split_archive_cleanup_service.run_cleanup(self.source_root, self.apply)
        self.finished.emit(report)


class SplitArchiveCleanupDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None, source_root: Path) -> None:
        super().__init__(parent)
        self.source_root = source_root
        self.setWindowTitle("Cleanup Split Archive")
        self._worker: _CleanupWorker | None = None
        self._thread: QtCore.QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        self.report_area = QtWidgets.QPlainTextEdit()
        self.report_area.setReadOnly(True)
        self.report_area.setMinimumHeight(240)
        layout.addWidget(self.report_area)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.dry_run_btn = QtWidgets.QPushButton("Dry Run")
        self.dry_run_btn.setDefault(True)
        self.apply_btn = QtWidgets.QPushButton("Apply Cleanup…")
        btn_row.addWidget(self.dry_run_btn)
        btn_row.addWidget(self.apply_btn)
        layout.addLayout(btn_row)

        self.dry_run_btn.clicked.connect(lambda: self._start_cleanup(apply=False))
        self.apply_btn.clicked.connect(self._confirm_and_apply)

    def _set_running(self, running: bool) -> None:
        self.dry_run_btn.setEnabled(not running)
        self.apply_btn.setEnabled(not running)
        if running:
            self.report_area.setPlainText("Running cleanup...\n")

    def _confirm_and_apply(self) -> None:
        resp = QtWidgets.QMessageBox.question(
            self,
            "Confirm Cleanup",
            "This will permanently delete files in _split_archive that are verified safe. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return
        self._start_cleanup(apply=True)

    def _start_cleanup(self, apply: bool) -> None:
        if self._thread and self._thread.isRunning():
            return
        self._set_running(True)
        self._thread = QtCore.QThread(self)
        self._worker = _CleanupWorker(self.source_root, apply)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    @QtCore.Slot(str)
    def _on_finished(self, report: str) -> None:
        self.report_area.setPlainText(report or "No report returned.")

    @QtCore.Slot()
    def _on_thread_finished(self) -> None:
        self._set_running(False)
        self._thread = None
        self._worker = None

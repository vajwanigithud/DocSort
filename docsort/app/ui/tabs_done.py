import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from docsort.app.storage import done_log_store
from docsort.app.services import move_service, undo_store


class DoneTab(QtWidgets.QWidget):
    def __init__(self, refresh_all) -> None:
        super().__init__()
        self.refresh_all = refresh_all
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget()
        layout.addWidget(self.list_widget, 1)

        self.preview = QtWidgets.QLabel("Done")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa; padding: 12px;")

        self.details = QtWidgets.QTextEdit()
        self.details.setReadOnly(True)
        self.undo_btn = QtWidgets.QPushButton("Undo Last Move")
        detail_layout = QtWidgets.QVBoxLayout()
        detail_layout.addWidget(self.preview)
        detail_layout.addWidget(QtWidgets.QLabel("Details"))
        detail_layout.addWidget(self.details)
        detail_layout.addWidget(self.undo_btn)

        layout.addLayout(detail_layout, 2)

        self.list_widget.itemSelectionChanged.connect(self._update_preview)
        self.undo_btn.clicked.connect(self._undo_last)

    def _selected_item(self):
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _update_preview(self) -> None:
        ev = self._selected_item()
        if not ev:
            self.preview.setText("Done")
            self.details.clear()
            return
        self.preview.setText(f"Done\n{ev.get('final_filename', ev.get('dest', '') )}")
        detail_lines = [
            f"Time: {ev.get('timestamp','')}",
            f"Source: {ev.get('src','')}",
            f"Destination: {ev.get('dest','')}",
            f"Folder: {ev.get('folder','')}",
            f"Filename: {ev.get('final_filename','')}",
            f"Item ID: {ev.get('item_id','')}",
            f"Status: {ev.get('status','')}",
            f"Delete attempts: {ev.get('delete_attempts','')}",
            f"Last error: {ev.get('last_error','')}",
        ]
        self.details.setPlainText("\n".join(detail_lines))

    def refresh(self) -> None:
        events = done_log_store.list_recent(500)
        self.list_widget.clear()
        for ev in events:
            text = f"{ev.get('timestamp','')} -> {ev.get('final_filename','')}"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, ev)
            self.list_widget.addItem(item)
        self._update_preview()

    def _undo_last(self) -> None:
        record = undo_store.pop_last()
        if not record:
            QtWidgets.QMessageBox.information(self, "Undo Move", "No moves to undo.")
            return
        src = record.get("moved_dest")
        dest = record.get("original_src")
        if not src or not dest:
            QtWidgets.QMessageBox.warning(self, "Undo Move", "Invalid undo record.")
            return
        try:
            move_service.ensure_dir(str(Path(dest).parent))
            src_path = Path(src)
            dest_path = Path(dest)
            if not src_path.exists():
                QtWidgets.QMessageBox.warning(self, "Undo Move", f"File missing: {src}")
                return
            if src_path.drive == dest_path.drive:
                os.replace(src_path, dest_path)
            else:
                import shutil

                shutil.move(str(src_path), str(dest_path))
            QtWidgets.QMessageBox.information(self, "Undo Move", f"Restored to {dest_path}")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Undo Move", f"Failed to undo: {exc}")

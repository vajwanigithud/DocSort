import logging
import os
from pathlib import Path

from PySide6 import QtWidgets

from docsort.app.services.folder_service import FolderService
from docsort.app.storage import settings_store
from docsort.app.utils import folder_validation


class SettingsTab(QtWidgets.QWidget):
    def __init__(self, folder_service: FolderService, refresh_all, on_config_changed, start_watcher, stop_watcher) -> None:
        super().__init__()
        self.folder_service = folder_service
        self.refresh_all = refresh_all
        self.on_config_changed = on_config_changed
        self.start_watcher_cb = start_watcher
        self.stop_watcher_cb = stop_watcher
        self.log = logging.getLogger(__name__)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        self.note_label = QtWidgets.QLabel("Configure all four folders. Monitoring uses safe polling (1s interval) on Windows.")
        self.note_label.setStyleSheet("color: #555;")
        layout.addWidget(self.note_label)

        self.error_label = QtWidgets.QLabel("")
        self.error_label.setStyleSheet("color: #b33;")
        layout.addWidget(self.error_label)

        self.staging_field, self.change_staging_btn = self._folder_row(layout, "Staging Folder (Intake)", self._change_staging)
        self.splitter_field, self.change_splitter_btn = self._folder_row(layout, "Splitter Folder", self._change_splitter)
        self.rename_field, self.change_rename_btn = self._folder_row(layout, "Rename / Action Folder", self._change_rename)
        self.destination_field, self.change_destination_btn = self._folder_row(layout, "Destination Folder", self._change_destination)

        self.open_logs_btn = QtWidgets.QPushButton("Open Logs")
        layout.addWidget(self.open_logs_btn)

        self.folder_list = QtWidgets.QListWidget()
        layout.addWidget(self.folder_list)

        self.create_folder_btn = QtWidgets.QPushButton("Create New Destination Subfolder")
        layout.addWidget(self.create_folder_btn)

        help_text = QtWidgets.QLabel("Destination subfolders appear in Rename / Action.")
        help_text.setStyleSheet("color: #555;")
        layout.addWidget(help_text)
        layout.addStretch()

        self.change_staging_btn.clicked.connect(self._change_staging)
        self.change_splitter_btn.clicked.connect(self._change_splitter)
        self.change_rename_btn.clicked.connect(self._change_rename)
        self.change_destination_btn.clicked.connect(self._change_destination)
        self.create_folder_btn.clicked.connect(self._create_folder)
        self.open_logs_btn.clicked.connect(self._open_logs)

    def _folder_row(self, parent_layout: QtWidgets.QVBoxLayout, label: str, on_click) -> tuple[QtWidgets.QLineEdit, QtWidgets.QPushButton]:
        parent_layout.addWidget(QtWidgets.QLabel(label))
        row = QtWidgets.QHBoxLayout()
        field = QtWidgets.QLineEdit()
        field.setReadOnly(True)
        btn = QtWidgets.QPushButton("Change")
        row.addWidget(field)
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return field, btn

    def _change_staging(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Staging Folder")
        if selected:
            settings_store.set_staging_root(selected)
            self._after_change(f"Staging folder changed to {selected}")

    def _change_splitter(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Splitter Folder")
        if selected:
            settings_store.set_splitter_root(selected)
            self._after_change(f"Splitter folder changed to {selected}")

    def _change_rename(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Rename / Action Folder")
        if selected:
            settings_store.set_rename_root(selected)
            self._after_change(f"Rename / Action folder changed to {selected}")

    def _change_destination(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if selected:
            settings_store.set_destination_root(selected)
            self.folder_service.set_root(selected)
            self._after_change(f"Destination root changed to {selected}")

    def _after_change(self, log_msg: str) -> None:
        self.on_config_changed()
        self.refresh_all()
        self.log.info(log_msg)

    def _create_folder(self) -> None:
        if not self.folder_service.is_configured and not settings_store.get_destination_root():
            QtWidgets.QMessageBox.warning(self, "Destination Root", "Set a destination root first.")
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "Create New Folder", "Folder name:")
        if ok and text:
            name = text.replace(" ", "_")
            try:
                self.folder_service.create_folder(name)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "Destination Root", f"Unable to create folder: {exc}")
                return
            self.refresh_all()
            self.log.info("Created new destination folder: %s", name)

    def _open_logs(self) -> None:
        log_path = Path(__file__).resolve().parent.parent / "storage" / "app.log"
        if not log_path.exists():
            QtWidgets.QMessageBox.information(self, "Logs", "Log file does not exist yet.")
            return
        try:
            os.startfile(log_path)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Logs", f"Failed to open logs: {exc}")

    def refresh(self) -> None:
        cfg = settings_store.get_folder_config()
        ok, msg, _paths = folder_validation.validate_folder_config(cfg)
        if ok:
            self.error_label.setStyleSheet("color: #3a7;")
            self.error_label.setText("Folder configuration valid.")
        else:
            self.error_label.setStyleSheet("color: #b33;")
            self.error_label.setText(msg or "Folder configuration incomplete.")

        self.staging_field.setText(cfg.staging or "Not set")
        self.splitter_field.setText(cfg.splitter or "Not set")
        self.rename_field.setText(cfg.rename or "Not set")
        self.destination_field.setText(cfg.destination or "Not set")

        self.folder_list.clear()
        for name in self.folder_service.list_folders():
            self.folder_list.addItem(name)

from pathlib import Path

import logging
import os
from pathlib import Path

from PySide6 import QtWidgets

from docsort.app.services.folder_service import FolderService
from docsort.app.storage import settings_store


class SettingsTab(QtWidgets.QWidget):
    def __init__(self, folder_service: FolderService, refresh_all, on_source_changed, start_watcher, stop_watcher) -> None:
        super().__init__()
        self.folder_service = folder_service
        self.refresh_all = refresh_all
        self.on_source_changed = on_source_changed
        self.start_watcher_cb = start_watcher
        self.stop_watcher_cb = stop_watcher
        self.log = logging.getLogger(__name__)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        # Source folder section
        layout.addWidget(QtWidgets.QLabel("Source Folder"))
        source_row = QtWidgets.QHBoxLayout()
        self.source_field = QtWidgets.QLineEdit()
        self.source_field.setReadOnly(True)
        self.change_source_btn = QtWidgets.QPushButton("Change Source Folder")
        source_row.addWidget(self.source_field)
        source_row.addWidget(self.change_source_btn)
        layout.addLayout(source_row)

        layout.addWidget(QtWidgets.QLabel("Destination Root"))
        root_row = QtWidgets.QHBoxLayout()
        self.root_field = QtWidgets.QLineEdit()
        self.root_field.setReadOnly(True)
        self.change_root_btn = QtWidgets.QPushButton("Change Destination Root")
        root_row.addWidget(self.root_field)
        root_row.addWidget(self.change_root_btn)
        layout.addLayout(root_row)

        buttons_row = QtWidgets.QHBoxLayout()
        self.start_watcher_btn = QtWidgets.QPushButton("Start Monitoring")
        self.stop_watcher_btn = QtWidgets.QPushButton("Stop Monitoring")
        buttons_row.addWidget(self.start_watcher_btn)
        buttons_row.addWidget(self.stop_watcher_btn)
        layout.addLayout(buttons_row)

        self.note_label = QtWidgets.QLabel("Monitoring uses safe polling (1s interval) for stability on Windows.")
        self.note_label.setStyleSheet("color: #555;")
        layout.addWidget(self.note_label)

        self.open_logs_btn = QtWidgets.QPushButton("Open Logs")
        layout.addWidget(self.open_logs_btn)

        self.folder_list = QtWidgets.QListWidget()
        layout.addWidget(self.folder_list)

        self.create_folder_btn = QtWidgets.QPushButton("Create New Folder")
        layout.addWidget(self.create_folder_btn)

        help_text = QtWidgets.QLabel("Folders created here will be available in Rename & Move.")
        help_text.setStyleSheet("color: #555;")
        layout.addWidget(help_text)
        layout.addStretch()

        self.change_source_btn.clicked.connect(self._change_source)
        self.change_root_btn.clicked.connect(self._change_root)
        self.create_folder_btn.clicked.connect(self._create_folder)
        self.start_watcher_btn.clicked.connect(self.start_watcher_cb)
        self.stop_watcher_btn.clicked.connect(self.stop_watcher_cb)
        self.open_logs_btn.clicked.connect(self._open_logs)

    def _change_source(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if selected:
            settings_store.set_source_root(selected)
            self.on_source_changed(selected)
            self.refresh_all()
            self.log.info("Source folder changed to %s", selected)

    def _change_root(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Destination Root")
        if selected:
            settings_store.set_destination_root(selected)
            self.folder_service.set_root(selected)
            self.refresh_all()
            self.log.info("Destination root changed to %s", selected)

    def _create_folder(self) -> None:
        if not self.folder_service.is_configured and not settings_store.get_destination_root():
            QtWidgets.QMessageBox.warning(self, "Destination Root", "Set a destination root first.")
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "Create New Folder", "Folder name:")
        if ok and text:
            name = text.replace(" ", "_")
            self.folder_service.create_folder(name)
            self.refresh_all()
            self.log.info("Created new destination folder: %s", name)

    def _toggle_watcher(self, checked: bool) -> None:
        settings_store.set_watcher_enabled(checked)
        self.on_watcher_toggle(checked)
        self.log.info("Watcher toggled to %s", checked)

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
        source_val = settings_store.get_source_root()
        if source_val:
            self.source_field.setText(source_val)
        else:
            self.source_field.setText("Not set")
        root_val = settings_store.get_destination_root()
        if root_val:
            self.root_field.setText(root_val)
        else:
            self.root_field.setText("Not set")
        self.folder_list.clear()
        for name in self.folder_service.list_folders():
            self.folder_list.addItem(name)

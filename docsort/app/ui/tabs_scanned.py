import logging
import shutil
import uuid
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import move_service, pdf_utils, routing_service
from docsort.app.storage import settings_store, split_completion_store
from docsort.app.ui import ocr_status_utils
from docsort.app.ui.pdf_preview_widget import PdfPreviewWidget
from docsort.app.utils import folder_validation


class ScannedTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, refresh_all, start_monitor, stop_monitor) -> None:
        super().__init__()
        self.state = state
        self.refresh_all = refresh_all
        self.start_monitor_cb = start_monitor
        self.stop_monitor_cb = stop_monitor
        self.log = logging.getLogger(__name__)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("Staging Folder:"))
        self.source_label = QtWidgets.QLabel("Not set")
        header.addWidget(self.source_label, 1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh from Staging")
        self.warning_label = QtWidgets.QLabel("Set all folders in Settings")
        self.warning_label.setStyleSheet("color: #b33;")
        self.start_monitor_btn = QtWidgets.QPushButton("Start Monitoring")
        self.stop_monitor_btn = QtWidgets.QPushButton("Stop Monitoring")
        header.addWidget(self.refresh_btn)
        header.addWidget(self.start_monitor_btn)
        header.addWidget(self.stop_monitor_btn)
        header.addWidget(self.warning_label)
        main_layout.addLayout(header)

        layout = QtWidgets.QHBoxLayout()
        header_controls = QtWidgets.QHBoxLayout()
        self.show_completed = QtWidgets.QCheckBox("Show completed")
        self.show_completed.setChecked(False)
        header_controls.addWidget(self.show_completed)
        header_controls.addStretch()
        main_layout.addLayout(header_controls)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        layout.addWidget(self.list_widget, 1)

        self.preview_pdf = PdfPreviewWidget()
        self.preview_image = QtWidgets.QLabel("Preview")
        self.preview_image.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_image.setStyleSheet("border: 1px solid #ccc; background: #fafafa; padding: 12px;")
        self.preview_stack = QtWidgets.QStackedWidget()
        self.preview_stack.addWidget(self.preview_pdf)
        self.preview_stack.addWidget(self.preview_image)
        self.preview_stack.setCurrentWidget(self.preview_image)
        layout.addWidget(self.preview_stack, 2)

        actions = QtWidgets.QVBoxLayout()
        self.to_splitter_btn = QtWidgets.QPushButton("Send to Splitter")
        self.to_rename_btn = QtWidgets.QPushButton("Send to Rename / Action")
        actions.addWidget(self.to_splitter_btn)
        actions.addWidget(self.to_rename_btn)
        self.auto_route_all_btn = QtWidgets.QPushButton("Auto-route all")
        actions.addWidget(self.auto_route_all_btn)

        rule_label = QtWidgets.QLabel("Images default to Rename & Move. PDFs default to Auto.")
        rule_label.setWordWrap(True)
        rule_label.setStyleSheet("color: #555;")
        actions.addWidget(rule_label)

        actions.addStretch()
        layout.addLayout(actions, 1)

        self.list_widget.itemSelectionChanged.connect(self._update_preview)
        self.to_splitter_btn.clicked.connect(self._send_to_splitter)
        self.to_rename_btn.clicked.connect(self._send_to_rename)
        self.auto_route_all_btn.clicked.connect(self._auto_route_all)
        self.refresh_btn.clicked.connect(self._refresh_from_source)
        self.start_monitor_btn.clicked.connect(self.start_monitor_cb)
        self.stop_monitor_btn.clicked.connect(self.stop_monitor_cb)
        self.show_completed.toggled.connect(self.refresh)
        main_layout.addLayout(layout)

    def _selected_item(self) -> DocumentItem | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _config_status(self) -> tuple[bool, str, settings_store.FolderConfig]:
        cfg = settings_store.get_folder_config()
        ok, msg, _paths = folder_validation.validate_folder_config(cfg)
        return ok, msg, cfg

    def _staging_root_path(self) -> Path | None:
        cfg = settings_store.get_folder_config()
        staging = cfg.staging
        if not staging:
            return None
        try:
            return Path(staging).resolve()
        except Exception:
            return None

    def _is_in_staging_folder(self, path: Path) -> bool:
        root = self._staging_root_path()
        if not root:
            return False
        try:
            path.resolve().relative_to(root)
            return True
        except Exception:
            return False

    def _move_doc_to_role(self, doc: DocumentItem, role: str) -> bool:
        ok, msg, cfg = self._config_status()
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Move", msg or "Configure folders first.")
            return False
        target_root = cfg.splitter if role == "splitter" else cfg.rename
        if not target_root:
            QtWidgets.QMessageBox.warning(self, "Move", "Target folder not configured.")
            return False

        src_path = Path(doc.source_path)
        if not src_path.exists():
            QtWidgets.QMessageBox.warning(self, "Move", "Source file is missing.")
            return False
        staging_root = cfg.staging
        if staging_root:
            try:
                staging_path = Path(staging_root).resolve()
                if staging_path not in src_path.resolve().parents:
                    QtWidgets.QMessageBox.warning(self, "Move", "File is not in the Staging folder.")
                    return False
            except Exception:
                pass

        dest_dir = Path(target_root)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Move", f"Cannot create target folder: {exc}")
            return False
        if dest_dir in src_path.parents:
            doc.source_path = str(src_path.resolve())
            doc.display_name = src_path.name
            return True

        try:
            dest_path = Path(move_service.unique_path(str(dest_dir), src_path.name))
            shutil.move(str(src_path), dest_path)
            doc.source_path = str(dest_path.resolve())
            doc.display_name = dest_path.name
            return True
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Move", f"Failed to move file: {exc}")
            return False

    def _send_to_splitter(self) -> None:
        selected = self._selected_item()
        if selected:
            if self._move_doc_to_role(selected, "splitter"):
                self.state.move_between_named_lists("scanned_items", "splitter_items", selected.id)
                self.refresh_all()

    def _send_to_rename(self) -> None:
        selected = self._selected_item()
        if selected:
            if self._move_doc_to_role(selected, "rename"):
                self.state.move_between_named_lists("scanned_items", "rename_items", selected.id)
                self.refresh_all()

    def _auto_route_all(self) -> None:
        ok, msg, _cfg = self._config_status()
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Auto-route", msg or "Configure folders first.")
            return
        routes = routing_service.route_items(self.state.scanned_items)
        for item, target in routes:
            if target == "splitter":
                if self._move_doc_to_role(item, "splitter"):
                    self.state.move_between_named_lists("scanned_items", "splitter_items", item.id)
            else:
                if self._move_doc_to_role(item, "rename"):
                    self.state.move_between_named_lists("scanned_items", "rename_items", item.id)
        self.refresh_all()

    def refresh(self) -> None:
        ok, msg, cfg = self._config_status()
        staging_root = cfg.staging if cfg else None
        self.source_label.setText(staging_root or "Not set")
        self.warning_label.setVisible(not ok)
        if not ok:
            self.warning_label.setText(msg or "Configure folders in Settings")
        else:
            self.warning_label.setText("")
        self.refresh_btn.setEnabled(ok)
        self.start_monitor_btn.setEnabled(ok)
        self.to_splitter_btn.setEnabled(ok)
        self.to_rename_btn.setEnabled(ok)
        self.auto_route_all_btn.setEnabled(ok)
        self.list_widget.clear()
        show_completed = self.show_completed.isChecked()
        for doc in self.state.scanned_items:
            path = Path(doc.source_path)
            if not self._is_in_staging_folder(path):
                continue
            split_completion_store.prune_if_changed(path)
            is_done = split_completion_store.is_split_complete(path)
            if not show_completed and is_done:
                continue
            status = ocr_status_utils.get_ocr_status(path)
            badge = ocr_status_utils.format_ocr_badge(status)
            label = f"{doc.display_name} ({doc.page_count}p)"
            if badge:
                label = f"{label} - {badge}"
            if is_done:
                label = f"{label} ✅"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, doc)
            item.setToolTip(ocr_status_utils.get_ocr_tooltip(path))
            self.list_widget.addItem(item)
        if self.list_widget.count() and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)
        self._update_preview()

    def _refresh_from_source(self) -> None:
        ok, msg, cfg = self._config_status()
        if not ok:
            self.warning_label.setText(msg or "Configure folders in Settings")
            self.warning_label.setVisible(True)
            return
        source_root = cfg.staging if cfg else None
        if not source_root:
            return
        root_path = Path(source_root)
        if not root_path.exists():
            self.warning_label.setText("Staging folder missing")
            self.warning_label.setVisible(True)
            return
        existing_paths = {Path(doc.source_path).resolve() for doc in self.state.scanned_items}
        allowed_ext = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        new_items = []
        show_completed = self.show_completed.isChecked()
        for path in root_path.iterdir():
            if path.suffix.lower() not in allowed_ext or not path.is_file():
                continue
            split_completion_store.prune_if_changed(path)
            if (not show_completed) and split_completion_store.is_split_complete(path):
                continue
            abs_path = path.resolve()
            if abs_path in existing_paths:
                continue
            page_count = 1
            note = ""
            if path.suffix.lower() == ".pdf":
                page_count, err = pdf_utils.get_pdf_page_count(str(abs_path))
                if err:
                    note = f"page_count_error={err}"
            new_items.append(
                DocumentItem(
                    id=str(uuid.uuid4()),
                    source_path=str(abs_path),
                    display_name=path.name,
                    page_count=page_count,
                    notes=note,
                    suggested_folder="",
                    suggested_name="",
                    confidence=0.0,
                    vendor="Vendor",
                    doctype="Type",
                    number="000",
                    date_str="00-00-0000",
                    route_hint="AUTO",
                )
            )
        if new_items:
            self.state.scanned_items.extend(new_items)
        self.refresh()

    def _clear_preview(self) -> None:
        self.preview_pdf.clear()
        self.preview_image.setPixmap(QtGui.QPixmap())
        self.preview_image.setText("Preview")
        self.preview_stack.setCurrentWidget(self.preview_image)

    def _update_preview(self) -> None:
        doc = self._selected_item()
        if not doc:
            self._clear_preview()
            return
        path = Path(doc.source_path)
        if not path.exists():
            self._clear_preview()
            self.preview_image.setText("Preview unavailable")
            return
        try:
            if path.suffix.lower() == ".pdf":
                self.preview_pdf.force_release_document()
                ok = self.preview_pdf.load_pdf(str(path))
                if ok:
                    self.preview_pdf.set_page(0)
                    self.preview_stack.setCurrentWidget(self.preview_pdf)
                    self.log.info("Scanned preview loaded PDF: %s", path)
                else:
                    self._clear_preview()
                    self.preview_image.setText("Preview unavailable")
            else:
                pix = QtGui.QPixmap(str(path))
                if pix.isNull():
                    self._clear_preview()
                    self.preview_image.setText("Preview unavailable")
                else:
                    self.preview_image.setPixmap(pix.scaled(self.preview_image.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                    self.preview_stack.setCurrentWidget(self.preview_image)
                    self.log.info("Scanned preview loaded image: %s", path)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Scanned preview failed for %s: %s", path, exc)
            self._clear_preview()
            self.preview_image.setText("Preview unavailable")


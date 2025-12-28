import logging
import uuid
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import pdf_utils, routing_service
from docsort.app.storage import ocr_cache_store, settings_store, split_completion_store
from docsort.app.ui import ocr_status_utils
from docsort.app.ui.pdf_preview_widget import PdfPreviewWidget


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
        header.addWidget(QtWidgets.QLabel("Source Folder:"))
        self.source_label = QtWidgets.QLabel("Not set")
        header.addWidget(self.source_label, 1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh from Source")
        self.warning_label = QtWidgets.QLabel("Set Source Folder in Settings")
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
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
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
        self.to_rename_btn = QtWidgets.QPushButton("Send to Rename & Move")
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

    def _send_to_splitter(self) -> None:
        selected = self._selected_item()
        if selected:
            self.state.move_between_named_lists("scanned_items", "splitter_items", selected.id)
            self.refresh_all()

    def _send_to_rename(self) -> None:
        selected = self._selected_item()
        if selected:
            self.state.move_between_named_lists("scanned_items", "rename_items", selected.id)
            self.refresh_all()

    def _auto_route_all(self) -> None:
        routes = routing_service.route_items(self.state.scanned_items)
        for item, target in routes:
            if target == "splitter":
                self.state.move_between_named_lists("scanned_items", "splitter_items", item.id)
            else:
                self.state.move_between_named_lists("scanned_items", "rename_items", item.id)
        self.refresh_all()

    def refresh(self) -> None:
        source_root = settings_store.get_source_root()
        self.source_label.setText(source_root or "Not set")
        self.warning_label.setVisible(not bool(source_root))
        self.list_widget.clear()
        show_completed = self.show_completed.isChecked()
        for doc in self.state.scanned_items:
            path = Path(doc.source_path)
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
        source_root = settings_store.get_source_root()
        if not source_root:
            return
        root_path = Path(source_root)
        if not root_path.exists():
            self.warning_label.setText("Source folder missing")
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

    def _cached_ocr_text(self, path: Path) -> str:
        fingerprint = ""
        try:
            fingerprint = ocr_cache_store.compute_fingerprint(path)
        except Exception:
            fingerprint = ""
        try:
            return ocr_cache_store.get_cached_text(
                str(path),
                max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                fingerprint=fingerprint or None,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Failed to read cached OCR text for %s: %s", path, exc)
            return ""

    def _handle_rerun_ocr(self, path: Path) -> None:
        try:
            fingerprint = ocr_cache_store.compute_fingerprint(path)
            ocr_cache_store.delete_cached_text(
                str(path),
                max_pages=ocr_status_utils.OCR_STATUS_PAGES,
                fingerprint=fingerprint or None,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Failed to clear OCR cache for %s: %s", path, exc)
        self.refresh()

    def _show_cached_ocr_text(self, path: Path) -> None:
        cached_text = self._cached_ocr_text(path)
        if not cached_text:
            QtWidgets.QMessageBox.information(self, "OCR Text", "No cached OCR text found.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"OCR Text - {path.name}")
        layout = QtWidgets.QVBoxLayout(dialog)
        text_widget = QtWidgets.QPlainTextEdit()
        text_widget.setReadOnly(True)
        text_widget.setPlainText(cached_text)
        layout.addWidget(text_widget)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.resize(700, 500)
        dialog.exec()

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        doc = item.data(QtCore.Qt.UserRole)
        if not isinstance(doc, DocumentItem):
            return
        path = Path(doc.source_path)
        menu = QtWidgets.QMenu(self.list_widget)
        rerun_action = menu.addAction("Re-run OCR")
        view_action = menu.addAction("View OCR Text")
        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == rerun_action:
            self._handle_rerun_ocr(path)
        elif chosen == view_action:
            self._show_cached_ocr_text(path)

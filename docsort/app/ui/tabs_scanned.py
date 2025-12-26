import logging
import uuid
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import pdf_utils, routing_service
from docsort.app.storage import settings_store
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
        self.list_widget = QtWidgets.QListWidget()
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

        routing_group = QtWidgets.QGroupBox("Routing")
        routing_layout = QtWidgets.QVBoxLayout(routing_group)
        self.route_auto = QtWidgets.QRadioButton("Auto")
        self.route_split = QtWidgets.QRadioButton("Send to Splitter")
        self.route_rename = QtWidgets.QRadioButton("Send to Rename & Move")
        self.route_auto.setChecked(True)
        self.route_group = QtWidgets.QButtonGroup(self)
        for rb in [self.route_auto, self.route_split, self.route_rename]:
            self.route_group.addButton(rb)
            routing_layout.addWidget(rb)
        actions.addWidget(routing_group)

        self.route_now_btn = QtWidgets.QPushButton("Route Now")
        self.auto_route_all_btn = QtWidgets.QPushButton("Auto-route all")
        actions.addWidget(self.route_now_btn)
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
        self.route_now_btn.clicked.connect(self._route_selected)
        self.auto_route_all_btn.clicked.connect(self._auto_route_all)
        self.refresh_btn.clicked.connect(self._refresh_from_source)
        self.start_monitor_btn.clicked.connect(self.start_monitor_cb)
        self.stop_monitor_btn.clicked.connect(self.stop_monitor_cb)
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

    def _route_selected(self) -> None:
        selected = self._selected_item()
        if not selected:
            return
        selected.route_hint = (
            "SPLIT" if self.route_split.isChecked() else "RENAME" if self.route_rename.isChecked() else "AUTO"
        )
        target = routing_service.route_item(selected)
        if target == "splitter":
            self.state.move_between_named_lists("scanned_items", "splitter_items", selected.id)
        else:
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
        for doc in self.state.scanned_items:
            item = QtWidgets.QListWidgetItem(f"{doc.display_name} ({doc.page_count}p)")
            item.setData(QtCore.Qt.UserRole, doc)
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
        for path in root_path.iterdir():
            if path.suffix.lower() not in allowed_ext or not path.is_file():
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

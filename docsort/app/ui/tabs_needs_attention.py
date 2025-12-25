from PySide6 import QtCore, QtWidgets

from docsort.app.core.state import AppState, DocumentItem


class NeedsAttentionTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, refresh_all) -> None:
        super().__init__()
        self.state = state
        self.refresh_all = refresh_all
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget()
        layout.addWidget(self.list_widget, 1)

        self.preview = QtWidgets.QLabel("Preview")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa; padding: 12px;")
        layout.addWidget(self.preview, 2)

        side = QtWidgets.QVBoxLayout()
        self.retry_btn = QtWidgets.QPushButton("Retry (mock)")
        self.to_splitter_btn = QtWidgets.QPushButton("Send to Splitter")
        self.archive_btn = QtWidgets.QPushButton("Archive (mock)")
        side.addWidget(self.retry_btn)
        side.addWidget(self.to_splitter_btn)
        side.addWidget(self.archive_btn)
        side.addStretch()
        layout.addLayout(side, 1)

        self.list_widget.itemSelectionChanged.connect(self._update_preview)
        self.retry_btn.clicked.connect(self._retry)
        self.to_splitter_btn.clicked.connect(self._send_to_splitter)
        self.archive_btn.clicked.connect(self._archive)

    def _selected_item(self) -> DocumentItem | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _update_preview(self) -> None:
        doc = self._selected_item()
        text = f"Preview\n{doc.display_name}" if doc else "Preview"
        self.preview.setText(text)

    def _retry(self) -> None:
        doc = self._selected_item()
        if doc:
            self.state.move_between_named_lists("attention_items", "rename_items", doc.id)
            self.refresh_all()

    def _send_to_splitter(self) -> None:
        doc = self._selected_item()
        if doc:
            self.state.move_between_named_lists("attention_items", "splitter_items", doc.id)
            self.refresh_all()

    def _archive(self) -> None:
        doc = self._selected_item()
        if doc:
            self.state.move_between_named_lists("attention_items", "done_items", doc.id)
            self.refresh_all()

    def refresh(self) -> None:
        self.list_widget.clear()
        for doc in self.state.attention_items:
            item = QtWidgets.QListWidgetItem(f"{doc.display_name} ({doc.page_count}p)")
            item.setData(QtCore.Qt.UserRole, doc)
            self.list_widget.addItem(item)
        self._update_preview()

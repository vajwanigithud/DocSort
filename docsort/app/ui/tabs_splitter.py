import logging
import uuid
from datetime import datetime
from pathlib import Path
import time

from PySide6 import QtCore, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import pdf_split_service, split_plan_service
from docsort.app.storage import settings_store, split_completion_store
from docsort.app.ui.pdf_preview_widget import PdfPreviewWidget

logger = logging.getLogger(__name__)


class SplitterTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, refresh_all) -> None:
        super().__init__()
        self.state = state
        self.refresh_all = refresh_all

        self.current_groups: list[tuple[int, int]] = []
        self.cursor_page = 1

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)

        # LEFT: list of splitter candidate PDFs
        left_col = QtWidgets.QVBoxLayout()
        self.show_completed = QtWidgets.QCheckBox("Show completed")
        self.show_completed.setChecked(False)
        left_col.addWidget(self.show_completed)
        self.list_widget = QtWidgets.QListWidget()
        left_col.addWidget(self.list_widget, 1)
        layout.addLayout(left_col, 1)
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # MIDDLE: big preview + page list
        mid_layout = QtWidgets.QVBoxLayout()
        self.preview_label = QtWidgets.QLabel("Preview")
        mid_layout.addWidget(self.preview_label)

        self.preview = PdfPreviewWidget()
        self.preview.setMinimumHeight(260)
        mid_layout.addWidget(self.preview, 2)

        mid_layout.addWidget(QtWidgets.QLabel("Pages"))
        self.thumb_list = QtWidgets.QListWidget()
        self.thumb_list.setStyleSheet("""
        QListWidget { background: white; }
        QListWidget::item { color: black; padding: 6px; }
        QListWidget::item:selected { background: #cfe8ff; color: black; }
    """)
        self.thumb_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        mid_layout.addWidget(self.thumb_list, 3)

        layout.addLayout(mid_layout, 2)

        # RIGHT: plan builder
        side = QtWidgets.QVBoxLayout()

        form = QtWidgets.QFormLayout()
        self.total_pages = QtWidgets.QSpinBox()
        self.total_pages.setRange(1, 5000)
        self.total_pages.setValue(1)
        form.addRow("Total pages", self.total_pages)
        side.addLayout(form)

        mode_group = QtWidgets.QGroupBox("Split Plan")
        mode_layout = QtWidgets.QVBoxLayout(mode_group)

        self.fixed_radio = QtWidgets.QRadioButton("Fixed batch")
        self.pattern_radio = QtWidgets.QRadioButton("Pattern")
        self.ranges_radio = QtWidgets.QRadioButton("Ranges")
        self.cut_radio = QtWidgets.QRadioButton("Cut Mode")
        self.fixed_radio.setChecked(True)

        mode_layout.addWidget(self.fixed_radio)
        self.batch_size = QtWidgets.QSpinBox()
        self.batch_size.setRange(1, 500)
        self.batch_size.setValue(2)
        mode_layout.addWidget(QtWidgets.QLabel("Batch size"))
        mode_layout.addWidget(self.batch_size)

        mode_layout.addWidget(self.pattern_radio)
        self.pattern_input = QtWidgets.QLineEdit()
        self.pattern_input.setPlaceholderText("e.g., 2,2,3,1")
        mode_layout.addWidget(QtWidgets.QLabel("Pattern (comma separated counts)"))
        mode_layout.addWidget(self.pattern_input)

        mode_layout.addWidget(self.ranges_radio)
        self.ranges_input = QtWidgets.QLineEdit()
        self.ranges_input.setPlaceholderText("e.g., 1-2,3-4,5-7")
        mode_layout.addWidget(QtWidgets.QLabel("Ranges"))
        mode_layout.addWidget(self.ranges_input)

        mode_layout.addWidget(self.cut_radio)
        cut_controls = QtWidgets.QVBoxLayout()

        self.cut_status = QtWidgets.QLabel("Next page: 1")
        cut_controls.addWidget(self.cut_status)

        buttons_row = QtWidgets.QHBoxLayout()
        for lbl, size in [("1-page", 1), ("2-page", 2), ("3-page", 3), ("4-page", 4)]:
            btn = QtWidgets.QPushButton(f"Add {lbl}")
            btn.clicked.connect(lambda _=None, s=size: self._add_slice(s))
            buttons_row.addWidget(btn)
        cut_controls.addLayout(buttons_row)

        self.custom_len = QtWidgets.QSpinBox()
        self.custom_len.setRange(1, 500)
        custom_btn = QtWidgets.QPushButton("Add custom length")
        custom_btn.clicked.connect(lambda: self._add_slice(self.custom_len.value()))
        cut_controls.addWidget(self.custom_len)
        cut_controls.addWidget(custom_btn)

        undo_btn = QtWidgets.QPushButton("Undo last slice")
        undo_btn.clicked.connect(self._undo_slice)
        clear_btn = QtWidgets.QPushButton("Clear plan")
        clear_btn.clicked.connect(self._clear_plan)
        cut_controls.addWidget(undo_btn)
        cut_controls.addWidget(clear_btn)

        mode_layout.addLayout(cut_controls)
        side.addWidget(mode_group)

        self.preview_btn = QtWidgets.QPushButton("Preview Plan")
        self.cut_all_btn = QtWidgets.QPushButton("Cut All 1-page")
        self.apply_btn = QtWidgets.QPushButton("Apply Split Plan")
        self.send_parent_done = QtWidgets.QCheckBox("Send Parent to Done")
        self.send_parent_done.setChecked(True)

        side.addWidget(self.preview_btn)
        side.addWidget(self.cut_all_btn)
        side.addWidget(self.apply_btn)
        side.addWidget(self.send_parent_done)

        side.addWidget(QtWidgets.QLabel("Plan preview"))
        self.plan_preview = QtWidgets.QListWidget()
        side.addWidget(self.plan_preview, 1)

        side.addStretch()
        layout.addLayout(side, 1)

        # signals
        self.preview_btn.clicked.connect(self._preview_plan)
        self.apply_btn.clicked.connect(self._apply_plan)
        self.cut_all_btn.clicked.connect(self._cut_all_singletons)
        self.list_widget.itemSelectionChanged.connect(self._update_preview)
        self.thumb_list.itemSelectionChanged.connect(self._on_page_selected)
        self.show_completed.toggled.connect(self.refresh)
        self.list_widget.customContextMenuRequested.connect(self._open_list_context_menu)

        self._clear_plan()

    def refresh(self) -> None:
        self.list_widget.clear()
        show_completed = self.show_completed.isChecked()
        for doc in self.state.splitter_items:
            split_completion_store.prune_if_changed(Path(doc.source_path))
            is_done = split_completion_store.is_split_complete(Path(doc.source_path))
            if not show_completed and is_done:
                continue
            label = f"{doc.display_name} ({doc.page_count}p)"
            if show_completed and is_done:
                label = f"{label} ✅"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, doc)
            self.list_widget.addItem(item)

    def _selected_item(self) -> DocumentItem | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    # -----------------------------
    # Preview handling (QtPdf only)
    # -----------------------------
    def _populate_pages_list(self, page_count: int) -> None:
        self.thumb_list.clear()
        count = max(1, int(page_count or 1))
        for i in range(count):
            it = QtWidgets.QListWidgetItem(f"Page {i + 1}")
            it.setData(QtCore.Qt.UserRole, i)
            self.thumb_list.addItem(it)
        logger.info("Pages list populated: %s items", self.thumb_list.count())
        if self.thumb_list.count() > 0:
            self.thumb_list.setCurrentRow(0)

    def _update_preview(self) -> None:
        doc = self._selected_item()
        self.preview.clear()

        if not doc:
            self.preview_label.setText("Preview")
            self.thumb_list.clear()
            return

        self.preview_label.setText(f"Preview — {doc.display_name}")
        self.total_pages.setValue(max(1, int(doc.page_count or 1)))
        self._clear_plan()

        self._populate_pages_list(doc.page_count)

        src = Path(doc.source_path)
        if src.suffix.lower() != ".pdf" or not src.exists():
            self.preview_label.setText(f"Preview — {doc.display_name} (no PDF)")
            return

        ok = self.preview.load_pdf(str(src))
        if not ok:
            self.preview_label.setText(f"Preview — {doc.display_name} (failed to load)")
            return

        if self.thumb_list.count() > 0:
            self.preview.set_page(0)

    def _on_page_selected(self) -> None:
        item = self.thumb_list.currentItem()
        if not item:
            return
        idx = item.data(QtCore.Qt.UserRole)
        if idx is None:
            return
        try:
            idx_int = int(idx)
        except Exception:  # noqa: BLE001
            idx_int = 0
        logger.info("Splitter preview switch to page index=%s", idx_int)
        self.preview.set_page(idx_int)

    # -----------------------------
    # Plan building
    # -----------------------------
    def _build_plan(self):
        total = self.total_pages.value()
        try:
            if self.cut_radio.isChecked():
                return list(self.current_groups)
            if self.fixed_radio.isChecked():
                return split_plan_service.build_fixed_batches(total, self.batch_size.value())
            if self.pattern_radio.isChecked():
                pattern_text = self.pattern_input.text().strip()
                pattern = [int(x.strip()) for x in pattern_text.split(",") if x.strip()]
                return split_plan_service.build_from_pattern(total, pattern)
            ranges_text = self.ranges_input.text().strip()
            return split_plan_service.build_from_ranges(ranges_text, total)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Split Plan", str(exc))
            return None

    def _preview_plan(self) -> None:
        groups = self._build_plan()
        self.plan_preview.clear()
        if not groups:
            return
        for start, end in groups:
            self.plan_preview.addItem(f"{start}-{end}")
        self._refresh_cut_status()

    def _apply_plan(self) -> None:
        doc = self._selected_item()
        if not doc:
            return

        groups = self._build_plan()
        if not groups:
            return

        total = self.total_pages.value()
        ok, error = split_plan_service.validate_groups(total, groups)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Split Plan", error)
            return

        source_root = settings_store.get_source_root()
        if not source_root:
            QtWidgets.QMessageBox.warning(self, "Split Plan", "Set Source folder in Settings first.")
            return
        source_root_path = Path(source_root)
        try:
            source_root_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Split Plan", f"Cannot access source folder: {exc}")
            return
        covered = sum((end - start + 1) for start, end in groups)
        if covered < total:
            resp = QtWidgets.QMessageBox.question(
                self,
                "Incomplete coverage",
                f"Plan covers {covered} of {total} pages. Apply anyway?",
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            )
            if resp != QtWidgets.QMessageBox.Ok:
                return

        children: list[DocumentItem] = []
        created_paths = None
        use_pdf_split = False

        src = Path(doc.source_path)
        if src.exists() and src.suffix.lower() == ".pdf":
            out_dir = source_root_path
            try:
                def _do_split() -> list[str]:
                    return pdf_split_service.split_pdf_to_ranges(str(src), str(out_dir), groups)

                try:
                    created_paths = _do_split()
                except Exception as first_exc:  # noqa: BLE001
                    msg = str(first_exc)
                    if "WinError 32" in msg or "being used by another process" in msg:
                        time.sleep(0.15)
                        created_paths = _do_split()
                    else:
                        raise
                use_pdf_split = True
                run_ts = datetime.now().strftime("%Y%m%d%H%M%S")
                renamed_paths: list[str] = []
                for created in created_paths or []:
                    created_path = Path(created)
                    if not created_path.exists():
                        renamed_paths.append(str(created_path))
                        continue
                    if created_path.suffix.lower() != ".pdf":
                        renamed_paths.append(str(created_path))
                        continue
                    base_stem = created_path.stem
                    idx = base_stem.find("_p")
                    stem_prefix = base_stem if idx == -1 else base_stem[:idx]
                    stem_suffix = "" if idx == -1 else base_stem[idx:]
                    run_id = f"{run_ts}_{uuid.uuid4().hex[:8]}"
                    rename_attempts = 0
                    while rename_attempts < 3:
                        rename_attempts += 1
                        new_name = f"{stem_prefix}_{run_id}{stem_suffix}{created_path.suffix}"
                        candidate = created_path.with_name(new_name)
                        if candidate.exists():
                            run_id = f"{run_ts}_{uuid.uuid4().hex[:8]}"
                            continue
                        try:
                            created_path = created_path.rename(candidate)
                            break
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Failed to rename split output to %s: %s", candidate, exc)
                            run_id = f"{run_ts}_{uuid.uuid4().hex[:8]}"
                    renamed_paths.append(str(created_path))
                    if created_path.exists():
                        self.state.enqueue_scanned_path(str(created_path))
                created_paths = renamed_paths
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(
                    self,
                    "Split PDF",
                    f"Failed to split PDF: {exc}\nFalling back to virtual splits.",
                )
                created_paths = None
                use_pdf_split = False

        for idx, (start, end) in enumerate(groups):
            pages = max(1, end - start + 1)

            child_source = str(src)
            display_extra = f"[{start}-{end}]"
            if use_pdf_split and created_paths and idx < len(created_paths):
                child_source = created_paths[idx]
                display_extra = Path(child_source).name + f" [{start}-{end}]"

            child = DocumentItem(
                id=uuid.uuid4().hex,
                source_path=child_source,
                display_name=f"{doc.display_name} {display_extra}",
                page_count=pages,
                notes=f"split_from={src.name} range={start}-{end}",
                suggested_folder=doc.suggested_folder,
                suggested_name=f"{doc.display_name.lower().replace(' ', '_')}_{start}-{end}",
                confidence=doc.confidence,
                vendor=doc.vendor,
                doctype=doc.doctype,
                number=doc.number,
                date_str=doc.date_str,
                route_hint="RENAME",
                is_virtual=not use_pdf_split,
                parent_id=doc.id,
                split_group=f"{start}-{end}",
            )
            children.append(child)

        self.state.rename_items.extend(children)
        if self.send_parent_done.isChecked():
            moved = self.state.move_between_named_lists("splitter_items", "done_items", doc.id)
            if moved:
                moved.notes = f"{moved.notes} split plan applied".strip()
        else:
            doc.notes = f"{doc.notes} split plan applied".strip()
        split_completion_store.mark_split_complete(src)
        self.refresh_all()

    def _open_list_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        doc = item.data(QtCore.Qt.UserRole)
        if not doc:
            return
        path = Path(doc.source_path)
        split_completion_store.prune_if_changed(path)
        is_done = split_completion_store.is_split_complete(path)
        menu = QtWidgets.QMenu(self)
        if is_done:
            action = menu.addAction("Mark as not completed")
            action.triggered.connect(lambda: self._unmark_and_refresh(path))
        else:
            action = menu.addAction("Mark as completed")
            action.triggered.connect(lambda: self._mark_and_refresh(path))
        menu.exec(self.list_widget.mapToGlobal(pos))

    def _mark_and_refresh(self, path: Path) -> None:
        split_completion_store.mark_split_complete(path)
        self.refresh_all()

    def _unmark_and_refresh(self, path: Path) -> None:
        split_completion_store.unmark_split_complete(path)
        self.refresh_all()

    # -----------------------------
    # Cut mode helpers
    # -----------------------------
    def _add_slice(self, length: int) -> None:
        total = self.total_pages.value()
        if length <= 0 or self.cursor_page > total:
            return
        start = self.cursor_page
        end = min(total, start + length - 1)
        self.current_groups.append((start, end))
        self.cursor_page = end + 1
        self._preview_plan()

    def _undo_slice(self) -> None:
        if not self.current_groups:
            return
        last = self.current_groups.pop()
        self.cursor_page = last[0]
        self._preview_plan()

    def _clear_plan(self) -> None:
        self.current_groups = []
        self.cursor_page = 1
        self._refresh_cut_status()
        self.plan_preview.clear()

    def _refresh_cut_status(self) -> None:
        total = self.total_pages.value()
        if self.cursor_page > total:
            self.cut_status.setText("Plan complete")
        else:
            self.cut_status.setText(f"Next page: {self.cursor_page}")

    def _cut_all_singletons(self) -> None:
        total = self.total_pages.value()
        self.current_groups = split_plan_service.make_singletons(total)
        self.cursor_page = total + 1
        self.cut_radio.setChecked(True)
        self._preview_plan()

import os
import re
import shutil
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6 import QtCore, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import move_service, naming_service, training_store, undo_store, pdf_utils
from docsort.app.services.pdf_utils import detect_doc_fields_from_pdf
from docsort.app.services.folder_service import FolderService
from docsort.app.storage import settings_store, done_log_store
from docsort.app.ui.pdf_preview_widget import PdfPreviewWidget
from docsort.app.ui.move_worker import MoveWorker

logger = logging.getLogger(__name__)


class RenameMoveTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, folder_service: FolderService, refresh_all) -> None:
        super().__init__()
        self.state = state
        self.folder_service = folder_service
        self.refresh_all = refresh_all
        self._manual_overrides: Dict[str, str] = {}
        self._preview_loading: bool = False
        self._last_preview_path: Optional[str] = None
        self._active_moves = 0
        self._active_worker: Optional[MoveWorker] = None
        self._active_thread: Optional[QtCore.QThread] = None
        self._suggest_cache: Dict[str, str] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget()
        layout.addWidget(self.list_widget, 1)

        preview_layout = QtWidgets.QVBoxLayout()
        self.preview_label = QtWidgets.QLabel("Preview")
        preview_layout.addWidget(self.preview_label)
        self.preview = PdfPreviewWidget()
        preview_layout.addWidget(self.preview, 2)
        layout.addLayout(preview_layout, 2)

        side = QtWidgets.QVBoxLayout()

        rename_group = QtWidgets.QGroupBox("Rename Options")
        rename_layout = QtWidgets.QVBoxLayout(rename_group)
        self.option_a = QtWidgets.QRadioButton("A) Date-DD-MM-YYYY + ID (no spaces)")
        self.option_b = QtWidgets.QRadioButton("B) Source name")
        self.option_c = QtWidgets.QRadioButton("C) Suggested name")
        self.option_d = QtWidgets.QRadioButton("D) Custom pattern")
        self.manual_edit = QtWidgets.QLineEdit()
        self.manual_edit.setPlaceholderText("Manual name")
        self.option_a.setChecked(True)
        rename_layout.addWidget(self.option_a)
        rename_layout.addWidget(self.option_b)
        rename_layout.addWidget(self.option_c)
        rename_layout.addWidget(self.option_d)
        rename_layout.addWidget(QtWidgets.QLabel("Manual"))
        rename_layout.addWidget(self.manual_edit)
        side.addWidget(rename_group)

        self.preview_field = QtWidgets.QLineEdit()
        self.preview_field.setReadOnly(True)
        self.final_field = QtWidgets.QLineEdit()
        self.final_field.setPlaceholderText("Final filename")

        folder_row = QtWidgets.QHBoxLayout()
        self.folder_dropdown = QtWidgets.QComboBox()
        self.create_folder_btn = QtWidgets.QPushButton("Create Folder")
        folder_row.addWidget(self.folder_dropdown)
        folder_row.addWidget(self.create_folder_btn)

        self.warning_label = QtWidgets.QLabel("Set Destination Root in Settings")
        self.warning_label.setStyleSheet("color: #b33;")

        self.confirm_btn = QtWidgets.QPushButton("Confirm & Move (This File)")
        self.bulk_confirm_btn = QtWidgets.QPushButton("Bulk Confirm Selected")
        self.to_splitter_btn = QtWidgets.QPushButton("Send to Splitter")
        self.to_attention_btn = QtWidgets.QPushButton("Needs Attention")

        side.addWidget(QtWidgets.QLabel("Live preview"))
        side.addWidget(self.preview_field)
        side.addWidget(QtWidgets.QLabel("Final filename"))
        side.addWidget(self.final_field)
        side.addLayout(folder_row)
        side.addWidget(self.warning_label)
        side.addWidget(self.confirm_btn)
        side.addWidget(self.bulk_confirm_btn)
        side.addWidget(self.to_splitter_btn)
        side.addWidget(self.to_attention_btn)
        side.addStretch()
        layout.addLayout(side, 1)

        self.list_widget.itemSelectionChanged.connect(self._sync_fields_from_selection)
        for rb in [self.option_a, self.option_b, self.option_c, self.option_d]:
            rb.toggled.connect(self._update_preview)
        self.option_c.toggled.connect(self._on_option_c_selected)
        self.manual_edit.textChanged.connect(self._update_preview)
        self.final_field.textEdited.connect(self._on_final_edited)
        self.create_folder_btn.clicked.connect(self._create_folder)
        self.confirm_btn.clicked.connect(self._confirm_current)
        self.bulk_confirm_btn.clicked.connect(self._bulk_confirm)
        self.to_splitter_btn.clicked.connect(self._send_to_splitter)
        self.to_attention_btn.clicked.connect(self._send_to_attention)

    def _selected_item(self) -> DocumentItem | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _all_items(self) -> List[QtWidgets.QListWidgetItem]:
        return [self.list_widget.item(i) for i in range(self.list_widget.count())]

    def _get_option_a_name(self, doc: DocumentItem) -> str:
        return naming_service.build_option_a(doc.vendor, doc.doctype, doc.number, doc.date_str, Path(doc.source_path).stem)

    def _final_filename_for_doc(self, doc: DocumentItem) -> str:
        name = self._manual_overrides.get(doc.id) or self._get_option_a_name(doc)
        name = naming_service.enforce_no_spaces(name)
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        return name

    def _on_final_edited(self, text: str) -> None:
        doc = self._selected_item()
        if not doc:
            return
        sanitized = naming_service.enforce_no_spaces(text)
        self._manual_overrides[doc.id] = sanitized
        if not sanitized.lower().endswith(".pdf"):
            sanitized = f"{sanitized}.pdf"
        # Avoid recursive textEdited; set using block signals.
        self.final_field.blockSignals(True)
        self.final_field.setText(sanitized)
        self.final_field.blockSignals(False)

    def _sanitize_filename(self, text: str) -> str:
        cleaned = re.sub(r'[<>:"/\\\\|?*]', "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _build_fallback_stem(self, doc: DocumentItem, folder_name: str) -> str:
        try:
            modified = getattr(doc, "modified_time", None)
            if isinstance(modified, datetime):
                dt = modified
            elif isinstance(modified, str):
                dt = datetime.fromisoformat(modified)
            else:
                dt = datetime.now()
        except Exception:
            dt = datetime.now()
        date_part = dt.strftime("%Y-%m-%d")
        category = (folder_name or "").strip() or "Uncategorized"
        source_name = Path(doc.source_path).stem or doc.display_name
        source_name = re.sub(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            "",
            source_name,
        )
        source_name = self._sanitize_filename(source_name)
        source_name = re.sub(r"\s+", " ", source_name).strip()
        source_name = source_name[:30]
        if not source_name:
            source_name = doc.id or "scan"
        slug = source_name
        stem = f"{date_part} - {category} - {slug}"
        stem = self._sanitize_filename(stem)
        return stem

    def _on_option_c_selected(self, checked: bool) -> None:
        if not checked:
            return
        doc = self._selected_item()
        if not doc:
            return
        suggested = self._get_suggested_name(doc)
        with QtCore.QSignalBlocker(self.manual_edit):
            self.manual_edit.setText(suggested)
        self._manual_overrides[doc.id] = suggested
        self._update_preview()

    def _get_suggested_name(self, doc: DocumentItem) -> str:
        folder_name = self.folder_dropdown.currentText() or ""
        resolved = str(Path(doc.source_path).resolve())
        key = f"{resolved}::{folder_name}"
        if key in self._suggest_cache:
            return self._suggest_cache[key]
        fallback_stem = self._build_fallback_stem(doc, folder_name)
        if doc.doctype and doc.number:
            suggested = f"{doc.doctype.title()}_{doc.number}"
            if not suggested.lower().endswith(".pdf"):
                suggested = f"{suggested}.pdf"
        else:
            suggested = pdf_utils.build_suggested_filename(doc.source_path, fallback_stem)
        self._suggest_cache[key] = suggested
        return suggested

    def _update_preview(self) -> None:
        doc = self._selected_item()
        if not doc:
            self.preview_field.clear()
            self.preview.clear()
            self.preview_label.setText("Preview")
            return
        manual_text = self.manual_edit.text().strip()
        if self.option_c.isChecked():
            if not manual_text:
                manual_text = self._get_suggested_name(doc)
                with QtCore.QSignalBlocker(self.manual_edit):
                    self.manual_edit.setText(manual_text)
            manual_name = self._sanitize_filename(manual_text)
            if not manual_name.lower().endswith(".pdf"):
                manual_name = f"{manual_name}.pdf"
            self.preview_field.setText(manual_name)
            self._manual_overrides[doc.id] = manual_name
            blocker = QtCore.QSignalBlocker(self.final_field)
            self.final_field.setText(manual_name)
        elif manual_text:
            manual_name = naming_service.enforce_no_spaces(manual_text)
            if not manual_name.lower().endswith(".pdf"):
                manual_name = f"{manual_name}.pdf"
            self.preview_field.setText(manual_name)
            self._manual_overrides[doc.id] = manual_name
            blocker = QtCore.QSignalBlocker(self.final_field)
            self.final_field.setText(manual_name)
        else:
            option_a = self._get_option_a_name(doc)
            self.preview_field.setText(option_a)
            if doc.id not in self._manual_overrides:
                blocker = QtCore.QSignalBlocker(self.final_field)
                self.final_field.setText(option_a)
        self._update_pdf_preview(doc)

    def _update_pdf_preview(self, doc: DocumentItem) -> None:
        if self._preview_loading:
            return
        path = Path(doc.source_path)
        norm_path = str(path.resolve()) if path.exists() else str(path)
        if norm_path == self._last_preview_path:
            return
        if not path.exists() or path.suffix.lower() != ".pdf":
            self.preview.clear()
            self.preview_label.setText("Preview unavailable")
            self._last_preview_path = None
            return
        self._preview_loading = True
        try:
            self._last_preview_path = norm_path
            logger.info("Rename preview load start: %s", path)
            ok = self.preview.load_pdf(str(path))
            if ok:
                self.preview.set_page(0)
                self.preview_label.setText(f"Preview - {doc.display_name}")
                logger.info("Rename preview loaded: %s", path)
                doctype, number, detected_date, err = detect_doc_fields_from_pdf(str(path))
                changed = False
                if doctype:
                    changed = changed or (doc.doctype != doctype)
                    doc.doctype = doctype
                if number:
                    changed = changed or (doc.number != number)
                    doc.number = number
                if doctype or number:
                    logger.info("Auto-detected fields: doctype=%s number=%s date=%s path=%s", doctype, number, detected_date, path)
                if err:
                    logger.debug("Auto-detect text error for %s: %s", path, err)
                if changed:
                    QtCore.QTimer.singleShot(0, self._update_preview)
            else:
                self.preview.clear()
                self.preview_label.setText("Preview unavailable")
                logger.warning("Rename preview failed to load: %s", path)
        finally:
            self._preview_loading = False

    def _sync_fields_from_selection(self) -> None:
        doc = self._selected_item()
        if not doc:
            self.preview_field.clear()
            self.final_field.clear()
            self.preview.clear()
            self.preview_label.setText("Preview")
            return
        self.manual_edit.clear()
        final_name = self._final_filename_for_doc(doc)
        blocker = QtCore.QSignalBlocker(self.final_field)
        self.final_field.setText(final_name)
        self._update_preview()

    def _create_folder(self) -> None:
        text, ok = QtWidgets.QInputDialog.getText(self, "Create Folder", "Folder name:")
        if ok and text:
            name = text.replace(" ", "_")
            self.folder_service.create_folder(name)
            self.refresh_all()

    def _confirm_current(self) -> None:
        logger.info("Confirm Move clicked")
        if not self.folder_service.is_configured:
            logger.warning("Confirm Move aborted: folder service not configured")
            return
        doc = self._selected_item()
        if not doc:
            logger.warning("Confirm Move aborted: no document selected")
            return
        self._confirm_documents([doc])

    def _bulk_confirm(self) -> None:
        if not self.folder_service.is_configured:
            return
        docs = []
        for item in self._all_items():
            if item.checkState() == QtCore.Qt.Checked:
                doc = item.data(QtCore.Qt.UserRole)
                docs.append(doc)
        if docs:
            self._confirm_documents(docs)

    def _send_to_splitter(self) -> None:
        doc = self._selected_item()
        if doc:
            self.state.move_between_named_lists("rename_items", "splitter_items", doc.id)
            self.refresh_all()

    def _send_to_attention(self) -> None:
        doc = self._selected_item()
        if doc:
            self.state.move_between_named_lists("rename_items", "attention_items", doc.id)
            self.refresh_all()

    def refresh(self) -> None:
        prev_doc = self._selected_item()
        prev_path = str(Path(prev_doc.source_path).resolve()) if prev_doc else None
        root_set = self.folder_service.is_configured and bool(settings_store.get_destination_root())
        self.warning_label.setVisible(not root_set)
        self.confirm_btn.setEnabled(root_set)
        self.bulk_confirm_btn.setEnabled(root_set)

        restore_index = None
        with QtCore.QSignalBlocker(self.list_widget):
            self.list_widget.clear()
            for idx, doc in enumerate(self.state.rename_items):
                item = QtWidgets.QListWidgetItem(f"{doc.display_name} ({doc.page_count}p)")
                item.setData(QtCore.Qt.UserRole, doc)
                item.setCheckState(QtCore.Qt.Unchecked)
                self.list_widget.addItem(item)
                if prev_path and str(Path(doc.source_path).resolve()) == prev_path:
                    restore_index = idx
            if restore_index is not None:
                self.list_widget.setCurrentRow(restore_index)

        self.folder_dropdown.clear()
        folders = self.folder_service.list_folders() if root_set else []
        self.folder_dropdown.addItems(folders)
        doc = self._selected_item()
        if doc:
            self.preview_label.setText(f"Preview — {doc.display_name}")
        else:
            self.preview_label.setText("Preview")
            self.preview.clear()
        current_path = str(Path(doc.source_path).resolve()) if doc else None
        if not doc:
            self._update_preview()
        elif current_path != prev_path:
            self._update_preview()

    def _confirm_documents(self, docs: List[DocumentItem]) -> None:
        destination_root = settings_store.get_destination_root()
        if not destination_root:
            logger.warning("Confirm Move aborted: destination root not set")
            return
        logger.info("Confirm Move begin: rename_items=%s done_items=%s", len(self.state.rename_items), len(self.state.done_items))
        final_folder = self.folder_dropdown.currentText() or ""
        dest_folder_path = Path(destination_root) / final_folder
        move_service.ensure_dir(str(dest_folder_path))

        # Release preview aggressively before moves
        try:
            self.preview.clear()
            self.preview.force_release_document()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

        for doc in list(docs):
            final_name = self._final_filename_for_doc(doc)
            chosen_option = "MANUAL" if doc.id in self._manual_overrides else "A"
            dest_path = dest_folder_path / final_name
            training_store.append_event(
                {
                    "item_id": doc.id,
                    "chosen_option": chosen_option,
                    "final_filename": final_name,
                    "final_folder": final_folder,
                    "destination_root": destination_root,
                    "notes": doc.notes,
                }
            )
            src_exists = Path(doc.source_path).exists()
            if not src_exists:
                note_prefix = f"{doc.notes} ".strip()
                doc.notes = f"{note_prefix}mock_move dest={dest_path}"
                self.state.move_between_named_lists("rename_items", "done_items", doc.id)
                self._manual_overrides.pop(doc.id, None)
                continue

            logger.info("Confirm Move paths: src=%s dst=%s", doc.source_path, dest_path)
            self._start_async_move(doc, dest_path, final_folder, final_name)

    def _move_with_retry(self, src: str, dest: Path) -> bool:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to create destination dir %s: %s", dest.parent, exc)
        same_drive = Path(src).drive == dest.drive
        backoffs = [0.2, 0.4, 0.8, 1.6, 2.0]
        for attempt in range(1, len(backoffs) + 1):
            try:
                if same_drive:
                    os.replace(src, dest)
                else:
                    shutil.move(src, dest)
                # verify move
                if not Path(dest).exists():
                    raise IOError("Destination missing after move")
                if Path(src).exists():
                    raise IOError("Source still exists after move")
                return True
            except PermissionError as exc:
                if "WinError 32" in str(exc) and attempt <= len(backoffs):
                    logger.info("Move retry %s for %s due to: %s", attempt, src, exc)
                    QtWidgets.QApplication.processEvents()
                    QtCore.QThread.msleep(int(backoffs[attempt - 1] * 1000))
                    continue
                raise
            except Exception:
                raise
        # fallback copy+delete
        try:
            shutil.copy2(src, dest)
            src_size = Path(src).stat().st_size if Path(src).exists() else 0
            dst_size = Path(dest).stat().st_size if Path(dest).exists() else 0
            if not Path(dest).exists() or dst_size == 0 or (src_size and dst_size != src_size):
                raise IOError("Copy verification failed")
            for attempt in range(30):
                try:
                    Path(src).unlink()
                    break
                except Exception as exc:  # noqa: BLE001
                    if "WinError 32" in str(exc) and attempt < 29:
                        QtWidgets.QApplication.processEvents()
                        QtCore.QThread.msleep(200 * (attempt + 1))
                        continue
                    raise
            if Path(src).exists():
                raise IOError("Source still exists after copy+delete attempts")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallback copy+delete failed: %s", exc)
            return False

    def _start_async_move(self, doc: DocumentItem, dest_path: Path, final_folder: str, final_name: str) -> None:
        if self._active_thread and self._active_thread.isRunning():
            logger.warning("Move already running; skipping new request.")
            return
        self._active_moves += 1
        self.confirm_btn.setEnabled(False)
        self.bulk_confirm_btn.setEnabled(False)
        self.warning_label.setText("Moving...")
        worker = MoveWorker(doc.source_path, str(dest_path), Path(doc.source_path).drive == dest_path.drive, final_folder, final_name, doc.id)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._active_worker = worker
        self._active_thread = thread

        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._on_move_finished)
        thread.finished.connect(thread.deleteLater)

        def _clear_refs():
            self._active_thread = None
            self._active_worker = None
            if self._active_moves == 0:
                self.confirm_btn.setEnabled(True)
                self.bulk_confirm_btn.setEnabled(True)

        thread.finished.connect(_clear_refs)

        thread.start()

    @QtCore.Slot(bool, str, str, str, str, str, str, str)
    def _on_move_finished(self, success: bool, msg: str, src: str, dest: str, doc_id: str, final_folder: str, final_name: str, status: str) -> None:
        self._active_moves = max(0, self._active_moves - 1)
        if success:
            doc = next((d for d in self.state.rename_items if d.id == doc_id), None)
            if doc:
                note_prefix = f"{doc.notes} ".strip()
                doc.notes = f"{note_prefix}dest={dest}"
                removed = self.state.move_between_named_lists("rename_items", "done_items", doc.id)
                if not removed:
                    try:
                        self.state.rename_items = [d for d in self.state.rename_items if d.id != doc.id]
                        self.state.done_items.append(doc)
                    except Exception:
                        logger.warning("State adjustment failed for %s", doc.id)
                self._manual_overrides.pop(doc.id, None)
            with QtCore.QSignalBlocker(self.list_widget):
                for i in range(self.list_widget.count()):
                    item = self.list_widget.item(i)
                    if item and item.data(QtCore.Qt.UserRole).id == doc_id:
                        self.list_widget.takeItem(i)
                        break
                self.list_widget.clearSelection()
            self.preview.clear()
            self.preview_label.setText("Preview")
            done_log_store.append_done(
                {
                    "item_id": doc_id,
                    "src": src,
                    "dest": dest,
                    "display_name": final_name,
                    "folder": final_folder,
                    "final_filename": final_name,
                    "status": status if status else "PENDING_DELETE",
                    "delete_attempts": 0,
                    "last_error": "WinError 32 lock" if status == "PENDING_DELETE" else "",
                }
            )
            logger.info("Move success (async) src=%s dest=%s status=%s", src, dest, status)
            self.refresh_all()
        else:
            self.warning_label.setText(f"Move failed: {msg}")
            logger.warning("Move failed (async) src=%s dest=%s msg=%s", src, dest, msg)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            if self._active_thread and self._active_thread.isRunning():
                self._active_thread.quit()
                self._active_thread.wait(1000)
        except Exception:
            pass
        self._active_thread = None
        self._active_worker = None
        super().closeEvent(event)

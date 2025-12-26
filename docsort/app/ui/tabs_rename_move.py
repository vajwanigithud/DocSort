import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6 import QtCore, QtWidgets

from docsort.app.core.state import AppState, DocumentItem
from docsort.app.services import move_service, naming_service, training_store, undo_store, pdf_utils
from docsort.app.services import ocr_suggestion_service
from docsort.app.services.pdf_utils import detect_doc_fields_from_pdf
from docsort.app.services.folder_service import FolderService
from docsort.app.storage import settings_store, done_log_store, suggestion_memory_store
from docsort.app.ui.pdf_preview_widget import PdfPreviewWidget
from docsort.app.ui.move_worker import MoveWorker

logger = logging.getLogger(__name__)

MIN_SUGGESTIONS = 5
OCR_LOADING_TEXT = "(OCR...loading)"
INVALID_CHARS_PATTERN = re.compile(r'[<>:"/\\\\|?*]')


class _OcrWorker(QtCore.QObject):
    finished = QtCore.Signal(str, list, str)
    failed = QtCore.Signal(str, str)

    def __init__(self, key: str, path: str, fallback_stem: str, max_pages: int) -> None:
        super().__init__()
        self.key = key
        self.path = path
        self.fallback_stem = fallback_stem
        self.max_pages = max_pages

    @QtCore.Slot()
    def run(self) -> None:
        try:
            logger.info("OCR started key=%s path=%s", self.key, self.path)
            text = ocr_suggestion_service.get_text_for_pdf(self.path, max_pages=self.max_pages)
            fingerprint = ocr_suggestion_service.fingerprint_text(text or "")
            ocr_names = ocr_suggestion_service.build_ocr_suggestions(text, self.fallback_stem)
            logger.info("OCR finished key=%s path=%s suggestions=%s", self.key, self.path, len(ocr_names))
            self.finished.emit(self.key, ocr_names, fingerprint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed key=%s path=%s err=%s", self.key, self.path, exc)
            self.failed.emit(self.key, str(exc))


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
        self._suggestions_map: Dict[str, List[str]] = {}
        self._selected_suggestion_idx: Dict[str, int] = {}
        self._suggest_memory: Dict[str, str] = suggestion_memory_store.load_memory()
        self._ocr_suggestions: Dict[str, List[str]] = {}
        self._ocr_fingerprints: Dict[str, str] = {}
        self._ocr_inflight: Set[str] = set()
        self._ocr_threads: Dict[str, QtCore.QThread] = {}
        self._ocr_workers: Dict[str, _OcrWorker] = {}
        self._programmatic_update: bool = False
        self._active_doc_key: Optional[str] = None
        self._build_ui()

    def _set_final_text_programmatically(self, text: str) -> None:
        self._programmatic_update = True
        with QtCore.QSignalBlocker(self.final_field):
            self.final_field.setText(text)
        self._programmatic_update = False

    def _set_manual_text_programmatically(self, text: str) -> None:
        self._programmatic_update = True
        with QtCore.QSignalBlocker(self.manual_edit):
            self.manual_edit.setText(text)
        self._programmatic_update = False

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

        self.suggestions_label = QtWidgets.QLabel("Suggested Filenames")
        self.suggestions_list = QtWidgets.QListWidget()
        self.suggestions_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        bulk_row = QtWidgets.QHBoxLayout()
        self.apply_selected_btn = QtWidgets.QPushButton("Apply selected suggestion to checked")
        self.apply_top_btn = QtWidgets.QPushButton("Apply top suggestion to checked")
        self.clear_manual_btn = QtWidgets.QPushButton("Clear manual for checked")
        bulk_row.addWidget(self.apply_selected_btn)
        bulk_row.addWidget(self.apply_top_btn)
        bulk_row.addWidget(self.clear_manual_btn)
        self.manual_edit = QtWidgets.QLineEdit()
        self.manual_edit.setPlaceholderText("Manual filename")
        self.final_field = QtWidgets.QLineEdit()
        self.final_field.setPlaceholderText("Final filename")
        self.final_field.setReadOnly(True)

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

        side.addWidget(self.suggestions_label)
        side.addWidget(self.suggestions_list)
        side.addLayout(bulk_row)
        side.addWidget(QtWidgets.QLabel("Manual filename"))
        side.addWidget(self.manual_edit)
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
        self.manual_edit.textEdited.connect(self._on_manual_edited)
        self.final_field.textEdited.connect(self._on_final_edited)
        self.suggestions_list.itemSelectionChanged.connect(self._on_suggestion_selected)
        self.apply_selected_btn.clicked.connect(self._apply_selected_to_checked)
        self.apply_top_btn.clicked.connect(self._apply_top_to_checked)
        self.clear_manual_btn.clicked.connect(self._clear_manual_for_checked)
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

    def _doc_key(self, doc_or_path) -> str:
        if isinstance(doc_or_path, DocumentItem):
            path = doc_or_path.source_path
        else:
            path = str(doc_or_path)
        try:
            return str(Path(path).resolve())
        except Exception:
            return os.path.abspath(path)

    def _is_placeholder_filename(self, name: str) -> bool:
        cleaned = (name or "").strip().lower()
        if not cleaned:
            return True
        if "00-00-0000" in cleaned or "00/00/0000" in cleaned:
            return True
        if cleaned.startswith("type_0"):
            return True
        if re.match(r"type_0+\.pdf$", cleaned):
            return True
        return False

    def _on_manual_edited(self, text: str) -> None:
        # user typing in manual_edit should reflect in preview, but not save overrides
        doc = self._selected_item()
        if not doc:
            return
        self._update_preview()

    def _cleanup_invalid_overrides(self) -> None:
        remove_keys = []
        for key, val in self._manual_overrides.items():
            if not Path(key).exists() or self._is_placeholder_filename(val):
                remove_keys.append(key)
        for key in remove_keys:
            self._manual_overrides.pop(key, None)
            logger.info("Removed invalid override key=%s", key)

    def _all_items(self) -> List[QtWidgets.QListWidgetItem]:
        return [self.list_widget.item(i) for i in range(self.list_widget.count())]

    def _checked_docs(self) -> List[DocumentItem]:
        docs: List[DocumentItem] = []
        for item in self._all_items():
            if not item:
                continue
            doc = item.data(QtCore.Qt.UserRole)
            if not isinstance(doc, DocumentItem):
                continue
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                docs.append(doc)
        return docs

    def _get_option_a_name(self, doc: DocumentItem) -> str:
        return naming_service.build_option_a(doc.vendor, doc.doctype, doc.number, doc.date_str, Path(doc.source_path).stem)

    def _learn_key_for_doc(self, doc: DocumentItem) -> str:
        vendor = (doc.vendor or "").strip().lower()
        doctype = (doc.doctype or "").strip().lower()
        number = (doc.number or "").strip()
        if vendor and doctype and number and doctype not in {"type", "document", "unknown"} and not re.fullmatch(r"0+", number):
            return "|".join([vendor, doctype, number])
        key = self._ocr_fingerprints.get(self._doc_key(doc))
        if key:
            return f"ocr|{key}"
        return Path(doc.source_path).stem.lower()

    def _final_filename_for_doc(self, doc: DocumentItem) -> str:
        key = self._doc_key(doc)
        if key in self._manual_overrides:
            name = self._manual_overrides.get(key, "")
        else:
            suggestions = self._get_suggestions_for_doc(doc)
            sel_idx = self._selected_suggestion_idx.get(key, 0)
            name = suggestions[sel_idx] if suggestions else self._get_option_a_name(doc)
        return self._normalize_suggestion(name)

    def _recompute_suggestion(self, doc: DocumentItem, reason: str = "") -> str:
        key = self._doc_key(doc)
        self._suggest_cache.pop(f"{Path(doc.source_path).resolve()}::{self.folder_dropdown.currentText() or ''}", None)
        self._suggestions_map.pop(key, None)
        self._populate_suggestions_ui(doc)
        self._apply_final_display(doc)
        logger.info(
            "Recompute suggestion reason=%s key=%s type=%s number=%s date=%s",
            reason,
            key,
            doc.doctype,
            doc.number,
            doc.date_str,
        )
        return self.final_field.text()

    def _on_final_edited(self, text: str) -> None:
        doc = self._selected_item()
        if not doc:
            return
        sanitized = self._normalize_suggestion(text)
        key = self._doc_key(doc)
        if self._programmatic_update:
            logger.debug("Programmatic final edit ignored key=%s text=%s", key, sanitized)
            return
        if self._is_placeholder_filename(sanitized):
            if key in self._manual_overrides:
                self._manual_overrides.pop(key, None)
                logger.info("Placeholder override removed key=%s filename=%s", key, sanitized)
            return
        self._manual_overrides[key] = sanitized
        logger.info("Manual override set key=%s filename=%s", key, sanitized)
        # Avoid recursive textEdited; set using block signals.
        self._set_final_text_programmatically(sanitized)

    def _sanitize_filename(self, text: str) -> str:
        cleaned = re.sub(r'[<>:"/\\\\|?*]', "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _normalize_suggestion(self, text: str) -> str:
        if text == OCR_LOADING_TEXT:
            return text
        cleaned = INVALID_CHARS_PATTERN.sub("", text or "")
        cleaned = naming_service.enforce_no_spaces(cleaned)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if cleaned and not cleaned.lower().endswith(".pdf"):
            cleaned = f"{cleaned}.pdf"
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

    def _get_suggested_name(self, doc: DocumentItem) -> str:
        folder_name = self.folder_dropdown.currentText() or ""
        resolved = str(Path(doc.source_path).resolve())
        key = f"{resolved}::{folder_name}"
        if key in self._suggest_cache:
            return self._suggest_cache[key]
        fallback_stem = self._build_fallback_stem(doc, folder_name)
        missing_type = not doc.doctype or doc.doctype.lower() in {"type", "document", "unknown"}
        missing_number = not doc.number or re.fullmatch(r"0+", doc.number or "") is not None
        if not missing_type and not missing_number:
            suggested = f"{doc.doctype.title()}_{doc.number}"
        else:
            suggested = pdf_utils.build_suggested_filename(doc.source_path, fallback_stem)
        self._suggest_cache[key] = suggested
        return suggested

    def _get_suggestions_for_doc(self, doc: DocumentItem) -> List[str]:
        key = self._doc_key(doc)
        if key in self._suggestions_map:
            return self._suggestions_map[key]
        suggestions: List[str] = []
        learn_keys = []
        primary_learn_key = self._learn_key_for_doc(doc)
        if primary_learn_key:
            learn_keys.append(primary_learn_key)
        stem_key = Path(doc.source_path).stem.lower()
        if stem_key and stem_key not in learn_keys:
            learn_keys.append(stem_key)
        for lk in learn_keys:
            learned = self._suggest_memory.get(lk)
            if learned and not self._is_placeholder_filename(learned):
                suggestions.append(self._normalize_suggestion(learned))
                break

        ocr_vals: List[str] = []
        ocr_ready = key in self._ocr_suggestions
        if ocr_ready:
            ocr_vals = self._ocr_suggestions.get(key, [])
        elif Path(doc.source_path).suffix.lower() == ".pdf":
            self._start_ocr_if_needed(doc)
            ocr_vals = [OCR_LOADING_TEXT]
        suggestions.extend(ocr_vals)

        opt_a = self._get_option_a_name(doc)
        if opt_a:
            suggestions.append(opt_a)
        try:
            suggested = self._get_suggested_name(doc)
            if suggested:
                suggestions.append(suggested)
        except Exception:
            pass
        fallback_stem = self._build_fallback_stem(doc, self.folder_dropdown.currentText() or "")
        fallback = f"{Path(doc.source_path).stem}.pdf"
        suggestions.append(fallback)
        suggestions.append(f"{fallback_stem}.pdf")

        normalized: List[str] = []
        seen = set()
        for raw in suggestions:
            norm = self._normalize_suggestion(raw)
            if not norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            normalized.append(norm)

        filler_idx = 1
        while len(normalized) < MIN_SUGGESTIONS:
            candidate = self._normalize_suggestion(f"{fallback_stem}_{filler_idx}.pdf")
            filler_idx += 1
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)

        self._suggestions_map[key] = normalized
        return normalized

    def _start_ocr_if_needed(self, doc: DocumentItem) -> None:
        key = self._doc_key(doc)
        if key in self._ocr_inflight or key in self._ocr_suggestions:
            return
        if Path(doc.source_path).suffix.lower() != ".pdf":
            return
        fallback_stem = self._build_fallback_stem(doc, self.folder_dropdown.currentText() or "")
        worker = _OcrWorker(key, doc.source_path, fallback_stem, max_pages=ocr_suggestion_service.OCR_MAX_PAGES)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ocr_finished)
        worker.failed.connect(self._on_ocr_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._ocr_inflight.add(key)
        self._ocr_workers[key] = worker
        self._ocr_threads[key] = thread
        thread.start()

    @QtCore.Slot(str, list, str)
    def _on_ocr_finished(self, key: str, suggestions: List[str], fingerprint: str) -> None:
        self._ocr_inflight.discard(key)
        self._ocr_suggestions[key] = [self._normalize_suggestion(s) for s in suggestions if s]
        if fingerprint:
            self._ocr_fingerprints[key] = fingerprint
        self._ocr_threads.pop(key, None)
        self._ocr_workers.pop(key, None)
        self._suggestions_map.pop(key, None)
        doc = self._selected_item()
        if doc and self._doc_key(doc) == key:
            self._populate_suggestions_ui(doc)
            self._apply_final_display(doc)

    @QtCore.Slot(str, str)
    def _on_ocr_failed(self, key: str, err: str) -> None:
        logger.info("OCR failed key=%s err=%s", key, err)
        self._ocr_inflight.discard(key)
        self._ocr_suggestions.setdefault(key, [])
        self._ocr_threads.pop(key, None)
        self._ocr_workers.pop(key, None)
        self._suggestions_map.pop(key, None)
        doc = self._selected_item()
        if doc and self._doc_key(doc) == key:
            self._populate_suggestions_ui(doc)
            self._apply_final_display(doc)

    def _populate_suggestions_ui(self, doc: DocumentItem) -> None:
        key = self._doc_key(doc)
        suggestions = self._get_suggestions_for_doc(doc)
        self._programmatic_update = True
        self.suggestions_list.clear()
        for s in suggestions:
            self.suggestions_list.addItem(s)
        if suggestions:
            sel_idx = self._selected_suggestion_idx.get(key, 0)
            sel_idx = min(sel_idx, len(suggestions) - 1)
            self.suggestions_list.setCurrentRow(sel_idx)
            self._selected_suggestion_idx[key] = sel_idx
        self._programmatic_update = False

    def _apply_final_display(self, doc: DocumentItem) -> None:
        key = self._doc_key(doc)
        manual = self._manual_overrides.get(key, "").strip()
        if manual:
            final_name = manual
        else:
            suggestions = self._get_suggestions_for_doc(doc)
            if suggestions:
                sel_idx = self._selected_suggestion_idx.get(key, 0)
                sel_idx = min(sel_idx, len(suggestions) - 1)
                chosen = suggestions[sel_idx]
                if chosen == OCR_LOADING_TEXT and len(suggestions) > 1:
                    chosen = suggestions[1]
                final_name = chosen
            else:
                final_name = self._get_option_a_name(doc)
        self._set_final_text_programmatically(self._normalize_suggestion(final_name))

    def _learn_suggestion(self, doc: DocumentItem, suggestion: str) -> None:
        suggestion = self._normalize_suggestion(suggestion)
        if not suggestion or suggestion == OCR_LOADING_TEXT:
            return
        if self._is_placeholder_filename(suggestion):
            return
        learn_key = self._learn_key_for_doc(doc)
        if not learn_key:
            return
        self._suggest_memory[learn_key] = suggestion
        suggestion_memory_store.save_memory(self._suggest_memory)
        logger.info("Learned suggestion key=%s filename=%s", learn_key, suggestion)

    def _on_suggestion_selected(self) -> None:
        if self._programmatic_update:
            return
        doc = self._selected_item()
        if not doc:
            return
        key = self._doc_key(doc)
        self._selected_suggestion_idx[key] = self.suggestions_list.currentRow()
        self._apply_final_display(doc)
        self._learn_suggestion(doc, self.final_field.text())

    def _apply_selected_to_checked(self) -> None:
        doc = self._selected_item()
        if not doc:
            return
        current_row = self.suggestions_list.currentRow()
        if current_row < 0:
            return
        key = self._doc_key(doc)
        suggestions = self._get_suggestions_for_doc(doc)
        if not suggestions:
            return
        sel_idx = min(current_row, len(suggestions) - 1)
        self._selected_suggestion_idx[key] = sel_idx
        changed = 0
        checked_docs = self._checked_docs()
        if not checked_docs:
            QtWidgets.QMessageBox.information(self, "Bulk apply", "No files checked.")
            return
        self._programmatic_update = True
        try:
            self.suggestions_list.setCurrentRow(sel_idx)
            for target_doc in checked_docs:
                tkey = self._doc_key(target_doc)
                t_suggestions = self._get_suggestions_for_doc(target_doc)
                if not t_suggestions:
                    continue
                clamped = min(sel_idx, len(t_suggestions) - 1)
                self._selected_suggestion_idx[tkey] = clamped
                existing_override = self._manual_overrides.get(tkey, "")
                if not existing_override or self._is_placeholder_filename(existing_override):
                    self._manual_overrides.pop(tkey, None)
                changed += 1
        finally:
            self._programmatic_update = False
        logger.info("Bulk apply selected suggestion to %s docs", changed)
        self._populate_suggestions_ui(doc)
        self._apply_final_display(doc)
        self._learn_suggestion(doc, self.final_field.text())
        for target_doc in checked_docs:
            tkey = self._doc_key(target_doc)
            sel = self._selected_suggestion_idx.get(tkey, 0)
            t_suggestions = self._get_suggestions_for_doc(target_doc)
            if t_suggestions:
                self._learn_suggestion(target_doc, t_suggestions[min(sel, len(t_suggestions) - 1)])
        if changed:
            QtWidgets.QMessageBox.information(self, "Bulk apply", f"Applied to {changed} files")

    def _apply_top_to_checked(self) -> None:
        doc = self._selected_item()
        if not doc:
            return
        key = self._doc_key(doc)
        self._selected_suggestion_idx[key] = 0
        changed = 0
        checked_docs = self._checked_docs()
        if not checked_docs:
            QtWidgets.QMessageBox.information(self, "Bulk apply", "No files checked.")
            return
        self._programmatic_update = True
        try:
            self.suggestions_list.setCurrentRow(0)
            for target_doc in checked_docs:
                tkey = self._doc_key(target_doc)
                t_suggestions = self._get_suggestions_for_doc(target_doc)
                if not t_suggestions:
                    continue
                self._selected_suggestion_idx[tkey] = 0
                existing_override = self._manual_overrides.get(tkey, "")
                if not existing_override or self._is_placeholder_filename(existing_override):
                    self._manual_overrides.pop(tkey, None)
                changed += 1
        finally:
            self._programmatic_update = False
        logger.info("Bulk apply top suggestion to %s docs", changed)
        self._populate_suggestions_ui(doc)
        self._apply_final_display(doc)
        self._learn_suggestion(doc, self.final_field.text())
        for target_doc in checked_docs:
            t_suggestions = self._get_suggestions_for_doc(target_doc)
            if t_suggestions:
                self._learn_suggestion(target_doc, t_suggestions[0])
        if changed:
            QtWidgets.QMessageBox.information(self, "Bulk apply", f"Applied to {changed} files")

    def _clear_manual_for_checked(self) -> None:
        doc = self._selected_item()
        self._programmatic_update = True
        for target_doc in self._checked_docs():
            tkey = self._doc_key(target_doc)
            self._manual_overrides.pop(tkey, None)
            if doc and self._doc_key(doc) == tkey:
                self._set_manual_text_programmatically("")
        self._programmatic_update = False
        if doc:
            self._apply_final_display(doc)
            self._learn_suggestion(doc, self.final_field.text())

    def _update_preview(self) -> None:
        doc = self._selected_item()
        if not doc:
            self.suggestions_list.clear()
            self.preview.clear()
            self.preview_label.setText("Preview")
            return
        key = self._doc_key(doc)
        existing = self._manual_overrides.get(key)
        if existing and self._is_placeholder_filename(existing):
            self._manual_overrides.pop(key, None)
            logger.info("Removed placeholder override for key=%s existing=%s", key, existing)
        manual_text = self.manual_edit.text().strip()
        self._populate_suggestions_ui(doc)
        if manual_text:
            manual_name = self._normalize_suggestion(manual_text)
            if not self._programmatic_update and not self._is_placeholder_filename(manual_name):
                self._manual_overrides[key] = manual_name
                logger.info("Preview manual override set key=%s filename=%s", key, manual_name)
        else:
            if key in self._manual_overrides and self._is_placeholder_filename(self._manual_overrides[key]):
                self._manual_overrides.pop(key, None)
        self._apply_final_display(doc)
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
                detect_key = self._doc_key(doc)
                doctype, number, detected_date, err = detect_doc_fields_from_pdf(str(path))
                changed = False
                if doctype:
                    changed = changed or (doc.doctype != doctype)
                    doc.doctype = doctype
                if number:
                    changed = changed or (doc.number != number)
                    doc.number = number
                if detected_date:
                    changed = changed or (doc.date_str != detected_date)
                    doc.date_str = detected_date
                if doctype or number or detected_date:
                    logger.info("Auto-detected fields applied key=%s doctype=%s number=%s date=%s path=%s", detect_key, doc.doctype, doc.number, doc.date_str, path)
                if err:
                    logger.debug("Auto-detect text error for %s: %s", path, err)
                if changed:
                    if detect_key == self._active_doc_key:
                        self._recompute_suggestion(doc, reason="autodetect")
                    else:
                        logger.info("Detect result stored for inactive doc key=%s active=%s", detect_key, self._active_doc_key)
            else:
                self.preview.clear()
                self.preview_label.setText("Preview unavailable")
                logger.warning("Rename preview failed to load: %s", path)
        finally:
            self._preview_loading = False

    def _sync_fields_from_selection(self) -> None:
        doc = self._selected_item()
        if not doc:
            self.final_field.clear()
            self.preview.clear()
            self.preview_label.setText("Preview")
            return
        self._active_doc_key = self._doc_key(doc)
        self.manual_edit.clear()
        key = self._doc_key(doc)
        manual_existing = self._manual_overrides.get(key, "")
        if manual_existing:
            self._set_manual_text_programmatically(manual_existing)
        self._populate_suggestions_ui(doc)
        self._apply_final_display(doc)
        self._start_ocr_if_needed(doc)
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
        docs = self._checked_docs()
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
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
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
            self._active_doc_key = self._doc_key(doc)
            self.preview_label.setText(f"Preview - {doc.display_name}")
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
            key = self._doc_key(doc)
            chosen_option = "MANUAL" if key in self._manual_overrides else "A"
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
                self._manual_overrides.pop(key, None)
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
                self._manual_overrides.pop(self._doc_key(doc), None)
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
            for thread in list(self._ocr_threads.values()):
                if thread and thread.isRunning():
                    thread.quit()
                    thread.wait(500)
        except Exception:
            pass
        self._active_thread = None
        self._active_worker = None
        super().closeEvent(event)

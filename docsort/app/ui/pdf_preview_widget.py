import logging
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

from docsort.app.services import preview_cache

logger = logging.getLogger(__name__)


class PdfPreviewWidget(QtWidgets.QWidget):
    """
    PDF preview widget using QtPdf (QPdfDocument + QPdfView).

    PySide6 6.10.x note:
      - QPdfView does NOT expose setPage() / setPageIndex()
      - Page navigation is done via QPdfView.pageNavigator().jump(pageIndex, QPointF, zoom)
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._pending_page: Optional[int] = None
        self._current_path: Optional[str] = None
        self._is_swapping: bool = False

        self._empty_doc = QPdfDocument(self)
        self.document = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)

        try:
            self.document.statusChanged.connect(self._on_status_changed)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF preview: statusChanged connect failed: %s", exc)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.setMinimumHeight(240)
        logger.info("PDF preview init: pageNavigator available=%s", hasattr(self.view, "pageNavigator"))

        self._page_navigator = None

    def load_pdf(self, path: str) -> bool:
        pdf_path = Path(path)
        cached_path = preview_cache.cache_pdf_for_preview(pdf_path)
        if not cached_path:
            logger.warning("PDF preview load aborted: could not cache %s", pdf_path)
            return False
        self._current_path = str(pdf_path)
        self._pending_page = 0
        logger.info("PDF preview using cached copy: src=%s cached=%s", pdf_path, cached_path)

        if not cached_path.exists():
            logger.warning("PDF preview load failed: missing cached path %s", cached_path)
            return False

        status = self.document.load(str(cached_path))
        logger.info("PDF preview load started path=%s (cached=%s) status=%s", pdf_path, cached_path, status)
        if status == QPdfDocument.Status.Error:
            logger.warning("PDF preview load immediate error for %s", pdf_path)
            return False
        if status == QPdfDocument.Status.Ready:
            logger.info(
                "PDF preview ready immediately path=%s cached=%s pageCount=%s",
                pdf_path,
                cached_path,
                self.document.pageCount(),
            )
            self._apply_pending_page()
        return True

    def set_page(self, index: int) -> None:
        if self.document.pageCount() > 0:
            index = max(0, min(index, self.document.pageCount() - 1))
        self._pending_page = index
        if self.document.status() == QPdfDocument.Status.Ready:
            self._apply_pending_page()

    def clear(self) -> None:
        logger.info("PDF preview cleared")
        self.release_document()

    def _detach_navigator(self) -> None:
        try:
            if hasattr(self, "_page_navigator") and self._page_navigator:
                # Best-effort reset; navigator API is limited
                try:
                    self._page_navigator = None
                except Exception:
                    self._page_navigator = None
        except Exception:
            pass

    def force_release_document(self) -> None:
        logger.info("PDF preview force release start")
        if not getattr(self, "view", None):
            logger.debug("PDF preview force release skipped: no view")
            return
        if self._is_swapping:
            logger.debug("PDF preview force release skipped: already swapping")
            return
        self._is_swapping = True
        old_doc = getattr(self, "document", None)
        if old_doc is self._empty_doc:
            old_doc = None
        new_doc = QPdfDocument(self)
        try:
            new_doc.statusChanged.connect(self._on_status_changed)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF preview: statusChanged connect failed after force release: %s", exc)
        try:
            self.view.setDocument(new_doc)
            self.view.setPageMode(QPdfView.PageMode.SinglePage)
        except Exception as exc:  # noqa: BLE001
            logger.debug("PDF preview setDocument during release failed: %s", exc)
        try:
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        if old_doc:
            try:
                old_doc.statusChanged.disconnect(self._on_status_changed)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                old_doc.close()
                logger.debug("PDF preview old document closed")
            except Exception:
                pass
            try:
                old_doc.deleteLater()
            except Exception:
                pass
        self.document = new_doc
        self._pending_page = None
        self._current_path = None
        try:
            self._page_navigator = self.view.pageNavigator()
            logger.debug("PDF preview navigator rebound")
        except Exception:
            self._page_navigator = None
        logger.info("PDF preview document swap complete")
        self._is_swapping = False

    @QtCore.Slot("QPdfDocument::Status")
    def _on_status_changed(self, status) -> None:
        logger.info("PDF preview statusChanged: %s path=%s", status, self._current_path)
        if status == QPdfDocument.Status.Ready:
            logger.info("PDF preview ready path=%s pageCount=%s", self._current_path, self.document.pageCount())
            self._apply_pending_page()
        elif status == QPdfDocument.Status.Error:
            logger.warning("PDF preview error status path=%s", self._current_path)

    def release_document(self) -> None:
        try:
            self.document.statusChanged.disconnect(self._on_status_changed)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self.document.close()
        except Exception:
            pass
        try:
            self.document.deleteLater()
        except Exception:
            pass
        self.document = QPdfDocument(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        try:
            self.document.statusChanged.connect(self._on_status_changed)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF preview: statusChanged connect failed after release: %s", exc)
        self._pending_page = None
        self._current_path = None
        logger.info("PDF preview document released/reset")

    def _apply_pending_page(self) -> None:
        if self.document.status() != QPdfDocument.Status.Ready:
            return
        if self._pending_page is None:
            return
        page_count = self.document.pageCount()
        if page_count <= 0:
            return
        idx = max(0, min(self._pending_page, page_count - 1))
        self._pending_page = None
        try:
            nav = self.view.pageNavigator()
            try:
                nav.jump(idx, QtCore.QPointF(0, 0))
            except TypeError:
                nav.jump(idx, QtCore.QPointF(0, 0), None)
            logger.info("PDF preview show page index=%s via pageNavigator.jump", idx)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF preview jump failed idx=%s: %s", idx, exc)
        # Fallback attempts (future-proof)
        for meth_name in ("setPageIndex", "setPage"):
            try:
                meth = getattr(self.view, meth_name, None)
                if callable(meth):
                    meth(idx)
                    logger.info("PDF preview show page index=%s via %s()", idx, meth_name)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("PDF preview %s failed: %s", meth_name, exc)
        logger.warning("PDF preview could not change page (no supported API found).")

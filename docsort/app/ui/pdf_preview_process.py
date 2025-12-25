import sys
from pathlib import Path

from PySide6 import QtWidgets, QtPdf, QtPdfWidgets


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    if len(sys.argv) < 2:
        print("Usage: pdf_preview_process.py <pdf_path>")
        return 1

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        return 1

    doc = QtPdf.QPdfDocument()
    status = doc.load(str(pdf_path))

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("DocSort PDF Preview")
    view = QtPdfWidgets.QPdfView()
    view.setDocument(doc)
    view.setPageMode(QtPdfWidgets.QPdfView.PageMode.SinglePage)

    if status == QtPdf.QPdfDocument.Status.Ready and doc.pageCount() > 0:
        view.pageNavigator().jump(0)

    window.setCentralWidget(view)
    window.resize(800, 600)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

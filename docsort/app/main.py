import sys

from PySide6 import QtWidgets

from docsort.app.ui.main_window import MainWindow
from docsort.app.utils.logging_setup import configure_logging


def main() -> None:
    configure_logging()
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

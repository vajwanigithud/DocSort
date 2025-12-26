import sys

from PySide6 import QtWidgets

from docsort.app.ui.main_window import MainWindow
from docsort.app.ui.app_style import apply_app_style
from docsort.app.utils.logging_setup import configure_logging


def main() -> None:
    configure_logging()
    app = QtWidgets.QApplication(sys.argv)
    apply_app_style(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

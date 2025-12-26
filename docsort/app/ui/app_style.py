from PySide6 import QtGui, QtWidgets


def apply_app_style(app: QtWidgets.QApplication) -> None:
    """Apply global QSS theme."""
    font = QtGui.QFont("Segoe UI", 10)
    app.setFont(font)

    qss = """
    /* Base */
    QWidget {
        background: #f7f8fa;
        color: #1f2933;
        font-size: 10pt;
    }

    QMainWindow {
        background: #f7f8fa;
    }

    /* Tabs */
    QTabWidget::pane {
        border: 1px solid #d4d8dd;
        border-radius: 8px;
        padding: 6px;
        background: #ffffff;
    }
    QTabBar::tab {
        background: #eef1f5;
        border: 1px solid #d4d8dd;
        border-bottom: none;
        border-radius: 8px 8px 0 0;
        padding: 8px 14px;
        margin-right: 4px;
        color: #1f2933;
    }
    QTabBar::tab:selected {
        background: #ffffff;
        border-color: #4c8bf5;
        color: #0f172a;
    }
    QTabBar::tab:hover {
        background: #e3e7ed;
    }

    /* Buttons */
    QPushButton {
        background: #e9ecf1;
        border: 1px solid #d4d8dd;
        border-radius: 6px;
        padding: 6px 14px;
        color: #1f2933;
    }
    QPushButton:hover {
        background: #dfe3e9;
    }
    QPushButton:pressed {
        background: #d1d6dd;
    }
    QPushButton:disabled {
        background: #f1f3f6;
        color: #9aa5b1;
        border-color: #e5e7eb;
    }

    /* Primary / Danger via dynamic property */
    QPushButton[class="primary"] {
        background: #4c8bf5;
        color: #ffffff;
        border: 1px solid #3975d6;
    }
    QPushButton[class="primary"]:hover {
        background: #3f7be0;
    }
    QPushButton[class="primary"]:pressed {
        background: #356bc4;
    }
    QPushButton[class="danger"] {
        background: #e35d6a;
        color: #ffffff;
        border: 1px solid #cf4d5b;
    }
    QPushButton[class="danger"]:hover {
        background: #d44f5c;
    }
    QPushButton[class="danger"]:pressed {
        background: #c44552;
    }

    /* Inputs */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
        background: #ffffff;
        border: 1px solid #d4d8dd;
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: #4c8bf5;
        selection-color: #ffffff;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
        border: 1px solid #4c8bf5;
    }

    /* Lists / Tables */
    QListWidget, QTreeWidget, QTableWidget {
        background: #ffffff;
        border: 1px solid #d4d8dd;
        border-radius: 6px;
    }
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
        background: #e5f0ff;
        color: #0f172a;
    }

    /* GroupBox */
    QGroupBox {
        border: 1px solid #d4d8dd;
        border-radius: 6px;
        margin-top: 12px;
        padding-top: 8px;
        font-weight: 600;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 4px;
    }

    /* Radio buttons */
    QRadioButton::indicator {
        width: 14px;
        height: 14px;
        border-radius: 7px;
        border: 1px solid #7a8699;
        background: #ffffff;
    }
    QRadioButton::indicator:checked {
        border: 1px solid #4c8bf5;
        background: qradialgradient(
            cx:0.5, cy:0.5, radius:0.45,
            fx:0.5, fy:0.5,
            stop:0 #4c8bf5,
            stop:0.55 #4c8bf5,
            stop:0.56 #ffffff,
            stop:1 #ffffff
        );
    }
    QRadioButton {
        color: #1f2933;
    }

    /* ScrollBars */
    QScrollBar:vertical {
        border: none;
        background: #f1f3f6;
        width: 12px;
        margin: 2px 0 2px 0;
        border-radius: 6px;
    }
    QScrollBar::handle:vertical {
        background: #cfd6de;
        min-height: 24px;
        border-radius: 6px;
    }
    QScrollBar::handle:vertical:hover {
        background: #b8c2cd;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
        background: none;
    }
    QScrollBar:horizontal {
        border: none;
        background: #f1f3f6;
        height: 12px;
        margin: 0 2px 0 2px;
        border-radius: 6px;
    }
    QScrollBar::handle:horizontal {
        background: #cfd6de;
        min-width: 24px;
        border-radius: 6px;
    }
    QScrollBar::handle:horizontal:hover {
        background: #b8c2cd;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0;
        background: none;
    }
    """

    app.setStyleSheet(qss)

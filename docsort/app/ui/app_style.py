from __future__ import annotations

from PySide6 import QtWidgets


def apply_app_style(app: QtWidgets.QApplication) -> None:
    qss = """
    /* Global */
    QWidget {
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 12px;
        color: #1f2933;
        background: #f7f9fc;
    }

    QMainWindow {
        background: #f7f9fc;
    }

    /* Tabs */
    QTabWidget::pane {
        border: 1px solid #d9e2ec;
        border-radius: 10px;
        padding: 6px;
        background: #ffffff;
    }
    QTabBar::tab {
        background: #eef2f7;
        border: 1px solid #d9e2ec;
        border-bottom: none;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        padding: 8px 14px;
        margin-right: 6px;
        color: #334e68;
    }
    QTabBar::tab:selected {
        background: #ffffff;
        border-color: #4c8bf5;
        color: #102a43;
    }

    /* Group boxes */
    QGroupBox {
        border: 1px solid #d9e2ec;
        border-radius: 10px;
        margin-top: 10px;
        padding: 10px;
        background: #ffffff;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: #334e68;
        font-weight: 600;
    }

    /* Inputs */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
        background: #ffffff;
        border: 1px solid #cbd2d9;
        border-radius: 8px;
        padding: 8px;
        selection-background-color: #4c8bf5;
        selection-color: #ffffff;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
        border: 1px solid #4c8bf5;
    }

    /* Lists */
    QListWidget {
        background: #ffffff;
        border: 1px solid #d9e2ec;
        border-radius: 10px;
        padding: 6px;
    }
    QListWidget::item {
        padding: 10px;
        border-radius: 8px;
    }
    QListWidget::item:selected {
        background: #4c8bf5;
        color: #ffffff;
    }

    /* Buttons */
    QPushButton {
        background: #e9eef6;
        border: 1px solid #cbd2d9;
        border-radius: 10px;
        padding: 10px 14px;
        font-weight: 600;
        color: #102a43;
    }
    QPushButton:hover {
        background: #dbe6ff;
        border-color: #4c8bf5;
    }
    QPushButton:pressed {
        background: #c7d8ff;
    }
    QPushButton:disabled {
        background: #f0f4f8;
        color: #9fb3c8;
        border-color: #e0e7ef;
    }

    /* "Primary" and "Danger" via objectName (if used) */
    QPushButton#primaryButton {
        background: #4c8bf5;
        border-color: #4c8bf5;
        color: #ffffff;
    }
    QPushButton#primaryButton:hover {
        background: #3a78e0;
        border-color: #3a78e0;
    }
    QPushButton#dangerButton {
        background: #e12d39;
        border-color: #e12d39;
        color: #ffffff;
    }
    QPushButton#dangerButton:hover {
        background: #cf1124;
        border-color: #cf1124;
    }

    /* Scrollbars */
    QScrollBar:vertical {
        background: transparent;
        width: 12px;
        margin: 4px;
    }
    QScrollBar::handle:vertical {
        background: #cbd2d9;
        border-radius: 6px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover {
        background: #9fb3c8;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }

    /* Radios + Checkboxes */
    QRadioButton, QCheckBox {
        spacing: 10px;
        color: #1f2933;
    }

    QRadioButton::indicator {
        width: 16px;
        height: 16px;
        border-radius: 8px;
        border: 1px solid #cbd2d9;
        background: #ffffff;
    }
    QRadioButton::indicator:checked {
        border: 1px solid #4c8bf5;
        /* Blue ring with a clear white dot center so "selected" is never invisible */
        background: qradialgradient(
            cx:0.5, cy:0.5, radius:0.50,
            fx:0.5, fy:0.5,
            stop:0 #ffffff,
            stop:0.22 #ffffff,
            stop:0.23 #4c8bf5,
            stop:1 #4c8bf5
        );
    }

    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid #cbd2d9;
        background: #ffffff;
    }
    QCheckBox::indicator:checked {
        border: 1px solid #4c8bf5;
        background: #4c8bf5;
    }

    /* Labels */
    QLabel {
        color: #102a43;
    }
    QLabel#mutedLabel {
        color: #52606d;
    }

    /* Status / warning */
    QLabel#warningLabel {
        color: #cf1124;
        font-weight: 600;
    }
    """
    app.setStyleSheet(qss)

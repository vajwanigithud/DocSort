"""Shared lightweight UI helpers."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


def create_preview_label(text: str = "Preview") -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setMinimumWidth(200)
    label.setStyleSheet(
        """
        QLabel {
            border: 1px solid #cccccc;
            background: #f9f9f9;
            padding: 16px;
        }
        """
    )
    return label


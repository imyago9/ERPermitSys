from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from coghud.ui.assets import current_theme_mode, icon_asset_path


class DialogTitleBar(QWidget):
    close_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, *, theme_mode: str = "dark") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme_mode = theme_mode if theme_mode in ("light", "dark") else "dark"
        self._title_label = QLabel(self)
        self._close_button = QToolButton(self)
        self._drag_active = False
        self._drag_offset = QPoint()

        self._title_label.setObjectName("DialogTitleLabel")
        self._close_button.setObjectName("DialogCloseButton")
        self._close_button.setAutoRaise(True)
        self._close_button.setFixedSize(30, 24)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolTip("Close")

        self._apply_close_icon()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addStretch(1)
        layout.addWidget(self._close_button)

        self._close_button.clicked.connect(self.close_requested.emit)

    def set_theme_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._apply_close_icon()

    def set_title(self, text: str) -> None:
        self._title_label.setText(text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_drag_target(event.position().toPoint()):
            self._drag_active = True
            window = self.window()
            if window:
                self._drag_offset = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_active:
            window = self.window()
            if window:
                window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
            self._drag_active = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _is_drag_target(self, pos) -> bool:
        child = self.childAt(pos)
        if child is None:
            return True
        return not (child is self._close_button or self._close_button.isAncestorOf(child))

    def _apply_close_icon(self) -> None:
        icon = QIcon(icon_asset_path("close_window.png", mode=self._theme_mode))
        if icon.isNull():
            self._close_button.setIcon(QIcon())
            self._close_button.setText("x")
            return
        self._close_button.setText("")
        self._close_button.setIcon(icon)
        self._close_button.setIconSize(self._close_button.size() * 0.45)


class FramelessDialog(QDialog):
    def __init__(
        self,
        title: str = "",
        parent: Optional[QWidget] = None,
        *,
        theme_mode: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_mode = (
            theme_mode
            if isinstance(theme_mode, str) and theme_mode in ("light", "dark")
            else current_theme_mode()
        )
        self.setObjectName("FramelessDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumSize(520, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        self._frame = QFrame(self)
        self._frame.setObjectName("FramelessDialogFrame")
        root.addWidget(self._frame)

        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.title_bar = DialogTitleBar(self._frame, theme_mode=self._theme_mode)
        self.title_bar.setObjectName("DialogTitleBar")
        self.title_bar.close_requested.connect(self.reject)
        frame_layout.addWidget(self.title_bar)

        self.body = QWidget(self._frame)
        self.body.setObjectName("DialogBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 14, 14, 14)
        self.body_layout.setSpacing(10)
        frame_layout.addWidget(self.body, 1)

        self.set_dialog_title(title)

    def set_dialog_title(self, title: str) -> None:
        self.title_bar.set_title(title)

    def set_theme_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        self._theme_mode = mode
        self.title_bar.set_theme_mode(mode)
        self._refresh_theme_styles()

    def _refresh_theme_styles(self) -> None:
        widgets = (self._frame, self.title_bar, self.body)
        for widget in widgets:
            style = widget.style()
            if style is None:
                continue
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

from __future__ import annotations

from typing import Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from erpermitsys.ui.window.frameless_dialog import FramelessDialog


def _normalize_theme_mode(mode: str | None) -> str:
    if mode in ("dark", "light"):
        return mode
    return "dark"


class AppMessageDialog(FramelessDialog):
    def __init__(
        self,
        *,
        title: str,
        message: str,
        warning: bool,
        parent=None,
        theme_mode: str | None = None,
    ) -> None:
        super().__init__(
            title=title,
            parent=parent,
            theme_mode=_normalize_theme_mode(theme_mode),
        )
        self.setMinimumSize(500, 220)
        self.resize(560, 250)

        message_label = QLabel(message, self.body)
        message_label.setWordWrap(True)
        message_label.setObjectName("PluginPickerWarning" if warning else "PluginPickerHint")
        self.body_layout.addWidget(message_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)
        ok_button = QPushButton("OK", self.body)
        ok_button.setObjectName("PluginPickerButton")
        ok_button.setProperty("primary", "true")
        ok_button.clicked.connect(self.accept)
        footer.addWidget(ok_button)
        self.body_layout.addLayout(footer)

        ok_button.setFocus()

    @classmethod
    def show_info(
        cls,
        *,
        parent,
        title: str,
        message: str,
        theme_mode: str | None = None,
    ) -> None:
        dialog = cls(
            title=title,
            message=message,
            warning=False,
            parent=parent,
            theme_mode=theme_mode,
        )
        dialog.exec()

    @classmethod
    def show_warning(
        cls,
        *,
        parent,
        title: str,
        message: str,
        theme_mode: str | None = None,
    ) -> None:
        dialog = cls(
            title=title,
            message=message,
            warning=True,
            parent=parent,
            theme_mode=theme_mode,
        )
        dialog.exec()


class AppConfirmDialog(FramelessDialog):
    def __init__(
        self,
        *,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
        parent=None,
        theme_mode: str | None = None,
    ) -> None:
        super().__init__(
            title=title,
            parent=parent,
            theme_mode=_normalize_theme_mode(theme_mode),
        )
        self.setMinimumSize(500, 230)
        self.resize(560, 260)

        message_label = QLabel(message, self.body)
        message_label.setWordWrap(True)
        message_label.setObjectName("PluginPickerWarning" if danger else "PluginPickerHint")
        self.body_layout.addWidget(message_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton(cancel_text, self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        confirm_button = QPushButton(confirm_text, self.body)
        if danger:
            confirm_button.setObjectName("PermitFormDangerButton")
        else:
            confirm_button.setObjectName("PluginPickerButton")
            confirm_button.setProperty("primary", "true")
        confirm_button.clicked.connect(self.accept)
        footer.addWidget(confirm_button)
        self.body_layout.addLayout(footer)

        cancel_button.setFocus()

    @classmethod
    def ask(
        cls,
        *,
        parent,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
        theme_mode: str | None = None,
    ) -> bool:
        dialog = cls(
            title=title,
            message=message,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            danger=danger,
            parent=parent,
            theme_mode=theme_mode,
        )
        return dialog.exec() == dialog.DialogCode.Accepted


class AppTextInputDialog(FramelessDialog):
    def __init__(
        self,
        *,
        title: str,
        label_text: str,
        text: str = "",
        placeholder: str = "",
        confirm_text: str = "Save",
        cancel_text: str = "Cancel",
        parent=None,
        theme_mode: str | None = None,
    ) -> None:
        super().__init__(
            title=title,
            parent=parent,
            theme_mode=_normalize_theme_mode(theme_mode),
        )
        self.setMinimumSize(520, 240)
        self.resize(580, 270)

        prompt_label = QLabel(label_text, self.body)
        prompt_label.setWordWrap(True)
        prompt_label.setObjectName("PluginPickerHint")
        self.body_layout.addWidget(prompt_label)

        self.input = QLineEdit(self.body)
        self.input.setObjectName("PluginPickerSearch")
        self.input.setPlaceholderText(placeholder)
        self.input.setText(text)
        self.input.selectAll()
        self.body_layout.addWidget(self.input)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton(cancel_text, self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        submit_button = QPushButton(confirm_text, self.body)
        submit_button.setObjectName("PluginPickerButton")
        submit_button.setProperty("primary", "true")
        submit_button.clicked.connect(self.accept)
        footer.addWidget(submit_button)
        self.body_layout.addLayout(footer)

        self.input.returnPressed.connect(self.accept)
        self.input.setFocus()

    @classmethod
    def get_text(
        cls,
        *,
        parent,
        title: str,
        label_text: str,
        text: str = "",
        placeholder: str = "",
        confirm_text: str = "Save",
        cancel_text: str = "Cancel",
        theme_mode: str | None = None,
    ) -> Tuple[str, bool]:
        dialog = cls(
            title=title,
            label_text=label_text,
            text=text,
            placeholder=placeholder,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            parent=parent,
            theme_mode=theme_mode,
        )
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        return dialog.input.text(), accepted

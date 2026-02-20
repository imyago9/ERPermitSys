from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from erpermitsys.app.tracker_models import normalize_permit_type
from erpermitsys.ui.window.app_dialogs import AppConfirmDialog, AppMessageDialog
from erpermitsys.ui.window.frameless_dialog import FramelessDialog


class WindowDialogsMixin:
    def _dialog_theme_mode(self) -> str:
        return "dark" if self._dark_mode_enabled else "light"

    def _show_info_dialog(self, title: str, message: str) -> None:
        AppMessageDialog.show_info(
            parent=self,
            title=title,
            message=message,
            theme_mode=self._dialog_theme_mode(),
        )

    def _show_warning_dialog(self, title: str, message: str) -> None:
        AppMessageDialog.show_warning(
            parent=self,
            title=title,
            message=message,
            theme_mode=self._dialog_theme_mode(),
        )

    def _confirm_dialog(
        self,
        title: str,
        message: str,
        *,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
    ) -> bool:
        return AppConfirmDialog.ask(
            parent=self,
            title=title,
            message=message,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            danger=danger,
            theme_mode=self._dialog_theme_mode(),
        )

    def _pick_template_default_target_type(self, *, template_name: str) -> str:
        dialog = FramelessDialog(
            title="Set Default Template",
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        dialog.setMinimumSize(560, 250)
        dialog.resize(620, 280)

        prompt = QLabel(
            f"Set '{template_name}' as default for which permit type?",
            dialog.body,
        )
        prompt.setWordWrap(True)
        prompt.setObjectName("PluginPickerHint")
        dialog.body_layout.addWidget(prompt)

        hint = QLabel(
            "Choose Building, Remodeling, or Demolition. You can also cancel.",
            dialog.body,
        )
        hint.setWordWrap(True)
        hint.setObjectName("TrackerPanelMeta")
        dialog.body_layout.addWidget(hint)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        selected: dict[str, str] = {"permit_type": ""}

        def _pick(value: str) -> None:
            selected["permit_type"] = value
            dialog.accept()

        for label, value in (
            ("Building", "building"),
            ("Remodeling", "remodeling"),
            ("Demolition", "demolition"),
        ):
            button = QPushButton(label, dialog.body)
            button.setObjectName("TrackerPanelActionButton")
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, choice=value: _pick(choice))
            footer.addWidget(button, 0)

        cancel_button = QPushButton("Cancel", dialog.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.setMinimumHeight(32)
        cancel_button.clicked.connect(dialog.reject)
        footer.addWidget(cancel_button, 0)

        dialog.body_layout.addLayout(footer)

        cancel_button.setFocus()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return ""
        selected_type = str(selected.get("permit_type", "")).strip()
        if not selected_type:
            return ""
        return normalize_permit_type(selected_type)

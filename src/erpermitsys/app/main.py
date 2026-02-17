from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional runtime dependency
    QWebEngineView = None  # type: ignore[assignment]
try:
    from PySide6.QtWebChannel import QWebChannel
except Exception:  # pragma: no cover - optional runtime dependency
    QWebChannel = None  # type: ignore[assignment]

from erpermitsys.app.command_runtime import AppCommandContext, CommandRuntime
from erpermitsys.app.background_plugin_bridge import BackgroundPluginBridge
from erpermitsys.core import StateStreamer
from erpermitsys.plugins import PluginManager
from erpermitsys.plugins.api import PluginApiService
from erpermitsys.app.settings_store import (
    DEFAULT_PALETTE_SHORTCUT,
    load_active_plugin_ids,
    load_dark_mode,
    load_palette_shortcut_enabled,
    load_palette_shortcut_keybind,
    save_active_plugin_ids,
    save_dark_mode,
    save_palette_shortcut_settings,
)
from erpermitsys.ui.assets import icon_asset_path
from erpermitsys.ui.settings import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme
from erpermitsys.ui.window.frameless_window import FramelessWindow


@dataclass
class ContactRecord:
    name: str
    number: str
    email: str


@dataclass
class PermitRecord:
    parcel_id: str
    address: str
    request_date: str
    application_date: str
    completion_date: str
    client_name: str
    contractor_name: str


class ContactEntryDialog(QDialog):
    def __init__(self, *, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContactEntryDialog")
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(title, self)
        heading.setObjectName("ContactEntryDialogTitle")
        layout.addWidget(heading)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        self._name_input = QLineEdit(self)
        self._name_input.setObjectName("ContactEntryName")
        self._name_input.setPlaceholderText("Full name")
        self._number_input = QLineEdit(self)
        self._number_input.setObjectName("ContactEntryNumber")
        self._number_input.setPlaceholderText("Phone number")
        self._email_input = QLineEdit(self)
        self._email_input.setObjectName("ContactEntryEmail")
        self._email_input.setPlaceholderText("Email")

        form_layout.addRow("Name", self._name_input)
        form_layout.addRow("Number", self._number_input)
        form_layout.addRow("Email", self._email_input)
        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._name_input.textChanged.connect(self._sync_save_enabled)
        self._sync_save_enabled()

    def _sync_save_enabled(self) -> None:
        if self._save_button is None:
            return
        self._save_button.setEnabled(bool(self._name_input.text().strip()))

    def record(self) -> ContactRecord:
        return ContactRecord(
            name=self._name_input.text().strip(),
            number=self._number_input.text().strip(),
            email=self._email_input.text().strip(),
        )


class ErPermitSysWindow(FramelessWindow):
    def __init__(
        self,
        *,
        dark_mode_enabled: bool = False,
        palette_shortcut_enabled: bool = True,
        palette_shortcut_keybind: str = DEFAULT_PALETTE_SHORTCUT,
        state_streamer: StateStreamer | None = None,
    ) -> None:
        theme_mode = "dark" if dark_mode_enabled else "light"
        super().__init__(
            title="erpermitsys",
            icon_path=icon_asset_path("resize_handle.png", mode=theme_mode),
            theme_mode=theme_mode,
        )
        self.setMinimumSize(940, 620)
        self.resize(1080, 700)
        self._plugin_manager = PluginManager.from_default_layout()
        self._plugin_api = PluginApiService(self._plugin_manager, active_kind="html-background")
        self._plugin_bridge = BackgroundPluginBridge(self._plugin_api)
        self._current_background_url: str | None = None
        self._dark_mode_enabled = bool(dark_mode_enabled)
        self._palette_shortcut_enabled = bool(palette_shortcut_enabled)
        self._palette_shortcut_keybind = (
            palette_shortcut_keybind.strip() if isinstance(palette_shortcut_keybind, str) else ""
        ) or DEFAULT_PALETTE_SHORTCUT
        self._state_streamer = state_streamer or StateStreamer()
        self._settings_dialog: SettingsDialog | None = None
        self._command_runtime: CommandRuntime | None = None

        self._clients: list[ContactRecord] = []
        self._contractors: list[ContactRecord] = []
        self._permits: list[PermitRecord] = []

        self._stack: QStackedLayout | None = None
        self._fallback_widget: QFrame | None = None
        self._background_view: QWebEngineView | None = None
        self._background_web_channel: QWebChannel | None = None
        self._scene_widget: QWidget | None = None
        self._panel_host: QWidget | None = None
        self._panel_stack: QStackedLayout | None = None
        self._panel_home_view: QWidget | None = None
        self._permit_form_view: QWidget | None = None
        self._clients_list_widget: QListWidget | None = None
        self._contractors_list_widget: QListWidget | None = None
        self._permits_list_widget: QListWidget | None = None
        self._permit_parcel_input: QLineEdit | None = None
        self._permit_address_input: QLineEdit | None = None
        self._permit_request_date_input: QLineEdit | None = None
        self._permit_application_date_input: QLineEdit | None = None
        self._permit_completion_date_input: QLineEdit | None = None
        self._permit_client_combo: QComboBox | None = None
        self._permit_contractor_combo: QComboBox | None = None
        self._settings_button: QPushButton | None = None
        self._settings_button_shadow: QGraphicsDropShadowEffect | None = None

        self._build_body()
        self._plugin_manager.discover(auto_activate_background=False)
        self._restore_active_plugins()
        self._sync_background_from_plugins()
        QTimer.singleShot(0, self._sync_foreground_layout)
        self._state_streamer.record(
            "window.initialized",
            source="main_window",
            payload={
                "theme_mode": theme_mode,
                "has_webengine": bool(QWebEngineView is not None),
            },
        )

    def _build_body(self) -> None:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scene = QWidget(page)
        scene.setObjectName("AppScene")
        self._scene_widget = scene
        scene.installEventFilter(self)
        scene_layout = QVBoxLayout(scene)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_layout.setSpacing(0)

        stack_host = QWidget(scene)
        stack = QStackedLayout(stack_host)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._stack = stack

        fallback = QFrame(stack_host)
        fallback.setObjectName("FallbackBackground")
        stack.addWidget(fallback)
        self._fallback_widget = fallback

        if QWebEngineView is not None:
            web = QWebEngineView(stack_host)
            web.setObjectName("BackgroundWebView")
            web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            web.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            web.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
            web.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            try:
                web.page().setBackgroundColor(QColor(6, 10, 16))
            except Exception:
                pass
            try:
                web.loadFinished.connect(self._on_background_load_finished)
            except Exception:
                pass
            if QWebChannel is not None:
                try:
                    channel = QWebChannel(web.page())
                    channel.registerObject("erpermitsysBridge", self._plugin_bridge)
                    web.page().setWebChannel(channel)
                    self._background_web_channel = channel
                except Exception:
                    self._background_web_channel = None
            stack.addWidget(web)
            self._background_view = web
        else:
            self._background_view = None
            self._background_web_channel = None

        scene_layout.addWidget(stack_host, 1)

        button = QPushButton("Settings", scene)
        button.setObjectName("SettingsLauncherButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        button.clicked.connect(self.open_settings_dialog)
        button.hide()  # Avoid initial top-left flash before first geometry sync.
        self._settings_button = button
        self._apply_settings_button_effect()

        self._build_tracker_overlay(scene)
        self._refresh_clients_list()
        self._refresh_contractors_list()
        self._refresh_permits_list()
        self._refresh_party_selectors()

        page_layout.addWidget(scene, 1)
        self.body_layout.addWidget(page)
        self._sync_foreground_layout()

    def _build_tracker_overlay(self, scene: QWidget) -> None:
        panel_host = QWidget(scene)
        panel_host.setObjectName("PermitPanelHost")
        panel_stack = QStackedLayout(panel_host)
        panel_stack.setContentsMargins(0, 0, 0, 0)
        panel_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._panel_host = panel_host
        self._panel_stack = panel_stack

        panel_home = QWidget(panel_host)
        panel_home.setObjectName("PermitPanelStrip")
        panel_home_layout = QHBoxLayout(panel_home)
        panel_home_layout.setContentsMargins(0, 0, 0, 0)
        panel_home_layout.setSpacing(24)
        self._panel_home_view = panel_home

        clients_panel, clients_layout = self._create_tracker_panel(panel_home, "Clients")
        add_client_button = QPushButton("Add Client", clients_panel)
        add_client_button.setObjectName("TrackerPanelActionButton")
        add_client_button.clicked.connect(self._add_client)
        clients_layout.addWidget(add_client_button)
        clients_list = QListWidget(clients_panel)
        clients_list.setObjectName("TrackerPanelList")
        clients_layout.addWidget(clients_list, 1)
        self._clients_list_widget = clients_list
        panel_home_layout.addWidget(clients_panel, 1)

        permits_panel, permits_layout = self._create_tracker_panel(panel_home, "Permits")
        add_permit_button = QPushButton("Add Permit", permits_panel)
        add_permit_button.setObjectName("TrackerPanelActionButton")
        add_permit_button.clicked.connect(self._open_add_permit_form)
        permits_layout.addWidget(add_permit_button)
        permits_list = QListWidget(permits_panel)
        permits_list.setObjectName("TrackerPanelList")
        permits_layout.addWidget(permits_list, 1)
        self._permits_list_widget = permits_list
        panel_home_layout.addWidget(permits_panel, 1)

        contractors_panel, contractors_layout = self._create_tracker_panel(panel_home, "Contractors")
        add_contractor_button = QPushButton("Add Contractor", contractors_panel)
        add_contractor_button.setObjectName("TrackerPanelActionButton")
        add_contractor_button.clicked.connect(self._add_contractor)
        contractors_layout.addWidget(add_contractor_button)
        contractors_list = QListWidget(contractors_panel)
        contractors_list.setObjectName("TrackerPanelList")
        contractors_layout.addWidget(contractors_list, 1)
        self._contractors_list_widget = contractors_list
        panel_home_layout.addWidget(contractors_panel, 1)

        panel_stack.addWidget(panel_home)

        permit_form_view = QWidget(panel_host)
        permit_form_view.setObjectName("PermitFormView")
        permit_form_layout = QVBoxLayout(permit_form_view)
        permit_form_layout.setContentsMargins(0, 0, 0, 0)
        permit_form_layout.setSpacing(0)
        permit_form_layout.addStretch(1)

        permit_form_card = QFrame(permit_form_view)
        permit_form_card.setObjectName("PermitFormCard")
        permit_form_card.setMinimumWidth(440)
        permit_form_card.setMaximumWidth(680)
        permit_card_layout = QVBoxLayout(permit_form_card)
        permit_card_layout.setContentsMargins(24, 22, 24, 22)
        permit_card_layout.setSpacing(12)

        permit_form_title = QLabel("Add Permit", permit_form_card)
        permit_form_title.setObjectName("PermitFormTitle")
        permit_card_layout.addWidget(permit_form_title)

        permit_form_fields = QFormLayout()
        permit_form_fields.setContentsMargins(0, 0, 0, 0)
        permit_form_fields.setHorizontalSpacing(14)
        permit_form_fields.setVerticalSpacing(10)

        parcel_id_input = QLineEdit(permit_form_card)
        parcel_id_input.setObjectName("PermitFormInput")
        permit_form_fields.addRow("Parcel ID", parcel_id_input)
        self._permit_parcel_input = parcel_id_input

        address_input = QLineEdit(permit_form_card)
        address_input.setObjectName("PermitFormInput")
        permit_form_fields.addRow("Address", address_input)
        self._permit_address_input = address_input

        request_date_input = QLineEdit(permit_form_card)
        request_date_input.setObjectName("PermitFormInput")
        request_date_input.setPlaceholderText("YYYY-MM-DD")
        permit_form_fields.addRow("Request Date", request_date_input)
        self._permit_request_date_input = request_date_input

        application_date_input = QLineEdit(permit_form_card)
        application_date_input.setObjectName("PermitFormInput")
        application_date_input.setPlaceholderText("YYYY-MM-DD")
        permit_form_fields.addRow("Application Date", application_date_input)
        self._permit_application_date_input = application_date_input

        completion_date_input = QLineEdit(permit_form_card)
        completion_date_input.setObjectName("PermitFormInput")
        completion_date_input.setPlaceholderText("YYYY-MM-DD")
        permit_form_fields.addRow("Completion Date", completion_date_input)
        self._permit_completion_date_input = completion_date_input

        client_combo = QComboBox(permit_form_card)
        client_combo.setObjectName("PermitFormCombo")
        permit_form_fields.addRow("Clients", client_combo)
        self._permit_client_combo = client_combo

        contractor_combo = QComboBox(permit_form_card)
        contractor_combo.setObjectName("PermitFormCombo")
        permit_form_fields.addRow("Contractors", contractor_combo)
        self._permit_contractor_combo = contractor_combo

        permit_card_layout.addLayout(permit_form_fields)

        permit_actions = QHBoxLayout()
        permit_actions.setContentsMargins(0, 6, 0, 0)
        permit_actions.setSpacing(10)
        permit_actions.addStretch(1)
        cancel_button = QPushButton("Back", permit_form_card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(self._close_add_permit_form)
        save_button = QPushButton("Save Permit", permit_form_card)
        save_button.setObjectName("PermitFormPrimaryButton")
        save_button.clicked.connect(self._save_permit_from_form)
        permit_actions.addWidget(cancel_button)
        permit_actions.addWidget(save_button)
        permit_card_layout.addLayout(permit_actions)

        permit_form_layout.addWidget(permit_form_card, 0, Qt.AlignmentFlag.AlignHCenter)
        permit_form_layout.addStretch(1)

        self._permit_form_view = permit_form_view
        panel_stack.addWidget(permit_form_view)
        panel_stack.setCurrentWidget(panel_home)
        panel_host.hide()  # Avoid initial top-left flash before first geometry sync.

    def _create_tracker_panel(self, parent: QWidget, title: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame(parent)
        panel.setObjectName("TrackerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title_label = QLabel(title, panel)
        title_label.setObjectName("TrackerPanelTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        return panel, layout

    def _add_client(self) -> None:
        dialog = ContactEntryDialog(title="Add Client", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._clients.append(dialog.record())
        self._refresh_clients_list()
        self._refresh_party_selectors()

    def _add_contractor(self) -> None:
        dialog = ContactEntryDialog(title="Add Contractor", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._contractors.append(dialog.record())
        self._refresh_contractors_list()
        self._refresh_party_selectors()

    def _open_add_permit_form(self) -> None:
        if self._panel_stack is None or self._permit_form_view is None:
            return
        self._reset_permit_form()
        self._refresh_party_selectors()
        self._panel_stack.setCurrentWidget(self._permit_form_view)
        self._sync_foreground_layout()
        if self._permit_parcel_input is not None:
            self._permit_parcel_input.setFocus()

    def _close_add_permit_form(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._sync_foreground_layout()

    def _reset_permit_form(self) -> None:
        inputs = (
            self._permit_parcel_input,
            self._permit_address_input,
            self._permit_request_date_input,
            self._permit_application_date_input,
            self._permit_completion_date_input,
        )
        for field in inputs:
            if field is None:
                continue
            field.clear()

        for combo in (self._permit_client_combo, self._permit_contractor_combo):
            if combo is None or combo.count() == 0:
                continue
            combo.setCurrentIndex(0)

    def _save_permit_from_form(self) -> None:
        if (
            self._permit_parcel_input is None
            or self._permit_address_input is None
            or self._permit_request_date_input is None
            or self._permit_application_date_input is None
            or self._permit_completion_date_input is None
            or self._permit_client_combo is None
            or self._permit_contractor_combo is None
        ):
            return

        parcel_id = self._permit_parcel_input.text().strip()
        address = self._permit_address_input.text().strip()
        request_date = self._permit_request_date_input.text().strip()
        application_date = self._permit_application_date_input.text().strip()
        completion_date = self._permit_completion_date_input.text().strip()
        client_name = str(self._permit_client_combo.currentData() or "").strip()
        contractor_name = str(self._permit_contractor_combo.currentData() or "").strip()

        if not parcel_id:
            QMessageBox.warning(self, "Missing Parcel ID", "Please provide a Parcel ID before saving.")
            return
        if not address:
            QMessageBox.warning(self, "Missing Address", "Please provide an Address before saving.")
            return
        if not client_name:
            QMessageBox.warning(self, "Missing Client", "Add at least one client and select it.")
            return
        if not contractor_name:
            QMessageBox.warning(self, "Missing Contractor", "Add at least one contractor and select it.")
            return

        permit = PermitRecord(
            parcel_id=parcel_id,
            address=address,
            request_date=request_date,
            application_date=application_date,
            completion_date=completion_date,
            client_name=client_name,
            contractor_name=contractor_name,
        )
        self._permits.append(permit)
        self._refresh_permits_list()
        self._close_add_permit_form()

    def _refresh_clients_list(self) -> None:
        lines: list[str] = []
        for client in self._clients:
            contact_bits = [part for part in (client.number, client.email) if part]
            if contact_bits:
                lines.append(f"{client.name} ({' | '.join(contact_bits)})")
            else:
                lines.append(client.name)
        self._refresh_list_widget(self._clients_list_widget, lines, "No clients added yet.")

    def _refresh_contractors_list(self) -> None:
        lines: list[str] = []
        for contractor in self._contractors:
            contact_bits = [part for part in (contractor.number, contractor.email) if part]
            if contact_bits:
                lines.append(f"{contractor.name} ({' | '.join(contact_bits)})")
            else:
                lines.append(contractor.name)
        self._refresh_list_widget(self._contractors_list_widget, lines, "No contractors added yet.")

    def _refresh_permits_list(self) -> None:
        lines: list[str] = []
        for permit in self._permits:
            lines.append(
                f"{permit.parcel_id} - {permit.address} | {permit.client_name} / {permit.contractor_name}"
            )
        self._refresh_list_widget(self._permits_list_widget, lines, "No permits added yet.")

    def _refresh_list_widget(self, widget: QListWidget | None, rows: list[str], empty_message: str) -> None:
        if widget is None:
            return
        widget.clear()
        if not rows:
            widget.addItem(empty_message)
            return
        for row in rows:
            widget.addItem(row)

    def _refresh_party_selectors(self) -> None:
        self._refresh_contact_combo(
            self._permit_client_combo,
            self._clients,
            "Add a client first",
        )
        self._refresh_contact_combo(
            self._permit_contractor_combo,
            self._contractors,
            "Add a contractor first",
        )

    def _refresh_contact_combo(
        self,
        combo: QComboBox | None,
        rows: list[ContactRecord],
        empty_message: str,
    ) -> None:
        if combo is None:
            return
        selected_name = str(combo.currentData() or "").strip()
        combo.blockSignals(True)
        combo.clear()
        if not rows:
            combo.addItem(empty_message, "")
            combo.setEnabled(False)
            combo.blockSignals(False)
            return

        for row in rows:
            combo.addItem(row.name, row.name)
        combo.setEnabled(True)
        if selected_name:
            selected_index = combo.findData(selected_name)
            if selected_index >= 0:
                combo.setCurrentIndex(selected_index)
        combo.blockSignals(False)

    def set_command_runtime(self, runtime: CommandRuntime) -> None:
        self._command_runtime = runtime
        runtime.configure_shortcut(
            enabled=self._palette_shortcut_enabled,
            shortcut_text=self._palette_shortcut_keybind,
        )
        self._palette_shortcut_enabled = runtime.shortcut_enabled
        self._palette_shortcut_keybind = runtime.shortcut_text

    def build_command_context(self) -> AppCommandContext:
        return AppCommandContext(
            open_settings_dialog=self.open_settings_dialog,
            close_settings_dialog=self.close_settings_dialog,
            is_settings_dialog_open=self.is_settings_dialog_open,
            minimize_window=self.showMinimized,
            close_app=self.close,
            expand_window=self.expand_window,
            shrink_window=self.shrink_window,
        )

    def open_settings_dialog(self) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            dialog = SettingsDialog(
                self._plugin_manager,
                parent=self,
                dark_mode_enabled=self._dark_mode_enabled,
                on_dark_mode_changed=self._on_dark_mode_changed,
                palette_shortcut_enabled=self._palette_shortcut_enabled,
                palette_shortcut_keybind=self._palette_shortcut_keybind,
                on_palette_shortcut_changed=self._on_palette_shortcut_changed,
            )
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.plugins_changed.connect(self._sync_background_from_plugins)
            dialog.finished.connect(self._on_settings_dialog_finished)
            self._settings_dialog = dialog

        mode = "dark" if self._dark_mode_enabled else "light"
        dialog.set_theme_mode(mode)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._state_streamer.record(
            "settings.opened",
            source="main_window",
            payload={},
        )

    def close_settings_dialog(self) -> bool:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return False
        dialog.close()
        return True

    def is_settings_dialog_open(self) -> bool:
        dialog = self._settings_dialog
        return dialog is not None and dialog.isVisible()

    def expand_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        if self.isFullScreen():
            self.showNormal()
        if not self.isMaximized():
            self.showMaximized()

    def shrink_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
            return
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
            return

        current_w = max(1, self.width())
        current_h = max(1, self.height())
        target_w = max(self.minimumWidth(), int(round(current_w * 0.9)))
        target_h = max(self.minimumHeight(), int(round(current_h * 0.9)))
        if target_w == current_w and target_h == current_h:
            return
        self.resize(target_w, target_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_foreground_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_foreground_layout)

    def closeEvent(self, event) -> None:
        dialog = self._settings_dialog
        if dialog is not None:
            dialog.close()
        self._plugin_bridge.shutdown()
        self._plugin_api.shutdown()
        self._plugin_manager.shutdown()
        self._state_streamer.record(
            "window.closed",
            source="main_window",
            payload={},
        )
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scene_widget and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        ):
            QTimer.singleShot(0, self._sync_foreground_layout)
        return super().eventFilter(watched, event)

    def _on_settings_dialog_finished(self, _result: int) -> None:
        dialog = self._settings_dialog
        self._settings_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        self._state_streamer.record(
            "settings.closed",
            source="main_window",
            payload={},
        )
        self._sync_background_from_plugins()

    def _on_dark_mode_changed(self, enabled: bool) -> None:
        self._dark_mode_enabled = bool(enabled)
        save_dark_mode(self._dark_mode_enabled)
        mode = "dark" if self._dark_mode_enabled else "light"
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, mode=mode)
        self.set_theme_mode(mode)
        self.setWindowIcon(QIcon(icon_asset_path("resize_handle.png", mode=mode)))
        if self._settings_dialog is not None:
            self._settings_dialog.set_theme_mode(mode)
        self._apply_settings_button_effect()
        self._sync_foreground_layout()
        self._state_streamer.record(
            "theme.changed",
            source="main_window",
            payload={"mode": mode},
        )

    def _on_palette_shortcut_changed(self, enabled: bool, keybind: str) -> None:
        self._palette_shortcut_enabled = bool(enabled)
        self._palette_shortcut_keybind = (
            keybind.strip() if isinstance(keybind, str) else ""
        ) or DEFAULT_PALETTE_SHORTCUT

        runtime = self._command_runtime
        if runtime is not None:
            runtime.configure_shortcut(
                enabled=self._palette_shortcut_enabled,
                shortcut_text=self._palette_shortcut_keybind,
            )
            self._palette_shortcut_enabled = runtime.shortcut_enabled
            self._palette_shortcut_keybind = runtime.shortcut_text

        save_palette_shortcut_settings(
            self._palette_shortcut_enabled,
            self._palette_shortcut_keybind,
        )
        self._state_streamer.record(
            "palette.shortcut_settings_changed",
            source="main_window",
            payload={
                "enabled": self._palette_shortcut_enabled,
                "keybind": self._palette_shortcut_keybind,
            },
        )

    def _sync_background_from_plugins(self) -> None:
        self._persist_active_plugins()
        background_url = self._plugin_manager.active_background_url()

        if self._settings_button is not None:
            active_count = len(self._plugin_manager.active_plugin_ids)
            self._settings_button.setToolTip(f"{active_count} plugin(s) active")

        if self._stack is None or self._fallback_widget is None:
            return

        if background_url and self._background_view is not None:
            self._stack.setCurrentWidget(self._background_view)
            self._sync_foreground_layout()
            self._schedule_background_url_load(background_url)
            QTimer.singleShot(0, self._restore_settings_dialog_z_order)
            return

        self._current_background_url = None
        self._stack.setCurrentWidget(self._fallback_widget)
        self._sync_foreground_layout()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_active_plugins(self) -> None:
        for plugin_id in load_active_plugin_ids(default=()):
            if self._plugin_manager.get_plugin(plugin_id) is None:
                continue
            try:
                self._plugin_manager.activate(plugin_id)
            except Exception:
                continue

    def _persist_active_plugins(self) -> None:
        save_active_plugin_ids(self._plugin_manager.active_plugin_ids)

    def _schedule_background_url_load(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        QTimer.singleShot(0, lambda url=background_url: self._load_background_url(url))

    def _load_background_url(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        if self._plugin_manager.active_background_url() != background_url:
            return
        self._background_view.setUrl(QUrl(background_url))
        self._current_background_url = background_url

    def _on_background_load_finished(self, _ok: bool) -> None:
        self._raise_foreground_widgets()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_settings_dialog_z_order(self) -> None:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return
        try:
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            return

    def _position_settings_button(self) -> None:
        if not self._settings_button or not self._scene_widget:
            return
        if self._scene_widget.width() <= 0 or self._scene_widget.height() <= 0:
            return
        margin = 16
        self._settings_button.adjustSize()
        x = margin
        y = max(margin, self._scene_widget.height() - self._settings_button.height() - margin)
        self._settings_button.move(x, y)
        if not self._settings_button.isVisible():
            self._settings_button.show()

    def _position_tracker_panels(self) -> None:
        if self._panel_host is None or self._scene_widget is None:
            return

        scene_width = self._scene_widget.width()
        scene_height = self._scene_widget.height()
        if scene_width <= 0 or scene_height <= 0:
            return

        horizontal_margin = 48
        vertical_margin = 60
        viewing_permit_form = (
            self._panel_stack is not None
            and self._permit_form_view is not None
            and self._panel_stack.currentWidget() is self._permit_form_view
        )
        if viewing_permit_form:
            desired_width = min(760, max(460, scene_width - 80))
            desired_height = min(520, max(320, scene_height - 90))
        else:
            desired_width = min(1080, max(360, scene_width - (horizontal_margin * 2)))
            desired_height = min(400, max(220, scene_height - (vertical_margin * 2)))
        content_width = min(desired_width, scene_width)
        content_height = min(desired_height, scene_height)

        x = max(0, int((scene_width - content_width) / 2))
        y = max(0, int((scene_height - content_height) / 2))
        self._panel_host.setGeometry(x, y, int(content_width), int(content_height))
        if not self._panel_host.isVisible():
            self._panel_host.show()

    def _raise_foreground_widgets(self) -> None:
        if self._panel_host is not None:
            self._panel_host.raise_()
        if self._settings_button is not None:
            self._settings_button.raise_()
        resize_handle = getattr(self, "_resize_handle", None)
        if resize_handle is not None:
            try:
                resize_handle.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
                resize_handle.raise_()
            except Exception:
                pass

    def _sync_foreground_layout(self) -> None:
        self._position_tracker_panels()
        self._position_settings_button()
        self._raise_foreground_widgets()

    def _apply_settings_button_effect(self) -> None:
        if self._settings_button is None:
            return
        shadow = self._settings_button_shadow
        if shadow is None:
            shadow = QGraphicsDropShadowEffect(self._settings_button)
            self._settings_button.setGraphicsEffect(shadow)
            self._settings_button_shadow = shadow

        if self._dark_mode_enabled:
            shadow.setBlurRadius(34.0)
            shadow.setOffset(0.0, 7.0)
            shadow.setColor(QColor(33, 109, 185, 138))
            return

        shadow.setBlurRadius(28.0)
        shadow.setOffset(0.0, 5.0)
        shadow.setColor(QColor(86, 150, 208, 112))


def run(argv: Sequence[str] | None = None) -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv or sys.argv))

    app.setApplicationName("erpermitsys")
    app.setOrganizationName("Bellboard")

    state_streamer = StateStreamer()
    dark_mode_enabled = load_dark_mode(default=False)
    palette_shortcut_enabled = load_palette_shortcut_enabled(default=True)
    palette_shortcut_keybind = load_palette_shortcut_keybind(
        default=DEFAULT_PALETTE_SHORTCUT
    )
    theme_mode = "dark" if dark_mode_enabled else "light"
    apply_app_theme(app, mode=theme_mode)
    state_streamer.record(
        "app.started",
        source="app.main",
        payload={"theme_mode": theme_mode},
    )

    window = ErPermitSysWindow(
        dark_mode_enabled=dark_mode_enabled,
        palette_shortcut_enabled=palette_shortcut_enabled,
        palette_shortcut_keybind=palette_shortcut_keybind,
        state_streamer=state_streamer,
    )
    command_runtime = CommandRuntime(
        app=app,
        context_provider=window.build_command_context,
        event_streamer=state_streamer,
        shortcut_enabled=palette_shortcut_enabled,
        shortcut_text=palette_shortcut_keybind,
        anchor_provider=lambda: window,
    )
    window.set_command_runtime(command_runtime)
    save_palette_shortcut_settings(
        command_runtime.shortcut_enabled,
        command_runtime.shortcut_text,
    )
    state_streamer.snapshot(
        "app.session",
        data={
            "window_title": window.windowTitle(),
            "theme_mode": theme_mode,
        },
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

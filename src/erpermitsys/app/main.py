from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
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
from erpermitsys.app.data_store import (
    BACKEND_LOCAL_JSON,
    BACKEND_SUPABASE,
    LocalJsonDataStore,
)
from erpermitsys.app.background_plugin_bridge import BackgroundPluginBridge
from erpermitsys.core import StateStreamer
from erpermitsys.plugins import PluginManager
from erpermitsys.plugins.api import PluginApiService
from erpermitsys.app.settings_store import (
    DEFAULT_PALETTE_SHORTCUT,
    load_active_plugin_ids,
    load_data_storage_backend,
    load_data_storage_folder,
    load_dark_mode,
    load_palette_shortcut_enabled,
    load_palette_shortcut_keybind,
    normalize_data_storage_folder,
    save_active_plugin_ids,
    save_data_storage_backend,
    save_data_storage_folder,
    save_dark_mode,
    save_palette_shortcut_settings,
)
from erpermitsys.app.tracker_models import ContactRecord, PermitRecord, TrackerDataBundle
from erpermitsys.ui.assets import icon_asset_path
from erpermitsys.ui.settings import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme
from erpermitsys.ui.window.frameless_window import FramelessWindow


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
        self._data_storage_backend = load_data_storage_backend(default=BACKEND_LOCAL_JSON)
        self._data_storage_folder = load_data_storage_folder()
        self._data_store = LocalJsonDataStore(self._data_storage_folder)

        self._clients: list[ContactRecord] = []
        self._contractors: list[ContactRecord] = []
        self._permits: list[PermitRecord] = []
        self._editing_client_index: int | None = None
        self._editing_contractor_index: int | None = None
        self._editing_permit_index: int | None = None

        self._stack: QStackedLayout | None = None
        self._fallback_widget: QFrame | None = None
        self._background_view: QWebEngineView | None = None
        self._background_web_channel: QWebChannel | None = None
        self._scene_widget: QWidget | None = None
        self._panel_host: QWidget | None = None
        self._panel_stack: QStackedLayout | None = None
        self._panel_home_view: QWidget | None = None
        self._permit_form_view: QWidget | None = None
        self._client_form_view: QWidget | None = None
        self._contractor_form_view: QWidget | None = None
        self._clients_list_widget: QListWidget | None = None
        self._contractors_list_widget: QListWidget | None = None
        self._permits_list_widget: QListWidget | None = None
        self._client_search_input: QLineEdit | None = None
        self._client_filter_combo: QComboBox | None = None
        self._client_result_label: QLabel | None = None
        self._contractor_search_input: QLineEdit | None = None
        self._contractor_filter_combo: QComboBox | None = None
        self._contractor_result_label: QLabel | None = None
        self._permit_search_input: QLineEdit | None = None
        self._permit_filter_combo: QComboBox | None = None
        self._permit_result_label: QLabel | None = None
        self._permit_parcel_input: QLineEdit | None = None
        self._permit_address_input: QLineEdit | None = None
        self._permit_request_date_input: QLineEdit | None = None
        self._permit_application_date_input: QLineEdit | None = None
        self._permit_completion_date_input: QLineEdit | None = None
        self._permit_client_combo: QComboBox | None = None
        self._permit_contractor_combo: QComboBox | None = None
        self._permit_form_title_label: QLabel | None = None
        self._permit_form_save_button: QPushButton | None = None
        self._permit_form_delete_button: QPushButton | None = None
        self._client_name_input: QLineEdit | None = None
        self._client_number_input: QLineEdit | None = None
        self._client_email_input: QLineEdit | None = None
        self._client_form_title_label: QLabel | None = None
        self._client_form_save_button: QPushButton | None = None
        self._client_form_delete_button: QPushButton | None = None
        self._contractor_name_input: QLineEdit | None = None
        self._contractor_number_input: QLineEdit | None = None
        self._contractor_email_input: QLineEdit | None = None
        self._contractor_form_title_label: QLabel | None = None
        self._contractor_form_save_button: QPushButton | None = None
        self._contractor_form_delete_button: QPushButton | None = None
        self._settings_button: QPushButton | None = None
        self._settings_button_shadow: QGraphicsDropShadowEffect | None = None

        storage_warning = self._initialize_data_store()
        self._build_body()
        self._plugin_manager.discover(auto_activate_background=False)
        self._restore_active_plugins()
        self._sync_background_from_plugins()
        if storage_warning:
            QTimer.singleShot(0, lambda message=storage_warning: self._show_data_storage_warning(message))
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
        add_client_button.clicked.connect(self._open_add_client_form)
        clients_layout.addWidget(add_client_button)
        client_search_input, client_filter_combo = self._build_panel_filters(
            clients_panel,
            placeholder="Search clients",
            filter_options=(
                ("All", "all"),
                ("Has Email", "email"),
                ("Has Number", "number"),
                ("Missing Contact", "missing_contact"),
            ),
            on_change=self._refresh_clients_list,
        )
        clients_layout.addLayout(self._make_filter_row(client_search_input, client_filter_combo))
        client_result_label = QLabel("0 results", clients_panel)
        client_result_label.setObjectName("TrackerPanelMeta")
        clients_layout.addWidget(client_result_label)
        self._client_search_input = client_search_input
        self._client_filter_combo = client_filter_combo
        self._client_result_label = client_result_label
        clients_list = QListWidget(clients_panel)
        clients_list.setObjectName("TrackerPanelList")
        clients_list.setWordWrap(True)
        clients_list.setSpacing(8)
        clients_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        clients_list.itemClicked.connect(self._on_client_item_selected)
        clients_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._clients_list_widget)
        )
        clients_layout.addWidget(clients_list, 1)
        self._clients_list_widget = clients_list
        panel_home_layout.addWidget(clients_panel, 1)

        permits_panel, permits_layout = self._create_tracker_panel(panel_home, "Permits")
        add_permit_button = QPushButton("Add Permit", permits_panel)
        add_permit_button.setObjectName("TrackerPanelActionButton")
        add_permit_button.clicked.connect(self._open_add_permit_form)
        permits_layout.addWidget(add_permit_button)
        permit_search_input, permit_filter_combo = self._build_panel_filters(
            permits_panel,
            placeholder="Search permits",
            filter_options=(
                ("All", "all"),
                ("Has Request Date", "requested"),
                ("Has Application Date", "applied"),
                ("Completed", "completed"),
                ("Open", "open"),
            ),
            on_change=self._refresh_permits_list,
        )
        permits_layout.addLayout(self._make_filter_row(permit_search_input, permit_filter_combo))
        permit_result_label = QLabel("0 results", permits_panel)
        permit_result_label.setObjectName("TrackerPanelMeta")
        permits_layout.addWidget(permit_result_label)
        self._permit_search_input = permit_search_input
        self._permit_filter_combo = permit_filter_combo
        self._permit_result_label = permit_result_label
        permits_list = QListWidget(permits_panel)
        permits_list.setObjectName("TrackerPanelList")
        permits_list.setWordWrap(True)
        permits_list.setSpacing(8)
        permits_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        permits_list.itemClicked.connect(self._on_permit_item_selected)
        permits_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._permits_list_widget)
        )
        permits_layout.addWidget(permits_list, 1)
        self._permits_list_widget = permits_list
        panel_home_layout.addWidget(permits_panel, 1)

        contractors_panel, contractors_layout = self._create_tracker_panel(panel_home, "Contractors")
        add_contractor_button = QPushButton("Add Contractor", contractors_panel)
        add_contractor_button.setObjectName("TrackerPanelActionButton")
        add_contractor_button.clicked.connect(self._open_add_contractor_form)
        contractors_layout.addWidget(add_contractor_button)
        contractor_search_input, contractor_filter_combo = self._build_panel_filters(
            contractors_panel,
            placeholder="Search contractors",
            filter_options=(
                ("All", "all"),
                ("Has Email", "email"),
                ("Has Number", "number"),
                ("Missing Contact", "missing_contact"),
            ),
            on_change=self._refresh_contractors_list,
        )
        contractors_layout.addLayout(self._make_filter_row(contractor_search_input, contractor_filter_combo))
        contractor_result_label = QLabel("0 results", contractors_panel)
        contractor_result_label.setObjectName("TrackerPanelMeta")
        contractors_layout.addWidget(contractor_result_label)
        self._contractor_search_input = contractor_search_input
        self._contractor_filter_combo = contractor_filter_combo
        self._contractor_result_label = contractor_result_label
        contractors_list = QListWidget(contractors_panel)
        contractors_list.setObjectName("TrackerPanelList")
        contractors_list.setWordWrap(True)
        contractors_list.setSpacing(8)
        contractors_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        contractors_list.itemClicked.connect(self._on_contractor_item_selected)
        contractors_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._contractors_list_widget)
        )
        contractors_layout.addWidget(contractors_list, 1)
        self._contractors_list_widget = contractors_list
        panel_home_layout.addWidget(contractors_panel, 1)

        panel_stack.addWidget(panel_home)

        (
            permit_form_view,
            permit_form_title_label,
            permit_form_save_button,
            permit_form_delete_button,
        ) = self._build_permit_form_view(panel_host)
        self._permit_form_view = permit_form_view
        self._permit_form_title_label = permit_form_title_label
        self._permit_form_save_button = permit_form_save_button
        self._permit_form_delete_button = permit_form_delete_button
        panel_stack.addWidget(permit_form_view)

        (
            client_form_view,
            client_name_input,
            client_number_input,
            client_email_input,
            client_form_title_label,
            client_form_save_button,
            client_form_delete_button,
        ) = self._build_contact_form_view(
            panel_host,
            title="Add Client",
            save_handler=self._save_client_from_form,
            delete_handler=self._delete_client_from_form,
            back_handler=self._close_to_home_view,
        )
        self._client_form_view = client_form_view
        self._client_name_input = client_name_input
        self._client_number_input = client_number_input
        self._client_email_input = client_email_input
        self._client_form_title_label = client_form_title_label
        self._client_form_save_button = client_form_save_button
        self._client_form_delete_button = client_form_delete_button
        panel_stack.addWidget(client_form_view)

        (
            contractor_form_view,
            contractor_name_input,
            contractor_number_input,
            contractor_email_input,
            contractor_form_title_label,
            contractor_form_save_button,
            contractor_form_delete_button,
        ) = (
            self._build_contact_form_view(
                panel_host,
                title="Add Contractor",
                save_handler=self._save_contractor_from_form,
                delete_handler=self._delete_contractor_from_form,
                back_handler=self._close_to_home_view,
            )
        )
        self._contractor_form_view = contractor_form_view
        self._contractor_name_input = contractor_name_input
        self._contractor_number_input = contractor_number_input
        self._contractor_email_input = contractor_email_input
        self._contractor_form_title_label = contractor_form_title_label
        self._contractor_form_save_button = contractor_form_save_button
        self._contractor_form_delete_button = contractor_form_delete_button
        panel_stack.addWidget(contractor_form_view)

        panel_stack.setCurrentWidget(panel_home)
        panel_host.hide()  # Avoid initial top-left flash before first geometry sync.

    def _build_panel_filters(
        self,
        parent: QWidget,
        *,
        placeholder: str,
        filter_options: Sequence[tuple[str, str]],
        on_change,
    ) -> tuple[QLineEdit, QComboBox]:
        search_input = QLineEdit(parent)
        search_input.setObjectName("TrackerPanelSearch")
        search_input.setPlaceholderText(placeholder)
        search_input.textChanged.connect(lambda _text: on_change())

        filter_combo = QComboBox(parent)
        filter_combo.setObjectName("TrackerPanelFilter")
        for label, value in filter_options:
            filter_combo.addItem(label, value)
        filter_combo.currentIndexChanged.connect(lambda _index: on_change())
        return search_input, filter_combo

    def _make_filter_row(self, search_input: QLineEdit, filter_combo: QComboBox) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(search_input, 1)
        row.addWidget(filter_combo, 0)
        return row

    def _build_permit_form_view(
        self, parent: QWidget
    ) -> tuple[QWidget, QLabel, QPushButton, QPushButton]:
        permit_form_view = QWidget(parent)
        permit_form_view.setObjectName("PermitFormView")
        permit_form_layout = QVBoxLayout(permit_form_view)
        permit_form_layout.setContentsMargins(28, 24, 28, 24)
        permit_form_layout.setSpacing(0)

        permit_form_card = QFrame(permit_form_view)
        permit_form_card.setObjectName("PermitFormCard")
        permit_form_card.setMinimumWidth(420)
        permit_form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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
        delete_button = QPushButton("Delete Permit", permit_form_card)
        delete_button.setObjectName("PermitFormDangerButton")
        delete_button.clicked.connect(self._delete_permit_from_form)
        permit_actions.addWidget(cancel_button)
        permit_actions.addWidget(delete_button)
        permit_actions.addWidget(save_button)
        permit_card_layout.addLayout(permit_actions)

        self._wire_enter_to_submit(
            permit_form_view,
            self._save_permit_from_form,
            (
                parcel_id_input,
                address_input,
                request_date_input,
                application_date_input,
                completion_date_input,
            ),
        )

        permit_form_layout.addWidget(permit_form_card, 1)
        return permit_form_view, permit_form_title, save_button, delete_button

    def _build_contact_form_view(
        self,
        parent: QWidget,
        *,
        title: str,
        save_handler,
        delete_handler,
        back_handler,
    ) -> tuple[QWidget, QLineEdit, QLineEdit, QLineEdit, QLabel, QPushButton, QPushButton]:
        contact_form_view = QWidget(parent)
        contact_form_view.setObjectName("PermitFormView")
        contact_form_layout = QVBoxLayout(contact_form_view)
        contact_form_layout.setContentsMargins(28, 24, 28, 24)
        contact_form_layout.setSpacing(0)

        contact_form_card = QFrame(contact_form_view)
        contact_form_card.setObjectName("PermitFormCard")
        contact_form_card.setMinimumWidth(420)
        contact_form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        contact_card_layout = QVBoxLayout(contact_form_card)
        contact_card_layout.setContentsMargins(24, 22, 24, 22)
        contact_card_layout.setSpacing(12)

        contact_form_title = QLabel(title, contact_form_card)
        contact_form_title.setObjectName("PermitFormTitle")
        contact_card_layout.addWidget(contact_form_title)

        contact_form_fields = QFormLayout()
        contact_form_fields.setContentsMargins(0, 0, 0, 0)
        contact_form_fields.setHorizontalSpacing(14)
        contact_form_fields.setVerticalSpacing(10)

        name_input = QLineEdit(contact_form_card)
        name_input.setObjectName("PermitFormInput")
        name_input.setPlaceholderText("Name")
        contact_form_fields.addRow("Name", name_input)

        number_input = QLineEdit(contact_form_card)
        number_input.setObjectName("PermitFormInput")
        number_input.setPlaceholderText("Number")
        contact_form_fields.addRow("Number", number_input)

        email_input = QLineEdit(contact_form_card)
        email_input.setObjectName("PermitFormInput")
        email_input.setPlaceholderText("Email")
        contact_form_fields.addRow("Email", email_input)

        contact_card_layout.addLayout(contact_form_fields)

        contact_actions = QHBoxLayout()
        contact_actions.setContentsMargins(0, 6, 0, 0)
        contact_actions.setSpacing(10)
        contact_actions.addStretch(1)
        cancel_button = QPushButton("Back", contact_form_card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(back_handler)
        save_button = QPushButton("Save", contact_form_card)
        save_button.setObjectName("PermitFormPrimaryButton")
        save_button.clicked.connect(save_handler)
        delete_button = QPushButton("Delete", contact_form_card)
        delete_button.setObjectName("PermitFormDangerButton")
        delete_button.clicked.connect(delete_handler)
        contact_actions.addWidget(cancel_button)
        contact_actions.addWidget(delete_button)
        contact_actions.addWidget(save_button)
        contact_card_layout.addLayout(contact_actions)

        self._wire_enter_to_submit(
            contact_form_view,
            save_handler,
            (name_input, number_input, email_input),
        )

        contact_form_layout.addWidget(contact_form_card, 1)
        return (
            contact_form_view,
            name_input,
            number_input,
            email_input,
            contact_form_title,
            save_button,
            delete_button,
        )

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

    def _wire_enter_to_submit(
        self,
        container: QWidget,
        submit_handler,
        text_inputs: Sequence[QLineEdit],
    ) -> None:
        for field in text_inputs:
            field.returnPressed.connect(submit_handler)
        return_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), container)
        return_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return_shortcut.activated.connect(submit_handler)
        enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Enter), container)
        enter_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        enter_shortcut.activated.connect(submit_handler)

    def _on_client_item_selected(self, _item: QListWidgetItem) -> None:
        index = self._extract_item_index(_item)
        if index < 0 or index >= len(self._clients):
            return
        self._open_edit_client_form(index)

    def _on_contractor_item_selected(self, _item: QListWidgetItem) -> None:
        index = self._extract_item_index(_item)
        if index < 0 or index >= len(self._contractors):
            return
        self._open_edit_contractor_form(index)

    def _on_permit_item_selected(self, _item: QListWidgetItem) -> None:
        index = self._extract_item_index(_item)
        if index < 0 or index >= len(self._permits):
            return
        self._open_edit_permit_form(index)

    def _open_add_client_form(self) -> None:
        self._editing_client_index = None
        if self._client_form_title_label is not None:
            self._client_form_title_label.setText("Add Client")
        if self._client_form_save_button is not None:
            self._client_form_save_button.setText("Save Client")
        if self._client_form_delete_button is not None:
            self._client_form_delete_button.setVisible(False)
            self._client_form_delete_button.setEnabled(False)
        self._reset_contact_form(
            self._client_name_input,
            self._client_number_input,
            self._client_email_input,
        )
        self._open_panel_view(self._client_form_view)
        if self._client_name_input is not None:
            self._client_name_input.setFocus()

    def _open_edit_client_form(self, index: int) -> None:
        if index < 0 or index >= len(self._clients):
            return
        record = self._clients[index]
        self._editing_client_index = index
        if self._client_form_title_label is not None:
            self._client_form_title_label.setText("Edit Client")
        if self._client_form_save_button is not None:
            self._client_form_save_button.setText("Update Client")
        if self._client_form_delete_button is not None:
            self._client_form_delete_button.setVisible(True)
            self._client_form_delete_button.setEnabled(True)
        if self._client_name_input is not None:
            self._client_name_input.setText(record.name)
        if self._client_number_input is not None:
            self._client_number_input.setText(record.number)
        if self._client_email_input is not None:
            self._client_email_input.setText(record.email)
        self._open_panel_view(self._client_form_view)
        if self._client_name_input is not None:
            self._client_name_input.setFocus()

    def _open_add_contractor_form(self) -> None:
        self._editing_contractor_index = None
        if self._contractor_form_title_label is not None:
            self._contractor_form_title_label.setText("Add Contractor")
        if self._contractor_form_save_button is not None:
            self._contractor_form_save_button.setText("Save Contractor")
        if self._contractor_form_delete_button is not None:
            self._contractor_form_delete_button.setVisible(False)
            self._contractor_form_delete_button.setEnabled(False)
        self._reset_contact_form(
            self._contractor_name_input,
            self._contractor_number_input,
            self._contractor_email_input,
        )
        self._open_panel_view(self._contractor_form_view)
        if self._contractor_name_input is not None:
            self._contractor_name_input.setFocus()

    def _open_edit_contractor_form(self, index: int) -> None:
        if index < 0 or index >= len(self._contractors):
            return
        record = self._contractors[index]
        self._editing_contractor_index = index
        if self._contractor_form_title_label is not None:
            self._contractor_form_title_label.setText("Edit Contractor")
        if self._contractor_form_save_button is not None:
            self._contractor_form_save_button.setText("Update Contractor")
        if self._contractor_form_delete_button is not None:
            self._contractor_form_delete_button.setVisible(True)
            self._contractor_form_delete_button.setEnabled(True)
        if self._contractor_name_input is not None:
            self._contractor_name_input.setText(record.name)
        if self._contractor_number_input is not None:
            self._contractor_number_input.setText(record.number)
        if self._contractor_email_input is not None:
            self._contractor_email_input.setText(record.email)
        self._open_panel_view(self._contractor_form_view)
        if self._contractor_name_input is not None:
            self._contractor_name_input.setFocus()

    def _save_client_from_form(self) -> None:
        if self._client_name_input is None or self._client_number_input is None or self._client_email_input is None:
            return

        name = self._client_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please provide a client name before saving.")
            return

        record = ContactRecord(
            name=name,
            number=self._client_number_input.text().strip(),
            email=self._client_email_input.text().strip(),
        )
        editing_index = self._editing_client_index
        if editing_index is None:
            self._clients.append(record)
        elif 0 <= editing_index < len(self._clients):
            old_name = self._clients[editing_index].name
            self._clients[editing_index] = record
            if old_name != record.name:
                for permit in self._permits:
                    if permit.client_name == old_name:
                        permit.client_name = record.name
                self._refresh_permits_list()
        else:
            self._clients.append(record)

        self._refresh_clients_list()
        self._refresh_party_selectors()
        self._persist_tracker_data()
        self._close_to_home_view()

    def _save_contractor_from_form(self) -> None:
        if (
            self._contractor_name_input is None
            or self._contractor_number_input is None
            or self._contractor_email_input is None
        ):
            return

        name = self._contractor_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please provide a contractor name before saving.")
            return

        record = ContactRecord(
            name=name,
            number=self._contractor_number_input.text().strip(),
            email=self._contractor_email_input.text().strip(),
        )
        editing_index = self._editing_contractor_index
        if editing_index is None:
            self._contractors.append(record)
        elif 0 <= editing_index < len(self._contractors):
            old_name = self._contractors[editing_index].name
            self._contractors[editing_index] = record
            if old_name != record.name:
                for permit in self._permits:
                    if permit.contractor_name == old_name:
                        permit.contractor_name = record.name
                self._refresh_permits_list()
        else:
            self._contractors.append(record)

        self._refresh_contractors_list()
        self._refresh_party_selectors()
        self._persist_tracker_data()
        self._close_to_home_view()

    def _delete_client_from_form(self) -> None:
        index = self._editing_client_index
        if index is None or index < 0 or index >= len(self._clients):
            return
        record = self._clients[index]
        result = QMessageBox.question(
            self,
            "Delete Client",
            f"Delete client '{record.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        deleted_name = record.name
        del self._clients[index]
        for permit in self._permits:
            if permit.client_name == deleted_name:
                permit.client_name = ""
        self._refresh_clients_list()
        self._refresh_permits_list()
        self._refresh_party_selectors()
        self._persist_tracker_data()
        self._close_to_home_view()

    def _delete_contractor_from_form(self) -> None:
        index = self._editing_contractor_index
        if index is None or index < 0 or index >= len(self._contractors):
            return
        record = self._contractors[index]
        result = QMessageBox.question(
            self,
            "Delete Contractor",
            f"Delete contractor '{record.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        deleted_name = record.name
        del self._contractors[index]
        for permit in self._permits:
            if permit.contractor_name == deleted_name:
                permit.contractor_name = ""
        self._refresh_contractors_list()
        self._refresh_permits_list()
        self._refresh_party_selectors()
        self._persist_tracker_data()
        self._close_to_home_view()

    def _open_add_permit_form(self) -> None:
        self._editing_permit_index = None
        if self._permit_form_title_label is not None:
            self._permit_form_title_label.setText("Add Permit")
        if self._permit_form_save_button is not None:
            self._permit_form_save_button.setText("Save Permit")
        if self._permit_form_delete_button is not None:
            self._permit_form_delete_button.setVisible(False)
            self._permit_form_delete_button.setEnabled(False)
        self._reset_permit_form()
        self._refresh_party_selectors()
        self._open_panel_view(self._permit_form_view)
        if self._permit_parcel_input is not None:
            self._permit_parcel_input.setFocus()

    def _open_edit_permit_form(self, index: int) -> None:
        if index < 0 or index >= len(self._permits):
            return
        permit = self._permits[index]
        self._editing_permit_index = index
        if self._permit_form_title_label is not None:
            self._permit_form_title_label.setText("Edit Permit")
        if self._permit_form_save_button is not None:
            self._permit_form_save_button.setText("Update Permit")
        if self._permit_form_delete_button is not None:
            self._permit_form_delete_button.setVisible(True)
            self._permit_form_delete_button.setEnabled(True)

        self._refresh_party_selectors()

        if self._permit_parcel_input is not None:
            self._permit_parcel_input.setText(permit.parcel_id)
        if self._permit_address_input is not None:
            self._permit_address_input.setText(permit.address)
        if self._permit_request_date_input is not None:
            self._permit_request_date_input.setText(permit.request_date)
        if self._permit_application_date_input is not None:
            self._permit_application_date_input.setText(permit.application_date)
        if self._permit_completion_date_input is not None:
            self._permit_completion_date_input.setText(permit.completion_date)
        self._set_combo_selected_value(self._permit_client_combo, permit.client_name)
        self._set_combo_selected_value(self._permit_contractor_combo, permit.contractor_name)

        self._open_panel_view(self._permit_form_view)
        if self._permit_parcel_input is not None:
            self._permit_parcel_input.setFocus()

    def _close_add_permit_form(self) -> None:
        self._close_to_home_view()

    def _open_panel_view(self, view: QWidget | None) -> None:
        if self._panel_stack is None or view is None:
            return
        self._panel_stack.setCurrentWidget(view)
        self._sync_foreground_layout()

    def _close_to_home_view(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        self._editing_client_index = None
        self._editing_contractor_index = None
        self._editing_permit_index = None
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._sync_foreground_layout()

    def _reset_contact_form(
        self,
        name_input: QLineEdit | None,
        number_input: QLineEdit | None,
        email_input: QLineEdit | None,
    ) -> None:
        for field in (name_input, number_input, email_input):
            if field is None:
                continue
            field.clear()

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

        permit = PermitRecord(
            parcel_id=parcel_id,
            address=address,
            request_date=request_date,
            application_date=application_date,
            completion_date=completion_date,
            client_name=client_name,
            contractor_name=contractor_name,
        )
        editing_index = self._editing_permit_index
        if editing_index is None:
            self._permits.append(permit)
        elif 0 <= editing_index < len(self._permits):
            self._permits[editing_index] = permit
        else:
            self._permits.append(permit)
        self._refresh_permits_list()
        self._persist_tracker_data()
        self._close_add_permit_form()

    def _delete_permit_from_form(self) -> None:
        index = self._editing_permit_index
        if index is None or index < 0 or index >= len(self._permits):
            return
        permit = self._permits[index]
        label = permit.parcel_id or permit.address or "selected permit"
        result = QMessageBox.question(
            self,
            "Delete Permit",
            f"Delete permit '{label}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        del self._permits[index]
        self._refresh_permits_list()
        self._persist_tracker_data()
        self._close_add_permit_form()

    def _refresh_clients_list(self) -> None:
        search_query = self._normalized_search_text(self._client_search_input)
        filter_mode = self._current_filter_value(self._client_filter_combo)
        rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        for index, client in enumerate(self._clients):
            if not self._contact_matches_filter(client, filter_mode):
                continue
            searchable = f"{client.name} {client.number} {client.email}".lower()
            if search_query and search_query not in searchable:
                continue
            number_text = client.number.strip() or "No number"
            email_text = client.email.strip() or "No email"
            rows.append(
                (
                    index,
                    (
                        ("client", client.name),
                        ("number", number_text),
                        ("email", email_text),
                    ),
                )
            )

        self._populate_list_widget(
            self._clients_list_widget,
            rows,
            "No clients match current filters.",
        )
        self._set_result_label(
            self._client_result_label,
            shown=len(rows),
            total=len(self._clients),
            noun="clients",
        )

    def _refresh_contractors_list(self) -> None:
        search_query = self._normalized_search_text(self._contractor_search_input)
        filter_mode = self._current_filter_value(self._contractor_filter_combo)
        rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        for index, contractor in enumerate(self._contractors):
            if not self._contact_matches_filter(contractor, filter_mode):
                continue
            searchable = f"{contractor.name} {contractor.number} {contractor.email}".lower()
            if search_query and search_query not in searchable:
                continue
            number_text = contractor.number.strip() or "No number"
            email_text = contractor.email.strip() or "No email"
            rows.append(
                (
                    index,
                    (
                        ("contractor", contractor.name),
                        ("number", number_text),
                        ("email", email_text),
                    ),
                )
            )

        self._populate_list_widget(
            self._contractors_list_widget,
            rows,
            "No contractors match current filters.",
        )
        self._set_result_label(
            self._contractor_result_label,
            shown=len(rows),
            total=len(self._contractors),
            noun="contractors",
        )

    def _refresh_permits_list(self) -> None:
        search_query = self._normalized_search_text(self._permit_search_input)
        filter_mode = self._current_filter_value(self._permit_filter_combo)
        rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        for index, permit in enumerate(self._permits):
            if not self._permit_matches_filter(permit, filter_mode):
                continue
            searchable = f"{permit.parcel_id} {permit.address} {permit.request_date}".lower()
            if search_query and search_query not in searchable:
                continue
            parcel_text = permit.parcel_id.strip() or "No parcel ID"
            address_text = permit.address.strip() or "No address"
            request_text = permit.request_date.strip() or "No request date"
            rows.append(
                (
                    index,
                    (
                        ("parcel", parcel_text),
                        ("address", address_text),
                        ("request", request_text),
                    ),
                )
            )

        self._populate_list_widget(
            self._permits_list_widget,
            rows,
            "No permits match current filters.",
        )
        self._set_result_label(
            self._permit_result_label,
            shown=len(rows),
            total=len(self._permits),
            noun="permits",
        )

    def _populate_list_widget(
        self,
        widget: QListWidget | None,
        rows: list[tuple[int, tuple[tuple[str, str], ...]]],
        empty_message: str,
    ) -> None:
        if widget is None:
            return
        widget.blockSignals(True)
        widget.clear()
        if not rows:
            empty_item = QListWidgetItem(empty_message)
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(empty_item)
            widget.blockSignals(False)
            return
        for source_index, fields in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, source_index)
            item.setData(Qt.ItemDataRole.DisplayRole, "")
            widget.addItem(item)
            card = self._build_tracker_list_card(fields, widget)
            item.setSizeHint(card.sizeHint())
            widget.setItemWidget(item, card)
        widget.blockSignals(False)
        self._refresh_list_selection_visuals(widget)

    def _build_tracker_list_card(
        self,
        fields: tuple[tuple[str, str], ...],
        parent: QWidget,
    ) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("TrackerListCard")
        card.setProperty("selected", "false")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 9, 10, 9)
        card_layout.setSpacing(6)

        for field_name, value in fields:
            row = QWidget(card)
            row.setObjectName("TrackerListFieldRow")
            row.setProperty("field", field_name)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(7)

            dot = QFrame(row)
            dot.setObjectName("TrackerListDot")
            dot.setProperty("field", field_name)
            dot.setFixedSize(8, 8)
            dot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

            field_label = QLabel(self._display_label_for_field(field_name), row)
            field_label.setObjectName("TrackerListFieldLabel")
            field_label.setProperty("field", field_name)

            value_label = QLabel(value, row)
            value_label.setObjectName("TrackerListFieldValue")
            value_label.setProperty("field", field_name)
            value_label.setWordWrap(True)

            row_layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(field_label, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(value_label, 1, Qt.AlignmentFlag.AlignVCenter)
            card_layout.addWidget(row)

        return card

    def _display_label_for_field(self, field_name: str) -> str:
        labels = {
            "client": "Client",
            "contractor": "Contractor",
            "email": "Email",
            "number": "Number",
            "address": "Address",
            "parcel": "Parcel ID",
            "request": "Request Date",
        }
        return labels.get(field_name, field_name.strip().title())

    def _refresh_list_selection_visuals(self, widget: QListWidget | None) -> None:
        if widget is None:
            return
        for index in range(widget.count()):
            item = widget.item(index)
            if item is None:
                continue
            card = widget.itemWidget(item)
            if card is None:
                continue
            selected_flag = "true" if item.isSelected() else "false"
            if card.property("selected") == selected_flag:
                continue
            card.setProperty("selected", selected_flag)
            style = card.style()
            style.unpolish(card)
            style.polish(card)
            card.update()

    def _extract_item_index(self, item: QListWidgetItem) -> int:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return -1
        try:
            return int(data)
        except Exception:
            return -1

    def _normalized_search_text(self, widget: QLineEdit | None) -> str:
        if widget is None:
            return ""
        return widget.text().strip().lower()

    def _current_filter_value(self, combo: QComboBox | None) -> str:
        if combo is None:
            return "all"
        value = str(combo.currentData() or "").strip().lower()
        return value or "all"

    def _contact_matches_filter(self, record: ContactRecord, filter_mode: str) -> bool:
        has_email = bool(record.email.strip())
        has_number = bool(record.number.strip())
        if filter_mode == "email":
            return has_email
        if filter_mode == "number":
            return has_number
        if filter_mode == "missing_contact":
            return not (has_email and has_number)
        return True

    def _permit_matches_filter(self, record: PermitRecord, filter_mode: str) -> bool:
        has_request = bool(record.request_date.strip())
        has_application = bool(record.application_date.strip())
        has_completion = bool(record.completion_date.strip())
        if filter_mode == "requested":
            return has_request
        if filter_mode == "applied":
            return has_application
        if filter_mode == "completed":
            return has_completion
        if filter_mode == "open":
            return not has_completion
        return True

    def _set_result_label(self, label: QLabel | None, *, shown: int, total: int, noun: str) -> None:
        if label is None:
            return
        if total <= 0:
            label.setText(f"0 {noun}")
            return
        label.setText(f"{shown} of {total} {noun}")

    def _set_combo_selected_value(self, combo: QComboBox | None, value: str) -> None:
        if combo is None:
            return
        target = value.strip()
        if not target:
            return
        index = combo.findData(target)
        if index < 0:
            index = combo.findText(target)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _refresh_party_selectors(self) -> None:
        self._refresh_contact_combo(
            self._permit_client_combo,
            self._clients,
            "No clients yet",
        )
        self._refresh_contact_combo(
            self._permit_contractor_combo,
            self._contractors,
            "No contractors yet",
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

        none_label = "None"
        if not rows and empty_message.strip():
            none_label = f"None ({empty_message.strip()})"
        combo.addItem(none_label, "")

        for row in rows:
            combo.addItem(row.name, row.name)
        combo.setEnabled(True)
        selected_index = combo.findData(selected_name) if selected_name else -1
        if selected_index >= 0:
            combo.setCurrentIndex(selected_index)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _initialize_data_store(self) -> str:
        warning_lines: list[str] = []

        configured_backend = str(self._data_storage_backend or "").strip().lower()
        if configured_backend == BACKEND_SUPABASE:
            warning_lines.append(
                "Supabase data storage is not enabled yet. Using local JSON storage."
            )
            self._data_storage_backend = BACKEND_LOCAL_JSON
        elif configured_backend != BACKEND_LOCAL_JSON:
            self._data_storage_backend = BACKEND_LOCAL_JSON
        if self._data_storage_backend != configured_backend:
            save_data_storage_backend(self._data_storage_backend)

        configured_folder = self._data_storage_folder
        self._data_storage_folder = normalize_data_storage_folder(configured_folder)
        if self._data_storage_folder != configured_folder:
            save_data_storage_folder(self._data_storage_folder)
        self._data_store = LocalJsonDataStore(self._data_storage_folder)

        load_result = self._data_store.load_bundle()
        self._apply_tracker_bundle(load_result.bundle, refresh_ui=False)
        if load_result.warning:
            warning_lines.append(load_result.warning)

        self._state_streamer.record(
            "data.loaded",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "source": load_result.source,
                "clients": len(self._clients),
                "contractors": len(self._contractors),
                "permits": len(self._permits),
            },
        )

        return "\n\n".join(line for line in warning_lines if line.strip())

    def _snapshot_tracker_bundle(self) -> TrackerDataBundle:
        return TrackerDataBundle(
            clients=[
                ContactRecord(
                    name=record.name,
                    number=record.number,
                    email=record.email,
                )
                for record in self._clients
            ],
            contractors=[
                ContactRecord(
                    name=record.name,
                    number=record.number,
                    email=record.email,
                )
                for record in self._contractors
            ],
            permits=[
                PermitRecord(
                    parcel_id=record.parcel_id,
                    address=record.address,
                    request_date=record.request_date,
                    application_date=record.application_date,
                    completion_date=record.completion_date,
                    client_name=record.client_name,
                    contractor_name=record.contractor_name,
                )
                for record in self._permits
            ],
        )

    def _apply_tracker_bundle(self, bundle: TrackerDataBundle, *, refresh_ui: bool) -> None:
        cloned_bundle = bundle.clone()
        self._clients = list(cloned_bundle.clients)
        self._contractors = list(cloned_bundle.contractors)
        self._permits = list(cloned_bundle.permits)

        if not refresh_ui:
            return

        self._refresh_clients_list()
        self._refresh_contractors_list()
        self._refresh_permits_list()
        self._refresh_party_selectors()

    def _persist_tracker_data(self, *, show_error_dialog: bool = True) -> bool:
        bundle = self._snapshot_tracker_bundle()
        try:
            self._data_store.save_bundle(bundle)
        except Exception as exc:
            if show_error_dialog:
                QMessageBox.warning(
                    self,
                    "Storage Error",
                    f"Could not save local data.\n\n{exc}",
                )
            self._state_streamer.record(
                "data.save_failed",
                source="main_window",
                payload={
                    "backend": self._data_storage_backend,
                    "folder": str(self._data_storage_folder),
                    "error": str(exc),
                },
            )
            return False

        self._state_streamer.record(
            "data.saved",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "path": str(self._data_store.storage_file_path),
                "clients": len(self._clients),
                "contractors": len(self._contractors),
                "permits": len(self._permits),
            },
        )
        return True

    def _on_data_storage_folder_changed(self, requested_folder: str) -> str:
        target_folder = normalize_data_storage_folder(requested_folder)
        if target_folder == self._data_storage_folder:
            return str(self._data_storage_folder)

        target_store = LocalJsonDataStore(target_folder)
        loaded_existing = False
        warning_message = ""

        try:
            if target_store.has_saved_data():
                load_result = target_store.load_bundle()
                if load_result.source == "empty" and load_result.warning:
                    raise RuntimeError(
                        "The selected folder contains unreadable data. "
                        "Choose a different folder or repair the data file first."
                    )
                target_bundle = load_result.bundle
                warning_message = load_result.warning
                loaded_existing = True
            else:
                target_bundle = TrackerDataBundle()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Storage Folder Error",
                f"Could not switch storage folder.\n\n{exc}",
            )
            self._state_streamer.record(
                "data.folder_switch_failed",
                source="main_window",
                payload={
                    "from": str(self._data_storage_folder),
                    "to": str(target_folder),
                    "error": str(exc),
                },
            )
            return str(self._data_storage_folder)

        self._data_store = target_store
        self._data_storage_backend = BACKEND_LOCAL_JSON
        self._data_storage_folder = target_store.data_root
        save_data_storage_backend(self._data_storage_backend)
        save_data_storage_folder(self._data_storage_folder)

        self._close_to_home_view()
        self._apply_tracker_bundle(target_bundle, refresh_ui=True)

        self._state_streamer.record(
            "data.folder_switched",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "loaded_existing": loaded_existing,
                "clients": len(self._clients),
                "contractors": len(self._contractors),
                "permits": len(self._permits),
            },
        )

        if warning_message:
            self._show_data_storage_warning(warning_message)
        elif loaded_existing:
            QMessageBox.information(
                self,
                "Storage Folder Updated",
                f"Loaded existing data from:\n{self._data_storage_folder}",
            )
        else:
            QMessageBox.information(
                self,
                "Storage Folder Updated",
                f"No saved data found in:\n{self._data_storage_folder}\n\n"
                "The panels were reset to empty for this folder.",
            )

        return str(self._data_storage_folder)

    def _show_data_storage_warning(self, message: str) -> None:
        text = message.strip()
        if not text:
            return
        QMessageBox.warning(self, "Data Storage Notice", text)

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
                data_storage_folder=str(self._data_storage_folder),
                on_data_storage_folder_changed=self._on_data_storage_folder_changed,
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
        self._persist_tracker_data(show_error_dialog=False)
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

        current_view = self._panel_stack.currentWidget() if self._panel_stack is not None else None
        home_view_active = self._panel_home_view is not None and current_view is self._panel_home_view
        if home_view_active:
            desired_width = max(380, int(scene_width * 0.94))
            desired_height = max(240, int(scene_height * 0.78))
        else:
            desired_width = max(460, int(scene_width * 0.84))
            desired_height = max(320, int(scene_height * 0.84))
        content_width = min(desired_width, max(1, scene_width - 12))
        content_height = min(desired_height, max(1, scene_height - 12))

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

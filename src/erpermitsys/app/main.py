from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
from erpermitsys.app.document_store import LocalPermitDocumentStore
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
from erpermitsys.app.tracker_models import (
    ContactRecord,
    CountyRecord,
    PermitDocumentFolder,
    PermitDocumentRecord,
    PermitRecord,
    TrackerDataBundle,
)
from erpermitsys.app.updater import (
    GitHubReleaseUpdater,
    GitHubUpdateCheckResult,
    GitHubUpdateInfo,
    can_self_update_windows,
    is_packaged_runtime,
    launch_windows_zip_updater,
)
from erpermitsys.version import APP_VERSION, GITHUB_RELEASE_ASSET_NAME, GITHUB_RELEASE_REPO
from erpermitsys.ui.assets import icon_asset_path
from erpermitsys.ui.settings import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme
from erpermitsys.ui.window.app_dialogs import AppConfirmDialog, AppMessageDialog, AppTextInputDialog
from erpermitsys.ui.window.frameless_window import FramelessWindow


_PERMIT_CATEGORIES: tuple[str, ...] = ("building", "remodeling", "demolition")
_PERMIT_CATEGORY_LABELS: dict[str, str] = {
    "building": "Building0.0.5",
    "remodeling": "Remodeling0.0.5",
    "demolition": "Demolition0.0.5",
}
_DEFAULT_DOCUMENT_FOLDER_NAME = "General"
_UPDATE_STARTUP_DELAY_MS = 1800


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
        self._document_store = LocalPermitDocumentStore(self._data_storage_folder)
        self._app_version = APP_VERSION
        self._auto_update_github_repo = GITHUB_RELEASE_REPO
        self._auto_update_asset_name = GITHUB_RELEASE_ASSET_NAME
        self._updater = GitHubReleaseUpdater(timeout_seconds=3.5)
        self._update_check_in_progress = False

        self._clients: list[ContactRecord] = []
        self._contractors: list[ContactRecord] = []
        self._counties: list[CountyRecord] = []
        self._permits: list[PermitRecord] = []
        self._editing_client_index: int | None = None
        self._editing_contractor_index: int | None = None
        self._editing_county_index: int | None = None
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
        self._county_form_view: QWidget | None = None
        self._permit_panel_stack: QStackedLayout | None = None
        self._permit_panel_list_view: QWidget | None = None
        self._permit_add_form_view: QWidget | None = None
        self._client_panel_stack: QStackedLayout | None = None
        self._client_panel_list_view: QWidget | None = None
        self._contractor_panel_stack: QStackedLayout | None = None
        self._contractor_panel_list_view: QWidget | None = None
        self._county_panel_stack: QStackedLayout | None = None
        self._county_panel_list_view: QWidget | None = None
        self._tracker_panel_frames: list[QFrame] = []
        self._hovered_tracker_panel: QFrame | None = None
        self._focused_tracker_panel: QFrame | None = None
        self._focus_tracking_connected = False
        self._permit_add_mode_active = False
        self._clients_list_widget: QListWidget | None = None
        self._contractors_list_widget: QListWidget | None = None
        self._counties_list_widget: QListWidget | None = None
        self._permits_list_widget: QListWidget | None = None
        self._client_search_input: QLineEdit | None = None
        self._client_filter_combo: QComboBox | None = None
        self._client_result_label: QLabel | None = None
        self._contractor_search_input: QLineEdit | None = None
        self._contractor_filter_combo: QComboBox | None = None
        self._contractor_result_label: QLabel | None = None
        self._county_search_input: QLineEdit | None = None
        self._county_filter_combo: QComboBox | None = None
        self._county_result_label: QLabel | None = None
        self._permit_search_input: QLineEdit | None = None
        self._permit_filter_combo: QComboBox | None = None
        self._permit_result_label: QLabel | None = None
        self._permits_panel_title_label: QLabel | None = None
        self._active_permit_category: str = _PERMIT_CATEGORIES[0]
        self._permit_category_buttons: dict[str, QPushButton] = {}
        self._permit_parcel_input: QLineEdit | None = None
        self._permit_address_input: QLineEdit | None = None
        self._permit_request_date_input: QLineEdit | None = None
        self._permit_application_date_input: QLineEdit | None = None
        self._permit_completion_date_input: QLineEdit | None = None
        self._permit_client_combo: QComboBox | None = None
        self._permit_contractor_combo: QComboBox | None = None
        self._permit_add_parcel_input: QLineEdit | None = None
        self._permit_add_address_input: QLineEdit | None = None
        self._permit_add_request_date_input: QLineEdit | None = None
        self._permit_add_application_date_input: QLineEdit | None = None
        self._permit_add_completion_date_input: QLineEdit | None = None
        self._permit_add_client_combo: QComboBox | None = None
        self._permit_add_contractor_combo: QComboBox | None = None
        self._permit_document_back_button: QPushButton | None = None
        self._permit_documents_section: QFrame | None = None
        self._permit_document_breadcrumb_widget: QWidget | None = None
        self._permit_document_breadcrumb_layout: QHBoxLayout | None = None
        self._permit_document_toggle_button: QPushButton | None = None
        self._permit_document_subfolder_panel: QWidget | None = None
        self._permit_document_subfolder_layout: QVBoxLayout | None = None
        self._permit_document_subfolder_animation: QPropertyAnimation | None = None
        self._permit_document_status_label: QLabel | None = None
        self._permit_document_list_widget: QListWidget | None = None
        self._permit_document_add_folder_button: QPushButton | None = None
        self._permit_document_remove_folder_button: QPushButton | None = None
        self._permit_document_add_file_button: QPushButton | None = None
        self._permit_document_open_folder_button: QPushButton | None = None
        self._permit_document_delete_file_button: QPushButton | None = None
        self._active_document_folder_id: str = ""
        self._document_subfolders_expanded: bool = False
        self._selected_permit_document_id: str = ""
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
        self._county_name_input: QLineEdit | None = None
        self._county_url_input: QLineEdit | None = None
        self._county_number_input: QLineEdit | None = None
        self._county_email_input: QLineEdit | None = None
        self._county_form_title_label: QLabel | None = None
        self._county_form_save_button: QPushButton | None = None
        self._county_form_delete_button: QPushButton | None = None
        self._settings_button: QPushButton | None = None
        self._settings_button_shadow: QGraphicsDropShadowEffect | None = None

        storage_warning = self._initialize_data_store()
        self._build_body()
        self._connect_focus_tracking()
        self._plugin_manager.discover(auto_activate_background=False)
        self._restore_active_plugins()
        self._sync_background_from_plugins()
        if storage_warning:
            QTimer.singleShot(0, lambda message=storage_warning: self._show_data_storage_warning(message))
        QTimer.singleShot(0, self._sync_foreground_layout)
        QTimer.singleShot(_UPDATE_STARTUP_DELAY_MS, self._check_for_updates_on_startup)
        self._state_streamer.record(
            "window.initialized",
            source="main_window",
            payload={
                "theme_mode": theme_mode,
                "has_webengine": bool(QWebEngineView is not None),
            },
        )

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

    def _prompt_text_dialog(
        self,
        title: str,
        label_text: str,
        *,
        text: str = "",
        placeholder: str = "",
        confirm_text: str = "Save",
        cancel_text: str = "Cancel",
    ) -> tuple[str, bool]:
        return AppTextInputDialog.get_text(
            parent=self,
            title=title,
            label_text=label_text,
            text=text,
            placeholder=placeholder,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            theme_mode=self._dialog_theme_mode(),
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
        self._refresh_counties_list()
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

        clients_panel, clients_layout, _clients_title_label = self._create_tracker_panel(
            panel_home,
            "Clients",
        )
        self._register_tracker_panel(clients_panel)
        clients_stack_host = QWidget(clients_panel)
        clients_stack = QStackedLayout(clients_stack_host)
        clients_stack.setContentsMargins(0, 0, 0, 0)
        clients_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._client_panel_stack = clients_stack
        clients_layout.addWidget(clients_stack_host, 1)

        clients_list_view = QWidget(clients_stack_host)
        clients_list_layout = QVBoxLayout(clients_list_view)
        clients_list_layout.setContentsMargins(0, 0, 0, 0)
        clients_list_layout.setSpacing(8)
        self._client_panel_list_view = clients_list_view

        add_client_button = QPushButton("Add Client", clients_list_view)
        add_client_button.setObjectName("TrackerPanelActionButton")
        add_client_button.clicked.connect(self._open_add_client_form)
        clients_list_layout.addWidget(add_client_button)
        client_search_input, client_filter_combo = self._build_panel_filters(
            clients_list_view,
            placeholder="Search clients",
            filter_options=(
                ("All", "all"),
                ("Has Email", "email"),
                ("Has Number", "number"),
                ("Missing Contact", "missing_contact"),
            ),
            on_change=self._refresh_clients_list,
        )
        clients_list_layout.addLayout(self._make_filter_row(client_search_input, client_filter_combo))
        client_result_label = QLabel("0 results", clients_list_view)
        client_result_label.setObjectName("TrackerPanelMeta")
        clients_list_layout.addWidget(client_result_label)
        self._client_search_input = client_search_input
        self._client_filter_combo = client_filter_combo
        self._client_result_label = client_result_label
        clients_list = QListWidget(clients_list_view)
        clients_list.setObjectName("TrackerPanelList")
        clients_list.setWordWrap(True)
        clients_list.setSpacing(8)
        clients_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        clients_list.itemClicked.connect(self._on_client_item_selected)
        clients_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._clients_list_widget)
        )
        clients_list_layout.addWidget(clients_list, 1)
        self._clients_list_widget = clients_list
        clients_stack.addWidget(clients_list_view)

        (
            client_form_view,
            client_name_input,
            client_number_input,
            client_email_input,
            client_form_title_label,
            client_form_save_button,
            client_form_delete_button,
        ) = self._build_contact_form_view(
            clients_stack_host,
            title="Add Client",
            save_handler=self._save_client_from_form,
            delete_handler=self._delete_client_from_form,
            back_handler=self._close_client_panel_form,
            inline=True,
        )
        self._client_form_view = client_form_view
        self._client_name_input = client_name_input
        self._client_number_input = client_number_input
        self._client_email_input = client_email_input
        self._client_form_title_label = client_form_title_label
        self._client_form_save_button = client_form_save_button
        self._client_form_delete_button = client_form_delete_button
        clients_stack.addWidget(client_form_view)
        clients_stack.setCurrentWidget(clients_list_view)
        panel_home_layout.addWidget(clients_panel, 1)

        permits_panel, permits_layout, permits_title_label = self._create_tracker_panel(
            panel_home,
            "Permits",
        )
        self._register_tracker_panel(permits_panel)
        self._permits_panel_title_label = permits_title_label
        permits_stack_host = QWidget(permits_panel)
        permits_stack = QStackedLayout(permits_stack_host)
        permits_stack.setContentsMargins(0, 0, 0, 0)
        permits_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._permit_panel_stack = permits_stack
        permits_layout.addWidget(permits_stack_host, 1)

        permits_list_view = QWidget(permits_stack_host)
        permits_list_layout = QVBoxLayout(permits_list_view)
        permits_list_layout.setContentsMargins(0, 0, 0, 0)
        permits_list_layout.setSpacing(8)
        self._permit_panel_list_view = permits_list_view

        add_permit_button = QPushButton("Add Permit", permits_list_view)
        add_permit_button.setObjectName("TrackerPanelActionButton")
        add_permit_button.clicked.connect(self._open_add_permit_form)
        permits_list_layout.addWidget(add_permit_button)
        permit_search_input, permit_filter_combo = self._build_panel_filters(
            permits_list_view,
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
        permits_list_layout.addLayout(self._make_filter_row(permit_search_input, permit_filter_combo))
        permit_result_label = QLabel("0 results", permits_list_view)
        permit_result_label.setObjectName("TrackerPanelMeta")
        permits_list_layout.addWidget(permit_result_label)
        self._permit_search_input = permit_search_input
        self._permit_filter_combo = permit_filter_combo
        self._permit_result_label = permit_result_label
        permits_list = QListWidget(permits_list_view)
        permits_list.setObjectName("TrackerPanelList")
        permits_list.setWordWrap(True)
        permits_list.setSpacing(8)
        permits_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        permits_list.itemClicked.connect(self._on_permit_item_selected)
        permits_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._permits_list_widget)
        )
        permits_list_layout.addWidget(permits_list, 1)
        self._permits_list_widget = permits_list
        permits_stack.addWidget(permits_list_view)

        (
            permit_add_form_view,
            permit_add_parcel_input,
            permit_add_address_input,
            permit_add_request_date_input,
            permit_add_application_date_input,
            permit_add_completion_date_input,
            permit_add_client_combo,
            permit_add_contractor_combo,
        ) = self._build_inline_add_permit_view(permits_stack_host)
        self._permit_add_form_view = permit_add_form_view
        self._permit_add_parcel_input = permit_add_parcel_input
        self._permit_add_address_input = permit_add_address_input
        self._permit_add_request_date_input = permit_add_request_date_input
        self._permit_add_application_date_input = permit_add_application_date_input
        self._permit_add_completion_date_input = permit_add_completion_date_input
        self._permit_add_client_combo = permit_add_client_combo
        self._permit_add_contractor_combo = permit_add_contractor_combo
        permits_stack.addWidget(permit_add_form_view)
        permits_stack.setCurrentWidget(permits_list_view)

        permit_category_picker = QWidget(permits_panel)
        permit_category_picker.setObjectName("PermitCategoryPicker")
        permit_category_picker_layout = QHBoxLayout(permit_category_picker)
        permit_category_picker_layout.setContentsMargins(0, 0, 0, 0)
        permit_category_picker_layout.setSpacing(8)
        for category in _PERMIT_CATEGORIES:
            category_label = _PERMIT_CATEGORY_LABELS.get(category, category.title())
            category_button = QPushButton(category_label, permit_category_picker)
            category_button.setObjectName("PermitCategoryPill")
            category_button.setCheckable(True)
            category_button.clicked.connect(
                lambda _checked, value=category: self._set_active_permit_category(value)
            )
            permit_category_picker_layout.addWidget(category_button, 1)
            self._permit_category_buttons[category] = category_button
        permits_layout.addWidget(permit_category_picker, 0)
        panel_home_layout.addWidget(permits_panel, 1)
        self._sync_permit_category_controls()

        right_column = QWidget(panel_home)
        right_column.setObjectName("TrackerRightColumn")
        right_column_layout = QVBoxLayout(right_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(14)

        contractors_panel, contractors_layout, _contractors_title_label = self._create_tracker_panel(
            right_column,
            "Contractors",
        )
        self._register_tracker_panel(contractors_panel)
        contractors_stack_host = QWidget(contractors_panel)
        contractors_stack = QStackedLayout(contractors_stack_host)
        contractors_stack.setContentsMargins(0, 0, 0, 0)
        contractors_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._contractor_panel_stack = contractors_stack
        contractors_layout.addWidget(contractors_stack_host, 1)

        contractors_list_view = QWidget(contractors_stack_host)
        contractors_list_layout = QVBoxLayout(contractors_list_view)
        contractors_list_layout.setContentsMargins(0, 0, 0, 0)
        contractors_list_layout.setSpacing(8)
        self._contractor_panel_list_view = contractors_list_view

        add_contractor_button = QPushButton("Add Contractor", contractors_list_view)
        add_contractor_button.setObjectName("TrackerPanelActionButton")
        add_contractor_button.clicked.connect(self._open_add_contractor_form)
        contractors_list_layout.addWidget(add_contractor_button)
        contractor_search_input, contractor_filter_combo = self._build_panel_filters(
            contractors_list_view,
            placeholder="Search contractors",
            filter_options=(
                ("All", "all"),
                ("Has Email", "email"),
                ("Has Number", "number"),
                ("Missing Contact", "missing_contact"),
            ),
            on_change=self._refresh_contractors_list,
        )
        contractors_list_layout.addLayout(self._make_filter_row(contractor_search_input, contractor_filter_combo))
        contractor_result_label = QLabel("0 results", contractors_list_view)
        contractor_result_label.setObjectName("TrackerPanelMeta")
        contractors_list_layout.addWidget(contractor_result_label)
        self._contractor_search_input = contractor_search_input
        self._contractor_filter_combo = contractor_filter_combo
        self._contractor_result_label = contractor_result_label
        contractors_list = QListWidget(contractors_list_view)
        contractors_list.setObjectName("TrackerPanelList")
        contractors_list.setWordWrap(True)
        contractors_list.setSpacing(8)
        contractors_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        contractors_list.itemClicked.connect(self._on_contractor_item_selected)
        contractors_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._contractors_list_widget)
        )
        contractors_list_layout.addWidget(contractors_list, 1)
        self._contractors_list_widget = contractors_list
        contractors_stack.addWidget(contractors_list_view)

        (
            contractor_form_view,
            contractor_name_input,
            contractor_number_input,
            contractor_email_input,
            contractor_form_title_label,
            contractor_form_save_button,
            contractor_form_delete_button,
        ) = self._build_contact_form_view(
            contractors_stack_host,
            title="Add Contractor",
            save_handler=self._save_contractor_from_form,
            delete_handler=self._delete_contractor_from_form,
            back_handler=self._close_contractor_panel_form,
            inline=True,
        )
        self._contractor_form_view = contractor_form_view
        self._contractor_name_input = contractor_name_input
        self._contractor_number_input = contractor_number_input
        self._contractor_email_input = contractor_email_input
        self._contractor_form_title_label = contractor_form_title_label
        self._contractor_form_save_button = contractor_form_save_button
        self._contractor_form_delete_button = contractor_form_delete_button
        contractors_stack.addWidget(contractor_form_view)
        contractors_stack.setCurrentWidget(contractors_list_view)
        right_column_layout.addWidget(contractors_panel, 1)

        counties_panel, counties_layout, _counties_title_label = self._create_tracker_panel(
            right_column,
            "Counties",
        )
        self._register_tracker_panel(counties_panel)
        counties_stack_host = QWidget(counties_panel)
        counties_stack = QStackedLayout(counties_stack_host)
        counties_stack.setContentsMargins(0, 0, 0, 0)
        counties_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._county_panel_stack = counties_stack
        counties_layout.addWidget(counties_stack_host, 1)

        counties_list_view = QWidget(counties_stack_host)
        counties_list_layout = QVBoxLayout(counties_list_view)
        counties_list_layout.setContentsMargins(0, 0, 0, 0)
        counties_list_layout.setSpacing(8)
        self._county_panel_list_view = counties_list_view

        add_county_button = QPushButton("Add County", counties_list_view)
        add_county_button.setObjectName("TrackerPanelActionButton")
        add_county_button.clicked.connect(self._open_add_county_form)
        counties_list_layout.addWidget(add_county_button)
        county_search_input, county_filter_combo = self._build_panel_filters(
            counties_list_view,
            placeholder="Search counties",
            filter_options=(
                ("All", "all"),
                ("Has URL", "url"),
                ("Has Email", "email"),
                ("Has Number", "number"),
                ("Missing Contact", "missing_contact"),
            ),
            on_change=self._refresh_counties_list,
        )
        counties_list_layout.addLayout(self._make_filter_row(county_search_input, county_filter_combo))
        county_result_label = QLabel("0 results", counties_list_view)
        county_result_label.setObjectName("TrackerPanelMeta")
        counties_list_layout.addWidget(county_result_label)
        self._county_search_input = county_search_input
        self._county_filter_combo = county_filter_combo
        self._county_result_label = county_result_label
        counties_list = QListWidget(counties_list_view)
        counties_list.setObjectName("TrackerPanelList")
        counties_list.setWordWrap(True)
        counties_list.setSpacing(8)
        counties_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        counties_list.itemClicked.connect(self._on_county_item_selected)
        counties_list.itemSelectionChanged.connect(
            lambda: self._refresh_list_selection_visuals(self._counties_list_widget)
        )
        counties_list_layout.addWidget(counties_list, 1)
        self._counties_list_widget = counties_list
        counties_stack.addWidget(counties_list_view)

        (
            county_form_view,
            county_name_input,
            county_url_input,
            county_number_input,
            county_email_input,
            county_form_title_label,
            county_form_save_button,
            county_form_delete_button,
        ) = self._build_county_form_view(
            counties_stack_host,
            title="Add County",
            save_handler=self._save_county_from_form,
            delete_handler=self._delete_county_from_form,
            back_handler=self._close_county_panel_form,
            inline=True,
        )
        self._county_form_view = county_form_view
        self._county_name_input = county_name_input
        self._county_url_input = county_url_input
        self._county_number_input = county_number_input
        self._county_email_input = county_email_input
        self._county_form_title_label = county_form_title_label
        self._county_form_save_button = county_form_save_button
        self._county_form_delete_button = county_form_delete_button
        counties_stack.addWidget(county_form_view)
        counties_stack.setCurrentWidget(counties_list_view)
        right_column_layout.addWidget(counties_panel, 1)

        panel_home_layout.addWidget(right_column, 1)

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

        documents_section = QFrame(permit_form_card)
        documents_section.setObjectName("PermitDocumentsSection")
        self._permit_documents_section = documents_section
        documents_layout = QVBoxLayout(documents_section)
        documents_layout.setContentsMargins(12, 10, 12, 10)
        documents_layout.setSpacing(8)

        documents_title = QLabel("Documents", documents_section)
        documents_title.setObjectName("PermitDocumentsTitle")
        documents_layout.addWidget(documents_title)

        documents_status_label = QLabel("", documents_section)
        documents_status_label.setObjectName("PermitDocumentStatus")
        documents_layout.addWidget(documents_status_label)
        self._permit_document_status_label = documents_status_label

        documents_folder_row = QHBoxLayout()
        documents_folder_row.setContentsMargins(0, 0, 0, 0)
        documents_folder_row.setSpacing(6)

        documents_back_button = QPushButton("Back", documents_section)
        documents_back_button.setObjectName("PermitFolderNavButton")
        documents_back_button.clicked.connect(self._open_parent_document_folder)
        documents_folder_row.addWidget(documents_back_button, 0)
        self._permit_document_back_button = documents_back_button

        documents_breadcrumb_widget = QWidget(documents_section)
        documents_breadcrumb_widget.setObjectName("PermitBreadcrumbStrip")
        documents_breadcrumb_layout = QHBoxLayout(documents_breadcrumb_widget)
        documents_breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        documents_breadcrumb_layout.setSpacing(4)
        documents_folder_row.addWidget(documents_breadcrumb_widget, 1)
        self._permit_document_breadcrumb_widget = documents_breadcrumb_widget
        self._permit_document_breadcrumb_layout = documents_breadcrumb_layout

        documents_toggle_button = QPushButton("\u25b8", documents_section)
        documents_toggle_button.setObjectName("PermitFolderToggleButton")
        documents_toggle_button.clicked.connect(self._toggle_document_subfolders)
        documents_toggle_button.setVisible(False)
        documents_toggle_button.setEnabled(False)
        documents_folder_row.addWidget(documents_toggle_button, 0)
        self._permit_document_toggle_button = documents_toggle_button

        documents_add_folder_button = QPushButton("Add Subfolder", documents_section)
        documents_add_folder_button.setObjectName("PermitFormSecondaryButton")
        documents_add_folder_button.clicked.connect(self._add_document_folder_from_form)
        documents_folder_row.addWidget(documents_add_folder_button, 0)
        self._permit_document_add_folder_button = documents_add_folder_button

        documents_remove_folder_button = QPushButton("Delete Folder", documents_section)
        documents_remove_folder_button.setObjectName("PermitFormDangerButton")
        documents_remove_folder_button.clicked.connect(self._delete_document_folder_from_form)
        documents_folder_row.addWidget(documents_remove_folder_button, 0)
        self._permit_document_remove_folder_button = documents_remove_folder_button

        documents_layout.addLayout(documents_folder_row)

        documents_subfolder_panel = QWidget(documents_section)
        documents_subfolder_panel.setObjectName("PermitFolderSubfolderPanel")
        documents_subfolder_panel_layout = QVBoxLayout(documents_subfolder_panel)
        documents_subfolder_panel_layout.setContentsMargins(0, 0, 0, 0)
        documents_subfolder_panel_layout.setSpacing(4)
        documents_subfolder_panel.setMaximumHeight(0)
        documents_subfolder_panel.setVisible(False)
        documents_layout.addWidget(documents_subfolder_panel, 0)
        self._permit_document_subfolder_panel = documents_subfolder_panel
        self._permit_document_subfolder_layout = documents_subfolder_panel_layout

        documents_subfolder_animation = QPropertyAnimation(documents_subfolder_panel, b"maximumHeight")
        documents_subfolder_animation.setDuration(180)
        documents_subfolder_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        documents_subfolder_animation.finished.connect(self._on_document_subfolder_animation_finished)
        self._permit_document_subfolder_animation = documents_subfolder_animation

        documents_list = QListWidget(documents_section)
        documents_list.setObjectName("PermitDocumentList")
        documents_list.setWordWrap(True)
        documents_list.setSpacing(6)
        documents_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        documents_list.itemClicked.connect(self._on_permit_document_item_selected)
        documents_list.itemDoubleClicked.connect(self._open_selected_permit_document)
        documents_list.itemSelectionChanged.connect(self._on_permit_document_selection_changed)
        documents_layout.addWidget(documents_list, 1)
        self._permit_document_list_widget = documents_list

        documents_actions = QHBoxLayout()
        documents_actions.setContentsMargins(0, 0, 0, 0)
        documents_actions.setSpacing(8)

        documents_add_file_button = QPushButton("Add Files", documents_section)
        documents_add_file_button.setObjectName("PermitFormSecondaryButton")
        documents_add_file_button.clicked.connect(self._add_documents_to_permit_from_form)
        documents_actions.addWidget(documents_add_file_button, 0)
        self._permit_document_add_file_button = documents_add_file_button

        documents_open_folder_button = QPushButton("Open Folder", documents_section)
        documents_open_folder_button.setObjectName("PermitFormSecondaryButton")
        documents_open_folder_button.clicked.connect(self._open_active_document_folder)
        documents_actions.addWidget(documents_open_folder_button, 0)
        self._permit_document_open_folder_button = documents_open_folder_button

        documents_delete_file_button = QPushButton("Delete File", documents_section)
        documents_delete_file_button.setObjectName("PermitFormDangerButton")
        documents_delete_file_button.clicked.connect(self._delete_selected_document_from_form)
        documents_actions.addWidget(documents_delete_file_button, 0)
        self._permit_document_delete_file_button = documents_delete_file_button

        documents_actions.addStretch(1)
        documents_layout.addLayout(documents_actions)
        permit_card_layout.addWidget(documents_section, 1)

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
        inline: bool = False,
    ) -> tuple[QWidget, QLineEdit, QLineEdit, QLineEdit, QLabel, QPushButton, QPushButton]:
        contact_form_view = QWidget(parent)
        contact_form_view.setObjectName("PermitFormView")
        contact_form_layout = QVBoxLayout(contact_form_view)
        if inline:
            contact_form_layout.setContentsMargins(0, 0, 0, 0)
        else:
            contact_form_layout.setContentsMargins(28, 24, 28, 24)
        contact_form_layout.setSpacing(0)

        contact_form_card = QFrame(contact_form_view)
        contact_form_card.setObjectName("PermitFormCard")
        contact_form_card.setMinimumWidth(0 if inline else 420)
        contact_form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        contact_card_layout = QVBoxLayout(contact_form_card)
        if inline:
            contact_card_layout.setContentsMargins(14, 12, 14, 12)
        else:
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
        number_input.setPlaceholderText("Number(s): comma, semicolon, or newline")
        contact_form_fields.addRow("Number", number_input)

        email_input = QLineEdit(contact_form_card)
        email_input.setObjectName("PermitFormInput")
        email_input.setPlaceholderText("Email(s): comma, semicolon, or newline")
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

    def _build_county_form_view(
        self,
        parent: QWidget,
        *,
        title: str,
        save_handler,
        delete_handler,
        back_handler,
        inline: bool = False,
    ) -> tuple[QWidget, QLineEdit, QLineEdit, QLineEdit, QLineEdit, QLabel, QPushButton, QPushButton]:
        county_form_view = QWidget(parent)
        county_form_view.setObjectName("PermitFormView")
        county_form_layout = QVBoxLayout(county_form_view)
        if inline:
            county_form_layout.setContentsMargins(0, 0, 0, 0)
        else:
            county_form_layout.setContentsMargins(28, 24, 28, 24)
        county_form_layout.setSpacing(0)

        county_form_card = QFrame(county_form_view)
        county_form_card.setObjectName("PermitFormCard")
        county_form_card.setMinimumWidth(0 if inline else 420)
        county_form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        county_card_layout = QVBoxLayout(county_form_card)
        if inline:
            county_card_layout.setContentsMargins(14, 12, 14, 12)
        else:
            county_card_layout.setContentsMargins(24, 22, 24, 22)
        county_card_layout.setSpacing(12)

        county_form_title = QLabel(title, county_form_card)
        county_form_title.setObjectName("PermitFormTitle")
        county_card_layout.addWidget(county_form_title)

        county_form_fields = QFormLayout()
        county_form_fields.setContentsMargins(0, 0, 0, 0)
        county_form_fields.setHorizontalSpacing(14)
        county_form_fields.setVerticalSpacing(10)

        name_input = QLineEdit(county_form_card)
        name_input.setObjectName("PermitFormInput")
        name_input.setPlaceholderText("County Name")
        county_form_fields.addRow("County Name", name_input)

        url_input = QLineEdit(county_form_card)
        url_input.setObjectName("PermitFormInput")
        url_input.setPlaceholderText("URL(s): comma, semicolon, or newline")
        county_form_fields.addRow("County Portal URL", url_input)

        number_input = QLineEdit(county_form_card)
        number_input.setObjectName("PermitFormInput")
        number_input.setPlaceholderText("Number(s): comma, semicolon, or newline")
        county_form_fields.addRow("County Number", number_input)

        email_input = QLineEdit(county_form_card)
        email_input.setObjectName("PermitFormInput")
        email_input.setPlaceholderText("Email(s): comma, semicolon, or newline")
        county_form_fields.addRow("County Email", email_input)

        county_card_layout.addLayout(county_form_fields)

        county_actions = QHBoxLayout()
        county_actions.setContentsMargins(0, 6, 0, 0)
        county_actions.setSpacing(10)
        county_actions.addStretch(1)
        cancel_button = QPushButton("Back", county_form_card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(back_handler)
        save_button = QPushButton("Save", county_form_card)
        save_button.setObjectName("PermitFormPrimaryButton")
        save_button.clicked.connect(save_handler)
        delete_button = QPushButton("Delete", county_form_card)
        delete_button.setObjectName("PermitFormDangerButton")
        delete_button.clicked.connect(delete_handler)
        county_actions.addWidget(cancel_button)
        county_actions.addWidget(delete_button)
        county_actions.addWidget(save_button)
        county_card_layout.addLayout(county_actions)

        self._wire_enter_to_submit(
            county_form_view,
            save_handler,
            (name_input, url_input, number_input, email_input),
        )

        county_form_layout.addWidget(county_form_card, 1)
        return (
            county_form_view,
            name_input,
            url_input,
            number_input,
            email_input,
            county_form_title,
            save_button,
            delete_button,
        )

    def _build_inline_add_permit_view(
        self,
        parent: QWidget,
    ) -> tuple[
        QWidget,
        QLineEdit,
        QLineEdit,
        QLineEdit,
        QLineEdit,
        QLineEdit,
        QComboBox,
        QComboBox,
    ]:
        add_form_view = QWidget(parent)
        add_form_view.setObjectName("PermitFormView")
        add_form_layout = QVBoxLayout(add_form_view)
        add_form_layout.setContentsMargins(0, 0, 0, 0)
        add_form_layout.setSpacing(0)

        add_form_card = QFrame(add_form_view)
        add_form_card.setObjectName("PermitFormCard")
        add_form_card.setMinimumWidth(0)
        add_form_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        add_card_layout = QVBoxLayout(add_form_card)
        add_card_layout.setContentsMargins(14, 12, 14, 12)
        add_card_layout.setSpacing(10)

        title_label = QLabel("Add Permit", add_form_card)
        title_label.setObjectName("PermitFormTitle")
        add_card_layout.addWidget(title_label)

        add_form_fields = QFormLayout()
        add_form_fields.setContentsMargins(0, 0, 0, 0)
        add_form_fields.setHorizontalSpacing(12)
        add_form_fields.setVerticalSpacing(8)

        parcel_id_input = QLineEdit(add_form_card)
        parcel_id_input.setObjectName("PermitFormInput")
        add_form_fields.addRow("Parcel ID", parcel_id_input)

        address_input = QLineEdit(add_form_card)
        address_input.setObjectName("PermitFormInput")
        add_form_fields.addRow("Address", address_input)

        request_date_input = QLineEdit(add_form_card)
        request_date_input.setObjectName("PermitFormInput")
        request_date_input.setPlaceholderText("YYYY-MM-DD")
        add_form_fields.addRow("Request Date", request_date_input)

        application_date_input = QLineEdit(add_form_card)
        application_date_input.setObjectName("PermitFormInput")
        application_date_input.setPlaceholderText("YYYY-MM-DD")
        add_form_fields.addRow("Application Date", application_date_input)

        completion_date_input = QLineEdit(add_form_card)
        completion_date_input.setObjectName("PermitFormInput")
        completion_date_input.setPlaceholderText("YYYY-MM-DD")
        add_form_fields.addRow("Completion Date", completion_date_input)

        client_combo = QComboBox(add_form_card)
        client_combo.setObjectName("PermitFormCombo")
        add_form_fields.addRow("Client", client_combo)

        contractor_combo = QComboBox(add_form_card)
        contractor_combo.setObjectName("PermitFormCombo")
        add_form_fields.addRow("Contractor", contractor_combo)

        add_card_layout.addLayout(add_form_fields)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(10)
        actions.addStretch(1)

        cancel_button = QPushButton("Back", add_form_card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(self._close_add_permit_inline_form)
        save_button = QPushButton("Save Permit", add_form_card)
        save_button.setObjectName("PermitFormPrimaryButton")
        save_button.clicked.connect(self._save_add_permit_inline_form)
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        add_card_layout.addLayout(actions)

        self._wire_enter_to_submit(
            add_form_view,
            self._save_add_permit_inline_form,
            (
                parcel_id_input,
                address_input,
                request_date_input,
                application_date_input,
                completion_date_input,
            ),
        )

        add_form_layout.addWidget(add_form_card, 1)
        return (
            add_form_view,
            parcel_id_input,
            address_input,
            request_date_input,
            application_date_input,
            completion_date_input,
            client_combo,
            contractor_combo,
        )

    def _create_tracker_panel(
        self,
        parent: QWidget,
        title: str,
    ) -> tuple[QFrame, QVBoxLayout, QLabel]:
        panel = QFrame(parent)
        panel.setObjectName("TrackerPanel")
        panel.setProperty("active", "false")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title_label = QLabel(title, panel)
        title_label.setObjectName("TrackerPanelTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        return panel, layout, title_label

    def _register_tracker_panel(self, panel: QFrame) -> None:
        if panel in self._tracker_panel_frames:
            return
        panel.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        panel.installEventFilter(self)
        self._tracker_panel_frames.append(panel)
        self._refresh_tracker_panel_highlight()

    def _connect_focus_tracking(self) -> None:
        if self._focus_tracking_connected:
            return
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.focusChanged.connect(self._on_app_focus_changed)
        except Exception:
            return
        self._focus_tracking_connected = True

    def _panel_for_widget(self, widget: QWidget | None) -> QFrame | None:
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self._tracker_panel_frames:
                return current
            current = current.parentWidget()
        return None

    def _on_app_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        self._focused_tracker_panel = self._panel_for_widget(new)
        self._refresh_tracker_panel_highlight()

    def _refresh_tracker_panel_highlight(self) -> None:
        for panel in self._tracker_panel_frames:
            active = panel is self._hovered_tracker_panel or panel is self._focused_tracker_panel
            target_flag = "true" if active else "false"
            if panel.property("active") == target_flag:
                continue
            panel.setProperty("active", target_flag)
            style = panel.style()
            style.unpolish(panel)
            style.polish(panel)
            panel.update()

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

    def _normalize_permit_category(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in _PERMIT_CATEGORIES:
            return normalized
        return _PERMIT_CATEGORIES[0]

    def _default_document_folders(self) -> list[PermitDocumentFolder]:
        return [
            PermitDocumentFolder(
                folder_id=uuid4().hex,
                name=_DEFAULT_DOCUMENT_FOLDER_NAME,
                parent_folder_id="",
            )
        ]

    def _ensure_permit_data_integrity(self, permit: PermitRecord) -> bool:
        changed = False
        if not permit.permit_id.strip():
            permit.permit_id = uuid4().hex
            changed = True

        normalized_category = self._normalize_permit_category(permit.category)
        if permit.category != normalized_category:
            permit.category = normalized_category
            changed = True

        folder_rows: list[PermitDocumentFolder] = []
        folder_ids: set[str] = set()
        for folder in permit.document_folders:
            folder_id = str(folder.folder_id).strip()
            folder_name = str(folder.name).strip()
            parent_folder_id = str(folder.parent_folder_id).strip()
            if not folder_id:
                folder_id = uuid4().hex
                changed = True
            if folder_id in folder_ids:
                folder_id = uuid4().hex
                changed = True
            if not folder_name:
                folder_name = "Folder"
                changed = True
            folder_rows.append(
                PermitDocumentFolder(
                    folder_id=folder_id,
                    name=folder_name,
                    parent_folder_id=parent_folder_id,
                )
            )
            folder_ids.add(folder_id)

        if not folder_rows:
            folder_rows = self._default_document_folders()
            folder_ids = {entry.folder_id for entry in folder_rows}
            changed = True

        root_folder = next((entry for entry in folder_rows if not entry.parent_folder_id.strip()), None)
        if root_folder is None:
            root_folder = folder_rows[0]
            root_folder.parent_folder_id = ""
            changed = True
        root_folder_id = root_folder.folder_id
        if not root_folder.name.strip():
            root_folder.name = _DEFAULT_DOCUMENT_FOLDER_NAME
            changed = True

        for folder in folder_rows:
            if folder.folder_id == root_folder_id:
                if folder.parent_folder_id:
                    folder.parent_folder_id = ""
                    changed = True
                continue
            parent_id = folder.parent_folder_id.strip()
            if not parent_id or parent_id == folder.folder_id or parent_id not in folder_ids:
                folder.parent_folder_id = root_folder_id
                changed = True

        # Guard against cycles in parent chains by reattaching cycle members to root.
        by_id: dict[str, PermitDocumentFolder] = {entry.folder_id: entry for entry in folder_rows}
        max_hops = max(1, len(folder_rows) + 1)
        for folder in folder_rows:
            if folder.folder_id == root_folder_id:
                continue
            visited: set[str] = set()
            current = folder
            hops = 0
            cycle_detected = False
            while hops < max_hops:
                hops += 1
                current_id = current.folder_id
                if current_id in visited:
                    cycle_detected = True
                    break
                visited.add(current_id)
                parent_id = current.parent_folder_id.strip()
                if not parent_id:
                    break
                parent = by_id.get(parent_id)
                if parent is None:
                    break
                current = parent
            if cycle_detected and folder.parent_folder_id != root_folder_id:
                folder.parent_folder_id = root_folder_id
                changed = True

        permit.document_folders = folder_rows

        normalized_documents: list[PermitDocumentRecord] = []
        for document in permit.documents:
            document_id = str(document.document_id).strip()
            if not document_id:
                document_id = uuid4().hex
                changed = True
            folder_id = str(document.folder_id).strip()
            if folder_id not in folder_ids:
                folder_id = permit.document_folders[0].folder_id
                changed = True
            original_name = str(document.original_name).strip()
            stored_name = str(document.stored_name).strip()
            relative_path = str(document.relative_path).strip()
            if not original_name and stored_name:
                original_name = stored_name
                changed = True
            if not stored_name and original_name:
                stored_name = original_name
                changed = True
            try:
                byte_size = max(0, int(document.byte_size))
            except Exception:
                byte_size = 0
            if byte_size != document.byte_size:
                changed = True
            normalized_documents.append(
                PermitDocumentRecord(
                    document_id=document_id,
                    folder_id=folder_id,
                    original_name=original_name,
                    stored_name=stored_name,
                    relative_path=relative_path,
                    imported_at=str(document.imported_at).strip(),
                    byte_size=byte_size,
                    sha256=str(document.sha256).strip(),
                )
            )
        permit.documents = normalized_documents

        return changed

    def _permit_category_label(self, category: str) -> str:
        normalized = self._normalize_permit_category(category)
        return _PERMIT_CATEGORY_LABELS.get(normalized, normalized.title())

    def _sync_permit_category_controls(self) -> None:
        active_category = self._normalize_permit_category(self._active_permit_category)
        self._active_permit_category = active_category
        for category, button in self._permit_category_buttons.items():
            checked = category == active_category
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)

        title_label = self._permits_panel_title_label
        if title_label is not None:
            title_label.setText(f"{self._permit_category_label(active_category)} Permits")
        self._set_permit_category_picker_enabled(not self._permit_add_mode_active)

    def _set_permit_category_picker_enabled(self, enabled: bool) -> None:
        for button in self._permit_category_buttons.values():
            button.setEnabled(enabled)

    def _set_active_permit_category(self, category: str) -> None:
        if self._permit_add_mode_active:
            self._sync_permit_category_controls()
            return
        normalized = self._normalize_permit_category(category)
        if normalized == self._active_permit_category:
            self._sync_permit_category_controls()
            return
        self._active_permit_category = normalized
        self._sync_permit_category_controls()
        self._refresh_permits_list()

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

    def _on_county_item_selected(self, _item: QListWidgetItem) -> None:
        index = self._extract_item_index(_item)
        if index < 0 or index >= len(self._counties):
            return
        self._open_edit_county_form(index)

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
        self._open_client_panel_form()
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
            self._client_number_input.setText(self._join_multi_value_input(record.numbers))
        if self._client_email_input is not None:
            self._client_email_input.setText(self._join_multi_value_input(record.emails))
        self._open_client_panel_form()
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
        self._open_contractor_panel_form()
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
            self._contractor_number_input.setText(self._join_multi_value_input(record.numbers))
        if self._contractor_email_input is not None:
            self._contractor_email_input.setText(self._join_multi_value_input(record.emails))
        self._open_contractor_panel_form()
        if self._contractor_name_input is not None:
            self._contractor_name_input.setFocus()

    def _open_add_county_form(self) -> None:
        self._editing_county_index = None
        if self._county_form_title_label is not None:
            self._county_form_title_label.setText("Add County")
        if self._county_form_save_button is not None:
            self._county_form_save_button.setText("Save County")
        if self._county_form_delete_button is not None:
            self._county_form_delete_button.setVisible(False)
            self._county_form_delete_button.setEnabled(False)
        self._reset_county_form()
        self._open_county_panel_form()
        if self._county_name_input is not None:
            self._county_name_input.setFocus()

    def _open_edit_county_form(self, index: int) -> None:
        if index < 0 or index >= len(self._counties):
            return
        record = self._counties[index]
        self._editing_county_index = index
        if self._county_form_title_label is not None:
            self._county_form_title_label.setText("Edit County")
        if self._county_form_save_button is not None:
            self._county_form_save_button.setText("Update County")
        if self._county_form_delete_button is not None:
            self._county_form_delete_button.setVisible(True)
            self._county_form_delete_button.setEnabled(True)
        if self._county_name_input is not None:
            self._county_name_input.setText(record.county_name)
        if self._county_url_input is not None:
            self._county_url_input.setText(self._join_multi_value_input(record.portal_urls))
        if self._county_number_input is not None:
            self._county_number_input.setText(self._join_multi_value_input(record.numbers))
        if self._county_email_input is not None:
            self._county_email_input.setText(self._join_multi_value_input(record.emails))
        self._open_county_panel_form()
        if self._county_name_input is not None:
            self._county_name_input.setFocus()

    def _save_client_from_form(self) -> None:
        if self._client_name_input is None or self._client_number_input is None or self._client_email_input is None:
            return

        name = self._client_name_input.text().strip()
        if not name:
            self._show_warning_dialog("Missing Name", "Please provide a client name before saving.")
            return

        record = ContactRecord(
            name=name,
            numbers=self._parse_multi_value_input(self._client_number_input.text()),
            emails=self._parse_multi_value_input(self._client_email_input.text()),
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
        self._close_client_panel_form()

    def _save_contractor_from_form(self) -> None:
        if (
            self._contractor_name_input is None
            or self._contractor_number_input is None
            or self._contractor_email_input is None
        ):
            return

        name = self._contractor_name_input.text().strip()
        if not name:
            self._show_warning_dialog("Missing Name", "Please provide a contractor name before saving.")
            return

        record = ContactRecord(
            name=name,
            numbers=self._parse_multi_value_input(self._contractor_number_input.text()),
            emails=self._parse_multi_value_input(self._contractor_email_input.text()),
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
        self._close_contractor_panel_form()

    def _save_county_from_form(self) -> None:
        if (
            self._county_name_input is None
            or self._county_url_input is None
            or self._county_number_input is None
            or self._county_email_input is None
        ):
            return

        county_name = self._county_name_input.text().strip()
        if not county_name:
            self._show_warning_dialog("Missing Name", "Please provide a county name before saving.")
            return

        record = CountyRecord(
            county_name=county_name,
            portal_urls=self._parse_multi_value_input(self._county_url_input.text()),
            numbers=self._parse_multi_value_input(self._county_number_input.text()),
            emails=self._parse_multi_value_input(self._county_email_input.text()),
        )
        editing_index = self._editing_county_index
        if editing_index is None:
            self._counties.append(record)
        elif 0 <= editing_index < len(self._counties):
            self._counties[editing_index] = record
        else:
            self._counties.append(record)

        self._refresh_counties_list()
        self._persist_tracker_data()
        self._close_county_panel_form()

    def _delete_client_from_form(self) -> None:
        index = self._editing_client_index
        if index is None or index < 0 or index >= len(self._clients):
            return
        record = self._clients[index]
        confirmed = self._confirm_dialog(
            "Delete Client",
            f"Delete client '{record.name}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
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
        self._close_client_panel_form()

    def _delete_contractor_from_form(self) -> None:
        index = self._editing_contractor_index
        if index is None or index < 0 or index >= len(self._contractors):
            return
        record = self._contractors[index]
        confirmed = self._confirm_dialog(
            "Delete Contractor",
            f"Delete contractor '{record.name}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
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
        self._close_contractor_panel_form()

    def _delete_county_from_form(self) -> None:
        index = self._editing_county_index
        if index is None or index < 0 or index >= len(self._counties):
            return
        record = self._counties[index]
        label = record.county_name or "selected county"
        confirmed = self._confirm_dialog(
            "Delete County",
            f"Delete county '{label}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        del self._counties[index]
        self._refresh_counties_list()
        self._persist_tracker_data()
        self._close_county_panel_form()

    def _open_add_permit_form(self) -> None:
        self._editing_permit_index = None
        self._permit_add_mode_active = True
        self._active_document_folder_id = ""
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
        self._sync_permit_category_controls()
        self._reset_inline_add_permit_form()
        self._refresh_party_selectors()
        self._set_permit_category_picker_enabled(False)
        self._set_inline_panel_view(self._permit_panel_stack, self._permit_add_form_view)
        if self._permit_add_parcel_input is not None:
            self._permit_add_parcel_input.setFocus()

    def _close_add_permit_inline_form(self) -> None:
        self._permit_add_mode_active = False
        self._set_permit_category_picker_enabled(True)
        self._reset_inline_add_permit_form()
        if self._permits_list_widget is not None:
            self._permits_list_widget.clearSelection()
        self._set_inline_panel_view(self._permit_panel_stack, self._permit_panel_list_view)

    def _save_add_permit_inline_form(self) -> None:
        if (
            self._permit_add_parcel_input is None
            or self._permit_add_address_input is None
            or self._permit_add_request_date_input is None
            or self._permit_add_application_date_input is None
            or self._permit_add_completion_date_input is None
            or self._permit_add_client_combo is None
            or self._permit_add_contractor_combo is None
        ):
            return

        parcel_id = self._permit_add_parcel_input.text().strip()
        address = self._permit_add_address_input.text().strip()
        request_date = self._permit_add_request_date_input.text().strip()
        application_date = self._permit_add_application_date_input.text().strip()
        completion_date = self._permit_add_completion_date_input.text().strip()
        client_name = str(self._permit_add_client_combo.currentData() or "").strip()
        contractor_name = str(self._permit_add_contractor_combo.currentData() or "").strip()

        if not parcel_id:
            self._show_warning_dialog("Missing Parcel ID", "Please provide a Parcel ID before saving.")
            return
        if not address:
            self._show_warning_dialog("Missing Address", "Please provide an Address before saving.")
            return

        permit = PermitRecord(
            permit_id=uuid4().hex,
            parcel_id=parcel_id,
            address=address,
            category=self._normalize_permit_category(self._active_permit_category),
            request_date=request_date,
            application_date=application_date,
            completion_date=completion_date,
            client_name=client_name,
            contractor_name=contractor_name,
            document_folders=[],
            documents=[],
        )
        self._ensure_permit_data_integrity(permit)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog(
                "Document Storage Error",
                f"Could not initialize permit folders.\n\n{exc}",
            )
            return

        self._permits.append(permit)
        self._refresh_permits_list()
        self._persist_tracker_data()
        self._close_add_permit_inline_form()

    def _open_edit_permit_form(self, index: int) -> None:
        if index < 0 or index >= len(self._permits):
            return
        self._permit_add_mode_active = False
        self._set_permit_category_picker_enabled(True)
        self._set_inline_panel_view(self._permit_panel_stack, self._permit_panel_list_view)
        permit = self._permits[index]
        migrated = self._ensure_permit_data_integrity(permit)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)
        self._active_permit_category = self._normalize_permit_category(permit.category)
        self._sync_permit_category_controls()
        self._editing_permit_index = index
        self._active_document_folder_id = ""
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
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
        self._refresh_permit_document_controls(edit_mode=True)

        self._open_panel_view(self._permit_form_view)
        if self._permit_parcel_input is not None:
            self._permit_parcel_input.setFocus()

    def _close_add_permit_form(self) -> None:
        self._permit_add_mode_active = False
        self._set_permit_category_picker_enabled(True)
        self._active_document_folder_id = ""
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
        self._close_to_home_view()

    def _editing_permit_record(self) -> PermitRecord | None:
        index = self._editing_permit_index
        if index is None or index < 0 or index >= len(self._permits):
            return None
        return self._permits[index]

    def _root_document_folder(self, permit: PermitRecord) -> PermitDocumentFolder | None:
        for folder in permit.document_folders:
            if not str(folder.parent_folder_id).strip():
                return folder
        return permit.document_folders[0] if permit.document_folders else None

    def _document_child_folders(
        self,
        permit: PermitRecord,
        parent_folder_id: str,
    ) -> list[PermitDocumentFolder]:
        parent_id = str(parent_folder_id or "").strip()
        rows = [
            folder
            for folder in permit.document_folders
            if str(folder.parent_folder_id).strip() == parent_id and folder.folder_id != parent_id
        ]
        rows.sort(key=lambda row: row.name.casefold())
        return rows

    def _document_folder_lineage(
        self,
        permit: PermitRecord,
        folder_id: str,
    ) -> list[PermitDocumentFolder]:
        target = str(folder_id or "").strip()
        if not target:
            return []
        by_id: dict[str, PermitDocumentFolder] = {
            folder.folder_id: folder
            for folder in permit.document_folders
            if folder.folder_id.strip()
        }
        current = by_id.get(target)
        if current is None:
            return []

        lineage: list[PermitDocumentFolder] = []
        visited: set[str] = set()
        max_hops = max(1, len(by_id) + 1)
        hops = 0
        while current is not None and hops < max_hops:
            hops += 1
            current_id = str(current.folder_id).strip()
            if not current_id or current_id in visited:
                break
            visited.add(current_id)
            lineage.append(current)
            parent_id = str(current.parent_folder_id).strip()
            if not parent_id:
                break
            current = by_id.get(parent_id)
        lineage.reverse()
        return lineage

    def _folder_has_children(self, permit: PermitRecord, folder_id: str) -> bool:
        target = str(folder_id or "").strip()
        if not target:
            return False
        for folder in permit.document_folders:
            if str(folder.parent_folder_id).strip() == target:
                return True
        return False

    def _folder_display_name(self, permit: PermitRecord, folder: PermitDocumentFolder) -> str:
        root = self._root_document_folder(permit)
        if root is not None and root.folder_id == folder.folder_id:
            return _DEFAULT_DOCUMENT_FOLDER_NAME
        return folder.name

    def _clear_layout_widgets(self, layout: QHBoxLayout | QVBoxLayout | None) -> None:
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _subfolder_panel_height_hint(self) -> int:
        panel = self._permit_document_subfolder_panel
        layout = self._permit_document_subfolder_layout
        if panel is None or layout is None:
            return 0

        layout.activate()
        hint = max(0, int(layout.sizeHint().height()))
        if hint > 0:
            return hint

        item_count = layout.count()
        if item_count <= 0:
            return 0

        content_height = 0
        for index in range(item_count):
            item = layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            content_height += max(0, widget.sizeHint().height())
        spacing = max(0, layout.spacing())
        margins = layout.contentsMargins()
        content_height += max(0, item_count - 1) * spacing
        content_height += max(0, margins.top()) + max(0, margins.bottom())
        if content_height <= 0:
            content_height = item_count * 30
        return max(0, int(content_height))

    def _on_document_subfolder_animation_finished(self) -> None:
        panel = self._permit_document_subfolder_panel
        if panel is None:
            return
        if panel.maximumHeight() <= 0:
            panel.setVisible(False)
            return
        panel.setVisible(True)

    def _animate_document_subfolder_panel(self, *, expand: bool) -> None:
        panel = self._permit_document_subfolder_panel
        animation = self._permit_document_subfolder_animation
        if panel is None or animation is None:
            return
        if panel.layout() is None:
            return

        animation.stop()
        panel.setVisible(True)
        panel.layout().activate()

        target_height = self._subfolder_panel_height_hint() if expand else 0
        start_height = max(0, int(panel.maximumHeight()))
        target_height = max(0, int(target_height))

        if start_height == target_height:
            panel.setMaximumHeight(target_height)
            panel.setVisible(target_height > 0)
            return

        animation.setStartValue(start_height)
        animation.setEndValue(target_height)
        animation.start()

    def _refresh_document_folder_navigation(self) -> None:
        permit = self._editing_permit_record()
        back_button = self._permit_document_back_button
        breadcrumb_widget = self._permit_document_breadcrumb_widget
        breadcrumb_layout = self._permit_document_breadcrumb_layout
        toggle_button = self._permit_document_toggle_button
        panel = self._permit_document_subfolder_panel
        panel_layout = self._permit_document_subfolder_layout

        self._clear_layout_widgets(panel_layout)
        self._clear_layout_widgets(breadcrumb_layout)

        if permit is None:
            if back_button is not None:
                back_button.setEnabled(False)
            if breadcrumb_layout is not None:
                placeholder_button = QPushButton(_DEFAULT_DOCUMENT_FOLDER_NAME, breadcrumb_widget)
                placeholder_button.setObjectName("PermitBreadcrumbButton")
                placeholder_button.setProperty("current", "true")
                placeholder_button.setEnabled(False)
                breadcrumb_layout.addWidget(placeholder_button)
            if toggle_button is not None:
                toggle_button.setVisible(False)
                toggle_button.setEnabled(False)
            if panel is not None:
                panel.setMaximumHeight(0)
                panel.setVisible(False)
            return

        current_folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if current_folder is None:
            current_folder = self._root_document_folder(permit)
        if current_folder is None:
            return
        self._active_document_folder_id = current_folder.folder_id

        child_folders = self._document_child_folders(permit, current_folder.folder_id)
        has_children = bool(child_folders)
        if not has_children:
            self._document_subfolders_expanded = False

        if breadcrumb_layout is not None:
            lineage = self._document_folder_lineage(permit, current_folder.folder_id)
            if not lineage:
                lineage = [current_folder]
            for index, lineage_folder in enumerate(lineage):
                is_current = index == len(lineage) - 1
                crumb_button = QPushButton(
                    self._folder_display_name(permit, lineage_folder),
                    breadcrumb_widget,
                )
                crumb_button.setObjectName("PermitBreadcrumbButton")
                crumb_button.setProperty("current", "true" if is_current else "false")
                if is_current:
                    crumb_button.clicked.connect(self._toggle_document_subfolders)
                else:
                    crumb_button.clicked.connect(
                        lambda _checked=False, value=lineage_folder.folder_id: self._set_document_folder_selection(
                            value
                        )
                    )
                breadcrumb_layout.addWidget(crumb_button)
                if is_current and has_children:
                    crumb_toggle = QPushButton(
                        "\u25be" if self._document_subfolders_expanded else "\u25b8",
                        breadcrumb_widget,
                    )
                    crumb_toggle.setObjectName("PermitBreadcrumbToggleButton")
                    crumb_toggle.clicked.connect(self._toggle_document_subfolders)
                    breadcrumb_layout.addWidget(crumb_toggle)
                if not is_current:
                    crumb_separator = QLabel("\u2192", breadcrumb_widget)
                    crumb_separator.setObjectName("PermitBreadcrumbSeparator")
                    breadcrumb_layout.addWidget(crumb_separator)
            breadcrumb_layout.addStretch(1)

        if back_button is not None:
            at_root = not str(current_folder.parent_folder_id).strip()
            back_button.setEnabled(not at_root)

        if toggle_button is not None:
            toggle_button.setVisible(False)
            toggle_button.setEnabled(False)

        if panel is None or panel_layout is None:
            return
        if not has_children:
            panel.setMaximumHeight(0)
            panel.setVisible(False)
            return

        for child_folder in child_folders:
            child_button = QPushButton(
                self._folder_display_name(permit, child_folder),
                panel,
            )
            child_button.setObjectName("PermitSubfolderButton")
            if self._folder_has_children(permit, child_folder.folder_id):
                child_button.setText(f"{self._folder_display_name(permit, child_folder)}  \u203a")
            child_button.clicked.connect(
                lambda _checked=False, value=child_folder.folder_id: self._enter_document_subfolder(value)
            )
            panel_layout.addWidget(child_button)

        self._animate_document_subfolder_panel(expand=self._document_subfolders_expanded)

    def _refresh_permit_document_controls(self, *, edit_mode: bool) -> None:
        documents_widget = self._permit_document_list_widget
        status_label = self._permit_document_status_label
        documents_section = self._permit_documents_section
        controls: tuple[QWidget | None, ...] = (
            self._permit_document_back_button,
            self._permit_document_breadcrumb_widget,
            self._permit_document_toggle_button,
            self._permit_document_add_folder_button,
            self._permit_document_remove_folder_button,
            self._permit_document_add_file_button,
            self._permit_document_open_folder_button,
            self._permit_document_delete_file_button,
            documents_widget,
        )
        if documents_widget is not None:
            documents_widget.blockSignals(True)
            documents_widget.clear()
            documents_widget.blockSignals(False)
        self._selected_permit_document_id = ""

        permit = self._editing_permit_record() if edit_mode else None
        if permit is None:
            if documents_section is not None:
                documents_section.setVisible(False)
            for control in controls:
                if control is not None:
                    control.setEnabled(False)
            if status_label is not None:
                status_label.setText("Save the permit first, then reopen it to manage documents.")
            if documents_widget is not None:
                empty_item = QListWidgetItem("No documents available.")
                empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                documents_widget.addItem(empty_item)
            self._active_document_folder_id = ""
            self._document_subfolders_expanded = False
            self._refresh_document_folder_navigation()
            return

        if documents_section is not None and not documents_section.isVisible():
            documents_section.setVisible(True)
        data_changed = self._ensure_permit_data_integrity(permit)
        if data_changed:
            self._persist_tracker_data(show_error_dialog=False)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            if status_label is not None:
                status_label.setText(f"Document storage error: {exc}")
        if not self._active_document_folder_id:
            root_folder = self._root_document_folder(permit)
            self._active_document_folder_id = root_folder.folder_id if root_folder is not None else ""
        elif self._document_folder_by_id(permit, self._active_document_folder_id) is None:
            root_folder = self._root_document_folder(permit)
            self._active_document_folder_id = root_folder.folder_id if root_folder is not None else ""

        for control in controls:
            if control is not None:
                control.setEnabled(True)
        self._refresh_document_folder_navigation()
        self._refresh_permit_document_list()

    def _refresh_permit_document_list(self) -> None:
        permit = self._editing_permit_record()
        widget = self._permit_document_list_widget
        status_label = self._permit_document_status_label
        if permit is None or widget is None:
            return

        selected_folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if selected_folder is None:
            selected_folder = self._root_document_folder(permit)
            if selected_folder is not None:
                self._active_document_folder_id = selected_folder.folder_id
        if selected_folder is None:
            widget.clear()
            if status_label is not None:
                status_label.setText("No folders configured for this permit.")
            return

        documents = [doc for doc in permit.documents if doc.folder_id == selected_folder.folder_id]
        documents.sort(key=lambda row: (row.imported_at, row.original_name, row.document_id), reverse=True)
        selected_document_id = self._selected_permit_document_id

        widget.blockSignals(True)
        widget.clear()
        if not documents:
            self._selected_permit_document_id = ""
            empty_item = QListWidgetItem("No documents in this folder.")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(empty_item)
        else:
            for document in documents:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, document.document_id)
                item.setData(Qt.ItemDataRole.DisplayRole, "")
                widget.addItem(item)
                display_name = document.original_name or document.stored_name or "Unnamed document"
                fields = (
                    ("document", display_name),
                    ("folder", self._folder_display_name(permit, selected_folder)),
                    ("size", self._format_byte_size(document.byte_size)),
                    ("added", self._format_imported_value(document.imported_at)),
                )
                card = self._build_tracker_list_card(fields, widget)
                item.setSizeHint(card.sizeHint())
                widget.setItemWidget(item, card)
                if selected_document_id and selected_document_id == document.document_id:
                    item.setSelected(True)
                    self._selected_permit_document_id = document.document_id
        if documents and self._selected_permit_document_id not in {
            document.document_id for document in documents
        }:
            self._selected_permit_document_id = ""
        widget.blockSignals(False)
        self._refresh_list_selection_visuals(widget)

        if status_label is not None:
            folder_label = self._folder_display_name(permit, selected_folder)
            status_label.setText(
                f"{len(documents)} document(s) in '{folder_label}' "
                f"({len(permit.documents)} total)"
            )

        if self._permit_document_delete_file_button is not None:
            self._permit_document_delete_file_button.setEnabled(bool(self._selected_permit_document_id))

    def _document_folder_by_id(
        self,
        permit: PermitRecord,
        folder_id: str,
    ) -> PermitDocumentFolder | None:
        target = str(folder_id or "").strip()
        if not target:
            return None
        for folder in permit.document_folders:
            if folder.folder_id == target:
                return folder
        return None

    def _document_record_by_id(
        self,
        permit: PermitRecord,
        document_id: str,
    ) -> PermitDocumentRecord | None:
        target = str(document_id or "").strip()
        if not target:
            return None
        for document in permit.documents:
            if document.document_id == target:
                return document
        return None

    def _set_document_folder_selection(self, folder_id: str) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        target = self._document_folder_by_id(permit, folder_id)
        if target is None:
            return
        self._active_document_folder_id = target.folder_id
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
        self._refresh_document_folder_navigation()
        self._refresh_permit_document_list()

    def _open_parent_document_folder(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        current = self._document_folder_by_id(permit, self._active_document_folder_id)
        if current is None or not current.parent_folder_id.strip():
            return
        self._active_document_folder_id = current.parent_folder_id.strip()
        self._document_subfolders_expanded = True
        self._selected_permit_document_id = ""
        self._refresh_document_folder_navigation()
        self._refresh_permit_document_list()

    def _toggle_document_subfolders(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        current = self._document_folder_by_id(permit, self._active_document_folder_id)
        if current is None:
            return
        if not self._folder_has_children(permit, current.folder_id):
            return
        self._document_subfolders_expanded = not self._document_subfolders_expanded
        self._refresh_document_folder_navigation()

    def _enter_document_subfolder(self, folder_id: str) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        target = self._document_folder_by_id(permit, folder_id)
        if target is None:
            return
        self._active_document_folder_id = target.folder_id
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
        self._refresh_document_folder_navigation()
        self._refresh_permit_document_list()

    def _on_permit_document_item_selected(self, item: QListWidgetItem) -> None:
        self._selected_permit_document_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if self._permit_document_delete_file_button is not None:
            self._permit_document_delete_file_button.setEnabled(bool(self._selected_permit_document_id))

    def _on_permit_document_selection_changed(self) -> None:
        widget = self._permit_document_list_widget
        if widget is None:
            self._selected_permit_document_id = ""
        else:
            current_item = widget.currentItem()
            self._selected_permit_document_id = (
                str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if current_item is not None
                else ""
            )
        if self._permit_document_delete_file_button is not None:
            self._permit_document_delete_file_button.setEnabled(bool(self._selected_permit_document_id))
        self._refresh_list_selection_visuals(widget)

    def _open_selected_permit_document(self, _item: QListWidgetItem | None = None) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        if _item is not None:
            self._selected_permit_document_id = str(
                _item.data(Qt.ItemDataRole.UserRole) or ""
            ).strip()
        document = self._document_record_by_id(permit, self._selected_permit_document_id)
        if document is None:
            return
        file_path = self._document_store.resolve_document_path(document.relative_path)
        if file_path is None or not file_path.exists():
            self._show_warning_dialog(
                "Document Missing",
                "The selected document file was not found in local storage.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))

    def _add_document_folder_from_form(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            self._show_info_dialog(
                "Save Permit First",
                "Save this permit, then reopen it to add document folders.",
            )
            return
        folder_name, accepted = self._prompt_text_dialog(
            "Add Document Folder",
            "Folder name:",
            confirm_text="Create",
        )
        if not accepted:
            return
        normalized_name = folder_name.strip()
        if not normalized_name:
            self._show_warning_dialog("Missing Name", "Please provide a folder name.")
            return

        parent_folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if parent_folder is None:
            parent_folder = self._root_document_folder(permit)
        if parent_folder is None:
            self._show_warning_dialog("Folder Error", "Could not resolve the active folder.")
            return
        for sibling in self._document_child_folders(permit, parent_folder.folder_id):
            if sibling.name.casefold() == normalized_name.casefold():
                self._set_document_folder_selection(sibling.folder_id)
                self._show_info_dialog(
                    "Folder Exists",
                    "A sibling folder with that name already exists.",
                )
                return

        new_folder = PermitDocumentFolder(
            folder_id=uuid4().hex,
            name=normalized_name,
            parent_folder_id=parent_folder.folder_id,
        )
        permit.document_folders.append(new_folder)
        self._ensure_permit_data_integrity(permit)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog(
                "Folder Error",
                f"Could not create the folder in storage.\n\n{exc}",
            )
            permit.document_folders = [
                folder for folder in permit.document_folders if folder.folder_id != new_folder.folder_id
            ]
            return

        self._persist_tracker_data()
        self._refresh_permit_document_controls(edit_mode=True)
        self._set_document_folder_selection(new_folder.folder_id)
        self._refresh_permit_document_list()

    def _document_folder_descendants(
        self,
        permit: PermitRecord,
        root_folder_id: str,
    ) -> set[str]:
        target = str(root_folder_id or "").strip()
        if not target:
            return set()
        by_parent: dict[str, list[str]] = {}
        for folder in permit.document_folders:
            parent_id = str(folder.parent_folder_id).strip()
            by_parent.setdefault(parent_id, []).append(folder.folder_id)
        descendants: set[str] = set()
        stack = [target]
        while stack:
            current = stack.pop()
            if current in descendants:
                continue
            descendants.add(current)
            for child_id in by_parent.get(current, ()):
                if child_id not in descendants:
                    stack.append(child_id)
        return descendants

    def _delete_document_folder_from_form(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return

        folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if folder is None:
            return
        root_folder = self._root_document_folder(permit)
        if root_folder is not None and root_folder.folder_id == folder.folder_id:
            self._show_info_dialog(
                "Cannot Delete General",
                "The General root folder cannot be deleted.",
            )
            return

        folder_ids_to_remove = self._document_folder_descendants(permit, folder.folder_id)
        document_count = sum(1 for entry in permit.documents if entry.folder_id in folder_ids_to_remove)
        confirmed = self._confirm_dialog(
            "Delete Folder",
            f"Delete folder '{folder.name}' and its nested folders with {document_count} document(s)?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        for document in list(permit.documents):
            if document.folder_id not in folder_ids_to_remove:
                continue
            self._document_store.delete_document_file(document)
        permit.documents = [entry for entry in permit.documents if entry.folder_id not in folder_ids_to_remove]
        self._document_store.delete_folder_tree(permit, folder)
        permit.document_folders = [
            entry for entry in permit.document_folders if entry.folder_id not in folder_ids_to_remove
        ]
        self._ensure_permit_data_integrity(permit)
        self._persist_tracker_data()
        parent_id = str(folder.parent_folder_id).strip()
        self._active_document_folder_id = parent_id
        self._document_subfolders_expanded = True
        self._selected_permit_document_id = ""
        self._refresh_permit_document_controls(edit_mode=True)

    def _add_documents_to_permit_from_form(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            self._show_info_dialog(
                "Save Permit First",
                "Save this permit, then reopen it to attach documents.",
            )
            return
        folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if folder is None:
            folder = self._root_document_folder(permit)
        if folder is None:
            self._show_warning_dialog("Folder Required", "Choose a document folder first.")
            return

        file_paths, _filter_used = QFileDialog.getOpenFileNames(
            self,
            "Add Permit Documents",
            "",
            "All Files (*)",
        )
        if not file_paths:
            return

        imported_count = 0
        failures: list[str] = []
        for raw_path in file_paths:
            source_path = Path(raw_path)
            try:
                document = self._document_store.import_document(
                    permit=permit,
                    folder=folder,
                    source_path=source_path,
                )
            except Exception as exc:
                failures.append(f"{source_path.name}: {exc}")
                continue
            permit.documents.append(document)
            imported_count += 1

        if imported_count > 0:
            self._persist_tracker_data()
        self._refresh_permit_document_controls(edit_mode=True)
        self._set_document_folder_selection(folder.folder_id)
        self._refresh_permit_document_list()

        if failures:
            preview_lines = "\n".join(failures[:5])
            suffix = ""
            if len(failures) > 5:
                suffix = f"\n...and {len(failures) - 5} more."
            self._show_warning_dialog(
                "Some Documents Were Not Imported",
                f"{preview_lines}{suffix}",
            )

    def _open_active_document_folder(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        folder = self._document_folder_by_id(permit, self._active_document_folder_id)
        if folder is None:
            folder = self._root_document_folder(permit)
        if folder is None:
            return
        try:
            folder_path = self._document_store.folder_path(permit, folder)
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_warning_dialog(
                "Folder Error",
                f"Could not open folder.\n\n{exc}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))

    def _delete_selected_document_from_form(self) -> None:
        permit = self._editing_permit_record()
        if permit is None:
            return
        document = self._document_record_by_id(permit, self._selected_permit_document_id)
        if document is None:
            return
        document_name = document.original_name or document.stored_name or "selected document"
        confirmed = self._confirm_dialog(
            "Delete Document",
            f"Delete document '{document_name}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        self._document_store.delete_document_file(document)
        permit.documents = [
            entry for entry in permit.documents if entry.document_id != document.document_id
        ]
        self._selected_permit_document_id = ""
        self._persist_tracker_data()
        self._refresh_permit_document_list()

    def _format_byte_size(self, raw_size: int) -> str:
        size = max(0, int(raw_size or 0))
        units = ("B", "KB", "MB", "GB")
        scaled = float(size)
        unit = units[0]
        for candidate in units:
            unit = candidate
            if scaled < 1024.0 or candidate == units[-1]:
                break
            scaled = scaled / 1024.0
        if unit == "B":
            return f"{int(scaled)} {unit}"
        return f"{scaled:.1f} {unit}"

    def _format_imported_value(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "Unknown import date"
        if len(text) >= 10:
            return text[:10]
        return text

    def _open_panel_view(self, view: QWidget | None) -> None:
        if self._panel_stack is None or view is None:
            return
        self._panel_stack.setCurrentWidget(view)
        self._sync_foreground_layout()

    def _set_inline_panel_view(self, stack: QStackedLayout | None, view: QWidget | None) -> None:
        if stack is None or view is None:
            return
        if stack.currentWidget() is view:
            return
        stack.setCurrentWidget(view)
        self._sync_foreground_layout()

    def _open_client_panel_form(self) -> None:
        self._set_inline_panel_view(self._client_panel_stack, self._client_form_view)

    def _close_client_panel_form(self) -> None:
        self._editing_client_index = None
        if self._clients_list_widget is not None:
            self._clients_list_widget.clearSelection()
        self._set_inline_panel_view(self._client_panel_stack, self._client_panel_list_view)

    def _open_contractor_panel_form(self) -> None:
        self._set_inline_panel_view(self._contractor_panel_stack, self._contractor_form_view)

    def _close_contractor_panel_form(self) -> None:
        self._editing_contractor_index = None
        if self._contractors_list_widget is not None:
            self._contractors_list_widget.clearSelection()
        self._set_inline_panel_view(self._contractor_panel_stack, self._contractor_panel_list_view)

    def _open_county_panel_form(self) -> None:
        self._set_inline_panel_view(self._county_panel_stack, self._county_form_view)

    def _close_county_panel_form(self) -> None:
        self._editing_county_index = None
        if self._counties_list_widget is not None:
            self._counties_list_widget.clearSelection()
        self._set_inline_panel_view(self._county_panel_stack, self._county_panel_list_view)

    def _close_to_home_view(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        self._editing_client_index = None
        self._editing_contractor_index = None
        self._editing_county_index = None
        self._editing_permit_index = None
        self._active_document_folder_id = ""
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""
        self._close_add_permit_inline_form()
        self._close_client_panel_form()
        self._close_contractor_panel_form()
        self._close_county_panel_form()
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

    def _reset_county_form(self) -> None:
        for field in (
            self._county_name_input,
            self._county_url_input,
            self._county_number_input,
            self._county_email_input,
        ):
            if field is None:
                continue
            field.clear()

    def _reset_inline_add_permit_form(self) -> None:
        inputs = (
            self._permit_add_parcel_input,
            self._permit_add_address_input,
            self._permit_add_request_date_input,
            self._permit_add_application_date_input,
            self._permit_add_completion_date_input,
        )
        for field in inputs:
            if field is None:
                continue
            field.clear()

        for combo in (self._permit_add_client_combo, self._permit_add_contractor_combo):
            if combo is None or combo.count() == 0:
                continue
            combo.setCurrentIndex(0)

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
        self._active_document_folder_id = ""
        self._document_subfolders_expanded = False
        self._selected_permit_document_id = ""

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
            self._show_warning_dialog("Missing Parcel ID", "Please provide a Parcel ID before saving.")
            return
        if not address:
            self._show_warning_dialog("Missing Address", "Please provide an Address before saving.")
            return

        editing_index = self._editing_permit_index
        existing = (
            self._permits[editing_index]
            if editing_index is not None and 0 <= editing_index < len(self._permits)
            else None
        )
        permit_id = existing.permit_id if existing is not None else uuid4().hex
        document_folders = (
            [
                PermitDocumentFolder.from_mapping(folder.to_mapping())
                for folder in existing.document_folders
            ]
            if existing is not None
            else []
        )
        documents = (
            [
                PermitDocumentRecord.from_mapping(document.to_mapping())
                for document in existing.documents
            ]
            if existing is not None
            else []
        )

        permit = PermitRecord(
            permit_id=permit_id,
            parcel_id=parcel_id,
            address=address,
            category=self._normalize_permit_category(self._active_permit_category),
            request_date=request_date,
            application_date=application_date,
            completion_date=completion_date,
            client_name=client_name,
            contractor_name=contractor_name,
            document_folders=document_folders,
            documents=documents,
        )
        self._ensure_permit_data_integrity(permit)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog(
                "Document Storage Error",
                f"Could not initialize permit folders.\n\n{exc}",
            )
            return

        if existing is None:
            self._permits.append(permit)
        elif editing_index is not None and 0 <= editing_index < len(self._permits):
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
        confirmed = self._confirm_dialog(
            "Delete Permit",
            f"Delete permit '{label}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        try:
            for document in permit.documents:
                self._document_store.delete_document_file(document)
            self._document_store.delete_permit_tree(permit)
        except Exception:
            pass
        del self._permits[index]
        self._selected_permit_document_id = ""
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
            searchable = (
                f"{client.name} {' '.join(client.numbers)} {' '.join(client.emails)}".lower()
            )
            if search_query and search_query not in searchable:
                continue
            number_text = self._summarize_multi_values(client.numbers, empty_text="No number")
            email_text = self._summarize_multi_values(client.emails, empty_text="No email")
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
            searchable = (
                f"{contractor.name} {' '.join(contractor.numbers)} {' '.join(contractor.emails)}".lower()
            )
            if search_query and search_query not in searchable:
                continue
            number_text = self._summarize_multi_values(contractor.numbers, empty_text="No number")
            email_text = self._summarize_multi_values(contractor.emails, empty_text="No email")
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

    def _refresh_counties_list(self) -> None:
        search_query = self._normalized_search_text(self._county_search_input)
        filter_mode = self._current_filter_value(self._county_filter_combo)
        rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        for index, county in enumerate(self._counties):
            if not self._county_matches_filter(county, filter_mode):
                continue
            searchable = (
                f"{county.county_name} {' '.join(county.portal_urls)} "
                f"{' '.join(county.numbers)} {' '.join(county.emails)}".lower()
            )
            if search_query and search_query not in searchable:
                continue
            url_text = self._summarize_multi_values(county.portal_urls, empty_text="No URL")
            number_text = self._summarize_multi_values(county.numbers, empty_text="No number")
            email_text = self._summarize_multi_values(county.emails, empty_text="No email")
            rows.append(
                (
                    index,
                    (
                        ("county", county.county_name),
                        ("url", url_text),
                        ("number", number_text),
                        ("email", email_text),
                    ),
                )
            )

        self._populate_list_widget(
            self._counties_list_widget,
            rows,
            "No counties match current filters.",
        )
        self._set_result_label(
            self._county_result_label,
            shown=len(rows),
            total=len(self._counties),
            noun="counties",
        )

    def _refresh_permits_list(self) -> None:
        self._sync_permit_category_controls()
        active_category = self._normalize_permit_category(self._active_permit_category)
        search_query = self._normalized_search_text(self._permit_search_input)
        filter_mode = self._current_filter_value(self._permit_filter_combo)
        rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        total_in_category = 0
        for index, permit in enumerate(self._permits):
            permit_category = self._normalize_permit_category(permit.category)
            permit.category = permit_category
            if permit_category != active_category:
                continue
            total_in_category += 1
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
                        ("address", address_text),
                        ("parcel", parcel_text),
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
            total=total_in_category,
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
            "county": "County",
            "url": "Portal URL",
            "email": "Email",
            "number": "Number",
            "address": "Address",
            "parcel": "Parcel ID",
            "request": "Request Date",
            "document": "Document",
            "folder": "Folder",
            "size": "Size",
            "added": "Imported",
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

    def _parse_multi_value_input(self, raw_value: str) -> list[str]:
        chunks = (
            str(raw_value or "")
            .replace("\r", "\n")
            .replace(";", "\n")
            .replace(",", "\n")
            .splitlines()
        )
        rows: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            text = chunk.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            rows.append(text)
        return rows

    def _join_multi_value_input(self, values: Sequence[str]) -> str:
        normalized = self._parse_multi_value_input("\n".join(str(value) for value in values))
        return ", ".join(normalized)

    def _summarize_multi_values(self, values: Sequence[str], *, empty_text: str) -> str:
        normalized = self._parse_multi_value_input("\n".join(str(value) for value in values))
        if not normalized:
            return empty_text
        return ", ".join(normalized)

    def _contact_matches_filter(self, record: ContactRecord, filter_mode: str) -> bool:
        has_email = bool(self._parse_multi_value_input("\n".join(record.emails)))
        has_number = bool(self._parse_multi_value_input("\n".join(record.numbers)))
        if filter_mode == "email":
            return has_email
        if filter_mode == "number":
            return has_number
        if filter_mode == "missing_contact":
            return not (has_email and has_number)
        return True

    def _county_matches_filter(self, record: CountyRecord, filter_mode: str) -> bool:
        has_url = bool(self._parse_multi_value_input("\n".join(record.portal_urls)))
        has_email = bool(self._parse_multi_value_input("\n".join(record.emails)))
        has_number = bool(self._parse_multi_value_input("\n".join(record.numbers)))
        if filter_mode == "url":
            return has_url
        if filter_mode == "email":
            return has_email
        if filter_mode == "number":
            return has_number
        if filter_mode == "missing_contact":
            return not (has_email and has_number and has_url)
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
            self._permit_add_client_combo,
            self._clients,
            "No clients yet",
        )
        self._refresh_contact_combo(
            self._permit_contractor_combo,
            self._contractors,
            "No contractors yet",
        )
        self._refresh_contact_combo(
            self._permit_add_contractor_combo,
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
        self._document_store.update_data_root(self._data_storage_folder)

        load_result = self._data_store.load_bundle()
        migrated = self._apply_tracker_bundle(load_result.bundle, refresh_ui=False)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)
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
                "counties": len(self._counties),
                "permits": len(self._permits),
            },
        )

        return "\n\n".join(line for line in warning_lines if line.strip())

    def _snapshot_tracker_bundle(self) -> TrackerDataBundle:
        return TrackerDataBundle(
            clients=[ContactRecord.from_mapping(record.to_mapping()) for record in self._clients],
            contractors=[ContactRecord.from_mapping(record.to_mapping()) for record in self._contractors],
            counties=[CountyRecord.from_mapping(record.to_mapping()) for record in self._counties],
            permits=[PermitRecord.from_mapping(record.to_mapping()) for record in self._permits],
        )

    def _apply_tracker_bundle(self, bundle: TrackerDataBundle, *, refresh_ui: bool) -> bool:
        cloned_bundle = bundle.clone()
        self._clients = list(cloned_bundle.clients)
        self._contractors = list(cloned_bundle.contractors)
        self._counties = list(cloned_bundle.counties)
        self._permits = list(cloned_bundle.permits)
        migrated = False
        for permit in self._permits:
            if self._ensure_permit_data_integrity(permit):
                migrated = True

        if not refresh_ui:
            return migrated

        self._refresh_clients_list()
        self._refresh_contractors_list()
        self._refresh_counties_list()
        self._refresh_permits_list()
        self._refresh_party_selectors()
        return migrated

    def _persist_tracker_data(self, *, show_error_dialog: bool = True) -> bool:
        bundle = self._snapshot_tracker_bundle()
        try:
            self._data_store.save_bundle(bundle)
        except Exception as exc:
            if show_error_dialog:
                self._show_warning_dialog(
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
                "counties": len(self._counties),
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
            self._show_warning_dialog(
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
        self._document_store.update_data_root(self._data_storage_folder)
        save_data_storage_backend(self._data_storage_backend)
        save_data_storage_folder(self._data_storage_folder)

        self._close_to_home_view()
        migrated = self._apply_tracker_bundle(target_bundle, refresh_ui=True)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)

        self._state_streamer.record(
            "data.folder_switched",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "loaded_existing": loaded_existing,
                "clients": len(self._clients),
                "contractors": len(self._contractors),
                "counties": len(self._counties),
                "permits": len(self._permits),
            },
        )

        if warning_message:
            self._show_data_storage_warning(warning_message)
        elif loaded_existing:
            self._show_info_dialog(
                "Storage Folder Updated",
                f"Loaded existing data from:\n{self._data_storage_folder}",
            )
        else:
            self._show_info_dialog(
                "Storage Folder Updated",
                f"No saved data found in:\n{self._data_storage_folder}\n\n"
                "The panels were reset to empty for this folder.",
            )

        return str(self._data_storage_folder)

    def _show_data_storage_warning(self, message: str) -> None:
        text = message.strip()
        if not text:
            return
        self._show_warning_dialog("Data Storage Notice", text)

    def _check_for_updates_on_startup(self) -> None:
        if not self._auto_update_github_repo:
            return
        self._check_for_updates(manual=False)

    def _set_update_settings_status(self, text: str, *, checking: bool) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            return
        dialog.set_update_status(text)
        dialog.set_update_check_running(checking)

    def _on_check_updates_requested(self) -> None:
        self._check_for_updates(manual=True)

    def _check_for_updates(self, *, manual: bool) -> None:
        if self._update_check_in_progress:
            if manual:
                self._show_info_dialog(
                    "Update Check",
                    "An update check is already in progress.",
                )
            return

        if not self._auto_update_github_repo:
            self._set_update_settings_status("Update source is not configured in this build.", checking=False)
            if manual:
                self._show_warning_dialog(
                    "Repository Required",
                    "This build does not define an update source.\n\n"
                    "Set GITHUB_RELEASE_REPO in src/erpermitsys/version.py and rebuild.",
                )
            return

        self._update_check_in_progress = True
        self._set_update_settings_status("Checking for updates...", checking=True)
        self._state_streamer.record(
            "updates.check_started",
            source="main_window",
            payload={
                "manual": manual,
                "repo": self._auto_update_github_repo,
                "asset_name": self._auto_update_asset_name,
            },
        )

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            result = self._updater.check_for_update(
                repo=self._auto_update_github_repo,
                current_version=self._app_version,
                asset_name=self._auto_update_asset_name,
            )
        finally:
            self._update_check_in_progress = False
            if app is not None and app.overrideCursor() is not None:
                app.restoreOverrideCursor()

        self._handle_update_check_result(result, manual=manual)

    def _handle_update_check_result(self, result: GitHubUpdateCheckResult, *, manual: bool) -> None:
        message = result.message.strip()
        status = result.status
        info = result.info

        if status == "update_available" and info is not None:
            self._set_update_settings_status(
                f"Update available: v{info.latest_version}.",
                checking=False,
            )
            self._state_streamer.record(
                "updates.available",
                source="main_window",
                payload={
                    "current_version": info.current_version,
                    "latest_version": info.latest_version,
                    "repo": info.repo,
                    "asset": info.asset.name if info.asset is not None else "",
                },
            )
            confirm = self._confirm_dialog(
                "Update Available",
                self._format_update_confirmation_message(info),
                confirm_text="Update Now",
                cancel_text="Later",
            )
            if confirm:
                self._download_and_apply_update(info)
            else:
                self._set_update_settings_status("Update postponed.", checking=False)
            return

        if status == "up_to_date":
            self._set_update_settings_status(message or "You are on the latest version.", checking=False)
            if manual:
                self._show_info_dialog("Update Check", message or "You are on the latest version.")
            return

        if status in ("not_configured", "no_release", "no_compatible_asset"):
            self._set_update_settings_status(message, checking=False)
            if manual:
                self._show_warning_dialog("Update Check", message)
            return

        self._set_update_settings_status(message or "Update check failed.", checking=False)
        if manual:
            self._show_warning_dialog("Update Check Failed", message or "Unknown update error.")

    def _format_update_confirmation_message(self, info: GitHubUpdateInfo) -> str:
        lines: list[str] = [
            f"A new version is available.",
            f"",
            f"Current: v{info.current_version}",
            f"Latest: v{info.latest_version}",
        ]
        if info.published_at:
            lines.append(f"Published: {info.published_at}")
        if info.asset is not None:
            lines.append(f"Asset: {info.asset.name}")
        lines.append("")
        notes = info.notes.strip()
        if notes:
            compact_notes = " ".join(notes.split())
            if len(compact_notes) > 260:
                compact_notes = f"{compact_notes[:257]}..."
            lines.append(f"Release notes: {compact_notes}")
            lines.append("")
        lines.append("Install this update now?")
        return "\n".join(lines)

    def _download_and_apply_update(self, info: GitHubUpdateInfo) -> None:
        asset = info.asset
        if asset is None:
            self._show_warning_dialog(
                "Update Download Missing",
                "This release does not include a downloadable asset.",
            )
            self._set_update_settings_status("Release asset missing.", checking=False)
            return

        self._set_update_settings_status(
            f"Downloading {asset.name}...",
            checking=True,
        )

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            temp_root = Path(tempfile.mkdtemp(prefix="erpermitsys_update_"))
            archive_path = temp_root / asset.name
            downloaded_file = self._updater.download_asset(asset=asset, destination=archive_path)
        except Exception as exc:
            self._set_update_settings_status("Download failed.", checking=False)
            self._show_warning_dialog(
                "Update Download Failed",
                f"Could not download update:\n\n{exc}",
            )
            return
        finally:
            if app is not None and app.overrideCursor() is not None:
                app.restoreOverrideCursor()

        self._set_update_settings_status("Update downloaded.", checking=False)
        self._state_streamer.record(
            "updates.downloaded",
            source="main_window",
            payload={
                "latest_version": info.latest_version,
                "file": str(downloaded_file),
                "asset": asset.name,
            },
        )

        is_zip = downloaded_file.name.lower().endswith(".zip")
        if can_self_update_windows() and is_zip:
            started, launcher_detail = launch_windows_zip_updater(
                archive_path=downloaded_file,
                app_pid=int(QApplication.applicationPid()),
                target_dir=Path(sys.executable).resolve().parent,
                executable_path=Path(sys.executable).resolve(),
            )
            if not started:
                self._set_update_settings_status("Installer launch failed.", checking=False)
                self._show_warning_dialog(
                    "Update Install Failed",
                    launcher_detail or "Could not launch update installer.",
                )
                return

            self._set_update_settings_status("Installing update and restarting...", checking=False)
            message_lines = [
                "The updater was launched in a separate window.",
                "",
                "The app will close now and restart after files are replaced.",
            ]
            if launcher_detail:
                message_lines.extend(["", launcher_detail])
            self._show_info_dialog(
                "Installing Update",
                "\n".join(message_lines),
            )
            self.close()
            return

        if info.release_url:
            QDesktopServices.openUrl(QUrl(info.release_url))

        if is_packaged_runtime():
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "Automatic install is currently supported for Windows .zip release assets only.\n"
                "Please install this release manually."
            )
        else:
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "You are running from source, so auto-replace is skipped.\n"
                "Use the GitHub release page to deploy your next build."
            )
        self._show_info_dialog("Manual Update Required", guidance)

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
                app_version=self._app_version,
                on_check_updates_requested=self._on_check_updates_requested,
            )
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.plugins_changed.connect(self._sync_background_from_plugins)
            dialog.finished.connect(self._on_settings_dialog_finished)
            self._settings_dialog = dialog

        mode = "dark" if self._dark_mode_enabled else "light"
        dialog.set_theme_mode(mode)
        dialog.set_update_check_running(False)
        source = self._auto_update_github_repo or "not configured"
        dialog.set_update_status(f"Current version: {self._app_version} • Source: {source}")
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
        if self._focus_tracking_connected:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.focusChanged.disconnect(self._on_app_focus_changed)
                except Exception:
                    pass
            self._focus_tracking_connected = False
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
        if isinstance(watched, QFrame) and watched in self._tracker_panel_frames:
            if event.type() == QEvent.Type.Enter:
                self._hovered_tracker_panel = watched
                self._refresh_tracker_panel_highlight()
            elif event.type() == QEvent.Type.Leave:
                if self._hovered_tracker_panel is watched:
                    self._hovered_tracker_panel = None
                    self._refresh_tracker_panel_highlight()
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
            desired_height = max(320, int(scene_height * 0.84))
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
    app.setApplicationVersion(APP_VERSION)

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
        payload={"theme_mode": theme_mode, "app_version": APP_VERSION},
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
            "app_version": APP_VERSION,
        },
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

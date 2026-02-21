from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional runtime dependency
    QWebEngineView = None  # type: ignore[assignment]

from erpermitsys.app.background_plugin_bridge import BackgroundPluginBridge
from erpermitsys.app.command_runtime import CommandRuntime
from erpermitsys.app.data_store import BACKEND_LOCAL_SQLITE, create_data_store
from erpermitsys.app.document_store import create_document_store
from erpermitsys.app.settings_store import (
    DEFAULT_PALETTE_SHORTCUT,
    SupabaseSettings,
    load_dark_mode,
    load_data_storage_backend,
    load_data_storage_folder,
    load_palette_shortcut_enabled,
    load_palette_shortcut_keybind,
    load_supabase_merge_on_switch,
    load_supabase_settings,
    save_palette_shortcut_settings,
)
from erpermitsys.app.state_containers import AdminState, StorageState, WorkspaceState
from erpermitsys.app.window_documents_mixin import WindowDocumentsMixin
from erpermitsys.app.window_document_templates_mixin import WindowDocumentTemplatesMixin
from erpermitsys.app.window_entity_actions_mixin import WindowEntityActionsMixin
from erpermitsys.app.window_inline_forms_mixin import WindowInlineFormsMixin
from erpermitsys.app.window_lookup_mixin import WindowLookupMixin
from erpermitsys.app.window_admin_mixin import WindowAdminMixin
from erpermitsys.app.window_dialogs_mixin import WindowDialogsMixin
from erpermitsys.app.window_member_defaults_mixin import WindowMemberDefaultsMixin
from erpermitsys.app.window_overlay_mixin import WindowOverlayMixin
from erpermitsys.app.window_shell_mixin import WindowShellMixin
from erpermitsys.app.window_storage_update_mixin import WindowStorageUpdateMixin
from erpermitsys.app.window_timeline_mixin import WindowTimelineMixin
from erpermitsys.app.window_workspace_state_mixin import WindowWorkspaceStateMixin
from erpermitsys.app.window_workspace_list_mixin import WindowWorkspaceListMixin
from erpermitsys.app.tracker_models import (
    ContactRecord,
    DocumentChecklistTemplate,
    JurisdictionRecord,
    PermitRecord,
    PropertyRecord,
)
from erpermitsys.app.updater import GitHubReleaseUpdater
from erpermitsys.core import StateStreamer
from erpermitsys.plugins import PluginManager
from erpermitsys.plugins.api import PluginApiService
from erpermitsys.ui.assets import icon_asset_path
from erpermitsys.ui.settings_dialog import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme
from erpermitsys.ui.window.frameless_window import FramelessWindow
from erpermitsys.version import APP_VERSION, GITHUB_RELEASE_ASSET_NAME, GITHUB_RELEASE_REPO


_UPDATE_STARTUP_DELAY_MS = 1800
_TIMELINE_DEBUG_ENV = "ERPERMITSYS_TIMELINE_DEBUG"
_TIMELINE_DEBUG_LOG_ENV = "ERPERMITSYS_TIMELINE_DEBUG_LOG"


def _is_truthy_env(raw_value: str) -> bool:
    return str(raw_value or "").strip().casefold() in {"1", "true", "yes", "on", "y"}


class ErPermitSysWindow(
    WindowDialogsMixin,
    WindowMemberDefaultsMixin,
    WindowLookupMixin,
    WindowWorkspaceListMixin,
    WindowWorkspaceStateMixin,
    WindowEntityActionsMixin,
    WindowStorageUpdateMixin,
    WindowDocumentsMixin,
    WindowDocumentTemplatesMixin,
    WindowInlineFormsMixin,
    WindowAdminMixin,
    WindowOverlayMixin,
    WindowTimelineMixin,
    WindowShellMixin,
    FramelessWindow,
):
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
        self.setMinimumSize(980, 640)
        self.resize(1220, 760)

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

        self._storage_state = StorageState(
            backend=load_data_storage_backend(default=BACKEND_LOCAL_SQLITE),
            data_storage_folder=load_data_storage_folder(),
            supabase_settings=load_supabase_settings(),
            supabase_merge_on_switch=load_supabase_merge_on_switch(),
        )
        self._data_storage_backend = self._storage_state.backend
        self._data_storage_folder = self._storage_state.data_storage_folder
        self._supabase_settings = self._storage_state.supabase_settings
        self._data_store = create_data_store(self._data_storage_backend, self._data_storage_folder)
        self._document_store = create_document_store(
            self._data_storage_backend,
            self._data_storage_folder,
        )

        self._app_version = APP_VERSION
        self._auto_update_github_repo = GITHUB_RELEASE_REPO
        self._auto_update_asset_name = GITHUB_RELEASE_ASSET_NAME
        self._updater = GitHubReleaseUpdater(timeout_seconds=3.5)
        self._update_check_in_progress = False

        self._contacts: list[ContactRecord] = []
        self._jurisdictions: list[JurisdictionRecord] = []
        self._properties: list[PropertyRecord] = []
        self._permits: list[PermitRecord] = []
        self._document_templates: list[DocumentChecklistTemplate] = []
        self._active_document_template_ids: dict[str, str] = {}

        self._workspace_state = WorkspaceState()
        self._selected_property_id = self._workspace_state.selected_property_id
        self._selected_permit_id = self._workspace_state.selected_permit_id
        self._active_permit_type_filter = self._workspace_state.active_permit_type_filter
        self._selected_document_slot_id = self._workspace_state.selected_document_slot_id
        self._selected_document_id = self._workspace_state.selected_document_id
        self._admin_state = AdminState()
        self._timeline_debug_enabled: bool = _is_truthy_env(os.getenv(_TIMELINE_DEBUG_ENV, ""))
        self._timeline_debug_log_path: str = str(os.getenv(_TIMELINE_DEBUG_LOG_ENV, "")).strip()
        self._timeline_debug_sequence: int = 0
        self._timeline_render_sequence: int = 0
        self._timeline_show_next_action_mode: bool = False

        self._initialize_widget_placeholders()

        storage_warning = self._initialize_data_store()
        self._build_body()
        self._plugin_manager.discover(auto_activate_background=False)
        self._restore_active_plugins()
        self._sync_background_from_plugins()
        self._refresh_all_views()
        self._sync_supabase_realtime_subscription()

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
        self._timeline_debug(
            "window_initialized",
            timeline_debug_enabled=self._timeline_debug_enabled,
            timeline_debug_env=_TIMELINE_DEBUG_ENV,
            timeline_debug_log_env=_TIMELINE_DEBUG_LOG_ENV,
            timeline_debug_log_path=self._timeline_debug_log_path or "stderr",
            contacts=len(self._contacts),
            jurisdictions=len(self._jurisdictions),
            properties=len(self._properties),
            permits=len(self._permits),
        )

    @property
    def _selected_property_id(self) -> str:
        return self._workspace_state.selected_property_id

    @_selected_property_id.setter
    def _selected_property_id(self, value: str) -> None:
        self._workspace_state.selected_property_id = str(value or "").strip()

    @property
    def _selected_permit_id(self) -> str:
        return self._workspace_state.selected_permit_id

    @_selected_permit_id.setter
    def _selected_permit_id(self, value: str) -> None:
        self._workspace_state.selected_permit_id = str(value or "").strip()

    @property
    def _selected_document_slot_id(self) -> str:
        return self._workspace_state.selected_document_slot_id

    @_selected_document_slot_id.setter
    def _selected_document_slot_id(self, value: str) -> None:
        self._workspace_state.selected_document_slot_id = str(value or "").strip()

    @property
    def _selected_document_id(self) -> str:
        return self._workspace_state.selected_document_id

    @_selected_document_id.setter
    def _selected_document_id(self, value: str) -> None:
        self._workspace_state.selected_document_id = str(value or "").strip()

    @property
    def _active_permit_type_filter(self) -> str:
        return self._workspace_state.active_permit_type_filter

    @_active_permit_type_filter.setter
    def _active_permit_type_filter(self, value: str) -> None:
        self._workspace_state.active_permit_type_filter = str(value or "").strip()

    @property
    def _data_storage_backend(self) -> str:
        return self._storage_state.backend

    @_data_storage_backend.setter
    def _data_storage_backend(self, value: str) -> None:
        self._storage_state.backend = str(value or "").strip()

    @property
    def _data_storage_folder(self) -> Path:
        return self._storage_state.data_storage_folder

    @_data_storage_folder.setter
    def _data_storage_folder(self, value: Path | str) -> None:
        self._storage_state.data_storage_folder = value if isinstance(value, Path) else Path(value)

    @property
    def _supabase_settings(self) -> SupabaseSettings:
        return self._storage_state.supabase_settings

    @_supabase_settings.setter
    def _supabase_settings(self, value: SupabaseSettings) -> None:
        self._storage_state.supabase_settings = value

    @property
    def _supabase_merge_on_switch(self) -> bool:
        return bool(self._storage_state.supabase_merge_on_switch)

    @_supabase_merge_on_switch.setter
    def _supabase_merge_on_switch(self, value: bool) -> None:
        self._storage_state.supabase_merge_on_switch = bool(value)

    @property
    def _admin_contact_dirty(self) -> bool:
        return self._admin_state.contact_dirty

    @_admin_contact_dirty.setter
    def _admin_contact_dirty(self, value: bool) -> None:
        self._admin_state.contact_dirty = bool(value)

    @property
    def _admin_jurisdiction_dirty(self) -> bool:
        return self._admin_state.jurisdiction_dirty

    @_admin_jurisdiction_dirty.setter
    def _admin_jurisdiction_dirty(self, value: bool) -> None:
        self._admin_state.jurisdiction_dirty = bool(value)

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
    palette_shortcut_keybind = load_palette_shortcut_keybind(default=DEFAULT_PALETTE_SHORTCUT)
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

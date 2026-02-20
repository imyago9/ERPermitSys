from __future__ import annotations

from pathlib import Path

from erpermitsys.app.data_store import DataLoadResult
from erpermitsys.app.settings_store import SupabaseSettings
from erpermitsys.app.storage_runtime import StorageRuntimeSelection
from erpermitsys.app.storage_update_service import WindowStorageUpdateService
from erpermitsys.app.tracker_models import TrackerDataBundleV3
from erpermitsys.app.updater import GitHubUpdateCheckResult, GitHubUpdateInfo


class WindowStorageUpdateMixin:
    def _storage_update_service(self) -> WindowStorageUpdateService:
        service = self.__dict__.get("_storage_update_service_instance")
        if service is None:
            service = WindowStorageUpdateService(self)
            self.__dict__["_storage_update_service_instance"] = service
        return service

    def _close_to_home_view(self) -> None:
        self._storage_update_service()._close_to_home_view()

    def _initialize_data_store(self) -> str:
        return self._storage_update_service()._initialize_data_store()

    def _build_storage_selection(
        self,
        *,
        backend: str,
        folder: Path | str,
        supabase_settings: SupabaseSettings | None,
    ) -> StorageRuntimeSelection:
        return self._storage_update_service()._build_storage_selection(
            backend=backend,
            folder=folder,
            supabase_settings=supabase_settings,
        )

    def _apply_storage_selection(
        self,
        selection: StorageRuntimeSelection,
        *,
        persist_settings: bool,
    ) -> None:
        self._storage_update_service()._apply_storage_selection(
            selection,
            persist_settings=persist_settings,
        )

    def _safe_load_bundle(self, data_store) -> DataLoadResult:
        return self._storage_update_service()._safe_load_bundle(data_store)

    def _snapshot_tracker_bundle(self) -> TrackerDataBundleV3:
        return self._storage_update_service()._snapshot_tracker_bundle()

    def _apply_tracker_bundle(self, bundle: TrackerDataBundleV3, *, refresh_ui: bool) -> bool:
        return self._storage_update_service()._apply_tracker_bundle(bundle, refresh_ui=refresh_ui)

    def _persist_tracker_data(self, *, show_error_dialog: bool = True) -> bool:
        return self._storage_update_service()._persist_tracker_data(show_error_dialog=show_error_dialog)

    def _on_data_storage_folder_changed(self, requested_folder: str) -> str:
        return self._storage_update_service()._on_data_storage_folder_changed(requested_folder)

    def _on_data_storage_backend_changed(
        self,
        requested_backend: str,
        *,
        force: bool = False,
    ) -> str:
        return self._storage_update_service()._on_data_storage_backend_changed(
            requested_backend,
            force=force,
        )

    def _on_export_json_backup_requested(self, requested_path: str) -> str:
        return self._storage_update_service()._on_export_json_backup_requested(requested_path)

    def _on_import_json_backup_requested(self, requested_path: str) -> bool:
        return self._storage_update_service()._on_import_json_backup_requested(requested_path)

    def _on_supabase_settings_changed(self, settings_value: dict[str, object]) -> dict[str, str]:
        return self._storage_update_service()._on_supabase_settings_changed(settings_value)

    def _on_supabase_merge_on_switch_changed(self, enabled: bool) -> bool:
        return self._storage_update_service()._on_supabase_merge_on_switch_changed(enabled)

    def _sync_supabase_realtime_subscription(self) -> None:
        self._storage_update_service()._sync_supabase_realtime_subscription()

    def _shutdown_supabase_realtime_subscription(self) -> None:
        self._storage_update_service()._shutdown_supabase_realtime_subscription()

    def _show_data_storage_warning(self, message: str) -> None:
        self._storage_update_service()._show_data_storage_warning(message)

    def _check_for_updates_on_startup(self) -> None:
        self._storage_update_service()._check_for_updates_on_startup()

    def _set_update_settings_status(self, text: str, *, checking: bool) -> None:
        self._storage_update_service()._set_update_settings_status(text, checking=checking)

    def _on_check_updates_requested(self) -> None:
        self._storage_update_service()._on_check_updates_requested()

    def _check_for_updates(self, *, manual: bool) -> None:
        self._storage_update_service()._check_for_updates(manual=manual)

    def _handle_update_check_result(self, result: GitHubUpdateCheckResult, *, manual: bool) -> None:
        self._storage_update_service()._handle_update_check_result(result, manual=manual)

    def _format_update_confirmation_message(self, info: GitHubUpdateInfo) -> str:
        return self._storage_update_service()._format_update_confirmation_message(info)

    def _download_and_apply_update(self, info: GitHubUpdateInfo) -> None:
        self._storage_update_service()._download_and_apply_update(info)

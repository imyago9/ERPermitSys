from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from erpermitsys.app.data_store import BACKEND_LOCAL_JSON, BACKEND_SUPABASE, LocalJsonDataStore
from erpermitsys.app.settings_store import (
    normalize_data_storage_folder,
    save_data_storage_backend,
    save_data_storage_folder,
)
from erpermitsys.app.tracker_models import (
    ContactRecord,
    DocumentChecklistTemplate,
    JurisdictionRecord,
    PermitRecord,
    PropertyRecord,
    TrackerDataBundleV3,
    build_document_slots_from_template,
    compute_permit_status,
    ensure_default_document_structure,
    normalize_parcel_id,
    refresh_slot_status_from_documents,
)
from erpermitsys.app.updater import (
    GitHubUpdateCheckResult,
    GitHubUpdateInfo,
    can_self_update_windows,
    is_packaged_runtime,
    launch_windows_zip_updater,
)


class WindowStorageUpdateMixin:
    def _close_to_home_view(self) -> None:
        self._selected_property_id = ""
        self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        if self._panel_stack is not None and self._panel_home_view is not None:
            self._panel_stack.setCurrentWidget(self._panel_home_view)

    def _initialize_data_store(self) -> str:
        warning_lines: list[str] = []

        configured_backend = str(self._data_storage_backend or "").strip().lower()
        if configured_backend == BACKEND_SUPABASE:
            warning_lines.append("Supabase data storage is not enabled yet. Using local JSON storage.")
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
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
            },
        )

        return "\n\n".join(line for line in warning_lines if line.strip())

    def _snapshot_tracker_bundle(self) -> TrackerDataBundleV3:
        return TrackerDataBundleV3(
            contacts=[ContactRecord.from_mapping(record.to_mapping()) for record in self._contacts],
            jurisdictions=[JurisdictionRecord.from_mapping(record.to_mapping()) for record in self._jurisdictions],
            properties=[PropertyRecord.from_mapping(record.to_mapping()) for record in self._properties],
            permits=[PermitRecord.from_mapping(record.to_mapping()) for record in self._permits],
            document_templates=[
                DocumentChecklistTemplate.from_mapping(record.to_mapping())
                for record in self._document_templates
            ],
            active_document_template_ids=dict(self._active_document_template_ids),
        )

    def _apply_tracker_bundle(self, bundle: TrackerDataBundleV3, *, refresh_ui: bool) -> bool:
        cloned_bundle = bundle.clone()
        self._contacts = list(cloned_bundle.contacts)
        self._jurisdictions = list(cloned_bundle.jurisdictions)
        self._properties = list(cloned_bundle.properties)
        self._permits = list(cloned_bundle.permits)
        self._document_templates = list(cloned_bundle.document_templates)
        self._active_document_template_ids = dict(cloned_bundle.active_document_template_ids)
        before_active_templates = dict(self._active_document_template_ids)
        self._prune_active_document_template_ids()

        migrated = False
        if before_active_templates != self._active_document_template_ids:
            migrated = True

        for property_record in self._properties:
            normalized = normalize_parcel_id(property_record.parcel_id)
            if property_record.parcel_id_norm != normalized:
                property_record.parcel_id_norm = normalized
                migrated = True

        for permit in self._permits:
            before_status = permit.status
            changed = ensure_default_document_structure(permit)
            changed = refresh_slot_status_from_documents(permit) or changed
            permit.status = compute_permit_status(permit.events, fallback=permit.status)
            if changed or permit.status != before_status:
                migrated = True

        for template in self._document_templates:
            normalized_slots = build_document_slots_from_template(
                template,
                permit_type=template.permit_type,
            )
            if self._template_slots_snapshot(template.slots) != self._template_slots_snapshot(normalized_slots):
                template.slots = normalized_slots
                migrated = True

        if refresh_ui:
            self._refresh_all_views()

        return migrated

    def _persist_tracker_data(self, *, show_error_dialog: bool = True) -> bool:
        bundle = self._snapshot_tracker_bundle()
        try:
            self._data_store.save_bundle(bundle)
        except Exception as exc:
            if show_error_dialog:
                self._show_warning_dialog("Storage Error", f"Could not save local data.\n\n{exc}")
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
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
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
                target_bundle = TrackerDataBundleV3()
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
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
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
                "The tracker was reset to empty for this folder.",
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
                self._show_info_dialog("Update Check", "An update check is already in progress.")
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
            self._set_update_settings_status(f"Update available: v{info.latest_version}.", checking=False)
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
            "A new version is available.",
            "",
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
            self._show_warning_dialog("Update Download Missing", "This release does not include a downloadable asset.")
            self._set_update_settings_status("Release asset missing.", checking=False)
            return

        self._set_update_settings_status(f"Downloading {asset.name}...", checking=True)

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            temp_root = Path(tempfile.mkdtemp(prefix="erpermitsys_update_"))
            archive_path = temp_root / asset.name
            downloaded_file = self._updater.download_asset(asset=asset, destination=archive_path)
        except Exception as exc:
            self._set_update_settings_status("Download failed.", checking=False)
            self._show_warning_dialog("Update Download Failed", f"Could not download update:\n\n{exc}")
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
            self._show_info_dialog("Installing Update", "\n".join(message_lines))
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


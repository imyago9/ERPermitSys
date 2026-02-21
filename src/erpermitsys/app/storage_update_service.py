from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QProgressDialog

from erpermitsys.app.data_store import (
    BACKEND_LOCAL_SQLITE,
    BACKEND_SUPABASE,
    DataLoadResult,
    SupabaseDataStore,
    SupabaseRevisionConflictError,
    load_bundle_from_json_file,
    save_bundle_as_json_file,
)
from erpermitsys.app.settings_store import (
    SupabaseSettings,
    normalize_data_storage_backend,
    normalize_data_storage_folder,
    normalize_supabase_settings,
    save_data_storage_backend,
    save_data_storage_folder,
    save_supabase_merge_on_switch,
    save_supabase_settings,
)
from erpermitsys.app.supabase_realtime import (
    SupabaseRealtimeClient,
    SupabaseRealtimeSubscription,
)
from erpermitsys.app.storage_runtime import StorageRuntimeSelection, build_storage_runtime
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
    launch_windows_installer_updater,
    launch_windows_zip_updater,
)
from erpermitsys.app.window_bound_service import WindowBoundService


_SUPABASE_REVISION_POLL_INTERVAL_MS = 5_000


class WindowStorageUpdateService(WindowBoundService):
    def _close_to_home_view(self) -> None:
        if hasattr(self, "_workspace_state"):
            self._workspace_state.selected_property_id = ""
            self._workspace_state.selected_permit_id = ""
            self._workspace_state.selected_document_slot_id = ""
            self._workspace_state.selected_document_id = ""
        self._selected_property_id = ""
        self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        if self._panel_stack is not None and self._panel_home_view is not None:
            self._panel_stack.setCurrentWidget(self._panel_home_view)

    def _initialize_data_store(self) -> str:
        backend = self._data_storage_backend
        folder = self._data_storage_folder
        supabase_settings = self._supabase_settings
        if hasattr(self, "_storage_state"):
            backend = self._storage_state.backend
            folder = self._storage_state.data_storage_folder
            supabase_settings = self._storage_state.supabase_settings
        selection = self._build_storage_selection(
            backend=backend,
            folder=folder,
            supabase_settings=supabase_settings,
        )
        self._apply_storage_selection(selection, persist_settings=True)
        warning_lines = list(selection.warnings)

        load_result = self._safe_load_bundle(self._data_store)
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

    def _build_storage_selection(
        self,
        *,
        backend: str,
        folder: Path | str,
        supabase_settings: SupabaseSettings | None,
    ) -> StorageRuntimeSelection:
        normalized_folder = normalize_data_storage_folder(folder)
        return build_storage_runtime(
            backend=backend,
            data_root=normalized_folder,
            supabase_settings=supabase_settings,
        )

    def _apply_storage_selection(
        self,
        selection: StorageRuntimeSelection,
        *,
        persist_settings: bool,
    ) -> None:
        self._data_storage_backend = selection.backend
        self._data_storage_folder = selection.data_root
        self._data_store = selection.data_store
        self._document_store = selection.document_store
        self._supabase_settings = selection.supabase_settings
        if hasattr(self, "_storage_state"):
            self._storage_state.backend = selection.backend
            self._storage_state.data_storage_folder = selection.data_root
            self._storage_state.supabase_settings = selection.supabase_settings
        if persist_settings:
            save_data_storage_backend(self._data_storage_backend)
            save_data_storage_folder(self._data_storage_folder)

    def _safe_load_bundle(self, data_store) -> DataLoadResult:
        try:
            return data_store.load_bundle()
        except Exception as exc:
            return DataLoadResult(
                bundle=TrackerDataBundleV3(),
                source="empty",
                warning=f"Could not load tracker data from {self._data_storage_backend}: {exc}",
            )

    def _ensure_supabase_realtime_client(self) -> SupabaseRealtimeClient:
        client = getattr(self, "_supabase_realtime_client", None)
        if isinstance(client, SupabaseRealtimeClient):
            return client
        client = SupabaseRealtimeClient(
            parent=self.window,
            on_state_row=self._on_supabase_realtime_state_row,
            on_status=self._on_supabase_realtime_status,
        )
        self._supabase_realtime_client = client
        return client

    def _sync_supabase_realtime_subscription(self) -> None:
        backend = normalize_data_storage_backend(
            self._data_storage_backend,
            default=BACKEND_LOCAL_SQLITE,
        )
        if backend != BACKEND_SUPABASE or not isinstance(self._data_store, SupabaseDataStore):
            self._shutdown_supabase_realtime_subscription()
            return
        settings = self._supabase_settings
        if not settings.configured:
            self._shutdown_supabase_realtime_subscription()
            return

        client = self._ensure_supabase_realtime_client()
        client.start(
            SupabaseRealtimeSubscription(
                url=settings.url,
                api_key=settings.api_key,
                schema=settings.schema,
                table=settings.tracker_table,
                app_id="erpermitsys",
            )
        )
        self._start_supabase_revision_polling()

    def _shutdown_supabase_realtime_subscription(self) -> None:
        client = getattr(self, "_supabase_realtime_client", None)
        if isinstance(client, SupabaseRealtimeClient):
            client.stop()
        self._stop_supabase_revision_polling()
        self._supabase_realtime_pending_refresh = False
        self._supabase_realtime_pending_notice_shown = False
        self._supabase_realtime_apply_running = False

    def _on_supabase_realtime_status(self, level: str, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        normalized_level = str(level or "info").strip().lower()
        self._state_streamer.record(
            "data.supabase_realtime_status",
            source="main_window",
            payload={
                "level": normalized_level,
                "message": text,
            },
        )

    def _on_supabase_realtime_state_row(self, state_row: dict[str, Any]) -> None:
        if normalize_data_storage_backend(self._data_storage_backend, default=BACKEND_LOCAL_SQLITE) != BACKEND_SUPABASE:
            return
        if not isinstance(self._data_store, SupabaseDataStore):
            return

        incoming_revision = self._coerce_revision_value(state_row.get("revision"), default=0)
        known_revision = self._coerce_revision_value(self._data_store.known_revision, default=-1)
        if incoming_revision <= known_revision:
            return
        if self._supabase_realtime_apply_running:
            return
        if self._has_local_editor_in_progress():
            self._supabase_realtime_pending_refresh = True
            if not self._supabase_realtime_pending_notice_shown:
                self._supabase_realtime_pending_notice_shown = True
                self._show_info_dialog(
                    "Remote Update Available",
                    "Supabase data changed on another computer.\n\n"
                    "Finish the current edit (save or cancel) and the latest data will be pulled in.",
                )
            return
        self._apply_remote_supabase_refresh(trigger="realtime")

    def _ensure_supabase_revision_poll_timer(self) -> QTimer:
        timer = getattr(self, "_supabase_revision_poll_timer", None)
        if isinstance(timer, QTimer):
            return timer
        timer = QTimer(self.window)
        timer.setInterval(_SUPABASE_REVISION_POLL_INTERVAL_MS)
        timer.timeout.connect(self._on_supabase_revision_poll_tick)
        self._supabase_revision_poll_timer = timer
        return timer

    def _start_supabase_revision_polling(self) -> None:
        timer = self._ensure_supabase_revision_poll_timer()
        if timer.isActive():
            return
        timer.start()

    def _stop_supabase_revision_polling(self) -> None:
        timer = getattr(self, "_supabase_revision_poll_timer", None)
        if isinstance(timer, QTimer):
            timer.stop()

    def _on_supabase_revision_poll_tick(self) -> None:
        if normalize_data_storage_backend(self._data_storage_backend, default=BACKEND_LOCAL_SQLITE) != BACKEND_SUPABASE:
            return
        if not isinstance(self._data_store, SupabaseDataStore):
            return
        if self._supabase_realtime_apply_running:
            return
        try:
            incoming_revision = self._data_store.fetch_remote_revision()
        except Exception:
            return
        if incoming_revision is None:
            return

        known_revision = self._coerce_revision_value(self._data_store.known_revision, default=-1)
        if int(incoming_revision) <= known_revision:
            return
        if self._has_local_editor_in_progress():
            self._supabase_realtime_pending_refresh = True
            if not self._supabase_realtime_pending_notice_shown:
                self._supabase_realtime_pending_notice_shown = True
                self._show_info_dialog(
                    "Remote Update Available",
                    "Supabase data changed on another computer.\n\n"
                    "Finish the current edit (save or cancel) and the latest data will be pulled in.",
                )
            return
        self._apply_remote_supabase_refresh(trigger="poll")

    def _has_local_editor_in_progress(self) -> bool:
        if str(getattr(self, "_active_inline_form_view", "") or "").strip():
            return True
        if bool(getattr(self, "_add_property_form_dirty", False)):
            return True
        if bool(getattr(self, "_add_permit_form_dirty", False)):
            return True
        if bool(getattr(self, "_admin_contact_dirty", False)):
            return True
        if bool(getattr(self, "_admin_jurisdiction_dirty", False)):
            return True
        if bool(getattr(self, "_template_dirty", False)):
            return True
        return False

    def _flush_pending_supabase_refresh_if_ready(self) -> None:
        if not bool(getattr(self, "_supabase_realtime_pending_refresh", False)):
            return
        if self._supabase_realtime_apply_running:
            return
        if self._has_local_editor_in_progress():
            return
        QTimer.singleShot(
            0,
            lambda: self._apply_remote_supabase_refresh(trigger="pending"),
        )

    def _apply_remote_supabase_refresh(self, *, trigger: str) -> None:
        if self._supabase_realtime_apply_running:
            return
        if normalize_data_storage_backend(self._data_storage_backend, default=BACKEND_LOCAL_SQLITE) != BACKEND_SUPABASE:
            return
        if not isinstance(self._data_store, SupabaseDataStore):
            return

        self._supabase_realtime_apply_running = True
        try:
            load_result = self._safe_load_bundle(self._data_store)
            if load_result.source == "empty" and load_result.warning:
                self._state_streamer.record(
                    "data.supabase_realtime_refresh_failed",
                    source="main_window",
                    payload={
                        "trigger": trigger,
                        "warning": load_result.warning,
                    },
                )
                return

            migrated = self._apply_tracker_bundle(load_result.bundle, refresh_ui=True)
            if migrated:
                self._persist_tracker_data(show_error_dialog=False)
            self._supabase_realtime_pending_refresh = False
            self._supabase_realtime_pending_notice_shown = False
            self._state_streamer.record(
                "data.supabase_realtime_refreshed",
                source="main_window",
                payload={
                    "trigger": trigger,
                    "source": load_result.source,
                },
            )
        finally:
            self._supabase_realtime_apply_running = False

    def _coerce_revision_value(self, value: object, *, default: int) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except Exception:
            return int(default)
        return int(parsed)

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

    def _supabase_merge_on_switch_enabled(self) -> bool:
        if hasattr(self, "_storage_state"):
            return bool(getattr(self._storage_state, "supabase_merge_on_switch", True))
        return bool(getattr(self, "_supabase_merge_on_switch", True))

    def _property_merge_tokens(self, property_record: PropertyRecord) -> tuple[str, ...]:
        tokens: list[str] = []
        parcel_token = normalize_parcel_id(property_record.parcel_id_norm or property_record.parcel_id)
        if parcel_token:
            tokens.append(f"parcel:{parcel_token}")
        address_token = " ".join(str(property_record.display_address or "").strip().casefold().split())
        if address_token:
            tokens.append(f"address:{address_token}")
        return tuple(tokens)

    def _merge_bundle_for_supabase_switch(
        self,
        target_bundle: TrackerDataBundleV3,
        current_bundle: TrackerDataBundleV3,
    ) -> tuple[TrackerDataBundleV3, dict[str, int | bool]]:
        merged_bundle = target_bundle.clone()
        incoming_bundle = current_bundle.clone()
        stats: dict[str, int | bool] = {
            "contacts_added": 0,
            "jurisdictions_added": 0,
            "properties_added": 0,
            "properties_duplicates_skipped": 0,
            "permits_added": 0,
            "document_templates_added": 0,
            "active_template_mappings_added": 0,
            "changed": False,
        }

        contact_ids = {
            str(record.contact_id).strip().casefold()
            for record in merged_bundle.contacts
            if str(record.contact_id).strip()
        }
        for record in incoming_bundle.contacts:
            contact_id = str(record.contact_id).strip()
            key = contact_id.casefold()
            if key and key in contact_ids:
                continue
            merged_bundle.contacts.append(ContactRecord.from_mapping(record.to_mapping()))
            if key:
                contact_ids.add(key)
            stats["contacts_added"] = int(stats["contacts_added"]) + 1

        jurisdiction_ids = {
            str(record.jurisdiction_id).strip().casefold()
            for record in merged_bundle.jurisdictions
            if str(record.jurisdiction_id).strip()
        }
        for record in incoming_bundle.jurisdictions:
            jurisdiction_id = str(record.jurisdiction_id).strip()
            key = jurisdiction_id.casefold()
            if key and key in jurisdiction_ids:
                continue
            merged_bundle.jurisdictions.append(JurisdictionRecord.from_mapping(record.to_mapping()))
            if key:
                jurisdiction_ids.add(key)
            stats["jurisdictions_added"] = int(stats["jurisdictions_added"]) + 1

        property_ids: dict[str, str] = {}
        property_tokens: dict[str, str] = {}
        property_aliases: dict[str, str] = {}
        for record in merged_bundle.properties:
            property_id = str(record.property_id).strip()
            if property_id:
                property_ids[property_id.casefold()] = property_id
                property_aliases[property_id] = property_id
            for token in self._property_merge_tokens(record):
                if property_id and token:
                    property_tokens.setdefault(token, property_id)

        for record in incoming_bundle.properties:
            incoming_property_id = str(record.property_id).strip()
            incoming_key = incoming_property_id.casefold()
            if incoming_key and incoming_key in property_ids:
                property_aliases[incoming_property_id] = property_ids[incoming_key]
                continue

            duplicate_property_id = ""
            for token in self._property_merge_tokens(record):
                existing_property_id = property_tokens.get(token, "")
                if existing_property_id:
                    duplicate_property_id = existing_property_id
                    break

            if duplicate_property_id:
                if incoming_property_id:
                    property_aliases[incoming_property_id] = duplicate_property_id
                stats["properties_duplicates_skipped"] = int(stats["properties_duplicates_skipped"]) + 1
                continue

            cloned = PropertyRecord.from_mapping(record.to_mapping())
            merged_bundle.properties.append(cloned)
            cloned_property_id = str(cloned.property_id).strip()
            if cloned_property_id:
                property_ids[cloned_property_id.casefold()] = cloned_property_id
                property_aliases[cloned_property_id] = cloned_property_id
                if incoming_property_id:
                    property_aliases[incoming_property_id] = cloned_property_id
            for token in self._property_merge_tokens(cloned):
                if token and cloned_property_id:
                    property_tokens.setdefault(token, cloned_property_id)
            stats["properties_added"] = int(stats["properties_added"]) + 1

        permit_ids = {
            str(record.permit_id).strip().casefold()
            for record in merged_bundle.permits
            if str(record.permit_id).strip()
        }
        for record in incoming_bundle.permits:
            permit_id = str(record.permit_id).strip()
            key = permit_id.casefold()
            if key and key in permit_ids:
                continue
            cloned = PermitRecord.from_mapping(record.to_mapping())
            mapped_property_id = property_aliases.get(str(cloned.property_id).strip(), "")
            if mapped_property_id:
                cloned.property_id = mapped_property_id
            merged_bundle.permits.append(cloned)
            if key:
                permit_ids.add(key)
            stats["permits_added"] = int(stats["permits_added"]) + 1

        template_ids = {
            str(record.template_id).strip().casefold()
            for record in merged_bundle.document_templates
            if str(record.template_id).strip()
        }
        for record in incoming_bundle.document_templates:
            template_id = str(record.template_id).strip()
            key = template_id.casefold()
            if key and key in template_ids:
                continue
            merged_bundle.document_templates.append(DocumentChecklistTemplate.from_mapping(record.to_mapping()))
            if key:
                template_ids.add(key)
            stats["document_templates_added"] = int(stats["document_templates_added"]) + 1

        for permit_type, template_id in incoming_bundle.active_document_template_ids.items():
            normalized_type = str(permit_type or "").strip()
            normalized_template_id = str(template_id or "").strip()
            if not normalized_type or not normalized_template_id:
                continue
            if normalized_type in merged_bundle.active_document_template_ids:
                continue
            if normalized_template_id.casefold() not in template_ids:
                continue
            merged_bundle.active_document_template_ids[normalized_type] = normalized_template_id
            stats["active_template_mappings_added"] = int(stats["active_template_mappings_added"]) + 1

        stats["changed"] = any(
            int(stats[key]) > 0
            for key in (
                "contacts_added",
                "jurisdictions_added",
                "properties_added",
                "permits_added",
                "document_templates_added",
                "active_template_mappings_added",
            )
        )
        return merged_bundle, stats

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
        saved_bundle = bundle
        save_mode = "direct"
        try:
            self._data_store.save_bundle(bundle)
        except SupabaseRevisionConflictError as exc:
            resolved_bundle = self._resolve_supabase_revision_conflict(
                local_bundle=bundle,
                conflict_error=exc,
            )
            if resolved_bundle is None:
                if show_error_dialog:
                    self._show_warning_dialog("Storage Error", f"Could not save tracker data.\n\n{exc}")
                self._state_streamer.record(
                    "data.save_failed",
                    source="main_window",
                    payload={
                        "backend": self._data_storage_backend,
                        "folder": str(self._data_storage_folder),
                        "error": str(exc),
                        "kind": "revision_conflict",
                    },
                )
                return False
            saved_bundle = resolved_bundle
            save_mode = "conflict_resolved"
        except Exception as exc:
            if show_error_dialog:
                self._show_warning_dialog("Storage Error", f"Could not save tracker data.\n\n{exc}")
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

        if save_mode == "conflict_resolved":
            if saved_bundle.to_payload() != bundle.to_payload():
                self._apply_tracker_bundle(saved_bundle, refresh_ui=True)

        self._state_streamer.record(
            "data.saved",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "path": str(self._data_store.storage_file_path),
                "mode": save_mode,
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
            },
        )
        self._flush_pending_supabase_refresh_if_ready()
        return True

    def _resolve_supabase_revision_conflict(
        self,
        *,
        local_bundle: TrackerDataBundleV3,
        conflict_error: SupabaseRevisionConflictError,
    ) -> TrackerDataBundleV3 | None:
        if normalize_data_storage_backend(self._data_storage_backend, default=BACKEND_LOCAL_SQLITE) != BACKEND_SUPABASE:
            return None
        if not isinstance(self._data_store, SupabaseDataStore):
            return None
        local_payload = local_bundle.to_payload()
        expected_revision = int(conflict_error.expected_revision)
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            remote_result = self._safe_load_bundle(self._data_store)
            if remote_result.source == "empty" and remote_result.warning:
                return None
            remote_bundle = remote_result.bundle
            remote_payload = remote_bundle.to_payload()

            if remote_payload == local_payload:
                self._state_streamer.record(
                    "data.supabase_conflict_resolved",
                    source="main_window",
                    payload={
                        "strategy": "remote_already_applied",
                        "expected_revision": expected_revision,
                        "attempt": attempt,
                    },
                )
                return remote_bundle

            merged_bundle, merge_stats = self._merge_bundle_for_supabase_switch(
                remote_bundle,
                local_bundle,
            )
            if merged_bundle.to_payload() == remote_payload:
                self._state_streamer.record(
                    "data.supabase_conflict_resolved",
                    source="main_window",
                    payload={
                        "strategy": "remote_preserved",
                        "expected_revision": expected_revision,
                        "attempt": attempt,
                        "merge_properties_added": int(merge_stats.get("properties_added", 0)),
                        "merge_property_duplicates_skipped": int(
                            merge_stats.get("properties_duplicates_skipped", 0)
                        ),
                    },
                )
                return remote_bundle

            try:
                self._data_store.save_bundle(merged_bundle)
                self._state_streamer.record(
                    "data.supabase_conflict_resolved",
                    source="main_window",
                    payload={
                        "strategy": "merge_then_save",
                        "expected_revision": expected_revision,
                        "attempt": attempt,
                        "merge_properties_added": int(merge_stats.get("properties_added", 0)),
                        "merge_property_duplicates_skipped": int(
                            merge_stats.get("properties_duplicates_skipped", 0)
                        ),
                    },
                )
                return merged_bundle
            except SupabaseRevisionConflictError as retry_conflict:
                expected_revision = int(retry_conflict.expected_revision)
                continue
            except Exception:
                return None
        return None

    def _on_export_json_backup_requested(self, requested_path: str) -> str:
        requested = str(requested_path or "").strip()
        if not requested:
            raise ValueError("Choose a destination file for JSON export.")

        target = Path(requested).expanduser()
        if not target.suffix:
            target = target.with_suffix(".json")
        if not target.is_absolute():
            target = self._data_storage_folder / target
        try:
            normalized_target = target.resolve()
        except Exception:
            normalized_target = target

        bundle = self._snapshot_tracker_bundle()
        saved_path = save_bundle_as_json_file(normalized_target, bundle)
        self._state_streamer.record(
            "data.json_exported",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "path": str(saved_path),
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
            },
        )
        self._show_info_dialog("JSON Export Complete", f"Tracker data exported to:\n{saved_path}")
        return str(saved_path)

    def _on_import_json_backup_requested(self, requested_path: str) -> bool:
        requested = str(requested_path or "").strip()
        if not requested:
            raise ValueError("Choose a JSON file to import.")

        source_path = Path(requested).expanduser()
        if not source_path.is_absolute():
            source_path = self._data_storage_folder / source_path
        try:
            normalized_source = source_path.resolve()
        except Exception:
            normalized_source = source_path

        if not normalized_source.exists() or not normalized_source.is_file():
            raise FileNotFoundError(f"JSON file not found: {normalized_source}")

        if not self._confirm_discard_inline_form_changes(action_label="Import JSON"):
            return False
        if not self._confirm_discard_admin_view_changes(action_label="Import JSON"):
            return False
        if not self._confirm_discard_template_changes(action_label="Import JSON"):
            return False

        confirm = self._confirm_dialog(
            "Import JSON Data",
            "Importing a JSON backup will replace current tracker data in this app session.\n\n"
            "Continue?",
            confirm_text="Import",
            cancel_text="Cancel",
        )
        if not confirm:
            return False

        load_result = load_bundle_from_json_file(normalized_source)
        if load_result.source == "empty":
            detail = load_result.warning.strip()
            if not detail:
                detail = "JSON file is empty or invalid."
            raise RuntimeError(detail)

        self._close_to_home_view()
        _ = self._apply_tracker_bundle(load_result.bundle, refresh_ui=True)
        if not self._persist_tracker_data(show_error_dialog=True):
            return False

        if load_result.warning.strip():
            self._show_data_storage_warning(load_result.warning)
        self._show_info_dialog("JSON Import Complete", f"Imported tracker data from:\n{normalized_source}")
        self._state_streamer.record(
            "data.json_imported",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "path": str(normalized_source),
                "source": load_result.source,
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

        target_selection = self._build_storage_selection(
            backend=self._data_storage_backend,
            folder=target_folder,
            supabase_settings=self._supabase_settings,
        )
        loaded_existing = False
        warning_lines = list(target_selection.warnings)
        current_bundle = self._snapshot_tracker_bundle()

        try:
            if target_selection.data_store.has_saved_data():
                load_result = target_selection.data_store.load_bundle()
                if load_result.source == "empty" and load_result.warning:
                    raise RuntimeError(
                        "The selected folder contains unreadable data. "
                        "Choose a different folder or repair the data file first."
                    )
                target_bundle = load_result.bundle
                loaded_existing = True
                if load_result.warning:
                    warning_lines.append(load_result.warning)
            else:
                target_bundle = current_bundle
                target_selection.data_store.save_bundle(current_bundle)
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

        self._apply_storage_selection(target_selection, persist_settings=True)

        self._close_to_home_view()
        migrated = self._apply_tracker_bundle(target_bundle, refresh_ui=True)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)
        self._sync_supabase_realtime_subscription()

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

        warning_message = "\n\n".join(line for line in warning_lines if line.strip())
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
                f"Data folder updated:\n{self._data_storage_folder}\n\n"
                "Current tracker data was copied to the new storage target.",
            )

        return str(self._data_storage_folder)

    def _on_data_storage_backend_changed(
        self,
        requested_backend: str,
        *,
        force: bool = False,
    ) -> str:
        target_backend = normalize_data_storage_backend(
            requested_backend,
            default=BACKEND_LOCAL_SQLITE,
        )
        current_backend = normalize_data_storage_backend(
            self._data_storage_backend,
            default=BACKEND_LOCAL_SQLITE,
        )
        if target_backend == current_backend and not force:
            return str(current_backend)

        target_selection = self._build_storage_selection(
            backend=target_backend,
            folder=self._data_storage_folder,
            supabase_settings=self._supabase_settings,
        )
        merge_on_supabase_switch = (
            target_backend == BACKEND_SUPABASE
            and current_backend == BACKEND_LOCAL_SQLITE
            and self._supabase_merge_on_switch_enabled()
        )
        loaded_existing = False
        copied_to_target = False
        merged_to_target = False
        merge_stats: dict[str, int | bool] = {
            "contacts_added": 0,
            "jurisdictions_added": 0,
            "properties_added": 0,
            "properties_duplicates_skipped": 0,
            "permits_added": 0,
            "document_templates_added": 0,
            "active_template_mappings_added": 0,
            "changed": False,
        }
        warning_lines = list(target_selection.warnings)
        current_bundle = self._snapshot_tracker_bundle()

        try:
            if target_selection.data_store.has_saved_data():
                load_result = target_selection.data_store.load_bundle()
                if load_result.source == "empty" and load_result.warning:
                    raise RuntimeError(
                        "The selected backend contains unreadable data. "
                        "Review backend settings and try again."
                    )
                target_bundle = load_result.bundle
                loaded_existing = True
                if load_result.warning:
                    warning_lines.append(load_result.warning)
                if merge_on_supabase_switch:
                    target_bundle, merge_stats = self._merge_bundle_for_supabase_switch(
                        target_bundle,
                        current_bundle,
                    )
                    if bool(merge_stats["changed"]):
                        target_selection.data_store.save_bundle(target_bundle)
                        merged_to_target = True
            else:
                if merge_on_supabase_switch:
                    target_bundle, merge_stats = self._merge_bundle_for_supabase_switch(
                        TrackerDataBundleV3(),
                        current_bundle,
                    )
                    if bool(merge_stats["changed"]):
                        target_selection.data_store.save_bundle(target_bundle)
                        copied_to_target = True
                        merged_to_target = True
                elif target_backend == BACKEND_SUPABASE and current_backend == BACKEND_LOCAL_SQLITE:
                    target_bundle = TrackerDataBundleV3()
                else:
                    target_bundle = current_bundle
                    target_selection.data_store.save_bundle(current_bundle)
                    copied_to_target = True
        except Exception as exc:
            self._show_warning_dialog(
                "Storage Backend Error",
                f"Could not switch storage backend.\n\n{exc}",
            )
            self._state_streamer.record(
                "data.backend_switch_failed",
                source="main_window",
                payload={
                    "from": str(current_backend),
                    "to": str(target_backend),
                    "error": str(exc),
                },
            )
            return str(current_backend)

        self._apply_storage_selection(target_selection, persist_settings=True)
        self._close_to_home_view()
        migrated = self._apply_tracker_bundle(target_bundle, refresh_ui=True)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)
        self._sync_supabase_realtime_subscription()

        self._state_streamer.record(
            "data.backend_switched",
            source="main_window",
                payload={
                    "backend": self._data_storage_backend,
                    "folder": str(self._data_storage_folder),
                    "loaded_existing": loaded_existing,
                    "merged_to_target": merged_to_target,
                    "copied_to_target": copied_to_target,
                    "merge_on_supabase_switch": merge_on_supabase_switch,
                    "merge_properties_added": int(merge_stats["properties_added"]),
                    "merge_property_duplicates_skipped": int(merge_stats["properties_duplicates_skipped"]),
                },
            )

        warning_message = "\n\n".join(line for line in warning_lines if line.strip())
        if warning_message:
            self._show_data_storage_warning(warning_message)
        elif merge_on_supabase_switch:
            if loaded_existing:
                if merged_to_target:
                    self._show_info_dialog(
                        "Storage Backend Updated",
                        "Loaded existing Supabase data and merged local data.\n\n"
                        f"Added new addresses: {int(merge_stats['properties_added'])}\n"
                        f"Skipped duplicate addresses: {int(merge_stats['properties_duplicates_skipped'])}",
                    )
                else:
                    self._show_info_dialog(
                        "Storage Backend Updated",
                        "Loaded existing Supabase data.\n\n"
                        "No new local records were merged.",
                    )
            elif copied_to_target:
                self._show_info_dialog(
                    "Storage Backend Updated",
                    "Supabase backend was empty, so local data was merged into Supabase.\n\n"
                    f"Added addresses: {int(merge_stats['properties_added'])}",
                )
            else:
                self._show_info_dialog(
                    "Storage Backend Updated",
                    "Switched to Supabase. No records were merged.",
                )
        elif loaded_existing:
            self._show_info_dialog(
                "Storage Backend Updated",
                f"Loaded existing data from backend: {self._data_storage_backend}",
            )
        elif copied_to_target:
            self._show_info_dialog(
                "Storage Backend Updated",
                f"Switched to backend: {self._data_storage_backend}\n\n"
                "Current tracker data was copied to the new backend.",
            )
        else:
            self._show_info_dialog(
                "Storage Backend Updated",
                f"Switched to backend: {self._data_storage_backend}\n\n"
                "No existing data was found in the selected backend.",
            )
        return str(self._data_storage_backend)

    def _on_supabase_settings_changed(self, settings_value: dict[str, object]) -> dict[str, str]:
        normalized = normalize_supabase_settings(settings_value)
        self._supabase_settings = save_supabase_settings(normalized)
        if hasattr(self, "_storage_state"):
            self._storage_state.supabase_settings = self._supabase_settings
        if self._data_storage_backend == BACKEND_SUPABASE:
            self._on_data_storage_backend_changed(BACKEND_SUPABASE, force=True)
        else:
            self._sync_supabase_realtime_subscription()
        self._state_streamer.record(
            "data.supabase_settings_changed",
            source="main_window",
            payload={
                "configured": self._supabase_settings.configured,
                "url_set": bool(self._supabase_settings.url),
                "table": self._supabase_settings.tracker_table,
                "bucket": self._supabase_settings.storage_bucket,
            },
        )
        return self._supabase_settings.to_mapping(redact_api_key=False)

    def _on_supabase_merge_on_switch_changed(self, enabled: bool) -> bool:
        normalized = save_supabase_merge_on_switch(enabled)
        if hasattr(self, "_storage_state"):
            self._storage_state.supabase_merge_on_switch = normalized
        self._supabase_merge_on_switch = normalized
        self._state_streamer.record(
            "data.supabase_merge_on_switch_changed",
            source="main_window",
            payload={
                "enabled": normalized,
            },
        )
        return normalized

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
                confirm_text="Update",
                cancel_text="Later",
            )
            if confirm:
                try:
                    self._download_and_apply_update(info)
                except Exception as exc:
                    self._set_update_settings_status("Update failed to start.", checking=False)
                    self._state_streamer.record(
                        "updates.apply_failed",
                        source="main_window",
                        payload={
                            "latest_version": info.latest_version,
                            "error": str(exc),
                        },
                    )
                    self._show_warning_dialog(
                        "Update Failed",
                        "The update could not be started.\n\n"
                        f"{exc}",
                    )
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

    @staticmethod
    def _format_download_size(value: int) -> str:
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(max(0, int(value)))
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{int(value)} B"

    def _show_update_download_dialog(self, *, asset_name: str) -> QProgressDialog:
        is_installer = str(asset_name or "").strip().lower().endswith(".exe")
        title = "Downloading Installer" if is_installer else "Downloading Update"
        label = "Downloading installer..." if is_installer else f"Downloading {asset_name}..."
        parent = self._settings_dialog if self._settings_dialog is not None else self.window
        dialog = QProgressDialog(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setCancelButton(None)
        dialog.setRange(0, 0)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(460)
        dialog.setValue(0)
        dialog.show()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        return dialog

    def _update_download_dialog_progress(
        self,
        *,
        dialog: QProgressDialog,
        asset_name: str,
        downloaded_bytes: int,
        total_bytes: int,
    ) -> None:
        if total_bytes > 0:
            if dialog.maximum() != total_bytes:
                dialog.setRange(0, total_bytes)
            dialog.setValue(min(max(0, downloaded_bytes), total_bytes))
            summary = (
                f"{self._format_download_size(downloaded_bytes)} / "
                f"{self._format_download_size(total_bytes)}"
            )
            dialog.setLabelText(f"Downloading {asset_name}...\n{summary}")
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"Downloading {asset_name}...")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _download_and_apply_update(self, info: GitHubUpdateInfo) -> None:
        asset = info.asset
        if asset is None:
            self._show_warning_dialog("Update Download Missing", "This release does not include a downloadable asset.")
            self._set_update_settings_status("Release asset missing.", checking=False)
            return

        self._set_update_settings_status(f"Downloading {asset.name}...", checking=True)
        download_dialog = self._show_update_download_dialog(asset_name=asset.name)

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            temp_root = Path(tempfile.mkdtemp(prefix="erpermitsys_update_"))
            archive_path = temp_root / asset.name
            downloaded_file = self._updater.download_asset(
                asset=asset,
                destination=archive_path,
                on_progress=lambda downloaded, total: self._update_download_dialog_progress(
                    dialog=download_dialog,
                    asset_name=asset.name,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                ),
            )
        except Exception as exc:
            self._set_update_settings_status("Download failed.", checking=False)
            self._show_warning_dialog("Update Download Failed", f"Could not download update:\n\n{exc}")
            return
        finally:
            download_dialog.close()
            download_dialog.deleteLater()
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

        lowered_name = downloaded_file.name.lower()
        is_installer = lowered_name.endswith(".exe")
        is_zip = lowered_name.endswith(".zip")
        if can_self_update_windows() and is_installer:
            started, launcher_detail = launch_windows_installer_updater(
                installer_path=downloaded_file,
                executable_path=Path(sys.executable).resolve(),
                app_pid=int(QApplication.applicationPid()),
            )
            if not started:
                self._set_update_settings_status("Installer launch failed.", checking=False)
                self._show_warning_dialog(
                    "Update Install Failed",
                    launcher_detail or "Could not launch installer update.",
                )
                return

            self._set_update_settings_status("Launching installer...", checking=False)
            self._close_requested_for_update = True
            if not self.close():
                self._close_requested_for_update = False
                self._set_update_settings_status("Close app to continue installer.", checking=False)
                self._show_warning_dialog(
                    "Close App Required",
                    "The installer was launched, but the app could not close automatically.\n\n"
                    "Please close the app to continue the update.",
                )
            return

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
            self._close_requested_for_update = True
            if not self.close():
                self._close_requested_for_update = False
                self._set_update_settings_status("Close app to continue update.", checking=False)
                self._show_warning_dialog(
                    "Close App Required",
                    "Updater launched, but the app could not close automatically.\n\n"
                    "Please close the app to continue the update.",
                )
            return

        if info.release_url:
            QDesktopServices.openUrl(QUrl(info.release_url))

        if is_packaged_runtime():
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "Automatic install is currently supported for Windows installer (.exe) "
                "or .zip release assets.\n"
                "Please install this release manually."
            )
        else:
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "You are running from source, so auto-replace is skipped.\n"
                "Use the GitHub release page to deploy your next build."
            )
        self._show_info_dialog("Manual Update Required", guidance)

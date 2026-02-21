from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from erpermitsys.app.db_debug import db_debug
from erpermitsys.app.tracker_models import TrackerDataBundleV3


BACKEND_LOCAL_SQLITE = "local_sqlite"
BACKEND_LOCAL_JSON = "local_json"  # legacy backend token kept for compatibility
BACKEND_SUPABASE = "supabase"
DEFAULT_DATA_FILE_NAME = "permit_tracker_data.json"
DEFAULT_SQLITE_FILE_NAME = "permit_tracker_data.sqlite3"
_SCHEMA_VERSION = 3
_APP_ID = "erpermitsys"
_DEFAULT_SUPABASE_SCHEMA = "public"
_DEFAULT_SUPABASE_TABLE = "erpermitsys_state"
_DEFAULT_SUPABASE_TIMEOUT_SECONDS = 8.0
_SUPABASE_SNAPSHOT_RPC = "erpermitsys_save_snapshot"
_SUPABASE_CONTACTS_TABLE = "erpermitsys_contacts"
_SUPABASE_JURISDICTIONS_TABLE = "erpermitsys_jurisdictions"
_SUPABASE_PROPERTIES_TABLE = "erpermitsys_properties"
_SUPABASE_PERMITS_TABLE = "erpermitsys_permits"
_SUPABASE_DOCUMENT_TEMPLATES_TABLE = "erpermitsys_document_templates"
_SUPABASE_ACTIVE_TEMPLATE_MAP_TABLE = "erpermitsys_active_document_templates"
_LOCAL_SQLITE_TABLE = "app_state"


@dataclass(frozen=True, slots=True)
class DataLoadResult:
    bundle: TrackerDataBundleV3
    source: str = "primary"
    warning: str = ""


class SupabaseRevisionConflictError(RuntimeError):
    """Raised when a conflict-safe Supabase save loses a revision race."""

    def __init__(
        self,
        *,
        expected_revision: int,
        message: str = "",
    ) -> None:
        detail = message.strip() if message.strip() else "Supabase state changed on another client."
        super().__init__(detail)
        self.expected_revision = max(0, int(expected_revision))


@dataclass(frozen=True, slots=True)
class SupabaseDataStoreConfig:
    url: str = ""
    api_key: str = ""
    schema: str = _DEFAULT_SUPABASE_SCHEMA
    table: str = _DEFAULT_SUPABASE_TABLE
    timeout_seconds: float = _DEFAULT_SUPABASE_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> SupabaseDataStoreConfig:
        raw = value or {}
        url = str(raw.get("url", "") or "").strip().rstrip("/")
        api_key = str(raw.get("api_key", "") or "").strip()
        schema = str(raw.get("schema", "") or "").strip() or _DEFAULT_SUPABASE_SCHEMA
        table = str(raw.get("table", "") or "").strip() or _DEFAULT_SUPABASE_TABLE
        timeout_raw = raw.get("timeout_seconds", _DEFAULT_SUPABASE_TIMEOUT_SECONDS)
        try:
            timeout_seconds = float(timeout_raw)
        except Exception:
            timeout_seconds = _DEFAULT_SUPABASE_TIMEOUT_SECONDS
        timeout_seconds = max(1.0, timeout_seconds)
        return cls(
            url=url,
            api_key=api_key,
            schema=schema,
            table=table,
            timeout_seconds=timeout_seconds,
        )


class TrackerDataStore(Protocol):
    backend: str
    data_root: Path

    @property
    def storage_file_path(self) -> Path:
        raise NotImplementedError

    def has_saved_data(self) -> bool:
        raise NotImplementedError

    def load_bundle(self) -> DataLoadResult:
        raise NotImplementedError

    def save_bundle(self, bundle: TrackerDataBundleV3) -> None:
        raise NotImplementedError


class LocalJsonDataStore:
    backend = BACKEND_LOCAL_JSON

    def __init__(
        self,
        data_root: Path | str,
        *,
        data_file_name: str = DEFAULT_DATA_FILE_NAME,
    ) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._data_file_name = data_file_name

    @property
    def storage_file_path(self) -> Path:
        return self.data_root / self._data_file_name

    @property
    def backup_file_path(self) -> Path:
        storage_file = self.storage_file_path
        return storage_file.with_suffix(f"{storage_file.suffix}.bak")

    def has_saved_data(self) -> bool:
        return self.storage_file_path.exists() and self.storage_file_path.is_file()

    def load_bundle(self) -> DataLoadResult:
        primary_path = self.storage_file_path
        if not primary_path.exists():
            return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty")

        try:
            bundle = self._read_bundle(primary_path)
            return DataLoadResult(bundle=bundle, source="primary")
        except Exception as primary_error:
            backup_path = self.backup_file_path
            if backup_path.exists() and backup_path.is_file():
                try:
                    bundle = self._read_bundle(backup_path)
                    warning = (
                        "Primary data file could not be read; recovered from backup copy."
                    )
                    return DataLoadResult(bundle=bundle, source="backup", warning=warning)
                except Exception as backup_error:
                    warning = (
                        "Primary and backup data files could not be read. "
                        f"Primary error: {primary_error}. Backup error: {backup_error}."
                    )
                    return DataLoadResult(
                        bundle=TrackerDataBundleV3(),
                        source="empty",
                        warning=warning,
                    )
            warning = f"Primary data file could not be read: {primary_error}."
            return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty", warning=warning)

    def save_bundle(self, bundle: TrackerDataBundleV3) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        payload = _build_storage_payload(bundle, backend=self.backend)
        self._write_atomic_json(payload)

    def _read_bundle(self, path: Path) -> TrackerDataBundleV3:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _bundle_from_storage_payload(raw)

    def _write_atomic_json(self, payload: dict[str, object]) -> None:
        target_path = self.storage_file_path
        backup_path = self.backup_file_path

        if target_path.exists():
            try:
                shutil.copy2(target_path, backup_path)
            except Exception:
                pass

        fd, temp_path = tempfile.mkstemp(
            prefix=f"{target_path.stem}.",
            suffix=".tmp",
            dir=str(self.data_root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise


class LocalSqliteDataStore:
    backend = BACKEND_LOCAL_SQLITE

    def __init__(
        self,
        data_root: Path | str,
        *,
        sqlite_file_name: str = DEFAULT_SQLITE_FILE_NAME,
        legacy_json_file_name: str = DEFAULT_DATA_FILE_NAME,
    ) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._sqlite_file_name = str(sqlite_file_name or DEFAULT_SQLITE_FILE_NAME).strip()
        if not self._sqlite_file_name:
            self._sqlite_file_name = DEFAULT_SQLITE_FILE_NAME
        self._legacy_json_store = LocalJsonDataStore(
            self.data_root,
            data_file_name=legacy_json_file_name,
        )

    @property
    def storage_file_path(self) -> Path:
        return self.data_root / self._sqlite_file_name

    @property
    def legacy_json_file_path(self) -> Path:
        return self._legacy_json_store.storage_file_path

    def has_saved_data(self) -> bool:
        sqlite_path = self.storage_file_path
        if sqlite_path.exists() and sqlite_path.is_file():
            try:
                row = self._fetch_payload_json()
                if row is not None:
                    return True
            except Exception:
                return True
        return self._legacy_json_store.has_saved_data()

    def load_bundle(self) -> DataLoadResult:
        sqlite_path = self.storage_file_path
        fallback_warning = ""
        started_at = perf_counter()

        if sqlite_path.exists() and sqlite_path.is_file():
            try:
                payload_json = self._fetch_payload_json()
            except Exception as exc:
                fallback_warning = f"SQLite data file could not be read: {exc}."
                db_debug(
                    "sqlite.load.fetch_error",
                    path=str(sqlite_path),
                    error=str(exc),
                )
                payload_json = None
            if payload_json is not None:
                try:
                    payload = json.loads(payload_json)
                    bundle = _bundle_from_storage_payload(payload)
                    db_debug(
                        "sqlite.load",
                        path=str(sqlite_path),
                        source="primary",
                        duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
                    )
                    return DataLoadResult(bundle=bundle, source="primary")
                except Exception as exc:
                    warning = f"SQLite state payload is invalid: {exc}"
                    db_debug(
                        "sqlite.load.payload_invalid",
                        path=str(sqlite_path),
                        error=str(exc),
                    )
                    return DataLoadResult(
                        bundle=TrackerDataBundleV3(),
                        source="empty",
                        warning=warning,
                    )

        result = self._load_from_legacy_json(fallback_warning=fallback_warning)
        db_debug(
            "sqlite.load",
            path=str(sqlite_path),
            source=result.source,
            warning=bool(result.warning),
            duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
        )
        return result

    def save_bundle(self, bundle: TrackerDataBundleV3) -> None:
        started_at = perf_counter()
        self.data_root.mkdir(parents=True, exist_ok=True)
        payload = _build_storage_payload(bundle, backend=self.backend)
        payload_json = json.dumps(payload, ensure_ascii=False)
        saved_at_utc = str(payload.get("savedAtUtc", "") or "")

        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    (
                        f"insert into {_LOCAL_SQLITE_TABLE} "
                        "(app_id, schema_version, backend, saved_at_utc, payload_json) "
                        "values (?, ?, ?, ?, ?) "
                        "on conflict(app_id) do update set "
                        "schema_version = excluded.schema_version, "
                        "backend = excluded.backend, "
                        "saved_at_utc = excluded.saved_at_utc, "
                        "payload_json = excluded.payload_json"
                    ),
                    (_APP_ID, _SCHEMA_VERSION, self.backend, saved_at_utc, payload_json),
                )
                connection.commit()
        except Exception as exc:
            db_debug(
                "sqlite.save.error",
                path=str(self.storage_file_path),
                bytes=len(payload_json.encode("utf-8")),
                error=str(exc),
            )
            raise
        db_debug(
            "sqlite.save",
            path=str(self.storage_file_path),
            bytes=len(payload_json.encode("utf-8")),
            duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
        )

    def _load_from_legacy_json(self, *, fallback_warning: str = "") -> DataLoadResult:
        if not self._legacy_json_store.has_saved_data():
            warning = fallback_warning.strip()
            if warning:
                return DataLoadResult(
                    bundle=TrackerDataBundleV3(),
                    source="empty",
                    warning=warning,
                )
            return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty")

        legacy_result = self._legacy_json_store.load_bundle()
        if legacy_result.source == "empty" and legacy_result.warning:
            warning_parts = [fallback_warning.strip(), legacy_result.warning.strip()]
            warning = " ".join(part for part in warning_parts if part)
            return DataLoadResult(
                bundle=TrackerDataBundleV3(),
                source="empty",
                warning=warning,
            )

        warning_parts: list[str] = []
        if fallback_warning.strip():
            warning_parts.append(fallback_warning.strip())
        warning_parts.append(
            (
                "Loaded tracker data from legacy JSON and migrated it to local SQLite."
                if legacy_result.source in {"primary", "backup"}
                else "Loaded tracker data from legacy JSON."
            )
        )
        if legacy_result.warning.strip():
            warning_parts.append(legacy_result.warning.strip())

        try:
            self.save_bundle(legacy_result.bundle)
        except Exception as exc:
            warning_parts.append(f"Could not persist migration to SQLite: {exc}")
            db_debug(
                "sqlite.migrate_json.error",
                json_path=str(self.legacy_json_file_path),
                sqlite_path=str(self.storage_file_path),
                error=str(exc),
            )
        else:
            db_debug(
                "sqlite.migrate_json",
                json_path=str(self.legacy_json_file_path),
                sqlite_path=str(self.storage_file_path),
                source=legacy_result.source,
            )

        warning = " ".join(part for part in warning_parts if part).strip()
        return DataLoadResult(
            bundle=legacy_result.bundle,
            source="migrated_json",
            warning=warning,
        )

    def _connect(self) -> sqlite3.Connection:
        self.data_root.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.storage_file_path), timeout=4.0)

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            create table if not exists {_LOCAL_SQLITE_TABLE} (
                app_id text primary key,
                schema_version integer not null default {_SCHEMA_VERSION},
                backend text not null,
                saved_at_utc text not null,
                payload_json text not null
            )
            """
        )

    def _fetch_payload_json(self) -> str | None:
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                f"select payload_json from {_LOCAL_SQLITE_TABLE} where app_id = ? limit 1",
                (_APP_ID,),
            ).fetchone()
        if row is None:
            return None
        value = row[0]
        return str(value) if isinstance(value, str) else None


def load_bundle_from_json_file(path: Path | str) -> DataLoadResult:
    normalized = _normalize_path(Path(path))
    store = LocalJsonDataStore(normalized.parent, data_file_name=normalized.name)
    return store.load_bundle()


def save_bundle_as_json_file(path: Path | str, bundle: TrackerDataBundleV3) -> Path:
    normalized = _normalize_path(Path(path))
    store = LocalJsonDataStore(normalized.parent, data_file_name=normalized.name)
    store.save_bundle(bundle)
    return normalized


class SupabaseDataStore:
    backend = BACKEND_SUPABASE

    def __init__(
        self,
        data_root: Path | str,
        *,
        config: SupabaseDataStoreConfig | None = None,
    ) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._config = config or SupabaseDataStoreConfig()
        self._known_revision = -1
        self._client_id = f"desktop-{uuid4().hex[:12]}"

    @property
    def storage_file_path(self) -> Path:
        return self.data_root / ".supabase-state.json"

    @property
    def known_revision(self) -> int:
        return max(-1, int(self._known_revision))

    @property
    def client_id(self) -> str:
        return self._client_id

    def has_saved_data(self) -> bool:
        return self._fetch_state_row() is not None

    def load_bundle(self) -> DataLoadResult:
        state_row = self._fetch_state_row()
        if state_row is None:
            self._known_revision = -1
            db_debug(
                "supabase.load",
                table=self._config.table,
                source="empty",
                revision=self._known_revision,
            )
            return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty")
        self._known_revision = _coerce_non_negative_int(state_row.get("revision"), default=0)
        try:
            payload = self._load_payload_from_tables()
            bundle = TrackerDataBundleV3.from_payload(payload)
            legacy_payload = state_row.get("payload")
            if _bundle_has_content(bundle) is False and isinstance(legacy_payload, dict):
                legacy_bundle = TrackerDataBundleV3.from_payload(legacy_payload)
                if _bundle_has_content(legacy_bundle):
                    warning = (
                        "Loaded fallback legacy payload row because table-backed Supabase tables "
                        "are empty for this app_id."
                    )
                    db_debug(
                        "supabase.load.fallback_legacy_payload",
                        table=self._config.table,
                        revision=self._known_revision,
                        reason="tables_empty",
                    )
                    return DataLoadResult(bundle=legacy_bundle, source="primary", warning=warning)
            db_debug(
                "supabase.load",
                table=self._config.table,
                source="tables",
                revision=self._known_revision,
            )
            return DataLoadResult(bundle=bundle, source="primary")
        except Exception as exc:
            legacy_payload = state_row.get("payload")
            if isinstance(legacy_payload, dict):
                try:
                    bundle = TrackerDataBundleV3.from_payload(legacy_payload)
                    warning = (
                        "Loaded fallback legacy payload row because table-backed Supabase state "
                        f"was unavailable: {exc}"
                    )
                    db_debug(
                        "supabase.load.fallback_legacy_payload",
                        table=self._config.table,
                        revision=self._known_revision,
                        error=str(exc),
                    )
                    return DataLoadResult(bundle=bundle, source="primary", warning=warning)
                except Exception as legacy_exc:
                    warning = f"Supabase legacy payload is invalid: {legacy_exc}"
                    db_debug(
                        "supabase.load.payload_invalid",
                        table=self._config.table,
                        revision=self._known_revision,
                        error=str(legacy_exc),
                    )
                    return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty", warning=warning)

            warning = (
                "Supabase state row exists, but table-backed data could not be loaded and no legacy payload "
                f"is available: {exc}"
            )
            db_debug(
                "supabase.load.payload_missing",
                table=self._config.table,
                revision=self._known_revision,
                error=str(exc),
            )
            return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty", warning=warning)

    def save_bundle(self, bundle: TrackerDataBundleV3) -> None:
        config = self._require_config()
        started_at = perf_counter()

        if self._known_revision < 0:
            existing_row = self._fetch_state_row()
            if existing_row is not None:
                self._known_revision = _coerce_non_negative_int(existing_row.get("revision"), default=0)
            else:
                self._known_revision = 0

        expected_revision = max(0, int(self._known_revision))
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = bundle.to_payload()

        if self._save_bundle_via_rpc(
            payload=payload,
            expected_revision=expected_revision,
            saved_at_utc=now_iso,
        ):
            db_debug(
                "supabase.save",
                table=config.table,
                mode="snapshot_rpc",
                expected_revision=expected_revision,
                revision=self._known_revision,
                duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
            )
            return

        self._save_bundle_legacy_payload(payload=payload, expected_revision=expected_revision, saved_at_utc=now_iso)
        db_debug(
            "supabase.save",
            table=config.table,
            mode="legacy_payload",
            expected_revision=expected_revision,
            revision=self._known_revision,
            duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
        )

    def _load_payload_from_tables(self) -> dict[str, Any]:
        contacts_rows = self._fetch_table_rows(
            table=_SUPABASE_CONTACTS_TABLE,
            select="contact_id,name,numbers,emails,roles,contact_methods,list_color",
            order="contact_id.asc",
        )
        jurisdictions_rows = self._fetch_table_rows(
            table=_SUPABASE_JURISDICTIONS_TABLE,
            select=(
                "jurisdiction_id,name,jurisdiction_type,parent_county,portal_urls,contact_ids,"
                "portal_vendor,notes,list_color"
            ),
            order="jurisdiction_id.asc",
        )
        properties_rows = self._fetch_table_rows(
            table=_SUPABASE_PROPERTIES_TABLE,
            select=(
                "property_id,display_address,parcel_id,parcel_id_norm,jurisdiction_id,contact_ids,"
                "list_color,tags,notes"
            ),
            order="property_id.asc",
        )
        permits_rows = self._fetch_table_rows(
            table=_SUPABASE_PERMITS_TABLE,
            select=(
                "permit_id,property_id,permit_type,permit_number,status,next_action_text,next_action_due,"
                "request_date,application_date,issued_date,final_date,completion_date,parties,events,"
                "document_slots,document_folders,documents"
            ),
            order="permit_id.asc",
        )
        templates_rows = self._fetch_table_rows(
            table=_SUPABASE_DOCUMENT_TEMPLATES_TABLE,
            select="template_id,name,permit_type,slots,notes",
            order="template_id.asc",
        )
        template_map_rows = self._fetch_table_rows(
            table=_SUPABASE_ACTIVE_TEMPLATE_MAP_TABLE,
            select="permit_type,template_id",
            order="permit_type.asc",
        )

        contacts: list[dict[str, Any]] = []
        for row in contacts_rows:
            contact_id = self._row_text(row, "contact_id")
            if not contact_id:
                continue
            contacts.append(
                {
                    "contact_id": contact_id,
                    "name": self._row_text(row, "name"),
                    "numbers": self._row_json_array(row, "numbers"),
                    "emails": self._row_json_array(row, "emails"),
                    "roles": self._row_json_array(row, "roles"),
                    "contact_methods": self._row_json_array(row, "contact_methods"),
                    "list_color": self._row_text(row, "list_color"),
                }
            )

        jurisdictions: list[dict[str, Any]] = []
        for row in jurisdictions_rows:
            jurisdiction_id = self._row_text(row, "jurisdiction_id")
            if not jurisdiction_id:
                continue
            jurisdictions.append(
                {
                    "jurisdiction_id": jurisdiction_id,
                    "name": self._row_text(row, "name"),
                    "jurisdiction_type": self._row_text(row, "jurisdiction_type"),
                    "parent_county": self._row_text(row, "parent_county"),
                    "portal_urls": self._row_json_array(row, "portal_urls"),
                    "contact_ids": self._row_json_array(row, "contact_ids"),
                    "portal_vendor": self._row_text(row, "portal_vendor"),
                    "notes": self._row_text(row, "notes"),
                    "list_color": self._row_text(row, "list_color"),
                }
            )

        properties: list[dict[str, Any]] = []
        for row in properties_rows:
            property_id = self._row_text(row, "property_id")
            if not property_id:
                continue
            properties.append(
                {
                    "property_id": property_id,
                    "display_address": self._row_text(row, "display_address"),
                    "parcel_id": self._row_text(row, "parcel_id"),
                    "parcel_id_norm": self._row_text(row, "parcel_id_norm"),
                    "jurisdiction_id": self._row_text(row, "jurisdiction_id"),
                    "contact_ids": self._row_json_array(row, "contact_ids"),
                    "list_color": self._row_text(row, "list_color"),
                    "tags": self._row_json_array(row, "tags"),
                    "notes": self._row_text(row, "notes"),
                }
            )

        permits: list[dict[str, Any]] = []
        for row in permits_rows:
            permit_id = self._row_text(row, "permit_id")
            if not permit_id:
                continue
            permits.append(
                {
                    "permit_id": permit_id,
                    "property_id": self._row_text(row, "property_id"),
                    "permit_type": self._row_text(row, "permit_type"),
                    "permit_number": self._row_text(row, "permit_number"),
                    "status": self._row_text(row, "status"),
                    "next_action_text": self._row_text(row, "next_action_text"),
                    "next_action_due": self._row_text(row, "next_action_due"),
                    "request_date": self._row_text(row, "request_date"),
                    "application_date": self._row_text(row, "application_date"),
                    "issued_date": self._row_text(row, "issued_date"),
                    "final_date": self._row_text(row, "final_date"),
                    "completion_date": self._row_text(row, "completion_date"),
                    "parties": self._row_json_array(row, "parties"),
                    "events": self._row_json_array(row, "events"),
                    "document_slots": self._row_json_array(row, "document_slots"),
                    "document_folders": self._row_json_array(row, "document_folders"),
                    "documents": self._row_json_array(row, "documents"),
                }
            )

        document_templates: list[dict[str, Any]] = []
        for row in templates_rows:
            template_id = self._row_text(row, "template_id")
            if not template_id:
                continue
            document_templates.append(
                {
                    "template_id": template_id,
                    "name": self._row_text(row, "name"),
                    "permit_type": self._row_text(row, "permit_type"),
                    "slots": self._row_json_array(row, "slots"),
                    "notes": self._row_text(row, "notes"),
                }
            )

        active_document_template_ids: dict[str, str] = {}
        for row in template_map_rows:
            permit_type = self._row_text(row, "permit_type")
            template_id = self._row_text(row, "template_id")
            if not permit_type or not template_id:
                continue
            active_document_template_ids[permit_type] = template_id

        return {
            "contacts": contacts,
            "jurisdictions": jurisdictions,
            "properties": properties,
            "permits": permits,
            "document_templates": document_templates,
            "active_document_template_ids": active_document_template_ids,
        }

    @staticmethod
    def _row_text(row: dict[str, Any], key: str) -> str:
        value = row.get(key, "")
        return str(value or "").strip()

    @staticmethod
    def _row_json_array(row: dict[str, Any], key: str) -> list[Any]:
        value = row.get(key)
        if isinstance(value, list):
            return value
        return []

    def _save_bundle_via_rpc(
        self,
        *,
        payload: dict[str, Any],
        expected_revision: int,
        saved_at_utc: str,
    ) -> bool:
        rpc_path = f"/rest/v1/rpc/{quote(_SUPABASE_SNAPSHOT_RPC, safe='_')}"
        rpc_payload = {
            "p_app_id": _APP_ID,
            "p_expected_revision": int(expected_revision),
            "p_schema_version": int(_SCHEMA_VERSION),
            "p_saved_at_utc": saved_at_utc,
            "p_updated_by": self._client_id,
            "p_contacts": payload.get("contacts", []),
            "p_jurisdictions": payload.get("jurisdictions", []),
            "p_properties": payload.get("properties", []),
            "p_permits": payload.get("permits", []),
            "p_document_templates": payload.get("document_templates", []),
            "p_active_document_template_ids": payload.get("active_document_template_ids", {}),
        }
        try:
            raw = self._request_json(
                method="POST",
                path=rpc_path,
                payload=rpc_payload,
                prefer="",
                expect_json=True,
            )
        except RuntimeError as exc:
            if _is_missing_rpc_function_error(exc):
                db_debug(
                    "supabase.save.snapshot_rpc_missing",
                    table=self._config.table,
                    rpc=_SUPABASE_SNAPSHOT_RPC,
                )
                return False
            raise

        result = raw
        if isinstance(raw, list):
            if raw and isinstance(raw[0], dict):
                result = raw[0]
            else:
                result = None
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Supabase snapshot RPC returned an invalid response type: {type(raw).__name__}"
            )

        conflict = bool(result.get("conflict"))
        revision = _coerce_non_negative_int(result.get("revision"), default=expected_revision)
        applied = bool(result.get("applied"))
        self._known_revision = revision
        if conflict or not applied:
            db_debug(
                "supabase.save.conflict",
                table=self._config.table,
                expected_revision=expected_revision,
                known_revision=self._known_revision,
                via="snapshot_rpc",
            )
            raise SupabaseRevisionConflictError(expected_revision=expected_revision)
        return True

    def _save_bundle_legacy_payload(
        self,
        *,
        payload: dict[str, Any],
        expected_revision: int,
        saved_at_utc: str,
    ) -> None:
        config = self._require_config()
        table = quote(config.table, safe="_")
        app_id = quote(_APP_ID, safe="_-")

        if expected_revision <= 0:
            insert_rows = [
                {
                    "app_id": _APP_ID,
                    "schema_version": _SCHEMA_VERSION,
                    "backend": self.backend,
                    "saved_at_utc": saved_at_utc,
                    "updated_at": saved_at_utc,
                    "updated_by": self._client_id,
                    "revision": 1,
                    "payload": payload,
                }
            ]
            try:
                inserted = self._request_json(
                    method="POST",
                    path=f"/rest/v1/{table}",
                    query="?select=revision,saved_at_utc,updated_by",
                    payload=insert_rows,
                    prefer="return=representation",
                    expect_json=True,
                )
                self._known_revision = _extract_saved_revision(inserted, default=1)
                return
            except RuntimeError as exc:
                if not _is_postgrest_conflict_error(exc):
                    raise
                existing_row = self._fetch_state_row()
                if existing_row is None:
                    raise SupabaseRevisionConflictError(
                        expected_revision=0,
                        message=f"Could not insert initial Supabase state: {exc}",
                    ) from exc
                self._known_revision = _coerce_non_negative_int(existing_row.get("revision"), default=0)
                expected_revision = max(0, int(self._known_revision))

        patched = self._request_json(
            method="PATCH",
            path=f"/rest/v1/{table}",
            query=(
                "?select=revision,saved_at_utc,updated_by"
                f"&app_id=eq.{app_id}"
                f"&revision=eq.{expected_revision}"
            ),
            payload={
                "schema_version": _SCHEMA_VERSION,
                "backend": self.backend,
                "saved_at_utc": saved_at_utc,
                "updated_at": saved_at_utc,
                "updated_by": self._client_id,
                "revision": expected_revision + 1,
                "payload": payload,
            },
            prefer="return=representation",
            expect_json=True,
        )

        if not isinstance(patched, list) or not patched:
            fresh_row = self._fetch_state_row()
            if fresh_row is not None:
                self._known_revision = _coerce_non_negative_int(fresh_row.get("revision"), default=expected_revision)
            raise SupabaseRevisionConflictError(expected_revision=expected_revision)

        self._known_revision = _extract_saved_revision(patched, default=expected_revision + 1)

    def _require_config(self) -> SupabaseDataStoreConfig:
        if self._config.configured:
            return self._config
        raise RuntimeError(
            "Supabase backend is selected, but Supabase URL or API key is missing. "
            "Set them in Settings > General Settings > Data backend."
        )

    def _fetch_state_row(self) -> dict[str, Any] | None:
        config = self._require_config()
        table = quote(config.table, safe="_")
        app_id = quote(_APP_ID, safe="_-")
        rows = self._request_json(
            method="GET",
            path=f"/rest/v1/{table}",
            query=(
                "?select=app_id,payload,saved_at_utc,updated_at,updated_by,revision"
                f"&app_id=eq.{app_id}&limit=1"
            ),
            payload=None,
            prefer="",
            expect_json=True,
        )
        if not isinstance(rows, list) or not rows:
            return None
        first = rows[0]
        if isinstance(first, dict):
            return first
        return None

    def _fetch_table_rows(
        self,
        *,
        table: str,
        select: str,
        order: str = "",
    ) -> list[dict[str, Any]]:
        safe_table = quote(table, safe="_")
        app_id = quote(_APP_ID, safe="_-")
        query = f"?select={select}&app_id=eq.{app_id}"
        if order:
            query = f"{query}&order={order}"
        rows = self._request_json(
            method="GET",
            path=f"/rest/v1/{safe_table}",
            query=query,
            payload=None,
            prefer="",
            expect_json=True,
        )
        if not isinstance(rows, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in rows:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        query: str = "",
        payload: Any | None = None,
        prefer: str = "",
        expect_json: bool,
    ) -> Any:
        config = self._require_config()
        base = config.url.rstrip("/")
        request_url = f"{base}{path}{query}"
        request_data: bytes | None = None
        if payload is not None:
            request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        db_debug(
            "supabase.request",
            method=method.upper(),
            path=path,
            query_present=bool(query),
            payload_bytes=len(request_data) if request_data is not None else 0,
            expect_json=expect_json,
        )
        headers = {
            "apikey": config.api_key,
            "Authorization": f"Bearer {config.api_key}",
        }
        if config.schema:
            headers["Accept-Profile"] = config.schema
            headers["Content-Profile"] = config.schema
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        request = Request(request_url, data=request_data, headers=headers, method=method.upper())

        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                status_code = int(response.getcode() or 0)
                body = response.read()
            db_debug(
                "supabase.response",
                method=method.upper(),
                path=path,
                status=status_code,
                body_bytes=len(body),
                expect_json=expect_json,
            )
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            detail = f"{exc.code} {exc.reason}"
            if body:
                detail = f"{detail}: {body}"
            db_debug(
                "supabase.request.error",
                method=method.upper(),
                path=path,
                code=int(exc.code),
                reason=str(exc.reason),
            )
            raise RuntimeError(f"Supabase request failed for {path}: {detail}") from exc
        except URLError as exc:
            db_debug(
                "supabase.request.error",
                method=method.upper(),
                path=path,
                error=str(exc),
            )
            raise RuntimeError(f"Supabase request failed for {path}: {exc}") from exc

        if not expect_json:
            return body
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            db_debug(
                "supabase.response.parse_error",
                method=method.upper(),
                path=path,
                body_bytes=len(body),
                error=str(exc),
            )
            raise RuntimeError(
                f"Supabase returned non-JSON payload for {path} ({len(body)} bytes)."
            ) from exc


def _bundle_from_storage_payload(raw: object) -> TrackerDataBundleV3:
    if not isinstance(raw, dict):
        raise ValueError("Storage payload must be a JSON object.")
    data_payload = raw.get("data")
    if isinstance(data_payload, dict):
        return TrackerDataBundleV3.from_payload(data_payload)
    return TrackerDataBundleV3.from_payload(raw)


def _build_storage_payload(
    bundle: TrackerDataBundleV3,
    *,
    backend: str,
) -> dict[str, object]:
    return {
        "app": _APP_ID,
        "schemaVersion": _SCHEMA_VERSION,
        "backend": str(backend or "").strip(),
        "savedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": bundle.to_payload(),
    }


def _coerce_non_negative_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        return max(0, int(default))
    return max(0, parsed)


def _extract_saved_revision(value: object, *, default: int) -> int:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return _coerce_non_negative_int(first.get("revision"), default=default)
    return _coerce_non_negative_int(None, default=default)


def _is_postgrest_conflict_error(exc: RuntimeError) -> bool:
    text = str(exc).casefold()
    if "409" in text or "conflict" in text:
        return True
    # PostgREST can return unique violation details while still represented as 400.
    return "duplicate key value" in text or "23505" in text


def _is_missing_rpc_function_error(exc: RuntimeError) -> bool:
    text = str(exc).casefold()
    return "could not find the function" in text or "pgrst202" in text or "404 not found" in text


def _bundle_has_content(bundle: TrackerDataBundleV3) -> bool:
    return any(
        (
            bool(bundle.contacts),
            bool(bundle.jurisdictions),
            bool(bundle.properties),
            bool(bundle.permits),
            bool(bundle.document_templates),
            bool(bundle.active_document_template_ids),
        )
    )


def create_data_store(
    backend: str,
    data_root: Path | str,
    *,
    supabase_config: SupabaseDataStoreConfig | None = None,
) -> TrackerDataStore:
    normalized_backend = str(backend or "").strip().lower()
    if normalized_backend == BACKEND_SUPABASE:
        return SupabaseDataStore(data_root, config=supabase_config)
    return LocalSqliteDataStore(data_root)


def _normalize_path(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except Exception:
        return expanded

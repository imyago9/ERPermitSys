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

        payload = state_row.get("payload")
        self._known_revision = _coerce_non_negative_int(state_row.get("revision"), default=0)
        if isinstance(payload, dict):
            try:
                bundle = TrackerDataBundleV3.from_payload(payload)
            except Exception as exc:
                warning = f"Supabase row exists but payload is invalid: {exc}"
                db_debug(
                    "supabase.load.payload_invalid",
                    table=self._config.table,
                    revision=self._known_revision,
                    error=str(exc),
                )
                return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty", warning=warning)
            db_debug(
                "supabase.load",
                table=self._config.table,
                source="primary",
                revision=self._known_revision,
            )
            return DataLoadResult(bundle=bundle, source="primary")

        warning = "Supabase row exists but payload is missing or not a JSON object."
        db_debug(
            "supabase.load.payload_missing",
            table=self._config.table,
            revision=self._known_revision,
        )
        return DataLoadResult(bundle=TrackerDataBundleV3(), source="empty", warning=warning)

    def save_bundle(self, bundle: TrackerDataBundleV3) -> None:
        config = self._require_config()
        table = quote(config.table, safe="_")
        app_id = quote(_APP_ID, safe="_-")
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state_payload = bundle.to_payload()
        started_at = perf_counter()

        if self._known_revision < 0:
            existing_row = self._fetch_state_row()
            if existing_row is not None:
                self._known_revision = _coerce_non_negative_int(existing_row.get("revision"), default=0)

        if self._known_revision < 0:
            insert_rows = [
                {
                    "app_id": _APP_ID,
                    "schema_version": _SCHEMA_VERSION,
                    "backend": self.backend,
                    "saved_at_utc": now_iso,
                    "updated_at": now_iso,
                    "updated_by": self._client_id,
                    "revision": 1,
                    "payload": state_payload,
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
                db_debug(
                    "supabase.save",
                    table=config.table,
                    mode="insert",
                    revision=self._known_revision,
                    duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
                )
                return
            except RuntimeError as exc:
                if not _is_postgrest_conflict_error(exc):
                    db_debug(
                        "supabase.save.error",
                        table=config.table,
                        mode="insert",
                        error=str(exc),
                    )
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
                "saved_at_utc": now_iso,
                "updated_at": now_iso,
                "updated_by": self._client_id,
                "revision": expected_revision + 1,
                "payload": state_payload,
            },
            prefer="return=representation",
            expect_json=True,
        )

        if not isinstance(patched, list) or not patched:
            fresh_row = self._fetch_state_row()
            if fresh_row is not None:
                self._known_revision = _coerce_non_negative_int(fresh_row.get("revision"), default=expected_revision)
            db_debug(
                "supabase.save.conflict",
                table=config.table,
                expected_revision=expected_revision,
                known_revision=self._known_revision,
            )
            raise SupabaseRevisionConflictError(expected_revision=expected_revision)

        self._known_revision = _extract_saved_revision(patched, default=expected_revision + 1)
        db_debug(
            "supabase.save",
            table=config.table,
            mode="update",
            expected_revision=expected_revision,
            revision=self._known_revision,
            duration_ms=round((perf_counter() - started_at) * 1000.0, 2),
        )

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

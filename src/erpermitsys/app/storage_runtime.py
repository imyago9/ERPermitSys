from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from erpermitsys.app.data_store import (
    BACKEND_LOCAL_SQLITE,
    BACKEND_SUPABASE,
    SupabaseDataStoreConfig,
    TrackerDataStore,
    create_data_store,
)
from erpermitsys.app.document_store import (
    PermitDocumentStore,
    SupabaseDocumentStoreConfig,
    create_document_store,
)
from erpermitsys.app.settings_store import (
    DEFAULT_SUPABASE_SCHEMA,
    DEFAULT_SUPABASE_STORAGE_BUCKET,
    DEFAULT_SUPABASE_STORAGE_PREFIX,
    DEFAULT_SUPABASE_TRACKER_TABLE,
    SupabaseSettings,
    normalize_data_storage_backend,
    normalize_supabase_settings,
)


@dataclass(frozen=True, slots=True)
class StorageRuntimeSelection:
    backend: str
    data_root: Path
    data_store: TrackerDataStore
    document_store: PermitDocumentStore
    supabase_settings: SupabaseSettings
    warnings: tuple[str, ...] = ()


def build_storage_runtime(
    *,
    backend: str,
    data_root: Path | str,
    supabase_settings: SupabaseSettings | None = None,
) -> StorageRuntimeSelection:
    normalized_backend = normalize_data_storage_backend(backend, default=BACKEND_LOCAL_SQLITE)
    normalized_root = _normalize_path(Path(data_root))
    resolved_supabase = resolve_supabase_settings(supabase_settings)
    warnings: list[str] = []

    effective_backend = normalized_backend
    data_config: SupabaseDataStoreConfig | None = None
    document_config: SupabaseDocumentStoreConfig | None = None
    if effective_backend == BACKEND_SUPABASE:
        if not resolved_supabase.configured:
            effective_backend = BACKEND_LOCAL_SQLITE
            warnings.append(
                "Supabase backend is selected, but URL/API key is missing. "
                "Falling back to local SQLite storage."
            )
        else:
            data_config = SupabaseDataStoreConfig.from_mapping(
                {
                    "url": resolved_supabase.url,
                    "api_key": resolved_supabase.api_key,
                    "schema": resolved_supabase.schema,
                    "table": resolved_supabase.tracker_table,
                }
            )
            document_config = SupabaseDocumentStoreConfig.from_mapping(
                {
                    "url": resolved_supabase.url,
                    "api_key": resolved_supabase.api_key,
                    "bucket": resolved_supabase.storage_bucket,
                    "prefix": resolved_supabase.storage_prefix,
                }
            )

    data_store = create_data_store(
        effective_backend,
        normalized_root,
        supabase_config=data_config,
    )
    document_store = create_document_store(
        effective_backend,
        normalized_root,
        supabase_config=document_config,
    )
    return StorageRuntimeSelection(
        backend=effective_backend,
        data_root=normalized_root,
        data_store=data_store,
        document_store=document_store,
        supabase_settings=resolved_supabase,
        warnings=tuple(warnings),
    )


def resolve_supabase_settings(value: SupabaseSettings | None) -> SupabaseSettings:
    stored = normalize_supabase_settings(value)
    env = os.environ

    url = stored.url or _first_env(
        env,
        (
            "ERPERMITSYS_SUPABASE_URL",
            "SUPABASE_URL",
            "URL",
        ),
    )
    api_key = stored.api_key or _first_env(
        env,
        (
            "ERPERMITSYS_SUPABASE_API_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ANON_KEY",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_PUBLISHABLE_KEY",
            "PUBLISHABLE_KEY",
        ),
    )
    schema = stored.schema or _first_env(env, ("ERPERMITSYS_SUPABASE_SCHEMA",))
    tracker_table = stored.tracker_table or _first_env(env, ("ERPERMITSYS_SUPABASE_TABLE",))
    storage_bucket = stored.storage_bucket or _first_env(env, ("ERPERMITSYS_SUPABASE_BUCKET",))
    storage_prefix = stored.storage_prefix or _first_env(env, ("ERPERMITSYS_SUPABASE_PREFIX",))

    return normalize_supabase_settings(
        {
            "url": url,
            "api_key": api_key,
            "schema": schema or DEFAULT_SUPABASE_SCHEMA,
            "tracker_table": tracker_table or DEFAULT_SUPABASE_TRACKER_TABLE,
            "storage_bucket": storage_bucket or DEFAULT_SUPABASE_STORAGE_BUCKET,
            "storage_prefix": storage_prefix or DEFAULT_SUPABASE_STORAGE_PREFIX,
        }
    )


def _first_env(values: Mapping[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(values.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_path(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except Exception:
        return expanded

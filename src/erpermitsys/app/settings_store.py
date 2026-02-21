from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable

from erpermitsys.app.runtime_paths import app_root

_REWRITE_ROOT = app_root()
_APP_SETTINGS_DIRNAME = "erpermitsys"
_LEGACY_SETTINGS_PATH = _REWRITE_ROOT / "config" / "settings.json"


def _resolve_settings_path() -> Path:
    env = os.environ
    if os.name == "nt":
        appdata = str(env.get("APPDATA", "") or "").strip()
        if appdata:
            return Path(appdata) / _APP_SETTINGS_DIRNAME / "config" / "settings.json"
        localappdata = str(env.get("LOCALAPPDATA", "") or "").strip()
        if localappdata:
            return Path(localappdata) / _APP_SETTINGS_DIRNAME / "config" / "settings.json"
    else:
        xdg_config_home = str(env.get("XDG_CONFIG_HOME", "") or "").strip()
        if xdg_config_home:
            return Path(xdg_config_home) / _APP_SETTINGS_DIRNAME / "settings.json"
        home = str(env.get("HOME", "") or "").strip()
        if home:
            return Path(home) / ".config" / _APP_SETTINGS_DIRNAME / "settings.json"

    return _LEGACY_SETTINGS_PATH


_SETTINGS_PATH = _resolve_settings_path()
_DARK_MODE_KEY = "darkMode"
_PALETTE_ENABLED_KEY = "paletteShortcutEnabled"
_PALETTE_KEYBIND_KEY = "paletteShortcutKeybind"
_ACTIVE_PLUGIN_IDS_KEY = "activePluginIds"
_DATA_STORAGE_FOLDER_KEY = "dataStorageFolder"
_DATA_STORAGE_BACKEND_KEY = "dataStorageBackend"
_LEGACY_VAULT_ROOT_KEY = "vaultRoot"
_SUPABASE_URL_KEY = "supabaseUrl"
_SUPABASE_API_KEY = "supabaseApiKey"
_SUPABASE_SCHEMA_KEY = "supabaseSchema"
_SUPABASE_TRACKER_TABLE_KEY = "supabaseTrackerTable"
_SUPABASE_STORAGE_BUCKET_KEY = "supabaseStorageBucket"
_SUPABASE_STORAGE_PREFIX_KEY = "supabaseStoragePrefix"
_SUPABASE_MERGE_ON_SWITCH_KEY = "supabaseMergeOnSwitch"
DEFAULT_PALETTE_SHORTCUT = "Ctrl+Space"
DEFAULT_DATA_STORAGE_BACKEND = "local_sqlite"
_LEGACY_DATA_STORAGE_BACKEND_MAP: dict[str, str] = {
    "local_json": DEFAULT_DATA_STORAGE_BACKEND,
}
DEFAULT_SUPABASE_SCHEMA = "public"
DEFAULT_SUPABASE_TRACKER_TABLE = "erpermitsys_state"
DEFAULT_SUPABASE_STORAGE_BUCKET = "erpermitsys-documents"
DEFAULT_SUPABASE_STORAGE_PREFIX = "tracker"
DEFAULT_SUPABASE_MERGE_ON_SWITCH = True
SUPPORTED_DATA_STORAGE_BACKENDS: tuple[str, ...] = (
    DEFAULT_DATA_STORAGE_BACKEND,
    "supabase",
)


@dataclass(frozen=True, slots=True)
class SupabaseSettings:
    url: str = ""
    api_key: str = ""
    schema: str = DEFAULT_SUPABASE_SCHEMA
    tracker_table: str = DEFAULT_SUPABASE_TRACKER_TABLE
    storage_bucket: str = DEFAULT_SUPABASE_STORAGE_BUCKET
    storage_prefix: str = DEFAULT_SUPABASE_STORAGE_PREFIX

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    def to_mapping(self, *, redact_api_key: bool = False) -> dict[str, str]:
        api_key = self.api_key
        if redact_api_key and api_key:
            api_key = "********"
        return {
            "url": self.url,
            "api_key": api_key,
            "schema": self.schema,
            "tracker_table": self.tracker_table,
            "storage_bucket": self.storage_bucket,
            "storage_prefix": self.storage_prefix,
        }


def rewrite_root() -> Path:
    return _REWRITE_ROOT


def settings_path() -> Path:
    return _SETTINGS_PATH


def _maybe_migrate_legacy_settings() -> None:
    target = _SETTINGS_PATH
    legacy = _LEGACY_SETTINGS_PATH
    if target == legacy or target.exists() or not legacy.exists():
        return

    try:
        raw = legacy.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return
    if not isinstance(data, dict):
        return

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        return


def load_settings() -> dict[str, Any]:
    _maybe_migrate_legacy_settings()
    path = settings_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_settings(settings: dict[str, Any]) -> None:
    _maybe_migrate_legacy_settings()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def default_data_storage_folder() -> Path:
    return (_REWRITE_ROOT / "data").resolve()


def normalize_data_storage_folder(
    value: str | Path | None,
    *,
    default: Path | None = None,
) -> Path:
    fallback = Path(default) if default is not None else default_data_storage_folder()
    candidate: Path
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        candidate = Path(text) if text else fallback
    else:
        candidate = fallback

    candidate = candidate.expanduser()
    if not candidate.is_absolute():
        candidate = _REWRITE_ROOT / candidate
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def load_data_storage_folder(default: Path | None = None) -> Path:
    fallback = normalize_data_storage_folder(default)
    settings = load_settings()

    value = settings.get(_DATA_STORAGE_FOLDER_KEY)
    if not isinstance(value, str) or not value.strip():
        legacy = settings.get(_LEGACY_VAULT_ROOT_KEY)
        if isinstance(legacy, str) and legacy.strip():
            value = legacy
        else:
            return fallback

    return normalize_data_storage_folder(value, default=fallback)


def save_data_storage_folder(value: str | Path | None) -> Path:
    resolved = normalize_data_storage_folder(value)
    settings = load_settings()
    settings[_DATA_STORAGE_FOLDER_KEY] = str(resolved)
    settings[_LEGACY_VAULT_ROOT_KEY] = str(resolved)
    save_settings(settings)
    return resolved


def normalize_data_storage_backend(
    value: str | None,
    *,
    default: str = DEFAULT_DATA_STORAGE_BACKEND,
) -> str:
    fallback = str(default or DEFAULT_DATA_STORAGE_BACKEND).strip().lower()
    if fallback not in SUPPORTED_DATA_STORAGE_BACKENDS:
        fallback = DEFAULT_DATA_STORAGE_BACKEND

    normalized = str(value or "").strip().lower()
    normalized = _LEGACY_DATA_STORAGE_BACKEND_MAP.get(normalized, normalized)
    if normalized in SUPPORTED_DATA_STORAGE_BACKENDS:
        return normalized
    return fallback


def load_data_storage_backend(default: str = DEFAULT_DATA_STORAGE_BACKEND) -> str:
    settings = load_settings()
    value = settings.get(_DATA_STORAGE_BACKEND_KEY)
    return normalize_data_storage_backend(value if isinstance(value, str) else None, default=default)


def save_data_storage_backend(value: str) -> str:
    resolved = normalize_data_storage_backend(value)
    settings = load_settings()
    settings[_DATA_STORAGE_BACKEND_KEY] = resolved
    save_settings(settings)
    return resolved


def normalize_supabase_settings(
    value: SupabaseSettings | dict[str, Any] | None,
) -> SupabaseSettings:
    if isinstance(value, SupabaseSettings):
        raw_url = value.url
        raw_api_key = value.api_key
        raw_schema = value.schema
        raw_tracker_table = value.tracker_table
        raw_bucket = value.storage_bucket
        raw_prefix = value.storage_prefix
    elif isinstance(value, dict):
        raw_url = value.get("url", "")
        raw_api_key = value.get("api_key", "")
        raw_schema = value.get("schema", "")
        raw_tracker_table = value.get("tracker_table", "")
        raw_bucket = value.get("storage_bucket", "")
        raw_prefix = value.get("storage_prefix", "")
    else:
        raw_url = ""
        raw_api_key = ""
        raw_schema = ""
        raw_tracker_table = ""
        raw_bucket = ""
        raw_prefix = ""

    url = str(raw_url or "").strip().rstrip("/")
    api_key = str(raw_api_key or "").strip()
    schema = str(raw_schema or "").strip() or DEFAULT_SUPABASE_SCHEMA
    tracker_table = str(raw_tracker_table or "").strip() or DEFAULT_SUPABASE_TRACKER_TABLE
    storage_bucket = str(raw_bucket or "").strip() or DEFAULT_SUPABASE_STORAGE_BUCKET
    storage_prefix = str(raw_prefix or "").strip().strip("/")
    if not storage_prefix:
        storage_prefix = DEFAULT_SUPABASE_STORAGE_PREFIX

    return SupabaseSettings(
        url=url,
        api_key=api_key,
        schema=schema,
        tracker_table=tracker_table,
        storage_bucket=storage_bucket,
        storage_prefix=storage_prefix,
    )


def load_supabase_settings(default: SupabaseSettings | None = None) -> SupabaseSettings:
    fallback = normalize_supabase_settings(default)
    settings = load_settings()
    return normalize_supabase_settings(
        {
            "url": settings.get(_SUPABASE_URL_KEY, fallback.url),
            "api_key": settings.get(_SUPABASE_API_KEY, fallback.api_key),
            "schema": settings.get(_SUPABASE_SCHEMA_KEY, fallback.schema),
            "tracker_table": settings.get(_SUPABASE_TRACKER_TABLE_KEY, fallback.tracker_table),
            "storage_bucket": settings.get(_SUPABASE_STORAGE_BUCKET_KEY, fallback.storage_bucket),
            "storage_prefix": settings.get(_SUPABASE_STORAGE_PREFIX_KEY, fallback.storage_prefix),
        }
    )


def save_supabase_settings(value: SupabaseSettings | dict[str, Any]) -> SupabaseSettings:
    normalized = normalize_supabase_settings(value)
    settings = load_settings()
    settings[_SUPABASE_URL_KEY] = normalized.url
    settings[_SUPABASE_API_KEY] = normalized.api_key
    settings[_SUPABASE_SCHEMA_KEY] = normalized.schema
    settings[_SUPABASE_TRACKER_TABLE_KEY] = normalized.tracker_table
    settings[_SUPABASE_STORAGE_BUCKET_KEY] = normalized.storage_bucket
    settings[_SUPABASE_STORAGE_PREFIX_KEY] = normalized.storage_prefix
    save_settings(settings)
    return normalized


def load_supabase_merge_on_switch(
    default: bool = DEFAULT_SUPABASE_MERGE_ON_SWITCH,
) -> bool:
    settings = load_settings()
    value = settings.get(_SUPABASE_MERGE_ON_SWITCH_KEY, default)
    if isinstance(value, bool):
        return value
    return bool(default)


def save_supabase_merge_on_switch(enabled: bool) -> bool:
    normalized = bool(enabled)
    settings = load_settings()
    settings[_SUPABASE_MERGE_ON_SWITCH_KEY] = normalized
    save_settings(settings)
    return normalized


def load_dark_mode(default: bool = False) -> bool:
    settings = load_settings()
    value = settings.get(_DARK_MODE_KEY, default)
    if isinstance(value, bool):
        return value
    return bool(default)


def save_dark_mode(enabled: bool) -> None:
    settings = load_settings()
    settings[_DARK_MODE_KEY] = bool(enabled)
    save_settings(settings)


def load_palette_shortcut_enabled(default: bool = True) -> bool:
    settings = load_settings()
    value = settings.get(_PALETTE_ENABLED_KEY, default)
    if isinstance(value, bool):
        return value
    return bool(default)


def load_palette_shortcut_keybind(default: str = DEFAULT_PALETTE_SHORTCUT) -> str:
    settings = load_settings()
    value = settings.get(_PALETTE_KEYBIND_KEY, default)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return str(default)


def save_palette_shortcut_settings(enabled: bool, keybind: str) -> None:
    settings = load_settings()
    settings[_PALETTE_ENABLED_KEY] = bool(enabled)
    key = str(keybind).strip() or DEFAULT_PALETTE_SHORTCUT
    settings[_PALETTE_KEYBIND_KEY] = key
    save_settings(settings)


def load_active_plugin_ids(default: Iterable[str] | None = None) -> tuple[str, ...]:
    fallback = tuple(_normalize_plugin_ids(default or ()))
    settings = load_settings()
    value = settings.get(_ACTIVE_PLUGIN_IDS_KEY)
    if not isinstance(value, list):
        return fallback
    return tuple(_normalize_plugin_ids(value))


def save_active_plugin_ids(plugin_ids: Iterable[str]) -> None:
    settings = load_settings()
    settings[_ACTIVE_PLUGIN_IDS_KEY] = list(_normalize_plugin_ids(plugin_ids))
    save_settings(settings)


def _normalize_plugin_ids(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        plugin_id = raw.strip()
        if not plugin_id or plugin_id in seen:
            continue
        seen.add(plugin_id)
        normalized.append(plugin_id)
    return normalized

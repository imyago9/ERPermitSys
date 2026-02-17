from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from typing import Any


_REWRITE_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS_PATH = _REWRITE_ROOT / "config" / "settings.json"
_DARK_MODE_KEY = "darkMode"
_PALETTE_ENABLED_KEY = "paletteShortcutEnabled"
_PALETTE_KEYBIND_KEY = "paletteShortcutKeybind"
_ACTIVE_PLUGIN_IDS_KEY = "activePluginIds"
DEFAULT_PALETTE_SHORTCUT = "Ctrl+Space"


def settings_path() -> Path:
    return _SETTINGS_PATH


def load_settings() -> dict[str, Any]:
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
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


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

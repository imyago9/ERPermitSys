from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PLUGIN_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ManifestError(ValueError):
    """Raised when a plugin manifest is invalid."""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    kind: str
    entry: str | None = None
    description: str = ""
    backend: str | None = None
    tags: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    plugin_dir: Path
    manifest_path: Path
    entry_path: Path | None = None
    backend_path: Path | None = None

    @property
    def plugin_id(self) -> str:
        return self.manifest.plugin_id


def load_manifest(manifest_path: Path, fallback_id: str | None = None) -> PluginManifest:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be an object")

    plugin_id = _read_required_id(data.get("id"), fallback_id=fallback_id)
    name = _read_optional_string(data.get("name")) or plugin_id
    version = _read_optional_string(data.get("version")) or "0.1.0"
    kind = _read_required_kind(data.get("kind") or "feature")
    description = _read_optional_string(data.get("description")) or ""
    entry = _read_optional_relative_path(data.get("entry"), field_name="entry")
    backend = _read_optional_relative_path(data.get("backend"), field_name="backend")
    tags = _read_optional_string_list(data.get("tags"), field_name="tags")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ManifestError("'enabled' must be a boolean when provided")

    return PluginManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        kind=kind,
        entry=entry,
        description=description,
        backend=backend,
        tags=tags,
        enabled=enabled,
    )


def resolve_plugin_path(plugin_dir: Path, relative_path: str) -> Path:
    root = plugin_dir.resolve()
    candidate = (plugin_dir / relative_path).resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    raise ManifestError(f"Path escapes plugin directory: {relative_path}")


def _read_required_id(value: Any, fallback_id: str | None = None) -> str:
    if value is None:
        value = fallback_id
    plugin_id = _read_optional_string(value)
    if not plugin_id:
        raise ManifestError("Missing required 'id'")
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ManifestError(
            "Plugin id must match ^[a-z0-9][a-z0-9_-]{0,63}$"
        )
    return plugin_id


def _read_required_kind(value: Any) -> str:
    kind = _read_optional_string(value)
    if not kind:
        raise ManifestError("Missing required 'kind'")
    if not _PLUGIN_KIND_RE.fullmatch(kind):
        raise ManifestError(
            "Plugin kind must match ^[a-z0-9][a-z0-9_-]{0,63}$"
        )
    return kind


def _read_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError("Expected string value")
    text = value.strip()
    return text or None


def _read_optional_relative_path(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError(f"'{field_name}' must be a string")
    return _clean_relative_path(value, field_name=field_name)


def _read_optional_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError(f"'{field_name}' must be an array of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ManifestError(f"'{field_name}' must be an array of strings")
        cleaned = item.strip()
        if cleaned:
            out.append(cleaned)
    return tuple(out)


def _clean_relative_path(value: str, field_name: str) -> str:
    candidate = value.strip().replace("\\", "/")
    if not candidate:
        raise ManifestError(f"'{field_name}' cannot be empty")
    rel_path = Path(candidate)
    if rel_path.is_absolute():
        raise ManifestError(f"'{field_name}' must be a relative path")
    if ".." in rel_path.parts:
        raise ManifestError(f"'{field_name}' cannot contain '..'")
    return candidate

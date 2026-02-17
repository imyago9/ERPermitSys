from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from coghud.plugins.manifest import (
    DiscoveredPlugin,
    ManifestError,
    load_manifest,
    resolve_plugin_path,
)


@dataclass(slots=True)
class DiscoveryResult:
    plugins: dict[str, DiscoveredPlugin]
    errors: list[str]


def discover_plugins(
    plugin_roots: Sequence[Path],
    logger: logging.Logger | None = None,
) -> DiscoveryResult:
    log = logger or logging.getLogger("coghud.plugins")
    discovered: dict[str, DiscoveredPlugin] = {}
    errors: list[str] = []

    for root in plugin_roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        if not root_path.is_dir():
            errors.append(f"Plugin root is not a directory: {root_path}")
            continue

        for plugin_dir in sorted(
            [path for path in root_path.iterdir() if path.is_dir()],
            key=lambda path: path.name.lower(),
        ):
            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = load_manifest(manifest_path, fallback_id=plugin_dir.name)
                if not manifest.enabled:
                    continue

                entry_path = None
                if manifest.entry:
                    entry_path = resolve_plugin_path(plugin_dir, manifest.entry)
                    if not entry_path.exists() or not entry_path.is_file():
                        raise ManifestError(f"Entry file not found: {manifest.entry}")
                if manifest.kind == "html-background" and entry_path is None:
                    raise ManifestError("'html-background' plugins require an 'entry' file")

                backend_path = None
                if manifest.backend:
                    backend_path = resolve_plugin_path(plugin_dir, manifest.backend)
                    if not backend_path.exists() or not backend_path.is_file():
                        raise ManifestError(f"Backend file not found: {manifest.backend}")

                plugin = DiscoveredPlugin(
                    manifest=manifest,
                    plugin_dir=plugin_dir,
                    manifest_path=manifest_path,
                    entry_path=entry_path,
                    backend_path=backend_path,
                )
            except Exception as exc:
                msg = f"{manifest_path}: {exc}"
                errors.append(msg)
                log.warning(msg)
                continue

            existing = discovered.get(plugin.plugin_id)
            if existing is not None:
                msg = (
                    f"Duplicate plugin id '{plugin.plugin_id}' in "
                    f"{plugin.manifest_path} (already loaded from {existing.manifest_path})"
                )
                errors.append(msg)
                log.warning(msg)
                continue

            discovered[plugin.plugin_id] = plugin

    ordered_plugins = dict(
        sorted(
            discovered.items(),
            key=lambda item: (item[1].manifest.name.lower(), item[1].plugin_id),
        )
    )
    return DiscoveryResult(plugins=ordered_plugins, errors=errors)

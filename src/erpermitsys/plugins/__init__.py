from __future__ import annotations

from erpermitsys.plugins.api import PluginApiService
from erpermitsys.plugins.discovery import DiscoveryResult, discover_plugins
from erpermitsys.plugins.manager import BackendContext, PluginKindPolicy, PluginManager
from erpermitsys.plugins.manifest import DiscoveredPlugin, ManifestError, PluginManifest

__all__ = [
    "BackendContext",
    "DiscoveredPlugin",
    "DiscoveryResult",
    "ManifestError",
    "PluginApiService",
    "PluginKindPolicy",
    "PluginManager",
    "PluginManifest",
    "discover_plugins",
]

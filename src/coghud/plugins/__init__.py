from __future__ import annotations

from coghud.plugins.api import PluginApiService
from coghud.plugins.discovery import DiscoveryResult, discover_plugins
from coghud.plugins.manager import BackendContext, PluginKindPolicy, PluginManager
from coghud.plugins.manifest import DiscoveredPlugin, ManifestError, PluginManifest

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

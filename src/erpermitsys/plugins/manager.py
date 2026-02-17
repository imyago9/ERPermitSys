from __future__ import annotations

import importlib.util
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

from erpermitsys.app.runtime_paths import app_path, bundled_path
from erpermitsys.plugins.discovery import discover_plugins
from erpermitsys.plugins.manifest import DiscoveredPlugin


BackendListener = Callable[[str, str, dict[str, Any]], None]
ActivationMode = str


@dataclass(frozen=True, slots=True)
class PluginKindPolicy:
    """Activation policy for a plugin kind."""

    kind: str
    activation_mode: ActivationMode = "multi"  # "single" or "multi"
    requires_entry: bool = False


@dataclass(slots=True)
class BackendContext:
    plugin: DiscoveredPlugin
    data_dir: Path
    settings_path: Path
    plugin_settings_path: Path | None
    emit_event: Callable[[str, Mapping[str, Any] | None], None]

    def load_settings(self, defaults: Mapping[str, Any] | None = None) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        if isinstance(defaults, Mapping):
            settings.update(dict(defaults))

        plugin_defaults = self._read_json_dict(self.plugin_settings_path)
        if plugin_defaults:
            settings.update(plugin_defaults)

        persisted = self._read_json_dict(self.settings_path)
        if persisted:
            settings.update(persisted)

        return settings

    def save_settings(self, settings: Mapping[str, Any]) -> None:
        if not isinstance(settings, Mapping):
            return
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(dict(settings), indent=2), encoding="utf-8")

    @staticmethod
    def _read_json_dict(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return dict(data)


class PluginManager:
    """Discovers plugins and manages activation by plugin kind."""

    DEFAULT_KIND_POLICIES: tuple[PluginKindPolicy, ...] = (
        PluginKindPolicy(kind="html-background", activation_mode="single", requires_entry=True),
        PluginKindPolicy(kind="feature", activation_mode="multi"),
    )

    def __init__(
        self,
        plugin_roots: Sequence[Path],
        data_root: Path,
        logger: logging.Logger | None = None,
        kind_policies: Sequence[PluginKindPolicy] | None = None,
    ) -> None:
        self._plugin_roots = tuple(Path(path) for path in plugin_roots)
        self._data_root = Path(data_root)
        self._logger = logger or logging.getLogger("erpermitsys.plugins")

        self._plugins: dict[str, DiscoveredPlugin] = {}
        self._discovery_errors: list[str] = []
        self._listeners: list[BackendListener] = []

        self._active_plugin_ids: set[str] = set()
        self._active_backends: dict[str, Any] = {}
        self._active_backend_modules: dict[str, ModuleType] = {}

        self._kind_policies: dict[str, PluginKindPolicy] = {}
        for policy in kind_policies or self.DEFAULT_KIND_POLICIES:
            self.register_kind_policy(policy)

    @classmethod
    def from_default_layout(
        cls,
        rewrite_root: Path | None = None,
        logger: logging.Logger | None = None,
        kind_policies: Sequence[PluginKindPolicy] | None = None,
    ) -> "PluginManager":
        plugin_roots: list[Path]
        if rewrite_root is not None:
            root = Path(rewrite_root)
            plugin_roots = [root / "plugins"]
        else:
            plugin_roots = []
            bundled_plugins = bundled_path("plugins")
            app_plugins = app_path("plugins")
            for candidate in (bundled_plugins, app_plugins):
                if candidate not in plugin_roots:
                    plugin_roots.append(candidate)

        data_root = app_path("config", "plugins")
        return cls(
            plugin_roots=plugin_roots,
            data_root=data_root,
            logger=logger,
            kind_policies=kind_policies,
        )

    @property
    def discovery_errors(self) -> tuple[str, ...]:
        return tuple(self._discovery_errors)

    @property
    def active_plugin_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_plugin_ids))

    @property
    def active_background_id(self) -> str | None:
        for plugin in self.active_plugins(kind="html-background"):
            return plugin.plugin_id
        return None

    def register_kind_policy(self, policy: PluginKindPolicy) -> None:
        if policy.activation_mode not in ("single", "multi"):
            raise ValueError(f"Unsupported activation mode: {policy.activation_mode}")
        self._kind_policies[policy.kind] = policy

    def kind_policy(self, kind: str) -> PluginKindPolicy:
        policy = self._kind_policies.get(kind)
        if policy is not None:
            return policy
        return PluginKindPolicy(kind=kind, activation_mode="multi", requires_entry=False)

    def plugins(self) -> tuple[DiscoveredPlugin, ...]:
        return tuple(self._plugins.values())

    def plugins_by_kind(self, kind: str) -> tuple[DiscoveredPlugin, ...]:
        return tuple(plugin for plugin in self._plugins.values() if plugin.manifest.kind == kind)

    def get_plugin(self, plugin_id: str) -> DiscoveredPlugin | None:
        return self._plugins.get(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        return plugin_id in self._active_plugin_ids

    def active_plugins(self, kind: str | None = None) -> tuple[DiscoveredPlugin, ...]:
        items: list[DiscoveredPlugin] = []
        for plugin_id in sorted(self._active_plugin_ids):
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                continue
            if kind is not None and plugin.manifest.kind != kind:
                continue
            items.append(plugin)
        return tuple(items)

    def active_background_plugin(self) -> DiscoveredPlugin | None:
        for plugin in self.active_plugins(kind="html-background"):
            return plugin
        return None

    def active_background_url(self) -> str | None:
        plugin = self.active_background_plugin()
        if plugin is None or plugin.entry_path is None:
            return None
        return plugin.entry_path.as_uri()

    def add_listener(self, listener: BackendListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BackendListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def discover(self, auto_activate_background: bool = False) -> tuple[DiscoveredPlugin, ...]:
        result = discover_plugins(self._plugin_roots, logger=self._logger)
        self._plugins = result.plugins
        self._discovery_errors = list(result.errors)

        for plugin_id in list(self._active_plugin_ids):
            if plugin_id not in self._plugins:
                self.deactivate(plugin_id)

        if auto_activate_background and not self.active_background_plugin():
            backgrounds = self.plugins_by_kind("html-background")
            if backgrounds:
                self.activate(backgrounds[0].plugin_id)

        return self.plugins()

    def activate(self, plugin_id: str) -> DiscoveredPlugin:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(f"Unknown plugin id: {plugin_id}")

        if plugin.entry_path is None and self.kind_policy(plugin.manifest.kind).requires_entry:
            raise RuntimeError(f"Plugin '{plugin_id}' requires an entry file")

        if self.is_active(plugin_id):
            return plugin

        policy = self.kind_policy(plugin.manifest.kind)
        if policy.activation_mode == "single":
            for other in self.active_plugins(kind=plugin.manifest.kind):
                if other.plugin_id != plugin_id:
                    self.deactivate(other.plugin_id)

        self._active_plugin_ids.add(plugin_id)
        if plugin.backend_path:
            try:
                backend = self._start_backend(plugin)
                self._active_backends[plugin_id] = backend
            except Exception as exc:
                self._active_plugin_ids.discard(plugin_id)
                self._active_backends.pop(plugin_id, None)
                self._active_backend_modules.pop(plugin_id, None)
                raise RuntimeError(f"Backend failed for '{plugin_id}': {exc}") from exc

        return plugin

    def deactivate(self, plugin_id: str) -> bool:
        if plugin_id not in self._active_plugin_ids:
            return False

        backend = self._active_backends.pop(plugin_id, None)
        if backend is not None:
            stop = getattr(backend, "stop", None)
            if callable(stop):
                try:
                    _invoke_callable(stop)
                except Exception as exc:
                    self._logger.warning("Backend stop failed for %s: %s", plugin_id, exc)

        self._active_backend_modules.pop(plugin_id, None)
        self._active_plugin_ids.discard(plugin_id)
        return True

    def set_enabled(self, plugin_id: str, enabled: bool) -> DiscoveredPlugin | None:
        if enabled:
            return self.activate(plugin_id)
        self.deactivate(plugin_id)
        return self.get_plugin(plugin_id)

    def clear_active(self, kind: str | None = None) -> None:
        if kind is None:
            target_ids = list(self._active_plugin_ids)
        else:
            target_ids = [plugin.plugin_id for plugin in self.active_plugins(kind=kind)]
        for plugin_id in target_ids:
            self.deactivate(plugin_id)

    def dispatch_command(
        self,
        command: str,
        payload: Mapping[str, Any] | None = None,
        plugin_id: str | None = None,
    ) -> dict[str, Any]:
        target_id = plugin_id
        if target_id is None:
            if len(self._active_plugin_ids) == 1:
                target_id = next(iter(self._active_plugin_ids))
            else:
                return {
                    "ok": False,
                    "error": "Plugin id required when zero or multiple plugins are active",
                }

        if target_id not in self._active_plugin_ids:
            return {"ok": False, "error": f"Plugin is not active: {target_id}"}

        backend = self._active_backends.get(target_id)
        if backend is None:
            return {"ok": False, "error": "Active plugin has no backend"}

        handler = getattr(backend, "handle_command", None)
        if not callable(handler):
            return {"ok": False, "error": "Backend does not support commands"}

        args = dict(payload or {})
        try:
            result = _invoke_callable(handler, command, args)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if result is None:
            return {"ok": True}
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": True, "result": result}

    def snapshot_state(self) -> dict[str, Any]:
        by_kind: dict[str, list[str]] = {}
        for plugin in self.active_plugins():
            by_kind.setdefault(plugin.manifest.kind, []).append(plugin.plugin_id)

        data: dict[str, Any] = {
            "active": sorted(self._active_plugin_ids),
            "active_by_kind": {kind: sorted(ids) for kind, ids in by_kind.items()},
            "active_background": self.active_background_id,
        }

        backend_snapshots: dict[str, Any] = {}
        for plugin_id, backend in self._active_backends.items():
            snapshot = getattr(backend, "snapshot_state", None)
            if not callable(snapshot):
                continue
            try:
                raw = _invoke_callable(snapshot)
            except Exception as exc:
                backend_snapshots[plugin_id] = {"error": str(exc)}
                continue
            if isinstance(raw, dict):
                backend_snapshots[plugin_id] = raw
            elif raw is not None:
                backend_snapshots[plugin_id] = {"value": raw}
        if backend_snapshots:
            data["backends"] = backend_snapshots

        return data

    def shutdown(self) -> None:
        self.clear_active()
        self._listeners.clear()

    def _start_backend(self, plugin: DiscoveredPlugin) -> Any:
        if plugin.backend_path is None:
            raise RuntimeError("Plugin has no backend file")

        context = self._build_backend_context(plugin)
        module = self._load_backend_module(plugin)
        backend = self._create_backend_instance(module, context)
        self._active_backend_modules[plugin.plugin_id] = module

        start = getattr(backend, "start", None)
        if callable(start):
            _invoke_callable(start, context)
        return backend

    def _build_backend_context(self, plugin: DiscoveredPlugin) -> BackendContext:
        data_dir = self._data_root / plugin.plugin_id
        data_dir.mkdir(parents=True, exist_ok=True)
        settings_path = data_dir / "settings.json"
        plugin_settings_path = plugin.plugin_dir / "settings.json"
        if not plugin_settings_path.exists() or not plugin_settings_path.is_file():
            plugin_settings_path = None
        return BackendContext(
            plugin=plugin,
            data_dir=data_dir,
            settings_path=settings_path,
            plugin_settings_path=plugin_settings_path,
            emit_event=lambda event_type, payload=None: self._emit_backend_event(
                plugin.plugin_id, event_type, payload
            ),
        )

    def _emit_backend_event(
        self,
        plugin_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> None:
        event_payload = dict(payload or {})
        for listener in list(self._listeners):
            try:
                listener(plugin_id, event_type, event_payload)
            except Exception as exc:
                self._logger.warning("Listener failed for %s/%s: %s", plugin_id, event_type, exc)

    def _load_backend_module(self, plugin: DiscoveredPlugin) -> ModuleType:
        if plugin.backend_path is None:
            raise RuntimeError("No backend path configured for plugin")

        safe_id = plugin.plugin_id.replace("-", "_")
        module_name = f"erpermitsys_plugin_{safe_id}"
        spec = importlib.util.spec_from_file_location(module_name, plugin.backend_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load backend module from {plugin.backend_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _create_backend_instance(self, module: ModuleType, context: BackendContext) -> Any:
        factory = getattr(module, "create_backend", None)
        if callable(factory):
            backend = _invoke_callable(factory, context)
            if backend is None:
                raise RuntimeError("create_backend returned None")
            return backend

        backend_class = getattr(module, "PluginBackend", None)
        if isinstance(backend_class, type):
            backend = _invoke_callable(backend_class, context)
            if backend is None:
                raise RuntimeError("PluginBackend constructor returned None")
            return backend

        raise RuntimeError(
            "Backend must define create_backend(context) or PluginBackend class"
        )


def _invoke_callable(func: Callable[..., Any], *preferred_args: Any) -> Any:
    """Call a function while tolerating smaller signatures for scaffold flexibility."""
    if not preferred_args:
        return func()

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(*preferred_args)

    params = list(signature.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params):
        return func(*preferred_args)

    positional_params = [
        param
        for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if not positional_params:
        return func()

    count = min(len(positional_params), len(preferred_args))
    return func(*preferred_args[:count])

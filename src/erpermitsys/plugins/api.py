from __future__ import annotations

from typing import Any, Callable, Mapping

from erpermitsys.plugins.manager import PluginManager


PluginApiListener = Callable[[dict[str, Any]], None]


class PluginApiService:
    """Versioned host API facade for active plugins."""

    API_VERSION = "1.0"

    def __init__(self, plugin_manager: PluginManager, *, active_kind: str = "html-background") -> None:
        self._plugin_manager = plugin_manager
        self._active_kind = active_kind
        self._listeners: list[PluginApiListener] = []
        self._plugin_manager.add_listener(self._on_backend_event)

    @property
    def api_version(self) -> str:
        return self.API_VERSION

    def shutdown(self) -> None:
        self._plugin_manager.remove_listener(self._on_backend_event)
        self._listeners.clear()

    def add_listener(self, listener: PluginApiListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: PluginApiListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def active_plugin_id(self) -> str | None:
        for plugin in self._plugin_manager.active_plugins(kind=self._active_kind):
            return plugin.plugin_id
        return None

    def request(self, request: Mapping[str, Any] | None) -> dict[str, Any]:
        body = dict(request or {})
        op = str(body.get("op") or "state").strip().lower()
        plugin_id = body.get("plugin_id")
        resolved_plugin_id = (
            str(plugin_id).strip() if isinstance(plugin_id, str) and plugin_id.strip() else self.active_plugin_id()
        )

        if not resolved_plugin_id:
            return self._response(
                ok=False,
                op=op,
                plugin_id=None,
                error="No active plugin",
            )

        if op == "ping":
            return self._response(ok=True, op=op, plugin_id=resolved_plugin_id, data={"pong": True})
        if op == "command":
            command = body.get("command")
            payload = body.get("payload")
            if not isinstance(command, str) or not command.strip():
                return self._response(
                    ok=False,
                    op=op,
                    plugin_id=resolved_plugin_id,
                    error="Missing command",
                )
            raw = self._plugin_manager.dispatch_command(
                command.strip(),
                payload=payload if isinstance(payload, Mapping) else {},
                plugin_id=resolved_plugin_id,
            )
            if isinstance(raw, dict):
                ok = bool(raw.get("ok", False))
                if ok:
                    return self._response(ok=True, op=op, plugin_id=resolved_plugin_id, data=raw)
                return self._response(
                    ok=False,
                    op=op,
                    plugin_id=resolved_plugin_id,
                    error=str(raw.get("error") or raw.get("message") or "Command failed"),
                    data=raw,
                )
            return self._response(
                ok=True,
                op=op,
                plugin_id=resolved_plugin_id,
                data={"ok": True, "result": raw},
            )
        if op == "state":
            state_result = self._read_state(resolved_plugin_id)
            if state_result is None:
                return self._response(
                    ok=False,
                    op=op,
                    plugin_id=resolved_plugin_id,
                    error="State unavailable",
                )
            return self._response(
                ok=True,
                op=op,
                plugin_id=resolved_plugin_id,
                data={"state": state_result},
            )

        return self._response(
            ok=False,
            op=op,
            plugin_id=resolved_plugin_id,
            error=f"Unsupported op: {op}",
        )

    def command(
        self,
        command: str,
        payload: Mapping[str, Any] | None = None,
        plugin_id: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            {
                "op": "command",
                "plugin_id": plugin_id,
                "command": command,
                "payload": dict(payload or {}),
            }
        )

    def state(self, plugin_id: str | None = None) -> dict[str, Any]:
        return self.request({"op": "state", "plugin_id": plugin_id})

    def _read_state(self, plugin_id: str) -> dict[str, Any] | None:
        command_id = f"{plugin_id}.state.get"
        raw = self._plugin_manager.dispatch_command(command_id, payload={}, plugin_id=plugin_id)
        if isinstance(raw, dict):
            state = raw.get("state")
            if isinstance(state, dict):
                return state
            if all(key in raw for key in ("nav", "audio")):
                return dict(raw)

        snapshot = self._plugin_manager.snapshot_state()
        backend_state = (((snapshot.get("backends") or {}).get(plugin_id) or {}).get("state"))
        if isinstance(backend_state, dict):
            return backend_state
        return None

    def _on_backend_event(self, plugin_id: str, event_type: str, payload: dict[str, Any]) -> None:
        active_id = self.active_plugin_id()
        if not active_id or plugin_id != active_id:
            return
        event = {
            "api_version": self.API_VERSION,
            "type": "event",
            "plugin_id": plugin_id,
            "event": event_type,
            "payload": dict(payload or {}),
        }
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                continue

    def _response(
        self,
        *,
        ok: bool,
        op: str,
        plugin_id: str | None,
        data: Mapping[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "ok": bool(ok),
            "api_version": self.API_VERSION,
            "op": op,
            "plugin_id": plugin_id or "",
        }
        if data is not None:
            response["data"] = dict(data)
        if error:
            response["error"] = error
        return response


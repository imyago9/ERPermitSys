from __future__ import annotations

from typing import Any, Mapping

from PySide6.QtCore import QObject, Signal, Slot

from coghud.plugins.api import PluginApiService


class BackgroundPluginBridge(QObject):
    """QWebChannel adapter for the plugin API service."""

    # Legacy event shape: (event_type, payload)
    eventReceived = Signal(str, "QVariantMap")
    # Versioned API event envelope.
    apiEvent = Signal("QVariantMap")

    def __init__(self, api_service: PluginApiService) -> None:
        super().__init__(None)
        self._api_service = api_service
        self._api_service.add_listener(self._on_api_event)

    def shutdown(self) -> None:
        self._api_service.remove_listener(self._on_api_event)

    @Slot(result="QVariantMap")
    def info(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": self._api_service.api_version,
            "transport": "qwebchannel",
            "active_plugin_id": self._api_service.active_plugin_id() or "",
        }

    @Slot("QVariantMap", result="QVariantMap")
    def request(self, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._api_service.request(request)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def command(self, command: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self._api_service.command(command, payload=payload)
        data = response.get("data")
        if isinstance(data, dict):
            data.setdefault("ok", bool(response.get("ok", False)))
            if response.get("error") and "error" not in data:
                data["error"] = str(response["error"])
            return data
        if bool(response.get("ok", False)):
            return {"ok": True}
        return {"ok": False, "error": str(response.get("error") or "Command failed")}

    @Slot(result="QVariantMap")
    def state(self) -> dict[str, Any]:
        response = self._api_service.state()
        data = response.get("data")
        if isinstance(data, dict):
            state = data.get("state")
            if isinstance(state, dict):
                return {"ok": bool(response.get("ok", False)), "state": state}
        return {"ok": bool(response.get("ok", False)), "state": {}, "error": response.get("error", "")}

    def _on_api_event(self, event: dict[str, Any]) -> None:
        event_payload = dict(event)
        self.apiEvent.emit(event_payload)
        event_type = str(event_payload.get("event") or "")
        payload = event_payload.get("payload")
        if isinstance(payload, dict):
            self.eventReceived.emit(event_type, dict(payload))
        else:
            self.eventReceived.emit(event_type, {})


from __future__ import annotations

from typing import Any, Mapping


class PluginBackend:
    def __init__(self, context=None) -> None:
        self._running = False
        self._last_command: str | None = None
        self._context = context

    def start(self, context=None) -> None:
        if context is not None:
            self._context = context
        self._running = True
        if self._context is not None:
            self._context.emit_event("status", {"ready": True, "message": "Voice demo backend active"})

    def stop(self) -> None:
        self._running = False

    def handle_command(self, command: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._last_command = command
        return {
            "ok": True,
            "handled": command,
            "payload": dict(payload or {}),
            "running": self._running,
        }

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "last_command": self._last_command,
        }


def create_backend(context):
    return PluginBackend(context)

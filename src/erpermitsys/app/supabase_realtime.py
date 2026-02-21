from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket

from erpermitsys.app.db_debug import db_debug

try:
    from PySide6.QtWebSockets import QWebSocket
except Exception:  # pragma: no cover - optional Qt module in some runtimes
    QWebSocket = None  # type: ignore[assignment]


_HEARTBEAT_INTERVAL_MS = 25_000
_RECONNECT_DELAY_MS = 2_000


@dataclass(frozen=True, slots=True)
class SupabaseRealtimeSubscription:
    url: str
    api_key: str
    schema: str
    table: str
    app_id: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.schema and self.table and self.app_id)


class SupabaseRealtimeClient(QObject):
    def __init__(
        self,
        *,
        parent: QObject | None = None,
        on_state_row: Callable[[dict[str, Any]], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_state_row = on_state_row
        self._on_status = on_status
        self._subscription: SupabaseRealtimeSubscription | None = None
        self._socket: QWebSocket | None = None
        self._active = False
        self._joined = False
        self._join_ref = ""
        self._next_ref_value = 0

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(_HEARTBEAT_INTERVAL_MS)
        self._heartbeat_timer.timeout.connect(self._send_heartbeat)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(_RECONNECT_DELAY_MS)
        self._reconnect_timer.timeout.connect(self._connect_socket)

    @property
    def available(self) -> bool:
        return QWebSocket is not None

    @property
    def active(self) -> bool:
        return bool(self._active and self._subscription is not None)

    def start(self, subscription: SupabaseRealtimeSubscription) -> None:
        normalized = self._normalize_subscription(subscription)
        if not normalized.configured:
            self._emit_status("warning", "Supabase realtime is not configured.")
            db_debug("supabase.realtime.start_skipped", reason="not_configured")
            self.stop()
            return
        if not self.available:
            self._emit_status("warning", "QtWebSockets is unavailable in this runtime.")
            db_debug("supabase.realtime.start_skipped", reason="qtwebsockets_unavailable")
            self.stop()
            return
        if (
            self._active
            and self._subscription == normalized
            and self._socket is not None
            and self._socket.state() != QAbstractSocket.SocketState.UnconnectedState
        ):
            db_debug(
                "supabase.realtime.start_skipped",
                reason="already_active",
                schema=normalized.schema,
                table=normalized.table,
            )
            return

        self.stop()
        self._subscription = normalized
        self._active = True
        db_debug("supabase.realtime.start", schema=normalized.schema, table=normalized.table)
        self._connect_socket()

    def stop(self) -> None:
        self._active = False
        self._joined = False
        self._join_ref = ""
        self._heartbeat_timer.stop()
        self._reconnect_timer.stop()
        self._teardown_socket()
        self._subscription = None
        db_debug("supabase.realtime.stop")

    def _ensure_socket(self) -> QWebSocket | None:
        if not self.available:
            return None
        socket = self._socket
        if socket is not None:
            return socket

        socket = QWebSocket(parent=self)
        socket.connected.connect(self._on_connected)
        socket.disconnected.connect(self._on_disconnected)
        socket.textMessageReceived.connect(self._on_text_message_received)
        socket.errorOccurred.connect(self._on_error_occurred)
        self._socket = socket
        return socket

    def _teardown_socket(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is None:
            return
        try:
            socket.connected.disconnect(self._on_connected)
        except Exception:
            pass
        try:
            socket.disconnected.disconnect(self._on_disconnected)
        except Exception:
            pass
        try:
            socket.textMessageReceived.disconnect(self._on_text_message_received)
        except Exception:
            pass
        try:
            socket.errorOccurred.disconnect(self._on_error_occurred)
        except Exception:
            pass
        try:
            socket.abort()
        except Exception:
            pass
        try:
            socket.close()
        except Exception:
            pass
        try:
            socket.deleteLater()
        except Exception:
            pass

    def _connect_socket(self) -> None:
        if not self._active:
            return
        subscription = self._subscription
        if subscription is None:
            return
        socket = self._ensure_socket()
        if socket is None:
            return
        if socket.state() in (
            QAbstractSocket.SocketState.ConnectingState,
            QAbstractSocket.SocketState.ConnectedState,
        ):
            return

        url = self._build_websocket_url(subscription)
        self._joined = False
        self._join_ref = ""
        self._emit_status("info", f"Connecting Supabase realtime websocket: {subscription.schema}.{subscription.table}")
        db_debug("supabase.realtime.connecting", schema=subscription.schema, table=subscription.table)
        socket.open(QUrl(url))

    def _on_connected(self) -> None:
        if not self._active or self._subscription is None:
            return
        self._emit_status("info", "Supabase realtime connected.")
        db_debug(
            "supabase.realtime.connected",
            schema=self._subscription.schema,
            table=self._subscription.table,
        )
        self._send_join()

    def _on_disconnected(self) -> None:
        self._heartbeat_timer.stop()
        self._joined = False
        db_debug("supabase.realtime.disconnected", will_reconnect=bool(self._active))
        if self._active:
            self._emit_status("warning", "Supabase realtime disconnected; reconnecting.")
            self._reconnect_timer.start()
        else:
            self._emit_status("info", "Supabase realtime stopped.")

    def _on_error_occurred(self, _error) -> None:
        socket = self._socket
        message = "Supabase realtime socket error."
        if socket is not None:
            detail = str(socket.errorString() or "").strip()
            if detail:
                message = f"{message} {detail}"
        self._emit_status("warning", message)
        db_debug("supabase.realtime.error", message=message)

    def _on_text_message_received(self, message: str) -> None:
        if not self._active:
            return
        try:
            event = json.loads(message)
        except Exception:
            self._emit_status("warning", "Supabase realtime sent a non-JSON frame.")
            db_debug("supabase.realtime.message_invalid_json")
            return
        if not isinstance(event, dict):
            return

        event_name = str(event.get("event", "") or "").strip()
        payload = event.get("payload")

        if event_name == "phx_reply":
            self._handle_join_reply(payload, ref=str(event.get("ref", "") or "").strip())
            return
        if event_name in {"postgres_changes", "INSERT", "UPDATE"}:
            state_row = self._extract_state_row(payload)
            if state_row is not None and self._on_state_row is not None:
                self._on_state_row(state_row)

    def _handle_join_reply(self, payload: object, *, ref: str) -> None:
        if ref != self._join_ref:
            return
        if not isinstance(payload, dict):
            return
        status = str(payload.get("status", "") or "").strip().lower()
        if status == "ok":
            self._joined = True
            self._heartbeat_timer.start()
            self._emit_status("info", "Supabase realtime subscription joined.")
            db_debug("supabase.realtime.joined")
            return
        self._emit_status("warning", "Supabase realtime join was rejected.")
        db_debug("supabase.realtime.join_rejected", status=status or "unknown")

    def _send_join(self) -> None:
        socket = self._socket
        subscription = self._subscription
        if socket is None or subscription is None:
            return
        if socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return

        topic = f"realtime:{subscription.schema}:{subscription.table}"
        self._join_ref = self._next_ref()
        join_payload = {
            "topic": topic,
            "event": "phx_join",
            "payload": {
                "config": {
                    "broadcast": {"self": False},
                    "presence": {"key": ""},
                    "postgres_changes": [
                        {
                            "event": "*",
                            "schema": subscription.schema,
                            "table": subscription.table,
                            "filter": f"app_id=eq.{subscription.app_id}",
                        }
                    ],
                    "private": False,
                }
            },
            "ref": self._join_ref,
        }
        socket.sendTextMessage(json.dumps(join_payload, separators=(",", ":")))

    def _send_heartbeat(self) -> None:
        socket = self._socket
        if socket is None or socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return
        heartbeat = {
            "topic": "phoenix",
            "event": "heartbeat",
            "payload": {},
            "ref": self._next_ref(),
        }
        socket.sendTextMessage(json.dumps(heartbeat, separators=(",", ":")))

    def _next_ref(self) -> str:
        self._next_ref_value += 1
        return str(self._next_ref_value)

    def _extract_state_row(self, payload: object) -> dict[str, Any] | None:
        subscription = self._subscription
        if subscription is None:
            return None
        if not isinstance(payload, dict):
            return None

        candidates: list[object] = [payload.get("data"), payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            event_type = str(candidate.get("eventType", "") or "").strip().upper()
            row = candidate.get("new")
            if isinstance(row, dict):
                if event_type == "DELETE":
                    continue
                if str(row.get("app_id", "") or "").strip() != subscription.app_id:
                    continue
                return row
            if "app_id" in candidate and (
                "payload" in candidate or "revision" in candidate or "updated_at" in candidate
            ):
                if str(candidate.get("app_id", "") or "").strip() != subscription.app_id:
                    continue
                return candidate
        return None

    def _normalize_subscription(
        self,
        subscription: SupabaseRealtimeSubscription,
    ) -> SupabaseRealtimeSubscription:
        return SupabaseRealtimeSubscription(
            url=str(subscription.url or "").strip().rstrip("/"),
            api_key=str(subscription.api_key or "").strip(),
            schema=str(subscription.schema or "").strip() or "public",
            table=str(subscription.table or "").strip() or "erpermitsys_state",
            app_id=str(subscription.app_id or "").strip() or "erpermitsys",
        )

    def _build_websocket_url(self, subscription: SupabaseRealtimeSubscription) -> str:
        parsed = urlsplit(subscription.url)
        scheme = "wss" if parsed.scheme.casefold() == "https" else "ws"
        query = urlencode({"apikey": subscription.api_key, "vsn": "1.0.0"})
        return urlunsplit((scheme, parsed.netloc, "/realtime/v1/websocket", query, ""))

    def _emit_status(self, level: str, message: str) -> None:
        callback = self._on_status
        if callback is None:
            return
        callback(str(level or "info"), str(message or "").strip())

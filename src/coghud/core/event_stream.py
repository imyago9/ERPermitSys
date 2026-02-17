from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class StreamEvent:
    sequence: int
    timestamp: str
    event_type: str
    source: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    sequence: int
    timestamp: str
    state_key: str
    data: dict[str, Any]


class StateStreamer:
    def __init__(self) -> None:
        self._sequence = 0
        self._events: list[StreamEvent] = []
        self._snapshots: list[StateSnapshot] = []
        self._subscribers: list[Callable[[StreamEvent], None]] = []
        self._lock = RLock()

    def record(
        self,
        event_type: str,
        *,
        source: str,
        payload: Mapping[str, Any] | None = None,
    ) -> StreamEvent:
        with self._lock:
            self._sequence += 1
            event = StreamEvent(
                sequence=self._sequence,
                timestamp=_utc_iso_now(),
                event_type=event_type,
                source=source,
                payload=deepcopy(dict(payload or {})),
            )
            self._events.append(event)
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                continue
        return event

    def snapshot(self, state_key: str, data: Mapping[str, Any] | None = None) -> StateSnapshot:
        with self._lock:
            snapshot = StateSnapshot(
                sequence=self._sequence,
                timestamp=_utc_iso_now(),
                state_key=state_key,
                data=deepcopy(dict(data or {})),
            )
            self._snapshots.append(snapshot)
            return snapshot

    def tail(self, *, limit: int = 100) -> tuple[StreamEvent, ...]:
        safe_limit = max(1, int(limit))
        with self._lock:
            return tuple(self._events[-safe_limit:])

    def snapshots(self, *, limit: int = 20) -> tuple[StateSnapshot, ...]:
        safe_limit = max(1, int(limit))
        with self._lock:
            return tuple(self._snapshots[-safe_limit:])

    def subscribe(self, callback: Callable[[StreamEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    return

        return _unsubscribe

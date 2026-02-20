from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


_DB_DEBUG_ENV = "ERPERMITSYS_DB_DEBUG"
_DB_DEBUG_LOG_ENV = "ERPERMITSYS_DB_DEBUG_LOG"
_REDACTED_VALUE = "<redacted>"
_REDACTED_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "token",
    "access_token",
    "secret",
}
_LOCK = Lock()
_SEQUENCE = 0


def db_debug_enabled() -> bool:
    return _is_truthy_env(os.getenv(_DB_DEBUG_ENV, ""))


def db_debug(event: str, **payload: object) -> None:
    if not db_debug_enabled():
        return
    global _SEQUENCE
    with _LOCK:
        _SEQUENCE += 1
        sequence = _SEQUENCE
    record = {
        "seq": sequence,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": str(event or "").strip() or "unknown",
        "data": _redact_value(payload),
    }
    line = json.dumps(record, ensure_ascii=True, default=str)
    target = str(os.getenv(_DB_DEBUG_LOG_ENV, "") or "").strip()
    if target:
        try:
            destination = Path(target).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
            return
        except Exception:
            pass
    try:
        sys.stderr.write(f"[db-debug] {line}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _is_truthy_env(value: str) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on", "y"}


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, raw in value.items():
            normalized_key = str(key or "").strip().casefold()
            if normalized_key in _REDACTED_KEYS:
                redacted[str(key)] = _REDACTED_VALUE
            else:
                redacted[str(key)] = _redact_value(raw)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(entry) for entry in value]
    if isinstance(value, set):
        return [_redact_value(entry) for entry in sorted(value, key=str)]
    return value

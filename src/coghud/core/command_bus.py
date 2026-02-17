from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Mapping

from coghud.core.event_stream import StateStreamer


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class CommandInfo:
    command_id: str
    title: str
    description: str = ""
    category: str = "General"
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    def score(self, query: str) -> int:
        normalized = query.strip().lower()
        if not normalized:
            return 1

        searchable = (
            self.command_id,
            self.title,
            self.description,
            self.category,
            *self.aliases,
            *self.keywords,
        )
        parts = [part.lower() for part in searchable if part]
        if not parts:
            return 0

        terms = [term for term in normalized.split() if term]
        if not terms:
            return 1

        joined = " ".join(parts)
        if not all(term in joined for term in terms):
            return 0

        score = 1
        if self.command_id.lower() == normalized:
            score += 120
        if self.title.lower() == normalized:
            score += 100
        if self.title.lower().startswith(normalized):
            score += 40
        if self.command_id.lower().startswith(normalized):
            score += 30
        for alias in self.aliases:
            alias_text = alias.lower()
            if alias_text == normalized:
                score += 80
            elif alias_text.startswith(normalized):
                score += 24
        score += min(20, sum(4 for term in terms if term in self.title.lower()))
        return score


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command_id: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    requested_at: str = field(default_factory=_utc_iso_now)


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


CommandHandler = Callable[[Any, CommandRequest], CommandResult | None]
CommandEnabledPredicate = Callable[[Any, CommandRequest], bool]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    info: CommandInfo
    handler: CommandHandler
    is_enabled: CommandEnabledPredicate | None = None


@dataclass(frozen=True, slots=True)
class CommandCatalogEntry:
    command_id: str
    title: str
    description: str
    category: str
    aliases: tuple[str, ...]
    enabled: bool


class CommandRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, CommandDefinition] = {}

    def register(self, definition: CommandDefinition) -> None:
        command_id = definition.info.command_id.strip()
        if not command_id:
            raise ValueError("Command id must not be empty.")
        if command_id in self._definitions:
            raise ValueError(f"Command already registered: {command_id}")
        self._definitions[command_id] = definition

    def register_many(self, definitions: list[CommandDefinition] | tuple[CommandDefinition, ...]) -> None:
        for definition in definitions:
            self.register(definition)

    def get(self, command_id: str) -> CommandDefinition | None:
        return self._definitions.get(command_id)

    def all(self) -> tuple[CommandDefinition, ...]:
        return tuple(sorted(self._definitions.values(), key=lambda item: item.info.title.lower()))

    def search(self, query: str) -> tuple[CommandDefinition, ...]:
        normalized = query.strip()
        scored: list[tuple[int, CommandDefinition]] = []
        for definition in self._definitions.values():
            score = definition.info.score(normalized)
            if score <= 0:
                continue
            scored.append((score, definition))
        scored.sort(key=lambda item: (-item[0], item[1].info.title.lower()))
        return tuple(item[1] for item in scored)


class CommandBus:
    def __init__(
        self,
        *,
        registry: CommandRegistry,
        context_provider: Callable[[], Any],
        event_streamer: StateStreamer | None = None,
    ) -> None:
        self._registry = registry
        self._context_provider = context_provider
        self._event_streamer = event_streamer

    @property
    def registry(self) -> CommandRegistry:
        return self._registry

    def catalog(
        self,
        *,
        query: str = "",
        source: str = "palette",
        include_disabled: bool = True,
    ) -> tuple[CommandCatalogEntry, ...]:
        context = self._context_provider()
        definitions = self._registry.search(query)
        entries: list[CommandCatalogEntry] = []
        for definition in definitions:
            request = CommandRequest(
                command_id=definition.info.command_id,
                source=source,
                args={},
            )
            enabled = self._is_enabled(definition, context, request)
            if not include_disabled and not enabled:
                continue
            entries.append(
                CommandCatalogEntry(
                    command_id=definition.info.command_id,
                    title=definition.info.title,
                    description=definition.info.description,
                    category=definition.info.category,
                    aliases=definition.info.aliases,
                    enabled=enabled,
                )
            )
        return tuple(entries)

    def execute(
        self,
        command_id: str,
        *,
        args: Mapping[str, Any] | None = None,
        source: str = "unknown",
    ) -> CommandResult:
        request = CommandRequest(
            command_id=command_id,
            args=dict(args or {}),
            source=source,
        )
        definition = self._registry.get(request.command_id)
        if definition is None:
            result = CommandResult(ok=False, message=f"Unknown command: {request.command_id}")
            self._emit(
                "command.failed",
                payload={
                    "command_id": request.command_id,
                    "source": request.source,
                    "message": result.message,
                },
            )
            return result

        context = self._context_provider()
        self._emit(
            "command.requested",
            payload={
                "command_id": request.command_id,
                "source": request.source,
                "args": dict(request.args),
            },
        )

        if not self._is_enabled(definition, context, request):
            result = CommandResult(ok=False, message=f"Command unavailable: {request.command_id}")
            self._emit(
                "command.failed",
                payload={
                    "command_id": request.command_id,
                    "source": request.source,
                    "message": result.message,
                },
            )
            return result

        started = perf_counter()
        try:
            raw_result = definition.handler(context, request)
            result = raw_result if isinstance(raw_result, CommandResult) else CommandResult(ok=True)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            result = CommandResult(ok=False, message=str(exc))
            self._emit(
                "command.failed",
                payload={
                    "command_id": request.command_id,
                    "source": request.source,
                    "duration_ms": duration_ms,
                    "message": result.message,
                },
            )
            self._snapshot_last_result(request, result, duration_ms)
            return result

        duration_ms = int((perf_counter() - started) * 1000)
        event_name = "command.succeeded" if result.ok else "command.failed"
        self._emit(
            event_name,
            payload={
                "command_id": request.command_id,
                "source": request.source,
                "duration_ms": duration_ms,
                "message": result.message,
                "data": dict(result.data),
            },
        )
        self._snapshot_last_result(request, result, duration_ms)
        return result

    def _is_enabled(self, definition: CommandDefinition, context: Any, request: CommandRequest) -> bool:
        predicate = definition.is_enabled
        if predicate is None:
            return True
        try:
            return bool(predicate(context, request))
        except Exception:
            return False

    def _emit(self, event_type: str, *, payload: Mapping[str, Any]) -> None:
        if self._event_streamer is None:
            return
        self._event_streamer.record(event_type, source="command_bus", payload=payload)

    def _snapshot_last_result(
        self,
        request: CommandRequest,
        result: CommandResult,
        duration_ms: int,
    ) -> None:
        if self._event_streamer is None:
            return
        self._event_streamer.snapshot(
            "command.last_result",
            data={
                "requested_at": request.requested_at,
                "command_id": request.command_id,
                "source": request.source,
                "ok": result.ok,
                "message": result.message,
                "duration_ms": duration_ms,
            },
        )

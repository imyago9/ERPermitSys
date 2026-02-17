from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Mapping

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from coghud.core import (
    CommandBus,
    CommandDefinition,
    CommandInfo,
    CommandRegistry,
    CommandRequest,
    CommandResult,
    StateStreamer,
)
from coghud.ui.commands import CommandPaletteDialog

_DEFAULT_SHORTCUT_TEXT = "Ctrl+Space"
_WINDOWS_MOD_ALT = 0x0001
_WINDOWS_MOD_CTRL = 0x0002
_WINDOWS_MOD_SHIFT = 0x0004
_WINDOWS_MOD_WIN = 0x0008


@dataclass(frozen=True, slots=True)
class ShortcutBinding:
    text: str
    key: int
    modifiers: Qt.KeyboardModifiers
    win_modifiers: int
    win_virtual_key: int | None


@dataclass(slots=True)
class AppCommandContext:
    open_settings_dialog: Callable[[], None]
    close_settings_dialog: Callable[[], bool]
    is_settings_dialog_open: Callable[[], bool]
    minimize_window: Callable[[], None]
    close_app: Callable[[], None]
    expand_window: Callable[[], None]
    shrink_window: Callable[[], None]


def _open_settings(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    context.open_settings_dialog()
    return CommandResult(ok=True, message="Opened settings dialog.")


def _minimize_window(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    context.minimize_window()
    return CommandResult(ok=True, message="Window minimized.")


def _close_settings(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    closed = context.close_settings_dialog()
    if closed:
        return CommandResult(ok=True, message="Closed settings dialog.")
    return CommandResult(ok=False, message="Settings dialog is not open.")


def _close_app(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    context.close_app()
    return CommandResult(ok=True, message="Close requested.")


def _expand_window(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    context.expand_window()
    return CommandResult(ok=True, message="Window expanded.")


def _shrink_window(context: AppCommandContext, _request: CommandRequest) -> CommandResult:
    context.shrink_window()
    return CommandResult(ok=True, message="Window shrink requested.")


def _settings_dialog_open(context: AppCommandContext, _request: CommandRequest) -> bool:
    return context.is_settings_dialog_open()


def _settings_dialog_closed(context: AppCommandContext, _request: CommandRequest) -> bool:
    return not context.is_settings_dialog_open()


def register_default_commands(registry: CommandRegistry) -> None:
    registry.register_many(
        (
            CommandDefinition(
                info=CommandInfo(
                    command_id="app.settings.open",
                    title="Open Settings",
                    description="Open the settings dialog.",
                    category="App",
                    aliases=("settings", "preferences"),
                    keywords=("config", "plugins"),
                ),
                handler=_open_settings,
                is_enabled=_settings_dialog_closed,
            ),
            CommandDefinition(
                info=CommandInfo(
                    command_id="app.settings.close",
                    title="Close Settings",
                    description="Close the settings dialog if it is open.",
                    category="App",
                    aliases=("hide settings", "dismiss settings"),
                    keywords=("settings", "dialog"),
                ),
                handler=_close_settings,
                is_enabled=_settings_dialog_open,
            ),
            CommandDefinition(
                info=CommandInfo(
                    command_id="window.minimize",
                    title="Minimize Window",
                    description="Send the main app window to the taskbar.",
                    category="Window",
                    aliases=("minimize", "min"),
                    keywords=("taskbar",),
                ),
                handler=_minimize_window,
            ),
            CommandDefinition(
                info=CommandInfo(
                    command_id="app.close",
                    title="Close App",
                    description="Close the application window.",
                    category="App",
                    aliases=("close", "quit", "exit"),
                    keywords=("shutdown",),
                ),
                handler=_close_app,
            ),
            CommandDefinition(
                info=CommandInfo(
                    command_id="window.expand",
                    title="Expand Window",
                    description="Maximize the application window.",
                    category="Window",
                    aliases=("maximize", "expand"),
                    keywords=("fullscreen", "grow"),
                ),
                handler=_expand_window,
            ),
            CommandDefinition(
                info=CommandInfo(
                    command_id="window.shrink",
                    title="Shrink Window",
                    description="Restore from maximized or shrink the window size.",
                    category="Window",
                    aliases=("restore", "shrink"),
                    keywords=("smaller", "downsize"),
                ),
                handler=_shrink_window,
            ),
        )
    )


class _CommandShortcutFilter(QObject):
    def __init__(
        self,
        *,
        on_toggle_palette: Callable[[], None],
        is_match: Callable[[QKeyEvent], bool],
    ) -> None:
        super().__init__(None)
        self._on_toggle_palette = on_toggle_palette
        self._is_match = is_match

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False
        if not isinstance(event, QKeyEvent):
            return False
        if not self._is_match(event):
            return False

        self._on_toggle_palette()
        return True


class _WindowsGlobalHotkeyFilter(QAbstractNativeEventFilter):
    _HOTKEY_ID = 0x0C0D
    _WM_HOTKEY = 0x0312

    def __init__(self, *, on_hotkey: Callable[[], None]) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey
        self._registered = False
        self._modifiers = 0
        self._virtual_key = 0

    def register(self, *, modifiers: int, virtual_key: int) -> bool:
        if not sys.platform.startswith("win"):
            return False
        if modifiers < 0 or virtual_key <= 0:
            return False
        try:
            import ctypes
        except Exception:
            return False

        user32 = ctypes.windll.user32
        if self._registered:
            self.unregister()
        if user32.RegisterHotKey(None, self._HOTKEY_ID, int(modifiers), int(virtual_key)) == 0:
            self._registered = False
            return False
        self._modifiers = int(modifiers)
        self._virtual_key = int(virtual_key)
        self._registered = True
        return True

    def unregister(self) -> None:
        if not self._registered:
            return
        if not sys.platform.startswith("win"):
            self._registered = False
            return
        try:
            import ctypes

            ctypes.windll.user32.UnregisterHotKey(None, self._HOTKEY_ID)
        except Exception:
            pass
        self._modifiers = 0
        self._virtual_key = 0
        self._registered = False

    def nativeEventFilter(self, _event_type, message):  # type: ignore[override]
        if not self._registered:
            return False, 0
        try:
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0

        if msg.message != self._WM_HOTKEY:
            return False, 0
        if int(msg.wParam) != self._HOTKEY_ID:
            return False, 0

        self._on_hotkey()
        return True, 0


class CommandRuntime:
    def __init__(
        self,
        *,
        app: QApplication,
        context_provider: Callable[[], AppCommandContext],
        event_streamer: StateStreamer,
        shortcut_enabled: bool = True,
        shortcut_text: str = _DEFAULT_SHORTCUT_TEXT,
        anchor_provider: Callable[[], QWidget | None] | None = None,
        registrars: tuple[Callable[[CommandRegistry], None], ...] = (),
    ) -> None:
        self._app = app
        self._context_provider = context_provider
        self._event_streamer = event_streamer
        self._anchor_provider = anchor_provider
        self._shortcut_enabled = bool(shortcut_enabled)
        self._shortcut_binding = self._parse_shortcut_binding(shortcut_text)
        self._native_shortcut_filter: _WindowsGlobalHotkeyFilter | None = None
        self._event_shortcut_filter: _CommandShortcutFilter | None = None

        self._registry = CommandRegistry()
        register_default_commands(self._registry)
        for registrar in registrars:
            registrar(self._registry)

        self._bus = CommandBus(
            registry=self._registry,
            context_provider=self._context_provider,
            event_streamer=self._event_streamer,
        )
        self._palette = CommandPaletteDialog()
        self._palette.query_changed.connect(self._refresh_palette)
        self._palette.command_requested.connect(self._run_from_palette)

        self._install_shortcut_handlers()
        self._app.aboutToQuit.connect(self._dispose_shortcut_handlers)

        self._refresh_palette("")
        self._event_streamer.record(
            "command_runtime.ready",
            source="command_runtime",
            payload={"registered_commands": len(self._registry.all())},
        )

    @property
    def command_bus(self) -> CommandBus:
        return self._bus

    @property
    def shortcut_enabled(self) -> bool:
        return self._shortcut_enabled

    @property
    def shortcut_text(self) -> str:
        return self._shortcut_binding.text

    def register_commands(self, registrar: Callable[[CommandRegistry], None]) -> None:
        registrar(self._registry)
        self._refresh_palette(self._palette.current_query())

    def configure_shortcut(self, *, enabled: bool, shortcut_text: str) -> None:
        binding = self._parse_shortcut_binding(shortcut_text)
        enabled_value = bool(enabled)
        if enabled_value == self._shortcut_enabled and binding.text == self._shortcut_binding.text:
            return

        self._shortcut_enabled = enabled_value
        self._shortcut_binding = binding
        self._dispose_shortcut_handlers()
        self._install_shortcut_handlers()

        if not self._shortcut_enabled:
            self._hide_palette(trigger="shortcut_disabled")

        self._event_streamer.record(
            "shortcut.configured",
            source="command_runtime",
            payload={
                "enabled": self._shortcut_enabled,
                "keybind": self._shortcut_binding.text,
            },
        )

    def execute(
        self,
        command_id: str,
        *,
        source: str = "runtime",
        args: Mapping[str, object] | None = None,
    ) -> CommandResult:
        return self._bus.execute(command_id, source=source, args=args)

    def toggle_palette(self) -> None:
        if self._palette.isVisible():
            self._hide_palette(trigger="toggle")
            return
        self.show_palette()

    def show_palette(self) -> None:
        self._refresh_palette(self._palette.current_query())
        self._palette.open_centered(anchor=self._anchor_widget())
        self._event_streamer.record(
            "palette.shown",
            source="command_runtime",
            payload={},
        )

    def _run_from_palette(self, command_id: str) -> None:
        result = self.execute(command_id, source="palette")
        if result.ok:
            self._hide_palette(trigger="command")
            return
        message = result.message or "Command failed."
        self._palette.set_status(message)

    def _refresh_palette(self, query: str) -> None:
        entries = self._bus.catalog(query=query, source="palette", include_disabled=True)
        self._palette.set_entries(entries)

    def _anchor_widget(self) -> QWidget | None:
        active = self._app.activeWindow()
        if active is not None:
            return active
        if self._anchor_provider is not None:
            return self._anchor_provider()
        return None

    def _install_shortcut_handlers(self) -> None:
        if not self._shortcut_enabled:
            self._event_streamer.record(
                "shortcut.bound",
                source="command_runtime",
                payload={
                    "enabled": False,
                    "key": self._shortcut_binding.text,
                    "scope": "disabled",
                    "backend": "none",
                },
            )
            return

        if sys.platform.startswith("win"):
            native_filter = _WindowsGlobalHotkeyFilter(on_hotkey=self._on_global_hotkey)
            self._app.installNativeEventFilter(native_filter)
            win_vk = self._shortcut_binding.win_virtual_key
            if win_vk is not None and native_filter.register(
                modifiers=self._shortcut_binding.win_modifiers,
                virtual_key=win_vk,
            ):
                self._native_shortcut_filter = native_filter
                self._event_streamer.record(
                    "shortcut.bound",
                    source="command_runtime",
                    payload={
                        "enabled": True,
                        "key": self._shortcut_binding.text,
                        "scope": "global_with_unfocused_close_only",
                        "backend": "winapi",
                    },
                )
                return
            self._app.removeNativeEventFilter(native_filter)
            self._event_streamer.record(
                "shortcut.bind_failed",
                source="command_runtime",
                payload={
                    "enabled": True,
                    "key": self._shortcut_binding.text,
                    "scope": "global_with_unfocused_close_only",
                    "backend": "winapi",
                },
            )

        self._event_shortcut_filter = _CommandShortcutFilter(
            on_toggle_palette=self.toggle_palette,
            is_match=self._shortcut_matches_event,
        )
        self._app.installEventFilter(self._event_shortcut_filter)
        self._event_streamer.record(
            "shortcut.bound",
            source="command_runtime",
            payload={
                "enabled": True,
                "key": self._shortcut_binding.text,
                "scope": "app",
                "backend": "qt_event_filter",
            },
        )

    def _dispose_shortcut_handlers(self) -> None:
        native_filter = self._native_shortcut_filter
        self._native_shortcut_filter = None
        if native_filter is not None:
            try:
                native_filter.unregister()
            finally:
                try:
                    self._app.removeNativeEventFilter(native_filter)
                except Exception:
                    pass

        event_filter = self._event_shortcut_filter
        self._event_shortcut_filter = None
        if event_filter is not None:
            try:
                self._app.removeEventFilter(event_filter)
            except Exception:
                pass

    def _on_global_hotkey(self) -> None:
        if self._app.applicationState() == Qt.ApplicationState.ApplicationActive:
            if self._palette.isVisible():
                self._hide_palette(trigger="global_hotkey_active")
                return
            self.show_palette()
            return
        if not self._palette.isVisible():
            return
        self._hide_palette(trigger="global_hotkey")

    def _hide_palette(self, *, trigger: str) -> None:
        if not self._palette.isVisible():
            return
        self._palette.hide()
        self._event_streamer.record(
            "palette.hidden",
            source="command_runtime",
            payload={"trigger": trigger},
        )

    def _shortcut_matches_event(self, event: QKeyEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.isAutoRepeat():
            return False
        modifiers = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if modifiers != self._shortcut_binding.modifiers:
            return False
        return int(event.key()) == self._shortcut_binding.key

    def _parse_shortcut_binding(self, text: str) -> ShortcutBinding:
        binding = self._try_parse_shortcut_binding(text)
        if binding is not None:
            return binding
        fallback = self._try_parse_shortcut_binding(_DEFAULT_SHORTCUT_TEXT)
        if fallback is None:
            raise RuntimeError("Failed to parse default shortcut binding.")
        return fallback

    def _try_parse_shortcut_binding(self, text: str | None) -> ShortcutBinding | None:
        if not isinstance(text, str):
            return None
        normalized = text.strip()
        if not normalized:
            return None

        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            return None

        modifiers = Qt.KeyboardModifier.NoModifier
        win_modifiers = 0
        for token in parts[:-1]:
            token_lower = token.lower()
            if token_lower in {"ctrl", "control", "ctl"}:
                modifiers |= Qt.KeyboardModifier.ControlModifier
                win_modifiers |= _WINDOWS_MOD_CTRL
                continue
            if token_lower in {"alt", "option"}:
                modifiers |= Qt.KeyboardModifier.AltModifier
                win_modifiers |= _WINDOWS_MOD_ALT
                continue
            if token_lower in {"shift"}:
                modifiers |= Qt.KeyboardModifier.ShiftModifier
                win_modifiers |= _WINDOWS_MOD_SHIFT
                continue
            if token_lower in {"meta", "win", "windows", "cmd", "command"}:
                modifiers |= Qt.KeyboardModifier.MetaModifier
                win_modifiers |= _WINDOWS_MOD_WIN
                continue
            return None

        key_info = self._parse_shortcut_key(parts[-1])
        if key_info is None:
            return None
        key_value, key_label, win_vk = key_info

        labels: list[str] = []
        if bool(modifiers & Qt.KeyboardModifier.ControlModifier):
            labels.append("Ctrl")
        if bool(modifiers & Qt.KeyboardModifier.AltModifier):
            labels.append("Alt")
        if bool(modifiers & Qt.KeyboardModifier.ShiftModifier):
            labels.append("Shift")
        if bool(modifiers & Qt.KeyboardModifier.MetaModifier):
            labels.append("Meta")
        labels.append(key_label)
        normalized_text = "+".join(labels)

        return ShortcutBinding(
            text=normalized_text,
            key=key_value,
            modifiers=modifiers,
            win_modifiers=win_modifiers,
            win_virtual_key=win_vk,
        )

    def _parse_shortcut_key(self, token: str) -> tuple[int, str, int | None] | None:
        name = token.strip().lower()
        if not name:
            return None

        named_map: dict[str, tuple[int, str, int | None]] = {
            "space": (int(Qt.Key.Key_Space), "Space", 0x20),
            "tab": (int(Qt.Key.Key_Tab), "Tab", 0x09),
            "enter": (int(Qt.Key.Key_Return), "Return", 0x0D),
            "return": (int(Qt.Key.Key_Return), "Return", 0x0D),
            "esc": (int(Qt.Key.Key_Escape), "Esc", 0x1B),
            "escape": (int(Qt.Key.Key_Escape), "Esc", 0x1B),
            "backspace": (int(Qt.Key.Key_Backspace), "Backspace", 0x08),
            "delete": (int(Qt.Key.Key_Delete), "Delete", 0x2E),
            "del": (int(Qt.Key.Key_Delete), "Delete", 0x2E),
            "up": (int(Qt.Key.Key_Up), "Up", 0x26),
            "down": (int(Qt.Key.Key_Down), "Down", 0x28),
            "left": (int(Qt.Key.Key_Left), "Left", 0x25),
            "right": (int(Qt.Key.Key_Right), "Right", 0x27),
            "home": (int(Qt.Key.Key_Home), "Home", 0x24),
            "end": (int(Qt.Key.Key_End), "End", 0x23),
            "pageup": (int(Qt.Key.Key_PageUp), "PageUp", 0x21),
            "pgup": (int(Qt.Key.Key_PageUp), "PageUp", 0x21),
            "pagedown": (int(Qt.Key.Key_PageDown), "PageDown", 0x22),
            "pgdown": (int(Qt.Key.Key_PageDown), "PageDown", 0x22),
            "minus": (int(Qt.Key.Key_Minus), "Minus", 0xBD),
            "plus": (int(Qt.Key.Key_Plus), "Plus", 0xBB),
            "equal": (int(Qt.Key.Key_Equal), "Equal", 0xBB),
            "comma": (int(Qt.Key.Key_Comma), "Comma", 0xBC),
            "period": (int(Qt.Key.Key_Period), "Period", 0xBE),
            "slash": (int(Qt.Key.Key_Slash), "Slash", 0xBF),
            "backslash": (int(Qt.Key.Key_Backslash), "Backslash", 0xDC),
            "semicolon": (int(Qt.Key.Key_Semicolon), "Semicolon", 0xBA),
            "apostrophe": (int(Qt.Key.Key_Apostrophe), "Apostrophe", 0xDE),
            "bracketleft": (int(Qt.Key.Key_BracketLeft), "BracketLeft", 0xDB),
            "bracketright": (int(Qt.Key.Key_BracketRight), "BracketRight", 0xDD),
            "grave": (int(Qt.Key.Key_QuoteLeft), "QuoteLeft", 0xC0),
            "quoteleft": (int(Qt.Key.Key_QuoteLeft), "QuoteLeft", 0xC0),
            "-": (int(Qt.Key.Key_Minus), "Minus", 0xBD),
            "=": (int(Qt.Key.Key_Equal), "Equal", 0xBB),
            ",": (int(Qt.Key.Key_Comma), "Comma", 0xBC),
            ".": (int(Qt.Key.Key_Period), "Period", 0xBE),
            "/": (int(Qt.Key.Key_Slash), "Slash", 0xBF),
            "\\": (int(Qt.Key.Key_Backslash), "Backslash", 0xDC),
            ";": (int(Qt.Key.Key_Semicolon), "Semicolon", 0xBA),
            "'": (int(Qt.Key.Key_Apostrophe), "Apostrophe", 0xDE),
            "[": (int(Qt.Key.Key_BracketLeft), "BracketLeft", 0xDB),
            "]": (int(Qt.Key.Key_BracketRight), "BracketRight", 0xDD),
            "`": (int(Qt.Key.Key_QuoteLeft), "QuoteLeft", 0xC0),
        }
        mapped = named_map.get(name)
        if mapped is not None:
            return mapped

        if len(name) == 1 and name.isalpha():
            upper = name.upper()
            return (
                int(getattr(Qt.Key, f"Key_{upper}")),
                upper,
                ord(upper),
            )
        if len(name) == 1 and name.isdigit():
            return (
                int(getattr(Qt.Key, f"Key_{name}")),
                name,
                ord(name),
            )
        if name.startswith("f") and name[1:].isdigit():
            index = int(name[1:])
            if 1 <= index <= 24:
                return (
                    int(getattr(Qt.Key, f"Key_F{index}")),
                    f"F{index}",
                    0x70 + index - 1,
                )
        return None

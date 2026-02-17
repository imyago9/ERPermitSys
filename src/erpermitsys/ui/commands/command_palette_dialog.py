from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QScreen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.core import CommandCatalogEntry


class CommandPaletteDialog(QDialog):
    command_requested = Signal(str)
    query_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandPaletteDialog")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(620, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        frame = QFrame(self)
        frame.setObjectName("CommandPaletteFrame")
        root.addWidget(frame)

        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(14, 14, 14, 14)
        frame_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("Command Palette", frame)
        title.setObjectName("CommandPaletteTitle")
        shortcut = QLabel("Ctrl+Space", frame)
        shortcut.setObjectName("CommandPaletteShortcut")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(shortcut)
        frame_layout.addLayout(title_row)

        self._input = QLineEdit(frame)
        self._input.setObjectName("CommandPaletteInput")
        self._input.setPlaceholderText("Type a command name...")
        frame_layout.addWidget(self._input)

        self._list = QListWidget(frame)
        self._list.setObjectName("CommandPaletteList")
        self._list.setUniformItemSizes(True)
        frame_layout.addWidget(self._list, 1)

        self._status = QLabel(frame)
        self._status.setObjectName("CommandPaletteStatus")
        self._status.setWordWrap(True)
        frame_layout.addWidget(self._status)

        self._input.textChanged.connect(self._on_query_text_changed)
        self._input.returnPressed.connect(self._emit_selected_command)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.itemClicked.connect(lambda *_: self._sync_status())
        self._list.itemSelectionChanged.connect(self._sync_status)

    def open_centered(self, *, anchor: QWidget | None = None) -> None:
        self._position(anchor=anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._input.selectAll()

    def current_query(self) -> str:
        return self._input.text()

    def set_entries(self, entries: Sequence[CommandCatalogEntry]) -> None:
        self._list.clear()
        for entry in entries:
            line = f"{entry.title}    [{entry.category}]"
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, entry.command_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.description)
            item.setData(Qt.ItemDataRole.UserRole + 2, entry.enabled)
            tooltip_bits = [entry.command_id]
            if entry.aliases:
                tooltip_bits.append("aliases: " + ", ".join(entry.aliases))
            if entry.description:
                tooltip_bits.append(entry.description)
            item.setToolTip("\n".join(tooltip_bits))
            if not entry.enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(f"{line} (Unavailable)")
            self._list.addItem(item)

        if self._list.count() == 0:
            self._status.setText("No commands match your search.")
            return

        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is not None and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
                self._list.setCurrentRow(index)
                break
        self._sync_status()

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def _on_query_text_changed(self, text: str) -> None:
        self.query_changed.emit(text)

    def _on_item_activated(self, _item: QListWidgetItem) -> None:
        self._emit_selected_command()

    def _emit_selected_command(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        if not bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(command_id, str) or not command_id:
            return
        self.command_requested.emit(command_id)

    def _sync_status(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        description = item.data(Qt.ItemDataRole.UserRole + 1)
        command_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = bool(item.data(Qt.ItemDataRole.UserRole + 2))
        if not isinstance(command_id, str):
            return
        if enabled:
            if isinstance(description, str) and description:
                self._status.setText(f"{description}\n{command_id}")
            else:
                self._status.setText(command_id)
            return
        self._status.setText(f"Unavailable right now\n{command_id}")

    def _position(self, *, anchor: QWidget | None = None) -> None:
        screen = self._resolve_screen(anchor=anchor)
        geometry = screen.availableGeometry()

        target_w = min(680, max(520, int(geometry.width() * 0.44)))
        target_h = min(380, max(290, int(geometry.height() * 0.36)))
        self.resize(target_w, target_h)

        center_x = geometry.left() + (geometry.width() - target_w) // 2
        centered_y = geometry.top() + (geometry.height() - target_h) // 2
        shift_up = int(geometry.height() * 0.16)
        target_y = max(geometry.top() + 28, centered_y - shift_up)

        self.move(center_x, target_y)

    def _resolve_screen(self, *, anchor: QWidget | None = None) -> QScreen:
        if anchor is not None:
            screen = anchor.screen()
            if screen is not None:
                return screen

        active = QApplication.activeWindow()
        if active is not None and active.screen() is not None:
            return active.screen()  # type: ignore[return-value]

        primary = QApplication.primaryScreen()
        if primary is not None:
            return primary
        raise RuntimeError("No screen available for command palette positioning.")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

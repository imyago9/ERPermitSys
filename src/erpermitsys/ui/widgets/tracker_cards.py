from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.tracker_models import normalize_document_review_status, normalize_slot_status
from erpermitsys.ui.assets import icon_asset_path


class AttachedContactChip(QFrame):
    def __init__(
        self,
        *,
        title: str,
        detail_lines: Sequence[str] | None,
        metadata_layout: str = "rows",
        on_edit: Callable[[], None] | None = None,
        edit_tooltip: str = "Edit",
        on_remove: Callable[[], None],
        remove_tooltip: str = "Remove",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AttachedContactChip")
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        title_label = QLabel(str(title or "").strip() or "(Unnamed)", self)
        title_label.setObjectName("AttachedContactChipTitle")
        title_label.setWordWrap(True)
        text_layout.addWidget(title_label, 0)

        normalized_layout = str(metadata_layout or "").strip().casefold()
        if normalized_layout == "bundle_groups":
            self._add_bundle_grouped_metadata_rows(text_layout, detail_lines or [])
        elif normalized_layout == "contact_bundle_values":
            self._add_contact_bundle_value_group_rows(text_layout, detail_lines or [])
        else:
            self._add_metadata_rows(text_layout, detail_lines or [])
        layout.addLayout(text_layout, 1)

        remove_button = QPushButton("x", self)
        remove_button.setObjectName("AttachedContactChipRemoveButton")
        remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_button.setFixedSize(18, 18)
        self._remove_button = remove_button
        remove_button.clicked.connect(lambda _checked=False: on_remove())
        remove_button.setToolTip(remove_tooltip)
        remove_button.setFlat(True)
        remove_button.setMouseTracking(True)
        remove_button.installEventFilter(self)
        remove_icon = QIcon(icon_asset_path("close_window.png"))
        if not remove_icon.isNull():
            remove_button.setText("")
            remove_button.setIcon(remove_icon)
            remove_button.setIconSize(remove_button.size() * 0.55)
        remove_button.hide()
        remove_button_layout = QVBoxLayout()
        remove_button_layout.setContentsMargins(0, 0, 0, 0)
        remove_button_layout.setSpacing(0)
        self._action_buttons: list[QPushButton] = []
        if on_edit is not None:
            edit_button = QPushButton("Edit", self)
            edit_button.setObjectName("AttachedContactChipEditButton")
            edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_button.setFixedHeight(18)
            edit_button.setToolTip(edit_tooltip)
            edit_button.setFlat(True)
            edit_button.setMouseTracking(True)
            edit_button.clicked.connect(lambda _checked=False: on_edit())
            edit_button.installEventFilter(self)
            edit_button.hide()
            self._edit_button = edit_button
            self._action_buttons.append(edit_button)
        self._action_buttons.append(remove_button)
        remove_button_layout.addWidget(
            remove_button,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        if on_edit is not None:
            edit_button = getattr(self, "_edit_button", None)
            if edit_button is not None:
                remove_button_layout.addWidget(
                    edit_button,
                    0,
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
                )
        remove_button_layout.addStretch(1)
        layout.addLayout(remove_button_layout, 0)

    @staticmethod
    def _metadata_segments(raw_line: str) -> list[str]:
        return [segment.strip() for segment in str(raw_line or "").split("|") if segment.strip()]

    def _add_detail_label(self, layout: QVBoxLayout, line: str) -> None:
        parent_widget = layout.parentWidget() if layout.parentWidget() is not None else self
        detail_label = QLabel(line, parent_widget)
        detail_label.setObjectName("AttachedContactChipDetail")
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label, 0)

    def _add_key_style_label(self, layout: QVBoxLayout, line: str) -> None:
        parent_widget = layout.parentWidget() if layout.parentWidget() is not None else self
        key_label = QLabel(line, parent_widget)
        key_label.setObjectName("AttachedContactChipLabel")
        key_label.setWordWrap(True)
        layout.addWidget(key_label, 0)

    def _add_metadata_pair_row(self, layout: QVBoxLayout, *, key_text: str, value_text: str) -> None:
        metadata_row = QFrame(self)
        metadata_row.setObjectName("AttachedContactChipMetaRow")
        metadata_row_layout = QHBoxLayout(metadata_row)
        metadata_row_layout.setContentsMargins(6, 2, 6, 2)
        metadata_row_layout.setSpacing(6)
        metadata_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        key_label = QLabel(key_text, metadata_row)
        key_label.setObjectName("AttachedContactChipLabel")
        key_label.setMinimumWidth(72)
        key_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        metadata_row_layout.addWidget(key_label, 0)

        value_label = QLabel(value_text, metadata_row)
        value_label.setObjectName("AttachedContactChipDetail")
        value_label.setWordWrap(True)
        value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        metadata_row_layout.addWidget(value_label, 1)
        layout.addWidget(metadata_row, 0)

    def _add_metadata_rows(self, layout: QVBoxLayout, detail_lines: Sequence[str]) -> None:
        for raw_line in detail_lines:
            for line in self._metadata_segments(str(raw_line or "")):
                metadata_pair = self._split_metadata_pair(line)
                if metadata_pair is None:
                    self._add_detail_label(layout, line)
                    continue
                key_text, value_text = metadata_pair
                key_normalized = self._normalized_metadata_key(key_text)
                if key_normalized == "bundle":
                    self._add_key_style_label(layout, value_text)
                    continue
                if key_normalized in {"email(s)", "number(s)"}:
                    self._add_detail_label(layout, value_text)
                    continue
                self._add_metadata_pair_row(layout, key_text=key_text, value_text=value_text)

    def _add_bundle_grouped_metadata_rows(self, layout: QVBoxLayout, detail_lines: Sequence[str]) -> None:
        segments: list[str] = []
        for raw_line in detail_lines:
            segments.extend(self._metadata_segments(str(raw_line or "")))

        groups: list[list[str]] = []
        leading_segments: list[str] = []
        current_group: list[str] = []
        for segment in segments:
            metadata_pair = self._split_metadata_pair(segment)
            key_normalized = self._normalized_metadata_key(metadata_pair[0]) if metadata_pair else ""
            if key_normalized == "bundle":
                if current_group:
                    groups.append(current_group)
                current_group = [segment]
                continue
            if current_group:
                current_group.append(segment)
            else:
                leading_segments.append(segment)
        if current_group:
            groups.append(current_group)

        for segment in leading_segments:
            metadata_pair = self._split_metadata_pair(segment)
            if metadata_pair is None:
                self._add_detail_label(layout, segment)
                continue
            key_text, value_text = metadata_pair
            key_normalized = self._normalized_metadata_key(key_text)
            if key_normalized == "bundle":
                self._add_key_style_label(layout, value_text)
                continue
            if key_normalized in {"email(s)", "number(s)"}:
                self._add_detail_label(layout, value_text)
                continue
            self._add_detail_label(layout, f"{key_text} {value_text}")

        if not groups:
            self._add_metadata_rows(layout, detail_lines)
            return

        for group_segments in groups:
            group_frame = QFrame(self)
            group_frame.setObjectName("AttachedContactChipMetaGroup")
            group_layout = QVBoxLayout(group_frame)
            group_layout.setContentsMargins(8, 6, 8, 6)
            group_layout.setSpacing(3)
            for segment in group_segments:
                metadata_pair = self._split_metadata_pair(segment)
                if metadata_pair is None:
                    self._add_detail_label(group_layout, segment)
                    continue

                key_text, value_text = metadata_pair
                key_normalized = self._normalized_metadata_key(key_text)
                if key_normalized == "bundle":
                    self._add_key_style_label(group_layout, value_text)
                    continue
                if key_normalized in {"email(s)", "number(s)"}:
                    self._add_detail_label(group_layout, value_text)
                    continue
                row_host = QWidget(group_frame)
                row_layout = QHBoxLayout(row_host)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(6)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

                key_label = QLabel(key_text, row_host)
                key_label.setObjectName("AttachedContactChipLabel")
                key_label.setMinimumWidth(72)
                key_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                row_layout.addWidget(key_label, 0)

                value_label = QLabel(value_text, row_host)
                value_label.setObjectName("AttachedContactChipDetail")
                value_label.setWordWrap(True)
                value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                row_layout.addWidget(value_label, 1)
                group_layout.addWidget(row_host, 0)
            layout.addWidget(group_frame, 0)

    def _add_contact_bundle_value_group_rows(self, layout: QVBoxLayout, detail_lines: Sequence[str]) -> None:
        segments: list[str] = []
        for raw_line in detail_lines:
            segments.extend(self._metadata_segments(str(raw_line or "")))

        value_lines: list[str] = []
        extra_lines: list[str] = []
        for segment in segments:
            metadata_pair = self._split_metadata_pair(segment)
            if metadata_pair is None:
                extra_lines.append(segment)
                continue
            key_text, value_text = metadata_pair
            key_normalized = self._normalized_metadata_key(key_text)
            if key_normalized in {"email(s)", "number(s)"}:
                value_lines.append(value_text)
                continue
            if key_normalized == "bundle":
                self._add_key_style_label(layout, value_text)
                continue
            extra_lines.append(f"{key_text} {value_text}")

        if value_lines:
            value_group = QFrame(self)
            value_group.setObjectName("AttachedContactChipValueGroup")
            value_layout = QVBoxLayout(value_group)
            value_layout.setContentsMargins(8, 6, 8, 6)
            value_layout.setSpacing(3)
            for value_line in value_lines:
                self._add_detail_label(value_layout, value_line)
            layout.addWidget(value_group, 0)

        for line in extra_lines:
            self._add_detail_label(layout, line)

    @staticmethod
    def _normalized_metadata_key(key_text: str) -> str:
        return str(key_text or "").rstrip(":").strip().casefold()

    @staticmethod
    def _split_metadata_pair(raw_segment: str) -> tuple[str, str] | None:
        segment = str(raw_segment or "").strip()
        if not segment or ":" not in segment:
            return None
        key, value = segment.split(":", 1)
        key_text = key.strip()
        value_text = value.strip()
        if not key_text:
            return None
        if key_text.casefold() in {"http", "https", "mailto", "tel"}:
            return None
        if len(key_text) > 28:
            return None
        return (f"{key_text}:", value_text or "None")

    def enterEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_remove_button_visibility)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_remove_button_visibility)
        super().leaveEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        action_buttons = getattr(self, "_action_buttons", [])
        if watched in action_buttons and event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.Leave,
            QEvent.Type.MouseMove,
            QEvent.Type.HoverEnter,
            QEvent.Type.HoverLeave,
        ):
            QTimer.singleShot(0, self._sync_remove_button_visibility)
        return super().eventFilter(watched, event)

    def _sync_remove_button_visibility(self) -> None:
        action_buttons = getattr(self, "_action_buttons", [])
        if not action_buttons:
            return
        show_actions = self.underMouse() or any(button.underMouse() for button in action_buttons)
        for button in action_buttons:
            button.setVisible(show_actions)


class TrackerHoverEntityCard(QFrame):
    def __init__(
        self,
        *,
        title: str,
        title_field: str,
        subtitle: str,
        subtitle_field: str,
        meta: str,
        meta_field: str,
        on_edit: Callable[[], None] | None,
        on_remove: Callable[[], None] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TrackerListCard")
        self.setProperty("selected", "false")
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(106)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        title_text = str(title or "").replace("\n", " ").strip()
        title_label = QLabel(title_text, self)
        title_label.setObjectName("TrackerListFieldValue")
        title_label.setProperty("field", title_field)
        title_label.setWordWrap(False)
        text_layout.addWidget(title_label, 0)

        if subtitle.strip():
            subtitle_text = str(subtitle or "").replace("\n", " ").strip()
            subtitle_label = QLabel(subtitle_text, self)
            subtitle_label.setObjectName("TrackerListFieldValue")
            subtitle_label.setProperty("field", subtitle_field)
            subtitle_label.setWordWrap(False)
            text_layout.addWidget(subtitle_label, 0)

        if meta.strip():
            meta_text = str(meta or "").replace("\n", " ").strip()
            meta_label = QLabel(meta_text, self)
            meta_label.setObjectName("TrackerListFieldValue")
            meta_label.setProperty("field", meta_field)
            meta_label.setWordWrap(False)
            text_layout.addWidget(meta_label, 0)

        layout.addLayout(text_layout, 1)

        self._action_buttons: list[QPushButton] = []
        if on_edit is None and on_remove is None:
            return

        actions_layout = QVBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        if on_remove is not None:
            remove_button = QPushButton("x", self)
            remove_button.setObjectName("AttachedContactChipRemoveButton")
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.setFixedSize(18, 18)
            remove_button.setToolTip("Delete")
            remove_button.setFlat(True)
            remove_button.setMouseTracking(True)
            remove_button.clicked.connect(lambda _checked=False: on_remove())
            remove_button.installEventFilter(self)
            remove_icon = QIcon(icon_asset_path("close_window.png"))
            if not remove_icon.isNull():
                remove_button.setText("")
                remove_button.setIcon(remove_icon)
                remove_button.setIconSize(remove_button.size() * 0.55)
            remove_button.hide()
            self._action_buttons.append(remove_button)
            actions_layout.addWidget(
                remove_button,
                0,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            )

        if on_edit is not None:
            edit_button = QPushButton("Edit", self)
            edit_button.setObjectName("AttachedContactChipEditButton")
            edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_button.setFixedHeight(18)
            edit_button.setToolTip("Edit")
            edit_button.setFlat(True)
            edit_button.setMouseTracking(True)
            edit_button.clicked.connect(lambda _checked=False: on_edit())
            edit_button.installEventFilter(self)
            edit_button.hide()
            self._action_buttons.append(edit_button)
            actions_layout.addWidget(
                edit_button,
                0,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            )

        actions_layout.addStretch(1)
        layout.addLayout(actions_layout, 0)

    def enterEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_action_buttons_visibility)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_action_buttons_visibility)
        super().leaveEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        action_buttons = getattr(self, "_action_buttons", [])
        if watched in action_buttons and event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.Leave,
            QEvent.Type.MouseMove,
            QEvent.Type.HoverEnter,
            QEvent.Type.HoverLeave,
        ):
            QTimer.singleShot(0, self._sync_action_buttons_visibility)
        return super().eventFilter(watched, event)

    def _sync_action_buttons_visibility(self) -> None:
        action_buttons = getattr(self, "_action_buttons", [])
        if not action_buttons:
            return
        show_actions = self.underMouse() or any(button.underMouse() for button in action_buttons)
        for button in action_buttons:
            button.setVisible(show_actions)


class DocumentChecklistSlotCard(QFrame):
    def __init__(
        self,
        *,
        slot_label: str,
        slot_id: str,
        required: bool,
        status: str,
        file_count: int,
        status_counts: dict[str, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DocumentChecklistSlotCard")
        self.setProperty("selected", "false")
        self.setProperty("status", normalize_slot_status(status))
        self.setProperty("required", "true" if required else "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        icon_label = QLabel(self)
        icon_label.setObjectName("DocumentChecklistSlotIcon")
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        icon_pixmap = folder_icon.pixmap(20, 20)
        if not icon_pixmap.isNull():
            icon_label.setPixmap(icon_pixmap)
        else:
            icon_label.setText("[]")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(24, 24)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        center.addStretch(1)

        title_label = QLabel(str(slot_label or "").strip() or "(Unnamed Slot)", self)
        title_label.setObjectName("DocumentChecklistSlotTitle")
        title_label.setWordWrap(False)
        center.addWidget(title_label, 0)
        center.addStretch(1)

        layout.addLayout(center, 1)
        layout.setAlignment(center, Qt.AlignmentFlag.AlignVCenter)

        badges = QVBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        badges.setSpacing(4)
        badges.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        required_badge = QLabel("Required" if required else "Optional", self)
        required_badge.setObjectName("DocumentChecklistSlotBadge")
        required_badge.setProperty("badgeRole", "required" if required else "optional")
        required_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badges.addWidget(required_badge, 0, Qt.AlignmentFlag.AlignRight)

        normalized_status_counts: dict[str, int] = {}
        for raw_status, raw_count in (status_counts or {}).items():
            normalized_key = normalize_slot_status(raw_status)
            count_value = max(0, int(raw_count or 0))
            if count_value <= 0:
                continue
            normalized_status_counts[normalized_key] = (
                normalized_status_counts.get(normalized_key, 0) + count_value
            )

        ordered_statuses: list[tuple[str, int]] = []
        for status_key in ("accepted", "rejected", "uploaded", "superseded"):
            count_value = normalized_status_counts.get(status_key, 0)
            if count_value > 0:
                ordered_statuses.append((status_key, count_value))

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        status_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if not ordered_statuses:
            status_badge = QLabel(normalize_slot_status(status).replace("_", " ").title(), self)
            status_badge.setObjectName("DocumentChecklistSlotBadge")
            status_badge.setProperty("badgeRole", normalize_slot_status(status))
            status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status_row.addWidget(status_badge, 0, Qt.AlignmentFlag.AlignRight)
        else:
            for status_key, count_value in ordered_statuses:
                status_badge = QLabel(
                    f"{status_key.replace('_', ' ').title()} ({count_value})",
                    self,
                )
                status_badge.setObjectName("DocumentChecklistSlotBadge")
                status_badge.setProperty("badgeRole", status_key)
                status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                status_row.addWidget(status_badge, 0, Qt.AlignmentFlag.AlignRight)

        badges.addLayout(status_row)

        count_label = QLabel(f"{max(0, int(file_count))} file(s)", self)
        count_label.setObjectName("DocumentChecklistSlotCount")
        count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        badges.addWidget(count_label, 0, Qt.AlignmentFlag.AlignRight)

        layout.addLayout(badges, 0)


class PermitDocumentFileCard(QFrame):
    def __init__(
        self,
        *,
        file_name: str,
        extension_label: str,
        meta_text: str,
        version_text: str,
        review_status: str,
        icon: QIcon | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PermitDocumentFileCard")
        self.setProperty("selected", "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(86)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        icon_label = QLabel(self)
        icon_label.setObjectName("PermitDocumentFileIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(26, 26)
        icon_pixmap = QPixmap()
        if icon is not None:
            icon_pixmap = icon.pixmap(22, 22)
        if icon_pixmap.isNull():
            fallback_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            icon_pixmap = fallback_icon.pixmap(22, 22)
        if not icon_pixmap.isNull():
            icon_label.setPixmap(icon_pixmap)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        center_layout = QVBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(2)

        title_label = QLabel(str(file_name or "").strip() or "Unnamed File", self)
        title_label.setObjectName("PermitDocumentFileTitle")
        title_label.setWordWrap(False)
        center_layout.addWidget(title_label, 0)

        version_label = QLabel(str(version_text or "").strip(), self)
        version_label.setObjectName("PermitDocumentFileMeta")
        version_label.setProperty("versionMeta", "true")
        version_label.setWordWrap(False)
        center_layout.addWidget(version_label, 0)

        meta_label = QLabel(str(meta_text or "").strip(), self)
        meta_label.setObjectName("PermitDocumentFileMeta")
        meta_label.setWordWrap(False)
        center_layout.addWidget(meta_label, 0)

        layout.addLayout(center_layout, 1)

        badges_layout = QVBoxLayout()
        badges_layout.setContentsMargins(0, 0, 0, 0)
        badges_layout.setSpacing(4)
        badges_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        extension_badge = QLabel(str(extension_label or "FILE").strip() or "FILE", self)
        extension_badge.setObjectName("PermitDocumentFileExt")
        extension_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        extension_badge.setMinimumWidth(52)
        badges_layout.addWidget(extension_badge, 0, Qt.AlignmentFlag.AlignRight)

        review_badge = QLabel(normalize_document_review_status(review_status).title(), self)
        review_badge.setObjectName("DocumentChecklistSlotBadge")
        review_badge.setProperty("badgeRole", normalize_document_review_status(review_status))
        review_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badges_layout.addWidget(review_badge, 0, Qt.AlignmentFlag.AlignRight)

        layout.addLayout(badges_layout, 0)


class TimelineEventBubble(QFrame):
    def __init__(
        self,
        *,
        date_text: str,
        event_type_text: str,
        summary: str,
        detail_lines: Sequence[str] | None = None,
        on_edit: Callable[[], None] | None = None,
        on_remove: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PermitTimelineBubble")
        self.setMouseTracking(True)
        self._remove_button: QPushButton | None = None
        self._edit_button: QPushButton | None = None
        self._action_buttons: list[QPushButton] = []
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(230)
        self.setMaximumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(5)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        date_label = QLabel(str(date_text or "").strip() or "(no date)", self)
        date_label.setObjectName("PermitTimelineDate")
        date_label.setWordWrap(False)
        header_layout.addWidget(date_label, 1)

        event_type_label_widget = QLabel(str(event_type_text or "").strip() or "Note", self)
        event_type_label_widget.setObjectName("PermitTimelineType")
        event_type_label_widget.setWordWrap(False)
        event_type_label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._event_type_label_widget = event_type_label_widget
        header_layout.addWidget(event_type_label_widget, 0, Qt.AlignmentFlag.AlignRight)

        action_layout = QVBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        if on_remove is not None:
            remove_button = QPushButton("", self)
            remove_button.setObjectName("PermitTimelineDeleteButton")
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.setFixedSize(18, 18)
            remove_button.setFlat(True)
            remove_button.setMouseTracking(True)
            remove_button.setToolTip("Delete timeline event")
            remove_icon = QIcon(icon_asset_path("close_window.png"))
            if not remove_icon.isNull():
                remove_button.setIcon(remove_icon)
                remove_button.setIconSize(remove_button.size() * 0.56)
            else:
                remove_button.setText("x")
            remove_button.clicked.connect(lambda _checked=False: on_remove())
            self._remove_button = remove_button
            remove_button.installEventFilter(self)
            remove_button.hide()
            self._action_buttons.append(remove_button)
            action_layout.addWidget(
                remove_button,
                0,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            )

        if on_edit is not None:
            edit_button = QPushButton("Edit", self)
            edit_button.setObjectName("AttachedContactChipEditButton")
            edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_button.setFixedHeight(18)
            edit_button.setFlat(True)
            edit_button.setToolTip("Edit timeline entry")
            edit_button.clicked.connect(lambda _checked=False: on_edit())
            edit_button.installEventFilter(self)
            edit_button.hide()
            self._edit_button = edit_button
            self._action_buttons.append(edit_button)
            action_layout.addWidget(
                edit_button,
                0,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            )

        action_layout.addStretch(1)
        header_layout.addLayout(action_layout, 0)

        layout.addLayout(header_layout)

        summary_label = QLabel(str(summary or "").strip() or "(no summary)", self)
        summary_label.setObjectName("PermitTimelineSummary")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label, 0)

        for raw_line in detail_lines or []:
            line = str(raw_line or "").strip()
            if not line:
                continue
            detail_label = QLabel(line, self)
            detail_label.setObjectName("PermitTimelineDetail")
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label, 0)

    def enterEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_remove_button_visibility)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        QTimer.singleShot(0, self._sync_remove_button_visibility)
        super().leaveEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        action_buttons = getattr(self, "_action_buttons", [])
        if watched in action_buttons and event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.Leave,
            QEvent.Type.MouseMove,
            QEvent.Type.HoverEnter,
            QEvent.Type.HoverLeave,
        ):
            QTimer.singleShot(0, self._sync_remove_button_visibility)
        return super().eventFilter(watched, event)

    def _sync_remove_button_visibility(self) -> None:
        action_buttons = getattr(self, "_action_buttons", [])
        if not action_buttons:
            return
        show_buttons = self.underMouse() or any(button.underMouse() for button in action_buttons)
        for button in action_buttons:
            button.setVisible(show_buttons)

    def event_type_badge_size_hint(self) -> tuple[int, int]:
        label = self._event_type_label_widget
        label.ensurePolished()
        width = max(
            label.sizeHint().width(),
            label.minimumSizeHint().width(),
            label.minimumWidth(),
        )
        height = max(
            label.sizeHint().height(),
            label.minimumSizeHint().height(),
            label.minimumHeight(),
        )
        return width, height

    def set_event_type_badge_size(self, width: int, height: int) -> None:
        label = self._event_type_label_widget
        if width > 0:
            label.setFixedWidth(width)
        if height > 0:
            label.setFixedHeight(height)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

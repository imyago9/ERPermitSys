from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import QEasingCurve, QEvent, QFileInfo, QObject, QPoint, QPropertyAnimation, QRect, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QColorDialog,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional runtime dependency
    QWebEngineView = None  # type: ignore[assignment]
try:
    from PySide6.QtWebChannel import QWebChannel
except Exception:  # pragma: no cover - optional runtime dependency
    QWebChannel = None  # type: ignore[assignment]

from erpermitsys.app.background_plugin_bridge import BackgroundPluginBridge
from erpermitsys.app.command_runtime import AppCommandContext, CommandRuntime
from erpermitsys.app.data_store import BACKEND_LOCAL_JSON, BACKEND_SUPABASE, LocalJsonDataStore
from erpermitsys.app.document_store import LocalPermitDocumentStore
from erpermitsys.app.settings_store import (
    DEFAULT_PALETTE_SHORTCUT,
    load_active_plugin_ids,
    load_dark_mode,
    load_data_storage_backend,
    load_data_storage_folder,
    load_palette_shortcut_enabled,
    load_palette_shortcut_keybind,
    normalize_data_storage_folder,
    save_active_plugin_ids,
    save_dark_mode,
    save_data_storage_backend,
    save_data_storage_folder,
    save_palette_shortcut_settings,
)
from erpermitsys.app.tracker_models import (
    PERMIT_EVENT_TYPES,
    ContactMethodRecord,
    ContactRecord,
    DocumentChecklistTemplate,
    JurisdictionRecord,
    PermitDocumentFolder,
    PermitDocumentRecord,
    PermitDocumentSlot,
    PermitEventRecord,
    PermitParty,
    PermitRecord,
    PropertyRecord,
    TrackerDataBundleV3,
    build_default_document_slots,
    build_document_slots_from_template,
    build_document_folders_from_slots,
    compute_permit_status,
    document_file_count_by_slot,
    ensure_default_document_structure,
    event_affects_status,
    event_type_label,
    normalize_event_type,
    normalize_list_color,
    normalize_document_review_status,
    normalize_parcel_id,
    normalize_permit_type,
    normalize_slot_id,
    normalize_slot_status,
    refresh_slot_status_from_documents,
)
from erpermitsys.app.updater import (
    GitHubReleaseUpdater,
    GitHubUpdateCheckResult,
    GitHubUpdateInfo,
    can_self_update_windows,
    is_packaged_runtime,
    launch_windows_zip_updater,
)
from erpermitsys.core import StateStreamer
from erpermitsys.plugins import PluginManager
from erpermitsys.plugins.api import PluginApiService
from erpermitsys.ui.assets import asset_path, icon_asset_path
from erpermitsys.ui.settings import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme
from erpermitsys.ui.window.app_dialogs import AppConfirmDialog, AppMessageDialog
from erpermitsys.ui.window.frameless_dialog import FramelessDialog
from erpermitsys.ui.window.frameless_window import FramelessWindow
from erpermitsys.version import APP_VERSION, GITHUB_RELEASE_ASSET_NAME, GITHUB_RELEASE_REPO


_PERMIT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("building", "Building"),
    ("demolition", "Demolition"),
    ("remodeling", "Remodeling"),
)
_ADMIN_LIST_COLOR_PRESETS: tuple[str, ...] = (
    "#2563EB",
    "#0D9488",
    "#16A34A",
    "#65A30D",
    "#CA8A04",
    "#EA580C",
    "#DC2626",
    "#BE185D",
    "#7C3AED",
    "#4338CA",
    "#0369A1",
    "#4B5563",
)
_UPDATE_STARTUP_DELAY_MS = 1800
_TimelineRenderRow = tuple[str, str, str, tuple[str, ...], str]
_TIMELINE_DEBUG_ENV = "ERPERMITSYS_TIMELINE_DEBUG"
_TIMELINE_DEBUG_LOG_ENV = "ERPERMITSYS_TIMELINE_DEBUG_LOG"
_TEMPLATE_DEFAULT_SENTINEL = "__built_in_default__"
_TEMPLATE_BUILTIN_BUILDING_ID = "__built_in_default_building__"
_TEMPLATE_BUILTIN_DEMOLITION_ID = "__built_in_default_demolition__"


def _hex_color_channels(value: str) -> tuple[int, int, int]:
    normalized = normalize_list_color(value)
    if not normalized:
        return (99, 116, 137)
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _mix_color_channels(
    source: tuple[int, int, int],
    target: tuple[int, int, int],
    target_weight: float,
) -> tuple[int, int, int]:
    weight = max(0.0, min(1.0, float(target_weight)))
    keep = 1.0 - weight
    return tuple(
        max(0, min(255, int(round((src * keep) + (dst * weight)))))
        for src, dst in zip(source, target)
    )


def _is_truthy_env(raw_value: str) -> bool:
    return str(raw_value or "").strip().casefold() in {"1", "true", "yes", "on", "y"}


def _normalize_card_tint_channels(channels: tuple[int, int, int]) -> tuple[int, int, int]:
    clamped = tuple(max(22, min(232, int(value))) for value in channels)
    darkest = min(clamped)
    brightest = max(clamped)
    if brightest < 56:
        return _mix_color_channels(clamped, (118, 136, 156), 0.46)
    if darkest > 224:
        return _mix_color_channels(clamped, (108, 126, 148), 0.52)
    return clamped


def _rgba_text(channels: tuple[int, int, int], alpha: int) -> str:
    r, g, b = channels
    return f"rgba({r}, {g}, {b}, {max(0, min(255, int(alpha)))})"


def _dot_ring_color(channels: tuple[int, int, int], *, selected: bool) -> str:
    luminance = (
        (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2])
    ) / 255.0
    if selected:
        return "rgba(241, 248, 255, 244)" if luminance < 0.52 else "rgba(22, 33, 46, 242)"
    return "rgba(204, 221, 240, 180)" if luminance < 0.52 else "rgba(40, 56, 72, 170)"


def _today_iso() -> str:
    return date.today().isoformat()


def _parse_iso_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed_dt = datetime.fromisoformat(normalized)
        return parsed_dt.date()
    except Exception:
        pass
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_action_detail_text(due_value: str) -> str:
    due_text = str(due_value or "").strip()
    return f"Due: {due_text}" if due_text else ""


def _extract_due_from_next_action_detail(detail_value: str) -> str:
    detail_text = str(detail_value or "").strip()
    if not detail_text:
        return ""
    prefix = "due:"
    if detail_text.casefold().startswith(prefix):
        return detail_text[len(prefix):].strip()
    return ""


def _set_dirty_bubble_state(bubble: QLabel | None, *, state: str) -> None:
    if bubble is None:
        return
    normalized_state = str(state or "").strip().casefold()
    if normalized_state not in {"dirty", "clean", "empty"}:
        normalized_state = "clean"
    bubble_text = {
        "dirty": "Unsaved",
        "clean": "Saved",
        "empty": "Empty",
    }.get(normalized_state, "Saved")
    bubble.setText(bubble_text)
    bubble.setProperty("dirtyState", normalized_state)
    style = bubble.style()
    style.unpolish(bubble)
    style.polish(bubble)
    bubble.update()


def _parse_multi_values(raw_value: str) -> list[str]:
    chunks = (
        str(raw_value or "")
        .replace("\r", "\n")
        .replace(";", "\n")
        .replace(",", "\n")
        .splitlines()
    )
    values: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def _join_multi_values(values: Sequence[str]) -> str:
    return ", ".join(_parse_multi_values("\n".join(str(value) for value in values)))


def _permit_type_label(permit_type: str) -> str:
    normalized = normalize_permit_type(permit_type)
    if normalized == "demolition":
        return "Demolition"
    if normalized == "remodeling":
        return "Remodeling"
    return "Building"


def _prefill_permit_events_from_milestones(
    *,
    request_date: str,
    application_date: str,
    issued_date: str,
    final_date: str,
    completion_date: str,
    next_action_text: str = "",
    next_action_due: str = "",
) -> list[PermitEventRecord]:
    rows: list[tuple[int, PermitEventRecord]] = []
    field_rows = (
        (request_date, "requested", "Permit requested"),
        (application_date, "submitted", "Application submitted"),
        (issued_date, "issued", "Permit issued"),
        (final_date, "finaled", "Permit finaled"),
        (completion_date, "closed", "Permit closed"),
    )
    for lifecycle_index, (raw_date, event_type, summary) in enumerate(field_rows):
        event_date = str(raw_date or "").strip()
        if not event_date:
            continue
        rows.append(
            (
                lifecycle_index,
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type=event_type,
                    event_date=event_date,
                    summary=summary,
                    detail="",
                    actor_contact_id="",
                    attachments=[],
                ),
            )
        )

    next_action_summary = str(next_action_text or "").strip()
    next_action_due_text = str(next_action_due or "").strip()
    if next_action_summary or next_action_due_text:
        timeline_date = (
            next_action_due_text
            if _parse_iso_date(next_action_due_text) is not None
            else _today_iso()
        )
        rows.append(
            (
                len(field_rows),
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type="note",
                    event_date=timeline_date,
                    summary=next_action_summary or "Next Action",
                    detail=_next_action_detail_text(next_action_due_text),
                    actor_contact_id="",
                    attachments=[],
                ),
            )
        )

    rows.sort(
        key=lambda pair: (
            _parse_iso_datetime(pair[1].event_date) or datetime.min.replace(tzinfo=timezone.utc),
            pair[0],
        )
    )
    return [event for _index, event in rows]


class _EdgeLockedScrollArea(QScrollArea):
    """Prevents wheel overscroll events from bubbling at scroll boundaries."""

    def wheelEvent(self, event) -> None:
        angle_x = 0
        angle_y = 0
        try:
            angle_delta = event.angleDelta()
            if angle_delta is not None:
                angle_x = int(angle_delta.x())
                angle_y = int(angle_delta.y())
        except Exception:
            angle_x = 0
            angle_y = 0

        pixel_x = 0
        pixel_y = 0
        try:
            pixel_delta = event.pixelDelta()
            if pixel_delta is not None:
                pixel_x = int(pixel_delta.x())
                pixel_y = int(pixel_delta.y())
        except Exception:
            pixel_x = 0
            pixel_y = 0

        # Keep wheel-mouse behavior native; only intercept touchpad-style gestures.
        is_touchpad = (pixel_x != 0 or pixel_y != 0)
        if not is_touchpad:
            try:
                is_touchpad = event.phase() != Qt.ScrollPhase.NoScrollPhase
            except Exception:
                is_touchpad = False
        if not is_touchpad:
            super().wheelEvent(event)
            return

        requested_vertical = (pixel_y != 0) or (angle_y != 0)
        requested_horizontal = (pixel_x != 0) or (angle_x != 0)

        vertical_bar = self.verticalScrollBar()
        horizontal_bar = self.horizontalScrollBar()

        v_can_scroll = (
            vertical_bar is not None and int(vertical_bar.maximum()) > int(vertical_bar.minimum())
        )
        h_can_scroll = (
            horizontal_bar is not None and int(horizontal_bar.maximum()) > int(horizontal_bar.minimum())
        )

        v_before = int(vertical_bar.value()) if vertical_bar is not None else 0
        h_before = int(horizontal_bar.value()) if horizontal_bar is not None else 0

        super().wheelEvent(event)

        v_after = int(vertical_bar.value()) if vertical_bar is not None else 0
        h_after = int(horizontal_bar.value()) if horizontal_bar is not None else 0
        moved = (v_after != v_before) or (h_after != h_before)
        if moved:
            return

        # If gesture asked to scroll an axis this area supports but nothing moved,
        # consume it so boundary overscroll doesn't bubble and repaint/flicker.
        if ((requested_vertical and v_can_scroll) or (requested_horizontal and h_can_scroll)):
            event.accept()


class PropertyEditorDialog(FramelessDialog):
    def __init__(
        self,
        *,
        jurisdictions: list[JurisdictionRecord],
        record: PropertyRecord | None,
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Address", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(560, 380)
        self.resize(640, 430)

        self._jurisdictions = list(jurisdictions)
        self._record = record

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._address_input = QLineEdit(self.body)
        self._address_input.setObjectName("PermitFormInput")
        form.addRow("Address", self._address_input)

        self._parcel_input = QLineEdit(self.body)
        self._parcel_input.setObjectName("PermitFormInput")
        form.addRow("Parcel ID", self._parcel_input)

        self._jurisdiction_combo = QComboBox(self.body)
        self._jurisdiction_combo.setObjectName("PermitFormCombo")
        self._jurisdiction_combo.addItem("Unassigned", "")
        for jurisdiction in sorted(self._jurisdictions, key=lambda row: row.name.casefold()):
            self._jurisdiction_combo.addItem(jurisdiction.name or "(Unnamed)", jurisdiction.jurisdiction_id)
        form.addRow("Jurisdiction", self._jurisdiction_combo)

        self._tags_input = QLineEdit(self.body)
        self._tags_input.setObjectName("PermitFormInput")
        self._tags_input.setPlaceholderText("comma-separated")
        form.addRow("Tags", self._tags_input)

        self._notes_input = QLineEdit(self.body)
        self._notes_input.setObjectName("PermitFormInput")
        form.addRow("Notes", self._notes_input)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self._on_save)
        footer.addWidget(save_button)

        self.body_layout.addLayout(footer)

        if record is not None:
            self._address_input.setText(record.display_address)
            self._parcel_input.setText(record.parcel_id)
            self._tags_input.setText(_join_multi_values(record.tags))
            self._notes_input.setText(record.notes)
            index = self._jurisdiction_combo.findData(record.jurisdiction_id)
            if index >= 0:
                self._jurisdiction_combo.setCurrentIndex(index)

        self._address_input.setFocus()

    def _on_save(self) -> None:
        if not self._address_input.text().strip():
            AppMessageDialog.show_warning(
                parent=self,
                title="Missing Address",
                message="Please provide a display address.",
                theme_mode=self._theme_mode,
            )
            return
        self.accept()

    def build_record(self) -> PropertyRecord:
        record = self._record
        property_id = record.property_id if record is not None else uuid4().hex
        parcel_id = self._parcel_input.text().strip()
        return PropertyRecord(
            property_id=property_id,
            display_address=self._address_input.text().strip(),
            parcel_id=parcel_id,
            parcel_id_norm=normalize_parcel_id(parcel_id),
            jurisdiction_id=str(self._jurisdiction_combo.currentData() or "").strip(),
            contact_ids=list(record.contact_ids) if record is not None else [],
            list_color=normalize_list_color(record.list_color) if record is not None else "",
            tags=_parse_multi_values(self._tags_input.text()),
            notes=self._notes_input.text().strip(),
        )


class PermitEditorDialog(FramelessDialog):
    def __init__(
        self,
        *,
        property_record: PropertyRecord,
        default_permit_type: str,
        record: PermitRecord | None = None,
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Permit", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(560, 420)
        self.resize(650, 470)
        self._property = property_record
        self._record = record

        hint = QLabel(
            f"Create permit for:\n{property_record.display_address or '(no address)'}\nParcel: {property_record.parcel_id or '(none)'}",
            self.body,
        )
        hint.setObjectName("PluginPickerHint")
        hint.setWordWrap(True)
        self.body_layout.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._permit_type_combo = QComboBox(self.body)
        self._permit_type_combo.setObjectName("PermitFormCombo")
        for permit_type, label in _PERMIT_TYPE_OPTIONS:
            if permit_type == "all":
                continue
            self._permit_type_combo.addItem(label, permit_type)
        desired_type = normalize_permit_type(default_permit_type)
        desired_index = self._permit_type_combo.findData(desired_type)
        if desired_index >= 0:
            self._permit_type_combo.setCurrentIndex(desired_index)
        form.addRow("Permit Type", self._permit_type_combo)

        self._permit_number_input = QLineEdit(self.body)
        self._permit_number_input.setObjectName("PermitFormInput")
        self._permit_number_input.setPlaceholderText("Portal case number (optional)")
        form.addRow("Permit #", self._permit_number_input)

        self._next_action_input = QLineEdit(self.body)
        self._next_action_input.setObjectName("PermitFormInput")
        self._next_action_input.setPlaceholderText("What should happen next?")
        form.addRow("Next Action", self._next_action_input)

        self._next_action_due_input = QLineEdit(self.body)
        self._next_action_due_input.setObjectName("PermitFormInput")
        self._next_action_due_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Next Action Due", self._next_action_due_input)

        self._request_date_input = QLineEdit(self.body)
        self._request_date_input.setObjectName("PermitFormInput")
        self._request_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Request Date", self._request_date_input)

        self._application_date_input = QLineEdit(self.body)
        self._application_date_input.setObjectName("PermitFormInput")
        self._application_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Application Date", self._application_date_input)

        self._issued_date_input = QLineEdit(self.body)
        self._issued_date_input.setObjectName("PermitFormInput")
        self._issued_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Issued Date", self._issued_date_input)

        self._final_date_input = QLineEdit(self.body)
        self._final_date_input.setObjectName("PermitFormInput")
        self._final_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Final Date", self._final_date_input)

        self._completion_date_input = QLineEdit(self.body)
        self._completion_date_input.setObjectName("PermitFormInput")
        self._completion_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Completion Date", self._completion_date_input)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self.accept)
        if record is not None:
            save_button.setText("Update")
        footer.addWidget(save_button)

        self.body_layout.addLayout(footer)

        if record is not None:
            existing_type_index = self._permit_type_combo.findData(normalize_permit_type(record.permit_type))
            if existing_type_index >= 0:
                self._permit_type_combo.setCurrentIndex(existing_type_index)
            self._permit_type_combo.setEnabled(False)
            self._permit_type_combo.setToolTip("Permit type is locked after creation.")
            self._permit_number_input.setText(record.permit_number)
            self._next_action_input.setText(record.next_action_text)
            self._next_action_due_input.setText(record.next_action_due)
            self._request_date_input.setText(record.request_date)
            self._application_date_input.setText(record.application_date)
            self._issued_date_input.setText(record.issued_date)
            self._final_date_input.setText(record.final_date)
            self._completion_date_input.setText(record.completion_date)
        self._permit_number_input.setFocus()

    def build_record(self) -> PermitRecord:
        permit_type = normalize_permit_type(self._permit_type_combo.currentData())
        request_date = self._request_date_input.text().strip()
        application_date = self._application_date_input.text().strip()
        issued_date = self._issued_date_input.text().strip()
        final_date = self._final_date_input.text().strip()
        completion_date = self._completion_date_input.text().strip()
        if self._record is not None:
            record = self._record
            record.property_id = self._property.property_id
            record.permit_type = permit_type
            record.permit_number = self._permit_number_input.text().strip()
            record.next_action_text = self._next_action_input.text().strip()
            record.next_action_due = self._next_action_due_input.text().strip()
            record.request_date = request_date
            record.application_date = application_date
            record.issued_date = issued_date
            record.final_date = final_date
            record.completion_date = completion_date
            return record

        record = PermitRecord(
            permit_id=uuid4().hex,
            property_id=self._property.property_id,
            permit_type=permit_type,
            permit_number=self._permit_number_input.text().strip(),
            status="requested",
            next_action_text=self._next_action_input.text().strip(),
            next_action_due=self._next_action_due_input.text().strip(),
            request_date=request_date,
            application_date=application_date,
            issued_date=issued_date,
            final_date=final_date,
            completion_date=completion_date,
            parties=[],
            events=_prefill_permit_events_from_milestones(
                request_date=request_date,
                application_date=application_date,
                issued_date=issued_date,
                final_date=final_date,
                completion_date=completion_date,
                next_action_text=self._next_action_input.text().strip(),
                next_action_due=self._next_action_due_input.text().strip(),
            ),
            document_slots=build_default_document_slots(permit_type),
            document_folders=[],
            documents=[],
        )
        record.document_folders = build_document_folders_from_slots(record.document_slots)
        return record


class NextActionDialog(FramelessDialog):
    def __init__(
        self,
        *,
        current_text: str,
        current_due: str,
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Next Action", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(520, 260)
        self.resize(560, 300)
        self._dirty_bubble: QLabel | None = None
        self._baseline_snapshot: tuple[str, str] = ("", "")
        self._dirty: bool = False

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._text_input = QLineEdit(self.body)
        self._text_input.setObjectName("PermitFormInput")
        self._text_input.setText(current_text)
        form.addRow("Action", self._text_input)

        self._due_input = QLineEdit(self.body)
        self._due_input.setObjectName("PermitFormInput")
        self._due_input.setText(current_due)
        self._due_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Due", self._due_input)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        dirty_bubble = QLabel("Saved", self.body)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "clean")
        dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dirty_bubble.setMinimumWidth(92)
        dirty_bubble.setMinimumHeight(24)
        footer.addWidget(dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dirty_bubble = dirty_bubble

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self.accept)
        footer.addWidget(save_button)

        self.body_layout.addLayout(footer)
        self._text_input.textChanged.connect(self._sync_dirty_state)
        self._due_input.textChanged.connect(self._sync_dirty_state)
        self._rebase_dirty_tracking()

    def values(self) -> tuple[str, str]:
        return self._text_input.text().strip(), self._due_input.text().strip()

    def _snapshot(self) -> tuple[str, str]:
        return (
            self._text_input.text().strip(),
            self._due_input.text().strip(),
        )

    def _is_empty(self) -> bool:
        action_text, due_text = self._snapshot()
        return not any((action_text, due_text))

    def _dirty_bubble_state(self) -> str:
        if self._dirty:
            return "dirty"
        if self._is_empty():
            return "empty"
        return "clean"

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        _set_dirty_bubble_state(
            self._dirty_bubble,
            state=self._dirty_bubble_state(),
        )

    def _rebase_dirty_tracking(self) -> None:
        self._baseline_snapshot = self._snapshot()
        self._set_dirty(False)

    def _sync_dirty_state(self, *_args: object) -> None:
        if not self._baseline_snapshot:
            self._rebase_dirty_tracking()
            return
        self._set_dirty(self._snapshot() != self._baseline_snapshot)

    def _confirm_discard_changes(self, *, action_label: str) -> bool:
        if not self._dirty:
            return True
        return AppConfirmDialog.ask(
            parent=self,
            title="Unsaved Next Action Changes",
            message=(
                "You have unsaved changes in this next-action form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
            theme_mode=self._theme_mode,
        )

    def reject(self) -> None:
        if not self._confirm_discard_changes(action_label="Cancel"):
            return
        super().reject()


class NextActionTimelineEntryDialog(FramelessDialog):
    def __init__(
        self,
        *,
        current_date: str,
        current_text: str,
        current_due: str,
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Edit Next Action Entry", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(560, 300)
        self.resize(620, 340)
        self._dirty_bubble: QLabel | None = None
        self._baseline_snapshot: tuple[str, str, str] = ("", "", "")
        self._dirty: bool = False

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._date_input = QLineEdit(self.body)
        self._date_input.setObjectName("PermitFormInput")
        self._date_input.setText(str(current_date or "").strip() or _today_iso())
        self._date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Date", self._date_input)

        self._text_input = QLineEdit(self.body)
        self._text_input.setObjectName("PermitFormInput")
        self._text_input.setText(str(current_text or "").strip())
        self._text_input.setPlaceholderText("What should happen next?")
        form.addRow("Action", self._text_input)

        self._due_input = QLineEdit(self.body)
        self._due_input.setObjectName("PermitFormInput")
        self._due_input.setText(str(current_due or "").strip())
        self._due_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Due", self._due_input)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        dirty_bubble = QLabel("Saved", self.body)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "clean")
        dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dirty_bubble.setMinimumWidth(92)
        dirty_bubble.setMinimumHeight(24)
        footer.addWidget(dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dirty_bubble = dirty_bubble

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self.accept)
        footer.addWidget(save_button)

        self.body_layout.addLayout(footer)
        self._text_input.setFocus()
        self._date_input.textChanged.connect(self._sync_dirty_state)
        self._text_input.textChanged.connect(self._sync_dirty_state)
        self._due_input.textChanged.connect(self._sync_dirty_state)
        self._rebase_dirty_tracking()

    def values(self) -> tuple[str, str, str]:
        return (
            self._date_input.text().strip() or _today_iso(),
            self._text_input.text().strip(),
            self._due_input.text().strip(),
        )

    def _snapshot(self) -> tuple[str, str, str]:
        return (
            self._date_input.text().strip(),
            self._text_input.text().strip(),
            self._due_input.text().strip(),
        )

    def _is_empty(self) -> bool:
        date_value, action_text, due_value = self._snapshot()
        return not any((date_value, action_text, due_value))

    def _dirty_bubble_state(self) -> str:
        if self._dirty:
            return "dirty"
        if self._is_empty():
            return "empty"
        return "clean"

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        _set_dirty_bubble_state(
            self._dirty_bubble,
            state=self._dirty_bubble_state(),
        )

    def _rebase_dirty_tracking(self) -> None:
        self._baseline_snapshot = self._snapshot()
        self._set_dirty(False)

    def _sync_dirty_state(self, *_args: object) -> None:
        if not self._baseline_snapshot:
            self._rebase_dirty_tracking()
            return
        self._set_dirty(self._snapshot() != self._baseline_snapshot)

    def _confirm_discard_changes(self, *, action_label: str) -> bool:
        if not self._dirty:
            return True
        return AppConfirmDialog.ask(
            parent=self,
            title="Unsaved Next Action Changes",
            message=(
                "You have unsaved changes in this next-action timeline entry. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
            theme_mode=self._theme_mode,
        )

    def reject(self) -> None:
        if not self._confirm_discard_changes(action_label="Cancel"):
            return
        super().reject()


class PermitEventDialog(FramelessDialog):
    def __init__(
        self,
        *,
        contacts: list[ContactRecord],
        available_event_types: Sequence[str] | None,
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Add Event", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(560, 360)
        self.resize(640, 420)
        self._contacts = list(contacts)
        self._dirty_bubble: QLabel | None = None
        self._baseline_snapshot: tuple[str, str, str, str, str] = ("", "", "", "", "")
        self._dirty: bool = False

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._event_type_combo = QComboBox(self.body)
        self._event_type_combo.setObjectName("PermitFormCombo")
        normalized_available = [
            normalize_event_type(event_type)
            for event_type in (available_event_types or PERMIT_EVENT_TYPES)
            if normalize_event_type(event_type) in PERMIT_EVENT_TYPES
        ]
        deduped_available: list[str] = []
        seen: set[str] = set()
        for event_type in normalized_available:
            if event_type in seen:
                continue
            seen.add(event_type)
            deduped_available.append(event_type)
        if not deduped_available:
            deduped_available = []
        for event_type in deduped_available:
            self._event_type_combo.addItem(event_type_label(event_type), event_type)
        self._has_available_event_types = bool(deduped_available)
        form.addRow("Event Type", self._event_type_combo)

        self._event_date_input = QLineEdit(self.body)
        self._event_date_input.setObjectName("PermitFormInput")
        self._event_date_input.setText(_today_iso())
        self._event_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Date", self._event_date_input)

        self._summary_input = QLineEdit(self.body)
        self._summary_input.setObjectName("PermitFormInput")
        self._summary_input.setPlaceholderText("Short summary")
        form.addRow("Summary", self._summary_input)

        self._detail_input = QLineEdit(self.body)
        self._detail_input.setObjectName("PermitFormInput")
        self._detail_input.setPlaceholderText("Detail")
        form.addRow("Detail", self._detail_input)

        self._actor_combo = QComboBox(self.body)
        self._actor_combo.setObjectName("PermitFormCombo")
        self._actor_combo.addItem("None", "")
        for contact in sorted(self._contacts, key=lambda row: row.name.casefold()):
            self._actor_combo.addItem(contact.name or "(Unnamed)", contact.contact_id)
        form.addRow("Actor", self._actor_combo)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        dirty_bubble = QLabel("Saved", self.body)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "clean")
        dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dirty_bubble.setMinimumWidth(92)
        dirty_bubble.setMinimumHeight(24)
        footer.addWidget(dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dirty_bubble = dirty_bubble

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self.accept)
        footer.addWidget(save_button)
        save_button.setEnabled(self._has_available_event_types)
        if not self._has_available_event_types:
            save_button.setToolTip("No unused event types remain for this permit.")
            self._event_type_combo.setEnabled(False)
            self._event_type_combo.setToolTip("All event types already exist in this timeline.")

        self.body_layout.addLayout(footer)
        self._summary_input.setFocus()
        self._event_type_combo.currentIndexChanged.connect(self._sync_dirty_state)
        self._event_date_input.textChanged.connect(self._sync_dirty_state)
        self._summary_input.textChanged.connect(self._sync_dirty_state)
        self._detail_input.textChanged.connect(self._sync_dirty_state)
        self._actor_combo.currentIndexChanged.connect(self._sync_dirty_state)
        self._rebase_dirty_tracking()

    def build_event(self) -> PermitEventRecord:
        return PermitEventRecord(
            event_id=uuid4().hex,
            event_type=normalize_event_type(self._event_type_combo.currentData()),
            event_date=self._event_date_input.text().strip() or _today_iso(),
            summary=self._summary_input.text().strip(),
            detail=self._detail_input.text().strip(),
            actor_contact_id=str(self._actor_combo.currentData() or "").strip(),
            attachments=[],
        )

    def _snapshot(self) -> tuple[str, str, str, str, str]:
        return (
            normalize_event_type(self._event_type_combo.currentData()),
            self._event_date_input.text().strip(),
            self._summary_input.text().strip(),
            self._detail_input.text().strip(),
            str(self._actor_combo.currentData() or "").strip(),
        )

    def _is_empty(self) -> bool:
        event_type, event_date, summary, detail, actor = self._snapshot()
        return not any((event_type, event_date, summary, detail, actor))

    def _dirty_bubble_state(self) -> str:
        if self._dirty:
            return "dirty"
        if self._is_empty():
            return "empty"
        return "clean"

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        _set_dirty_bubble_state(
            self._dirty_bubble,
            state=self._dirty_bubble_state(),
        )

    def _rebase_dirty_tracking(self) -> None:
        self._baseline_snapshot = self._snapshot()
        self._set_dirty(False)

    def _sync_dirty_state(self, *_args: object) -> None:
        if not self._baseline_snapshot:
            self._rebase_dirty_tracking()
            return
        self._set_dirty(self._snapshot() != self._baseline_snapshot)

    def _confirm_discard_changes(self, *, action_label: str) -> bool:
        if not self._dirty:
            return True
        return AppConfirmDialog.ask(
            parent=self,
            title="Unsaved Event Changes",
            message=(
                "You have unsaved changes in this event form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
            theme_mode=self._theme_mode,
        )

    def reject(self) -> None:
        if not self._confirm_discard_changes(action_label="Cancel"):
            return
        super().reject()


class TimelineEventEditDialog(FramelessDialog):
    def __init__(
        self,
        *,
        event: PermitEventRecord,
        contacts: list[ContactRecord],
        parent: QWidget,
        theme_mode: str,
    ) -> None:
        super().__init__(title="Edit Timeline Event", parent=parent, theme_mode=theme_mode)
        self.setMinimumSize(560, 360)
        self.resize(640, 420)
        self._event = event
        self._contacts = list(contacts)
        self._dirty_bubble: QLabel | None = None
        self._baseline_snapshot: tuple[str, str, str, str, str] = ("", "", "", "", "")
        self._dirty: bool = False

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._event_type_combo = QComboBox(self.body)
        self._event_type_combo.setObjectName("PermitFormCombo")
        for event_type in PERMIT_EVENT_TYPES:
            self._event_type_combo.addItem(event_type_label(event_type), event_type)
        selected_index = self._event_type_combo.findData(normalize_event_type(event.event_type))
        self._event_type_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        form.addRow("Event Type", self._event_type_combo)

        self._event_date_input = QLineEdit(self.body)
        self._event_date_input.setObjectName("PermitFormInput")
        self._event_date_input.setText(str(event.event_date or "").strip() or _today_iso())
        self._event_date_input.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Date", self._event_date_input)

        self._summary_input = QLineEdit(self.body)
        self._summary_input.setObjectName("PermitFormInput")
        self._summary_input.setText(str(event.summary or "").strip())
        self._summary_input.setPlaceholderText("Short summary")
        form.addRow("Summary", self._summary_input)

        self._detail_input = QLineEdit(self.body)
        self._detail_input.setObjectName("PermitFormInput")
        self._detail_input.setText(str(event.detail or "").strip())
        self._detail_input.setPlaceholderText("Detail")
        form.addRow("Detail", self._detail_input)

        self._actor_combo = QComboBox(self.body)
        self._actor_combo.setObjectName("PermitFormCombo")
        self._actor_combo.addItem("None", "")
        for contact in sorted(self._contacts, key=lambda row: row.name.casefold()):
            self._actor_combo.addItem(contact.name or "(Unnamed)", contact.contact_id)
        actor_index = self._actor_combo.findData(str(event.actor_contact_id or "").strip())
        self._actor_combo.setCurrentIndex(actor_index if actor_index >= 0 else 0)
        form.addRow("Actor", self._actor_combo)

        self.body_layout.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", self.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)

        dirty_bubble = QLabel("Saved", self.body)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "clean")
        dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dirty_bubble.setMinimumWidth(92)
        dirty_bubble.setMinimumHeight(24)
        footer.addWidget(dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dirty_bubble = dirty_bubble

        save_button = QPushButton("Save", self.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        save_button.clicked.connect(self.accept)
        footer.addWidget(save_button)

        self.body_layout.addLayout(footer)
        self._summary_input.setFocus()
        self._event_type_combo.currentIndexChanged.connect(self._sync_dirty_state)
        self._event_date_input.textChanged.connect(self._sync_dirty_state)
        self._summary_input.textChanged.connect(self._sync_dirty_state)
        self._detail_input.textChanged.connect(self._sync_dirty_state)
        self._actor_combo.currentIndexChanged.connect(self._sync_dirty_state)
        self._rebase_dirty_tracking()

    def values(self) -> tuple[str, str, str, str, str]:
        return (
            normalize_event_type(self._event_type_combo.currentData()),
            self._event_date_input.text().strip() or _today_iso(),
            self._summary_input.text().strip(),
            self._detail_input.text().strip(),
            str(self._actor_combo.currentData() or "").strip(),
        )

    def _snapshot(self) -> tuple[str, str, str, str, str]:
        return (
            normalize_event_type(self._event_type_combo.currentData()),
            self._event_date_input.text().strip(),
            self._summary_input.text().strip(),
            self._detail_input.text().strip(),
            str(self._actor_combo.currentData() or "").strip(),
        )

    def _is_empty(self) -> bool:
        event_type, event_date, summary, detail, actor = self._snapshot()
        return not any((event_type, event_date, summary, detail, actor))

    def _dirty_bubble_state(self) -> str:
        if self._dirty:
            return "dirty"
        if self._is_empty():
            return "empty"
        return "clean"

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        _set_dirty_bubble_state(
            self._dirty_bubble,
            state=self._dirty_bubble_state(),
        )

    def _rebase_dirty_tracking(self) -> None:
        self._baseline_snapshot = self._snapshot()
        self._set_dirty(False)

    def _sync_dirty_state(self, *_args: object) -> None:
        if not self._baseline_snapshot:
            self._rebase_dirty_tracking()
            return
        self._set_dirty(self._snapshot() != self._baseline_snapshot)

    def _confirm_discard_changes(self, *, action_label: str) -> bool:
        if not self._dirty:
            return True
        return AppConfirmDialog.ask(
            parent=self,
            title="Unsaved Timeline Event Changes",
            message=(
                "You have unsaved changes in this timeline event. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
            theme_mode=self._theme_mode,
        )

    def reject(self) -> None:
        if not self._confirm_discard_changes(action_label="Cancel"):
            return
        super().reject()


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


class ErPermitSysWindow(FramelessWindow):
    def __init__(
        self,
        *,
        dark_mode_enabled: bool = False,
        palette_shortcut_enabled: bool = True,
        palette_shortcut_keybind: str = DEFAULT_PALETTE_SHORTCUT,
        state_streamer: StateStreamer | None = None,
    ) -> None:
        theme_mode = "dark" if dark_mode_enabled else "light"
        super().__init__(
            title="erpermitsys",
            icon_path=icon_asset_path("resize_handle.png", mode=theme_mode),
            theme_mode=theme_mode,
        )
        self.setMinimumSize(980, 640)
        self.resize(1220, 760)

        self._plugin_manager = PluginManager.from_default_layout()
        self._plugin_api = PluginApiService(self._plugin_manager, active_kind="html-background")
        self._plugin_bridge = BackgroundPluginBridge(self._plugin_api)
        self._current_background_url: str | None = None

        self._dark_mode_enabled = bool(dark_mode_enabled)
        self._palette_shortcut_enabled = bool(palette_shortcut_enabled)
        self._palette_shortcut_keybind = (
            palette_shortcut_keybind.strip() if isinstance(palette_shortcut_keybind, str) else ""
        ) or DEFAULT_PALETTE_SHORTCUT

        self._state_streamer = state_streamer or StateStreamer()
        self._settings_dialog: SettingsDialog | None = None
        self._command_runtime: CommandRuntime | None = None

        self._data_storage_backend = load_data_storage_backend(default=BACKEND_LOCAL_JSON)
        self._data_storage_folder = load_data_storage_folder()
        self._data_store = LocalJsonDataStore(self._data_storage_folder)
        self._document_store = LocalPermitDocumentStore(self._data_storage_folder)

        self._app_version = APP_VERSION
        self._auto_update_github_repo = GITHUB_RELEASE_REPO
        self._auto_update_asset_name = GITHUB_RELEASE_ASSET_NAME
        self._updater = GitHubReleaseUpdater(timeout_seconds=3.5)
        self._update_check_in_progress = False

        self._contacts: list[ContactRecord] = []
        self._jurisdictions: list[JurisdictionRecord] = []
        self._properties: list[PropertyRecord] = []
        self._permits: list[PermitRecord] = []
        self._document_templates: list[DocumentChecklistTemplate] = []
        self._active_document_template_ids: dict[str, str] = {}

        self._selected_property_id: str = ""
        self._selected_permit_id: str = ""
        self._active_permit_type_filter: str = "all"
        self._selected_document_slot_id: str = ""
        self._selected_document_id: str = ""
        self._timeline_debug_enabled: bool = _is_truthy_env(os.getenv(_TIMELINE_DEBUG_ENV, ""))
        self._timeline_debug_log_path: str = str(os.getenv(_TIMELINE_DEBUG_LOG_ENV, "")).strip()
        self._timeline_debug_sequence: int = 0
        self._timeline_render_sequence: int = 0
        self._timeline_show_next_action_mode: bool = False

        self._stack: QStackedLayout | None = None
        self._fallback_widget: QFrame | None = None
        self._background_view: QWebEngineView | None = None
        self._background_web_channel: QWebChannel | None = None
        self._scene_widget: QWidget | None = None
        self._panel_host: QWidget | None = None
        self._panel_stack: QStackedLayout | None = None
        self._panel_home_view: QWidget | None = None
        self._panel_admin_view: QWidget | None = None
        self._panel_add_property_view: QWidget | None = None
        self._panel_add_permit_view: QWidget | None = None
        self._admin_tabs: QTabWidget | None = None
        self._admin_templates_tab_index: int = -1
        self._permit_workspace_panel: QFrame | None = None
        self._permit_workspace_content_host: QWidget | None = None
        self._permit_workspace_blur_effect: QGraphicsBlurEffect | None = None
        self._permit_workspace_blur_overlay: QFrame | None = None
        self._permit_workspace_blurred: bool = False
        self._active_inline_form_view: str = ""
        self._inline_form_cards: list[QFrame] = []

        self._settings_button: QPushButton | None = None
        self._settings_button_shadow: QGraphicsDropShadowEffect | None = None

        self._property_filter_combo: QComboBox | None = None
        self._property_search_input: QLineEdit | None = None
        self._property_result_label: QLabel | None = None
        self._properties_list_widget: QListWidget | None = None
        self._properties_list_stack: QStackedLayout | None = None
        self._properties_empty_label: QLabel | None = None

        self._permit_header_label: QLabel | None = None
        self._permit_type_buttons: dict[str, QPushButton] = {}
        self._permit_controls_host: QWidget | None = None
        self._permit_filter_combo: QComboBox | None = None
        self._permit_search_input: QLineEdit | None = None
        self._permit_result_label: QLabel | None = None
        self._add_permit_button: QPushButton | None = None
        self._permit_type_picker_host: QWidget | None = None
        self._permits_list_widget: QListWidget | None = None
        self._permits_list_stack: QStackedLayout | None = None
        self._permits_empty_label: QLabel | None = None
        self._left_column_layout: QVBoxLayout | None = None
        self._address_list_panel: QFrame | None = None
        self._permit_list_panel: QFrame | None = None
        self._address_list_panel_body: QWidget | None = None
        self._permit_list_panel_body: QWidget | None = None
        self._address_panel_toggle_button: QToolButton | None = None
        self._permit_panel_toggle_button: QToolButton | None = None
        self._left_column_expanded_panel: str = "address"

        self._workspace_title_label: QLabel | None = None
        self._workspace_next_step_label: QLabel | None = None
        self._workspace_info_values: dict[str, QLabel] = {}
        self._open_portal_button: QPushButton | None = None
        self._set_next_action_button: QPushButton | None = None
        self._add_event_button: QPushButton | None = None
        self._next_action_label: QLabel | None = None
        self._timeline_scroll_area: QScrollArea | None = None
        self._timeline_track_widget: QWidget | None = None
        self._timeline_track_layout: QHBoxLayout | None = None
        self._timeline_title_label: QLabel | None = None
        self._timeline_hint_label: QLabel | None = None
        self._timeline_mode_toggle_button: QPushButton | None = None
        self._document_slot_list_widget: QListWidget | None = None
        self._document_slot_cards: dict[str, DocumentChecklistSlotCard] = {}
        self._document_file_list_widget: QListWidget | None = None
        self._document_file_icon_provider: QFileIconProvider | None = None
        self._document_file_icon_cache: dict[str, QIcon] = {}
        self._document_status_label: QLabel | None = None
        self._document_upload_button: QPushButton | None = None
        self._document_new_cycle_button: QPushButton | None = None
        self._document_open_folder_button: QPushButton | None = None
        self._document_open_file_button: QPushButton | None = None
        self._document_remove_file_button: QPushButton | None = None
        self._document_mark_accepted_button: QPushButton | None = None
        self._document_mark_rejected_button: QPushButton | None = None
        self._document_template_apply_combo: QComboBox | None = None
        self._document_template_apply_button: QPushButton | None = None

        self._add_property_card: QFrame | None = None
        self._add_property_title_label: QLabel | None = None
        self._add_property_subtitle_label: QLabel | None = None
        self._add_property_dirty_bubble: QLabel | None = None
        self._add_property_submit_button: QPushButton | None = None
        self._add_property_editing_id: str = ""
        self._add_property_address_input: QLineEdit | None = None
        self._add_property_parcel_input: QLineEdit | None = None
        self._add_property_jurisdiction_combo: QComboBox | None = None
        self._add_property_contacts_label: QLabel | None = None
        self._add_property_contact_picker_combo: QComboBox | None = None
        self._add_property_contact_add_button: QPushButton | None = None
        self._add_property_attached_contacts_host: QWidget | None = None
        self._add_property_attached_contact_ids: list[str] = []
        self._add_property_color_shell: QFrame | None = None
        self._add_property_color_picker_host: QWidget | None = None
        self._add_property_list_color: str = ""
        self._add_property_custom_list_color: str = ""
        self._add_property_color_buttons: dict[str, QPushButton] = {}
        self._add_property_custom_color_button: QPushButton | None = None
        self._add_property_custom_color_dot: QPushButton | None = None
        self._add_property_color_selected_label: QLabel | None = None
        self._add_property_color_toggle_button: QPushButton | None = None
        self._add_property_color_content_host: QWidget | None = None
        self._add_property_color_animation: QPropertyAnimation | None = None
        self._add_property_color_picker_open: bool = False
        self._add_property_tags_input: QLineEdit | None = None
        self._add_property_notes_input: QLineEdit | None = None

        self._add_permit_card: QFrame | None = None
        self._add_permit_title_label: QLabel | None = None
        self._add_permit_dirty_bubble: QLabel | None = None
        self._add_permit_submit_button: QPushButton | None = None
        self._add_permit_editing_id: str = ""
        self._add_permit_property_id: str = ""
        self._add_permit_context_label: QLabel | None = None
        self._add_permit_type_combo: QComboBox | None = None
        self._add_permit_template_combo: QComboBox | None = None
        self._add_permit_contacts_label: QLabel | None = None
        self._add_permit_contact_picker_combo: QComboBox | None = None
        self._add_permit_contact_add_button: QPushButton | None = None
        self._add_permit_attached_contacts_host: QWidget | None = None
        self._add_permit_attached_contact_ids: list[str] = []
        self._add_permit_number_input: QLineEdit | None = None
        self._add_permit_next_action_input: QLineEdit | None = None
        self._add_permit_next_action_due_input: QLineEdit | None = None
        self._add_permit_request_date_input: QLineEdit | None = None
        self._add_permit_application_date_input: QLineEdit | None = None
        self._add_permit_issued_date_input: QLineEdit | None = None
        self._add_permit_final_date_input: QLineEdit | None = None
        self._add_permit_completion_date_input: QLineEdit | None = None
        self._add_property_form_loading: bool = False
        self._add_permit_form_loading: bool = False
        self._add_property_form_dirty: bool = False
        self._add_permit_form_dirty: bool = False
        self._add_property_baseline_snapshot: tuple[object, ...] = ()
        self._add_permit_baseline_snapshot: tuple[object, ...] = ()

        self._template_selected_id: str = ""
        self._template_form_widget: QFrame | None = None
        self._template_dirty_bubble: QLabel | None = None
        self._template_mode_label: QLabel | None = None
        self._template_save_button: QPushButton | None = None
        self._template_delete_button: QPushButton | None = None
        self._template_set_default_button: QPushButton | None = None
        self._templates_search_input: QLineEdit | None = None
        self._templates_count_label: QLabel | None = None
        self._templates_list_widget: QListWidget | None = None
        self._templates_list_stack: QStackedLayout | None = None
        self._templates_empty_label: QLabel | None = None
        self._template_field_shells: list[QFrame] = []
        self._template_name_input: QLineEdit | None = None
        self._template_type_combo: QComboBox | None = None
        self._template_notes_input: QLineEdit | None = None
        self._template_slots_list_widget: QListWidget | None = None
        self._template_slot_label_input: QLineEdit | None = None
        self._template_slot_id_input: QLineEdit | None = None
        self._template_slot_required_combo: QComboBox | None = None
        self._template_slot_notes_input: QLineEdit | None = None
        self._template_slot_add_button: QPushButton | None = None
        self._template_slot_update_button: QPushButton | None = None
        self._template_slot_remove_button: QPushButton | None = None
        self._template_slot_edit_index: int = -1
        self._template_slot_rows: list[PermitDocumentSlot] = []
        self._template_form_loading: bool = False
        self._template_form_read_only: bool = False
        self._template_dirty: bool = False
        self._template_baseline_snapshot: tuple[object, ...] = ()
        self._template_selection_guard: bool = False

        self._admin_selected_contact_id: str = ""
        self._admin_selected_jurisdiction_id: str = ""
        self._admin_contact_form_widget: QFrame | None = None
        self._admin_jurisdiction_form_widget: QFrame | None = None
        self._admin_contact_field_shells: list[QFrame] = []
        self._admin_jurisdiction_field_shells: list[QFrame] = []
        self._admin_contacts_list_widget: QListWidget | None = None
        self._admin_contacts_list_stack: QStackedLayout | None = None
        self._admin_contacts_empty_label: QLabel | None = None
        self._admin_contacts_count_label: QLabel | None = None
        self._admin_contacts_search_input: QLineEdit | None = None
        self._admin_contact_mode_label: QLabel | None = None
        self._admin_contact_dirty_bubble: QLabel | None = None
        self._admin_contact_save_button: QPushButton | None = None
        self._admin_contact_delete_button: QPushButton | None = None
        self._admin_contact_methods_label: QLabel | None = None
        self._admin_contact_name_input: QLineEdit | None = None
        self._admin_contact_bundle_name_input: QLineEdit | None = None
        self._admin_contact_numbers_input: QLineEdit | None = None
        self._admin_contact_emails_input: QLineEdit | None = None
        self._admin_contact_note_input: QLineEdit | None = None
        self._admin_contact_roles_input: QLineEdit | None = None
        self._admin_contact_color_shell: QFrame | None = None
        self._admin_contact_color_picker_host: QWidget | None = None
        self._admin_contact_list_color: str = ""
        self._admin_contact_custom_list_color: str = ""
        self._admin_contact_color_buttons: dict[str, QPushButton] = {}
        self._admin_contact_custom_color_button: QPushButton | None = None
        self._admin_contact_custom_color_dot: QPushButton | None = None
        self._admin_contact_color_selected_label: QLabel | None = None
        self._admin_contact_color_toggle_button: QPushButton | None = None
        self._admin_contact_color_content_host: QWidget | None = None
        self._admin_contact_color_animation: QPropertyAnimation | None = None
        self._admin_contact_color_picker_open: bool = False
        self._admin_contact_bundle_toggle_button: QPushButton | None = None
        self._admin_contact_bundle_fields_host: QWidget | None = None
        self._admin_contact_bundle_fields_animation: QPropertyAnimation | None = None
        self._admin_contact_bundle_fields_open: bool = False
        self._admin_contact_methods_host: QWidget | None = None
        self._admin_contact_method_rows: list[ContactMethodRecord] = []
        self._admin_contact_add_method_button: QPushButton | None = None
        self._admin_contact_cancel_method_button: QPushButton | None = None
        self._admin_contact_editing_bundle_index: int = -1
        self._admin_jurisdictions_list_widget: QListWidget | None = None
        self._admin_jurisdictions_list_stack: QStackedLayout | None = None
        self._admin_jurisdictions_empty_label: QLabel | None = None
        self._admin_jurisdictions_count_label: QLabel | None = None
        self._admin_jurisdictions_search_input: QLineEdit | None = None
        self._admin_jurisdiction_mode_label: QLabel | None = None
        self._admin_jurisdiction_dirty_bubble: QLabel | None = None
        self._admin_jurisdiction_save_button: QPushButton | None = None
        self._admin_jurisdiction_delete_button: QPushButton | None = None
        self._admin_jurisdiction_attached_label: QLabel | None = None
        self._admin_jurisdiction_name_input: QLineEdit | None = None
        self._admin_jurisdiction_type_combo: QComboBox | None = None
        self._admin_jurisdiction_parent_input: QLineEdit | None = None
        self._admin_jurisdiction_portals_input: QLineEdit | None = None
        self._admin_jurisdiction_vendor_input: QLineEdit | None = None
        self._admin_jurisdiction_notes_input: QLineEdit | None = None
        self._admin_jurisdiction_color_shell: QFrame | None = None
        self._admin_jurisdiction_color_picker_host: QWidget | None = None
        self._admin_jurisdiction_list_color: str = ""
        self._admin_jurisdiction_custom_list_color: str = ""
        self._admin_jurisdiction_color_buttons: dict[str, QPushButton] = {}
        self._admin_jurisdiction_custom_color_button: QPushButton | None = None
        self._admin_jurisdiction_custom_color_dot: QPushButton | None = None
        self._admin_jurisdiction_color_selected_label: QLabel | None = None
        self._admin_jurisdiction_color_toggle_button: QPushButton | None = None
        self._admin_jurisdiction_color_content_host: QWidget | None = None
        self._admin_jurisdiction_color_animation: QPropertyAnimation | None = None
        self._admin_jurisdiction_color_picker_open: bool = False
        self._admin_jurisdiction_contact_picker_combo: QComboBox | None = None
        self._admin_jurisdiction_contact_add_button: QPushButton | None = None
        self._admin_jurisdiction_fields_host: QWidget | None = None
        self._admin_jurisdiction_attached_panel: QFrame | None = None
        self._admin_jurisdiction_attached_picker_host: QWidget | None = None
        self._admin_jurisdiction_attached_contacts_host: QWidget | None = None
        self._admin_jurisdiction_attached_contact_ids: list[str] = []
        self._admin_contact_form_loading: bool = False
        self._admin_jurisdiction_form_loading: bool = False
        self._admin_contact_dirty: bool = False
        self._admin_jurisdiction_dirty: bool = False
        self._admin_contact_baseline_snapshot: tuple[object, ...] = ()
        self._admin_jurisdiction_baseline_snapshot: tuple[object, ...] = ()
        self._admin_contact_editing_mode: bool = False
        self._admin_jurisdiction_editing_mode: bool = False
        self._admin_contact_selection_guard: bool = False
        self._admin_jurisdiction_selection_guard: bool = False

        storage_warning = self._initialize_data_store()
        self._build_body()
        self._plugin_manager.discover(auto_activate_background=False)
        self._restore_active_plugins()
        self._sync_background_from_plugins()
        self._refresh_all_views()

        if storage_warning:
            QTimer.singleShot(0, lambda message=storage_warning: self._show_data_storage_warning(message))

        QTimer.singleShot(0, self._sync_foreground_layout)
        QTimer.singleShot(_UPDATE_STARTUP_DELAY_MS, self._check_for_updates_on_startup)
        self._state_streamer.record(
            "window.initialized",
            source="main_window",
            payload={
                "theme_mode": theme_mode,
                "has_webengine": bool(QWebEngineView is not None),
            },
        )
        self._timeline_debug(
            "window_initialized",
            timeline_debug_enabled=self._timeline_debug_enabled,
            timeline_debug_env=_TIMELINE_DEBUG_ENV,
            timeline_debug_log_env=_TIMELINE_DEBUG_LOG_ENV,
            timeline_debug_log_path=self._timeline_debug_log_path or "stderr",
            contacts=len(self._contacts),
            jurisdictions=len(self._jurisdictions),
            properties=len(self._properties),
            permits=len(self._permits),
        )

    def _dialog_theme_mode(self) -> str:
        return "dark" if self._dark_mode_enabled else "light"

    def _timeline_debug(self, event: str, **payload: object) -> None:
        if not self._timeline_debug_enabled:
            return
        self._timeline_debug_sequence += 1
        record = {
            "seq": self._timeline_debug_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event or "").strip() or "unknown",
            "selected_property_id": str(self._selected_property_id or "").strip(),
            "selected_permit_id": str(self._selected_permit_id or "").strip(),
            "active_type_filter": str(self._active_permit_type_filter or "").strip(),
            "data": payload,
        }
        line = json.dumps(record, ensure_ascii=True, default=str)
        target_path = str(self._timeline_debug_log_path or "").strip()
        if target_path:
            try:
                destination = Path(target_path).expanduser()
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("a", encoding="utf-8") as handle:
                    handle.write(f"{line}\n")
                return
            except Exception:
                pass
        try:
            sys.stderr.write(f"[timeline-debug] {line}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def _timeline_debug_capture_widget_widths(self, *, stage: str, permit_id: str) -> None:
        if not self._timeline_debug_enabled:
            return
        track_widget = self._timeline_track_widget
        track_layout = self._timeline_track_layout
        timeline_scroll = self._timeline_scroll_area
        if track_widget is None or track_layout is None:
            self._timeline_debug(
                "timeline_widths_unavailable",
                stage=stage,
                permit_id=permit_id,
                has_track_widget=bool(track_widget is not None),
                has_track_layout=bool(track_layout is not None),
            )
            return
        rows: list[dict[str, object]] = []
        for index in range(track_layout.count()):
            item = track_layout.itemAt(index)
            child = item.widget() if item is not None else None
            if child is None:
                continue
            rows.append(
                {
                    "index": index,
                    "class": child.__class__.__name__,
                    "object_name": child.objectName(),
                    "hidden": child.isHidden(),
                    "size_hint_w": child.sizeHint().width(),
                    "min_hint_w": child.minimumSizeHint().width(),
                    "width": child.width(),
                    "min_width": child.minimumWidth(),
                    "max_width": child.maximumWidth(),
                }
            )
        self._timeline_debug(
            "timeline_widths_snapshot",
            stage=stage,
            permit_id=permit_id,
            track_width=track_widget.width(),
            track_fixed_width=track_widget.minimumWidth(),
            viewport_width=(
                timeline_scroll.viewport().width()
                if timeline_scroll is not None
                else 0
            ),
            child_count=track_layout.count(),
            rows=rows,
        )

    def _show_info_dialog(self, title: str, message: str) -> None:
        AppMessageDialog.show_info(
            parent=self,
            title=title,
            message=message,
            theme_mode=self._dialog_theme_mode(),
        )

    def _show_warning_dialog(self, title: str, message: str) -> None:
        AppMessageDialog.show_warning(
            parent=self,
            title=title,
            message=message,
            theme_mode=self._dialog_theme_mode(),
        )

    def _confirm_dialog(
        self,
        title: str,
        message: str,
        *,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
    ) -> bool:
        return AppConfirmDialog.ask(
            parent=self,
            title=title,
            message=message,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            danger=danger,
            theme_mode=self._dialog_theme_mode(),
        )

    def _pick_template_default_target_type(self, *, template_name: str) -> str:
        dialog = FramelessDialog(
            title="Set Default Template",
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        dialog.setMinimumSize(560, 250)
        dialog.resize(620, 280)

        prompt = QLabel(
            f"Set '{template_name}' as default for which permit type?",
            dialog.body,
        )
        prompt.setWordWrap(True)
        prompt.setObjectName("PluginPickerHint")
        dialog.body_layout.addWidget(prompt)

        hint = QLabel(
            "Choose Building, Remodeling, or Demolition. You can also cancel.",
            dialog.body,
        )
        hint.setWordWrap(True)
        hint.setObjectName("TrackerPanelMeta")
        dialog.body_layout.addWidget(hint)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        selected: dict[str, str] = {"permit_type": ""}

        def _pick(value: str) -> None:
            selected["permit_type"] = value
            dialog.accept()

        for label, value in (
            ("Building", "building"),
            ("Remodeling", "remodeling"),
            ("Demolition", "demolition"),
        ):
            button = QPushButton(label, dialog.body)
            button.setObjectName("TrackerPanelActionButton")
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, choice=value: _pick(choice))
            footer.addWidget(button, 0)

        cancel_button = QPushButton("Cancel", dialog.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.setMinimumHeight(32)
        cancel_button.clicked.connect(dialog.reject)
        footer.addWidget(cancel_button, 0)

        dialog.body_layout.addLayout(footer)

        cancel_button.setFocus()
        if dialog.exec() != dialog.DialogCode.Accepted:
            return ""
        selected_type = str(selected.get("permit_type", "")).strip()
        if not selected_type:
            return ""
        return normalize_permit_type(selected_type)

    def _build_body(self) -> None:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scene = QWidget(page)
        scene.setObjectName("AppScene")
        scene.installEventFilter(self)
        self._scene_widget = scene

        scene_layout = QVBoxLayout(scene)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_layout.setSpacing(0)

        stack_host = QWidget(scene)
        stack = QStackedLayout(stack_host)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._stack = stack

        fallback = QFrame(stack_host)
        fallback.setObjectName("FallbackBackground")
        stack.addWidget(fallback)
        self._fallback_widget = fallback

        if QWebEngineView is not None:
            web = QWebEngineView(stack_host)
            web.setObjectName("BackgroundWebView")
            web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            web.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            web.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
            web.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            try:
                web.page().setBackgroundColor(QColor(6, 10, 16))
            except Exception:
                pass
            try:
                web.loadFinished.connect(self._on_background_load_finished)
            except Exception:
                pass
            if QWebChannel is not None:
                try:
                    channel = QWebChannel(web.page())
                    channel.registerObject("erpermitsysBridge", self._plugin_bridge)
                    web.page().setWebChannel(channel)
                    self._background_web_channel = channel
                except Exception:
                    self._background_web_channel = None
            stack.addWidget(web)
            self._background_view = web
        else:
            self._background_view = None
            self._background_web_channel = None

        scene_layout.addWidget(stack_host, 1)

        button = QPushButton("Settings", scene)
        button.setObjectName("SettingsLauncherButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        button.clicked.connect(self.open_settings_dialog)
        button.hide()
        self._settings_button = button
        self._apply_settings_button_effect()

        self._build_tracker_overlay(scene)

        page_layout.addWidget(scene, 1)
        self.body_layout.addWidget(page)

    def _create_tracker_panel(
        self,
        parent: QWidget,
        title: str,
        *,
        with_title: bool = True,
    ) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame(parent)
        panel.setObjectName("TrackerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        if with_title:
            label = QLabel(title, panel)
            label.setObjectName("TrackerPanelTitle")
            layout.addWidget(label)
        return panel, layout

    def _toggle_left_column_panel(self, panel_key: str) -> None:
        normalized = str(panel_key or "").strip().casefold()
        target = "permit" if normalized == "permit" else "address"
        current = str(self._left_column_expanded_panel or "").strip().casefold() or "address"
        if target == current:
            target = "permit" if current == "address" else "address"
        self._set_left_column_expanded_panel(target)

    def _set_left_column_expanded_panel(self, panel_key: str) -> None:
        normalized = str(panel_key or "").strip().casefold()
        target = "permit" if normalized == "permit" else "address"
        self._left_column_expanded_panel = target
        address_expanded = target == "address"
        permit_expanded = not address_expanded

        left_layout = self._left_column_layout
        left_column_widget = left_layout.parentWidget() if left_layout is not None else None
        if left_column_widget is not None:
            left_column_widget.setUpdatesEnabled(False)

        address_body = self._address_list_panel_body
        if address_body is not None:
            address_body.setEnabled(address_expanded)
            address_body.setMinimumHeight(0)
            address_body.setMaximumHeight(16777215 if address_expanded else 0)
            address_body.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding if address_expanded else QSizePolicy.Policy.Fixed,
            )
        permit_body = self._permit_list_panel_body
        if permit_body is not None:
            permit_body.setEnabled(permit_expanded)
            permit_body.setMinimumHeight(0)
            permit_body.setMaximumHeight(16777215 if permit_expanded else 0)
            permit_body.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding if permit_expanded else QSizePolicy.Policy.Fixed,
            )

        address_button = self._address_panel_toggle_button
        if address_button is not None:
            address_button.setText("▲" if address_expanded else "▼")
            address_button.setToolTip(
                "Collapse Address List" if address_expanded else "Expand Address List"
            )
        permit_button = self._permit_panel_toggle_button
        if permit_button is not None:
            permit_button.setText("▼" if permit_expanded else "▲")
            permit_button.setToolTip("Collapse Permits" if permit_expanded else "Expand Permits")

        if left_layout is not None:
            left_layout.setStretch(0, 1 if address_expanded else 0)
            left_layout.setStretch(1, 0 if address_expanded else 1)

        if left_column_widget is not None:
            left_column_widget.setUpdatesEnabled(True)
            left_column_widget.update()

    def _build_tracker_overlay(self, scene: QWidget) -> None:
        panel_host = QWidget(scene)
        panel_host.setObjectName("PermitPanelHost")
        panel_stack = QStackedLayout(panel_host)
        panel_stack.setContentsMargins(0, 0, 0, 0)
        panel_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._panel_host = panel_host
        self._panel_stack = panel_stack

        home_view = QWidget(panel_host)
        self._panel_home_view = home_view
        root_layout = QVBoxLayout(home_view)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        panels_shell = QFrame(home_view)
        panels_shell.setObjectName("TrackerPanelsShell")
        panels_shell_layout = QHBoxLayout(panels_shell)
        panels_shell_layout.setContentsMargins(16, 16, 16, 16)
        panels_shell_layout.setSpacing(16)
        root_layout.addWidget(panels_shell, 1)

        left_column = QWidget(panels_shell)
        left_column.setObjectName("TrackerPanelsLeftColumn")
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(16)
        self._left_column_layout = left_column_layout

        left_panel, left_layout = self._create_tracker_panel(left_column, "Address List", with_title=False)
        left_panel.setProperty("panelRole", "address")
        self._address_list_panel = left_panel

        address_header_row = QHBoxLayout()
        address_header_row.setContentsMargins(0, 0, 0, 0)
        address_header_row.setSpacing(8)
        address_title_label = QLabel("Address List", left_panel)
        address_title_label.setObjectName("TrackerPanelTitle")
        address_header_row.addWidget(address_title_label, 1)
        address_toggle_button = QToolButton(left_panel)
        address_toggle_button.setObjectName("TrackerPanelCollapseButton")
        address_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        address_toggle_button.clicked.connect(
            lambda _checked=False: self._toggle_left_column_panel("address")
        )
        address_header_row.addWidget(
            address_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._address_panel_toggle_button = address_toggle_button
        left_layout.addLayout(address_header_row, 0)

        left_panel_body = QWidget(left_panel)
        left_panel_body_layout = QVBoxLayout(left_panel_body)
        left_panel_body_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_body_layout.setSpacing(10)
        self._address_list_panel_body = left_panel_body

        self._property_filter_combo = QComboBox(left_panel)
        self._property_filter_combo.setObjectName("TrackerPanelFilter")
        self._property_filter_combo.currentIndexChanged.connect(self._refresh_property_list)
        left_panel_body_layout.addWidget(self._property_filter_combo)

        self._property_search_input = QLineEdit(left_panel)
        self._property_search_input.setObjectName("TrackerPanelSearch")
        self._property_search_input.setPlaceholderText("Search address or parcel")
        self._property_search_input.textChanged.connect(self._refresh_property_list)
        left_panel_body_layout.addWidget(self._property_search_input)

        self._property_result_label = QLabel("0 addresses", left_panel)
        self._property_result_label.setObjectName("TrackerPanelMeta")
        left_panel_body_layout.addWidget(self._property_result_label)

        properties_list_host = QWidget(left_panel)
        properties_list_stack = QStackedLayout(properties_list_host)
        properties_list_stack.setContentsMargins(0, 0, 0, 0)
        properties_list_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._properties_list_stack = properties_list_stack

        self._properties_list_widget = QListWidget(properties_list_host)
        self._properties_list_widget.setObjectName("TrackerPanelList")
        self._properties_list_widget.setWordWrap(True)
        self._properties_list_widget.itemSelectionChanged.connect(self._on_property_selection_changed)
        properties_list_stack.addWidget(self._properties_list_widget)

        properties_empty_label = QLabel(
            "No addresses yet.\nClick Add Address to create your first property.",
            properties_list_host,
        )
        properties_empty_label.setObjectName("TrackerPanelEmptyState")
        properties_empty_label.setWordWrap(True)
        properties_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._properties_empty_label = properties_empty_label
        properties_list_stack.addWidget(properties_empty_label)
        properties_list_stack.setCurrentWidget(properties_empty_label)

        left_panel_body_layout.addWidget(properties_list_host, 1)

        add_property_button = QPushButton("Add Address", left_panel)
        add_property_button.setObjectName("TrackerPanelActionButton")
        add_property_button.clicked.connect(self._add_property)
        left_panel_body_layout.addWidget(add_property_button)

        open_admin_button = QPushButton("Open Admin Panel", left_panel)
        open_admin_button.setObjectName("TrackerPanelActionButton")
        open_admin_button.clicked.connect(self._open_contacts_and_jurisdictions_dialog)
        left_panel_body_layout.addWidget(open_admin_button)

        left_layout.addWidget(left_panel_body, 1)
        left_column_layout.addWidget(left_panel, 1)

        middle_panel, middle_layout = self._create_tracker_panel(left_column, "Permits", with_title=False)
        middle_panel.setProperty("panelRole", "permit")
        middle_panel.setProperty("contextual", "true")
        self._permit_list_panel = middle_panel

        permit_title_row = QHBoxLayout()
        permit_title_row.setContentsMargins(0, 0, 0, 0)
        permit_title_row.setSpacing(8)
        permit_title_label = QLabel("Permits", middle_panel)
        permit_title_label.setObjectName("TrackerPanelTitle")
        permit_title_row.addWidget(permit_title_label, 1)
        permit_toggle_button = QToolButton(middle_panel)
        permit_toggle_button.setObjectName("TrackerPanelCollapseButton")
        permit_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        permit_toggle_button.clicked.connect(
            lambda _checked=False: self._toggle_left_column_panel("permit")
        )
        permit_title_row.addWidget(
            permit_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._permit_panel_toggle_button = permit_toggle_button
        middle_layout.addLayout(permit_title_row, 0)

        permit_panel_body = QWidget(middle_panel)
        permit_panel_body_layout = QVBoxLayout(permit_panel_body)
        permit_panel_body_layout.setContentsMargins(0, 0, 0, 0)
        permit_panel_body_layout.setSpacing(10)
        self._permit_list_panel_body = permit_panel_body

        permit_header_row = QHBoxLayout()
        permit_header_row.setContentsMargins(0, 0, 0, 0)
        permit_header_row.setSpacing(8)

        self._permit_header_label = QLabel("Select an address to view permits", middle_panel)
        self._permit_header_label.setObjectName("TrackerPanelMeta")
        self._permit_header_label.setProperty("permitHeader", "true")
        permit_header_row.addWidget(self._permit_header_label, 1)

        add_permit_button = QPushButton("Add Permit", middle_panel)
        add_permit_button.setObjectName("TrackerPanelActionButton")
        add_permit_button.setProperty("addPermitPrimary", "true")
        add_permit_button.clicked.connect(self._add_permit)
        self._add_permit_button = add_permit_button
        permit_header_row.addWidget(
            add_permit_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        permit_panel_body_layout.addLayout(permit_header_row)

        permit_controls_host = QWidget(middle_panel)
        permit_controls_host.setObjectName("PermitControlsHost")
        permit_controls_layout = QVBoxLayout(permit_controls_host)
        permit_controls_layout.setContentsMargins(0, 0, 0, 0)
        permit_controls_layout.setSpacing(8)
        self._permit_controls_host = permit_controls_host

        type_picker = QWidget(permit_panel_body)
        type_picker.setObjectName("PermitCategoryPicker")
        self._permit_type_picker_host = type_picker
        type_picker_layout = QHBoxLayout(type_picker)
        type_picker_layout.setContentsMargins(0, 0, 0, 0)
        type_picker_layout.setSpacing(8)
        for permit_type, label in _PERMIT_TYPE_OPTIONS:
            button = QPushButton(label, type_picker)
            button.setObjectName("PermitCategoryPill")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=permit_type: self._set_active_permit_type_filter(value)
            )
            type_picker_layout.addWidget(button, 1)
            self._permit_type_buttons[permit_type] = button

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        self._permit_filter_combo = QComboBox(permit_controls_host)
        self._permit_filter_combo.setObjectName("TrackerPanelFilter")
        self._permit_filter_combo.addItem("All Statuses", "all")
        self._permit_filter_combo.addItem("Open", "open")
        self._permit_filter_combo.addItem("Closed", "closed")
        self._permit_filter_combo.addItem("Overdue", "overdue")
        for event_type in PERMIT_EVENT_TYPES:
            if event_type == "note":
                continue
            self._permit_filter_combo.addItem(event_type_label(event_type), event_type)
        self._permit_filter_combo.currentIndexChanged.connect(self._refresh_permit_list)
        filter_row.addWidget(self._permit_filter_combo, 0)

        self._permit_search_input = QLineEdit(permit_controls_host)
        self._permit_search_input.setObjectName("TrackerPanelSearch")
        self._permit_search_input.setPlaceholderText("Search permit #, status, next action")
        self._permit_search_input.textChanged.connect(self._refresh_permit_list)
        filter_row.addWidget(self._permit_search_input, 1)

        self._permit_result_label = QLabel("0 permits", permit_controls_host)
        self._permit_result_label.setObjectName("TrackerPanelMeta")
        filter_row.addWidget(
            self._permit_result_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        permit_controls_layout.addLayout(filter_row)
        permit_panel_body_layout.addWidget(permit_controls_host, 0)

        permits_list_host = QWidget(middle_panel)
        permits_list_stack = QStackedLayout(permits_list_host)
        permits_list_stack.setContentsMargins(0, 0, 0, 0)
        permits_list_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._permits_list_stack = permits_list_stack

        self._permits_list_widget = QListWidget(permits_list_host)
        self._permits_list_widget.setObjectName("TrackerPanelList")
        self._permits_list_widget.setWordWrap(True)
        self._permits_list_widget.itemSelectionChanged.connect(self._on_permit_selection_changed)
        permits_list_stack.addWidget(self._permits_list_widget)

        permits_empty_label = QLabel(
            "No permits yet.\nSelect an address and click Add Permit to create one.",
            permits_list_host,
        )
        permits_empty_label.setObjectName("TrackerPanelEmptyState")
        permits_empty_label.setWordWrap(True)
        permits_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._permits_empty_label = permits_empty_label
        permits_list_stack.addWidget(permits_empty_label)
        permits_list_stack.setCurrentWidget(permits_empty_label)

        permit_panel_body_layout.addWidget(permits_list_host, 1)
        permit_panel_body_layout.addWidget(type_picker, 0)
        middle_layout.addWidget(permit_panel_body, 1)
        left_column_layout.addWidget(middle_panel, 1)
        self._set_left_column_expanded_panel("address")
        panels_shell_layout.addWidget(left_column, 1)

        right_panel, right_layout = self._create_tracker_panel(panels_shell, "Permit Workspace")
        right_panel.setProperty("panelRole", "workspace")
        self._permit_workspace_panel = right_panel
        self._workspace_title_label = right_panel.findChild(QLabel, "TrackerPanelTitle")
        if self._workspace_title_label is not None:
            self._workspace_title_label.setText("Permit Workspace - Select Permit")

        next_step_label = QLabel("", right_panel)
        next_step_label.setObjectName("PermitWorkspaceNextStepNote")
        next_step_label.setWordWrap(True)
        next_step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_step_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        next_step_label.setVisible(False)
        self._workspace_next_step_label = next_step_label

        workspace_width_host = QWidget(right_panel)
        workspace_width_layout = QHBoxLayout(workspace_width_host)
        workspace_width_layout.setContentsMargins(0, 0, 0, 0)
        workspace_width_layout.setSpacing(0)

        workspace_content_host = QWidget(workspace_width_host)
        workspace_content_host.setObjectName("PermitWorkspaceContentHost")
        workspace_content_layout = QVBoxLayout(workspace_content_host)
        workspace_content_layout.setContentsMargins(0, 0, 0, 0)
        workspace_content_layout.setSpacing(10)
        self._permit_workspace_content_host = workspace_content_host

        blur_overlay = QFrame(workspace_content_host)
        blur_overlay.setObjectName("PermitWorkspaceBlurOverlay")
        blur_overlay.setVisible(False)
        blur_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._permit_workspace_blur_overlay = blur_overlay

        summary_width_host = QWidget(workspace_content_host)
        summary_width_layout = QHBoxLayout(summary_width_host)
        summary_width_layout.setContentsMargins(0, 0, 0, 0)
        summary_width_layout.setSpacing(0)

        summary_grid_host = QWidget(summary_width_host)
        summary_grid_host.setObjectName("PermitWorkspaceSummaryGrid")
        summary_grid_layout = QGridLayout(summary_grid_host)
        summary_grid_layout.setContentsMargins(2, 2, 2, 2)
        summary_grid_layout.setHorizontalSpacing(12)
        summary_grid_layout.setVerticalSpacing(12)

        workspace_lower_width_host = QWidget(workspace_content_host)
        workspace_lower_width_layout = QHBoxLayout(workspace_lower_width_host)
        workspace_lower_width_layout.setContentsMargins(0, 0, 0, 0)
        workspace_lower_width_layout.setSpacing(0)

        workspace_lower_scroll = _EdgeLockedScrollArea(workspace_lower_width_host)
        workspace_lower_scroll.setObjectName("PermitWorkspaceDetailScroll")
        workspace_lower_scroll.setWidgetResizable(True)
        workspace_lower_scroll.setFrameShape(QFrame.Shape.NoFrame)
        workspace_lower_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        workspace_lower_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        workspace_lower_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        workspace_lower_scroll.viewport().setAutoFillBackground(False)
        workspace_lower_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        workspace_lower_content_host = QWidget(workspace_lower_scroll)
        workspace_lower_content_layout = QVBoxLayout(workspace_lower_content_host)
        workspace_lower_content_layout.setContentsMargins(0, 0, 0, 0)
        workspace_lower_content_layout.setSpacing(10)

        workspace_focus_region = QFrame(workspace_lower_content_host)
        workspace_focus_region.setObjectName("PermitWorkspaceFocusRegion")
        workspace_focus_layout = QVBoxLayout(workspace_focus_region)
        workspace_focus_layout.setContentsMargins(10, 10, 10, 10)
        workspace_focus_layout.setSpacing(8)

        def create_workspace_info_cell(
            *,
            label_text: str,
            row: int,
            column: int,
            key: str,
        ) -> None:
            cell = QFrame(summary_grid_host)
            cell.setObjectName("PermitWorkspaceInfoCell")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(12, 10, 12, 10)
            cell_layout.setSpacing(3)

            label = QLabel(label_text, cell)
            label.setObjectName("PermitWorkspaceInfoLabel")
            cell_layout.addWidget(label, 0)

            value = QLabel("—", cell)
            value.setObjectName("PermitWorkspaceInfoValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cell_layout.addWidget(value, 1)

            summary_grid_layout.addWidget(cell, row, column)
            self._workspace_info_values[key] = value

        create_workspace_info_cell(label_text="Address", row=0, column=0, key="address")
        create_workspace_info_cell(label_text="Parcel", row=0, column=1, key="parcel")
        create_workspace_info_cell(label_text="Jurisdiction", row=0, column=2, key="jurisdiction")
        create_workspace_info_cell(label_text="Permit #", row=1, column=0, key="permit_number")
        create_workspace_info_cell(label_text="Status", row=1, column=1, key="status")
        create_workspace_info_cell(label_text="Contacts / Portal", row=1, column=2, key="contacts_portal")

        for column in range(3):
            summary_grid_layout.setColumnStretch(column, 1)
        summary_width_layout.addStretch(5)
        summary_width_layout.addWidget(summary_grid_host, 90)
        summary_width_layout.addStretch(5)
        workspace_content_layout.addWidget(summary_width_host, 0)

        top_actions = QHBoxLayout()
        top_actions.setContentsMargins(0, 0, 0, 0)
        top_actions.setSpacing(8)
        top_actions.addStretch(1)

        self._open_portal_button = QPushButton("Open Portal", workspace_focus_region)
        self._open_portal_button.setObjectName("TrackerPanelActionButton")
        self._open_portal_button.clicked.connect(self._open_selected_portal)
        top_actions.addWidget(self._open_portal_button)

        self._set_next_action_button = QPushButton("Set Next Action", workspace_focus_region)
        self._set_next_action_button.setObjectName("TrackerPanelActionButton")
        self._set_next_action_button.clicked.connect(self._set_next_action)
        top_actions.addWidget(self._set_next_action_button)

        self._add_event_button = QPushButton("Add Event", workspace_focus_region)
        self._add_event_button.setObjectName("TrackerPanelActionButton")
        self._add_event_button.clicked.connect(self._add_event)
        top_actions.addWidget(self._add_event_button)

        top_actions.addStretch(1)
        workspace_focus_layout.addLayout(top_actions)

        next_action_card = QFrame(workspace_focus_region)
        next_action_card.setObjectName("PermitDocumentsSection")
        next_action_card.setProperty("nextActionPanel", "true")
        next_action_layout = QVBoxLayout(next_action_card)
        next_action_layout.setContentsMargins(12, 10, 12, 10)
        next_action_layout.setSpacing(6)

        next_action_title = QLabel("Next Action", next_action_card)
        next_action_title.setObjectName("PermitDocumentsTitle")
        next_action_title.setProperty("nextAction", "true")
        next_action_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_action_layout.addWidget(next_action_title)

        self._next_action_label = QLabel("No next action set.", next_action_card)
        self._next_action_label.setObjectName("PermitDocumentStatus")
        self._next_action_label.setProperty("nextAction", "true")
        self._next_action_label.setWordWrap(True)
        self._next_action_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_action_layout.addWidget(self._next_action_label)
        workspace_focus_layout.addWidget(next_action_card)

        timeline_card = QFrame(workspace_focus_region)
        timeline_card.setObjectName("PermitDocumentsSection")
        timeline_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        timeline_card.setMaximumHeight(240)
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(12, 10, 12, 10)
        timeline_layout.setSpacing(6)

        timeline_title_row = QHBoxLayout()
        timeline_title_row.setContentsMargins(0, 0, 0, 0)
        timeline_title_row.setSpacing(8)

        timeline_title = QLabel("Timeline", timeline_card)
        timeline_title.setObjectName("PermitDocumentsTitle")
        timeline_title.setProperty("timeline", "true")
        timeline_title_row.addWidget(timeline_title, 0, Qt.AlignmentFlag.AlignVCenter)
        self._timeline_title_label = timeline_title

        timeline_title_row.addStretch(1)
        timeline_mode_toggle_button = QPushButton("Show Next Action Timeline", timeline_card)
        timeline_mode_toggle_button.setObjectName("TrackerPanelActionButton")
        timeline_mode_toggle_button.clicked.connect(self._toggle_timeline_mode)
        timeline_title_row.addWidget(
            timeline_mode_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._timeline_mode_toggle_button = timeline_mode_toggle_button
        timeline_layout.addLayout(timeline_title_row)

        timeline_hint = QLabel(
            "Oldest on the left, newest on the right. Only saved events appear here.",
            timeline_card,
        )
        timeline_hint.setObjectName("TrackerPanelHint")
        timeline_hint.setProperty("timeline", "true")
        timeline_hint.setWordWrap(True)
        timeline_layout.addWidget(timeline_hint, 0)
        self._timeline_hint_label = timeline_hint

        timeline_scroll = _EdgeLockedScrollArea(timeline_card)
        timeline_scroll.setObjectName("PermitTimelineScroll")
        timeline_scroll.setWidgetResizable(False)
        timeline_scroll.setFrameShape(QFrame.Shape.NoFrame)
        timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        timeline_scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        timeline_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        timeline_scroll.setFixedHeight(132)
        self._timeline_scroll_area = timeline_scroll

        timeline_track = QWidget(timeline_scroll)
        timeline_track.setObjectName("PermitTimelineTrack")
        timeline_track_layout = QHBoxLayout(timeline_track)
        timeline_track_layout.setContentsMargins(2, 2, 2, 2)
        timeline_track_layout.setSpacing(10)
        self._timeline_track_widget = timeline_track
        self._timeline_track_layout = timeline_track_layout
        timeline_scroll.setWidget(timeline_track)
        timeline_layout.addWidget(timeline_scroll, 1)
        workspace_focus_layout.addWidget(timeline_card, 0)

        workspace_lower_content_layout.addWidget(workspace_focus_region, 0)

        docs_card = QFrame(workspace_lower_content_host)
        docs_card.setObjectName("PermitDocumentsSection")
        docs_layout = QVBoxLayout(docs_card)
        docs_layout.setContentsMargins(12, 10, 12, 10)
        docs_layout.setSpacing(6)

        docs_title_row = QHBoxLayout()
        docs_title_row.setContentsMargins(0, 0, 0, 0)
        docs_title_row.setSpacing(8)
        docs_title = QLabel("Documents Checklist", docs_card)
        docs_title.setObjectName("PermitDocumentsTitle")
        docs_title_row.addWidget(docs_title, 0)
        docs_title_row.addStretch(1)

        docs_title_actions = QVBoxLayout()
        docs_title_actions.setContentsMargins(0, 0, 0, 0)
        docs_title_actions.setSpacing(6)

        template_actions_row = QHBoxLayout()
        template_actions_row.setContentsMargins(0, 0, 0, 0)
        template_actions_row.setSpacing(8)

        template_apply_combo = QComboBox(docs_card)
        template_apply_combo.setObjectName("PermitFormCombo")
        template_apply_combo.setMinimumWidth(220)
        template_actions_row.addWidget(
            template_apply_combo,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._document_template_apply_combo = template_apply_combo

        template_apply_button = QPushButton("Select", docs_card)
        template_apply_button.setObjectName("TrackerPanelActionButton")
        template_apply_button.clicked.connect(self._apply_selected_document_template_to_permit)
        template_actions_row.addWidget(
            template_apply_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._document_template_apply_button = template_apply_button
        docs_title_actions.addLayout(template_actions_row)

        docs_title_row.addLayout(docs_title_actions, 0)
        docs_layout.addLayout(docs_title_row, 0)

        self._document_status_label = QLabel("Select a permit to manage documents.", docs_card)
        self._document_status_label.setObjectName("PermitDocumentStatus")
        self._document_status_label.setWordWrap(True)
        docs_layout.addWidget(self._document_status_label)

        docs_hint = QLabel(
            "Each slot tracks version cycles. Select a file to mark it, or leave no file selected to mark the entire slot folder.",
            docs_card,
        )
        docs_hint.setObjectName("TrackerPanelHint")
        docs_hint.setWordWrap(True)
        docs_layout.addWidget(docs_hint, 0)

        docs_slot_tools_row = QHBoxLayout()
        docs_slot_tools_row.setContentsMargins(0, 0, 0, 0)
        docs_slot_tools_row.setSpacing(8)

        self._document_open_folder_button = QPushButton("Open Folder", docs_card)
        self._document_open_folder_button.setObjectName("TrackerPanelActionButton")
        self._document_open_folder_button.clicked.connect(self._open_selected_slot_folder)
        docs_slot_tools_row.addWidget(
            self._document_open_folder_button,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_new_cycle_button = QPushButton("New Cycle", docs_card)
        self._document_new_cycle_button.setObjectName("TrackerPanelActionButton")
        self._document_new_cycle_button.clicked.connect(self._start_selected_slot_new_cycle)
        docs_slot_tools_row.addWidget(
            self._document_new_cycle_button,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        docs_slot_tools_row.addStretch(1)

        self._document_mark_accepted_button = QPushButton("Mark Accepted", docs_card)
        self._document_mark_accepted_button.setObjectName("TrackerPanelActionButton")
        self._document_mark_accepted_button.clicked.connect(
            lambda: self._mark_selected_slot_status("accepted")
        )
        docs_slot_tools_row.addWidget(
            self._document_mark_accepted_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_mark_rejected_button = QPushButton("Mark Rejected", docs_card)
        self._document_mark_rejected_button.setObjectName("TrackerPanelActionButton")
        self._document_mark_rejected_button.clicked.connect(
            lambda: self._mark_selected_slot_status("rejected")
        )
        docs_slot_tools_row.addWidget(
            self._document_mark_rejected_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        docs_layout.addLayout(docs_slot_tools_row, 0)

        docs_headers = QHBoxLayout()
        docs_headers.setContentsMargins(2, 0, 2, 0)
        docs_headers.setSpacing(8)
        slots_header = QLabel("Checklist Slots", docs_card)
        slots_header.setObjectName("TrackerPanelSubsectionTitle")
        docs_headers.addWidget(slots_header, 1)
        files_header = QLabel("Files in Active Cycle", docs_card)
        files_header.setObjectName("TrackerPanelSubsectionTitle")
        docs_headers.addWidget(files_header, 1)
        docs_layout.addLayout(docs_headers)

        docs_lists_row = QHBoxLayout()
        docs_lists_row.setContentsMargins(0, 0, 0, 0)
        docs_lists_row.setSpacing(8)

        self._document_slot_list_widget = QListWidget(docs_card)
        self._document_slot_list_widget.setObjectName("TrackerPanelList")
        self._document_slot_list_widget.itemSelectionChanged.connect(self._on_document_slot_selection_changed)
        docs_lists_row.addWidget(self._document_slot_list_widget, 1)

        self._document_file_list_widget = QListWidget(docs_card)
        self._document_file_list_widget.setObjectName("PermitDocumentList")
        self._document_file_list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._document_file_list_widget.itemSelectionChanged.connect(self._on_document_file_selection_changed)
        self._document_file_list_widget.itemDoubleClicked.connect(self._open_selected_document)
        docs_lists_row.addWidget(self._document_file_list_widget, 1)

        docs_layout.addLayout(docs_lists_row)

        docs_actions = QHBoxLayout()
        docs_actions.setContentsMargins(0, 0, 0, 0)
        docs_actions.setSpacing(8)

        self._document_upload_button = QPushButton("Upload", docs_card)
        self._document_upload_button.setObjectName("TrackerPanelActionButton")
        self._document_upload_button.clicked.connect(self._upload_documents_to_slot)
        docs_actions.addWidget(self._document_upload_button)

        docs_actions.addStretch(1)

        self._document_open_file_button = QPushButton("Open File", docs_card)
        self._document_open_file_button.setObjectName("TrackerPanelActionButton")
        self._document_open_file_button.clicked.connect(self._open_selected_document)
        docs_actions.addWidget(
            self._document_open_file_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_remove_file_button = QPushButton("Remove File", docs_card)
        self._document_remove_file_button.setObjectName("PermitFormDangerButton")
        self._document_remove_file_button.setProperty("adminHeaderDanger", "true")
        self._document_remove_file_button.clicked.connect(self._remove_selected_document)
        docs_actions.addWidget(
            self._document_remove_file_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        docs_layout.addLayout(docs_actions)

        workspace_lower_content_layout.addWidget(docs_card, 1)
        workspace_lower_scroll.setWidget(workspace_lower_content_host)

        workspace_lower_width_layout.addWidget(workspace_lower_scroll, 1)
        workspace_content_layout.addWidget(workspace_lower_width_host, 1)
        workspace_width_layout.addStretch(1)
        workspace_width_layout.addWidget(workspace_content_host, 6)
        workspace_width_layout.addStretch(1)
        right_layout.addWidget(workspace_width_host, 1)

        panels_shell_layout.addWidget(right_panel, 2)

        panel_stack.addWidget(home_view)
        admin_view = self._build_contacts_and_jurisdictions_view(panel_host)
        self._panel_admin_view = admin_view
        panel_stack.addWidget(admin_view)
        add_property_view = self._build_add_property_view(panel_host)
        self._panel_add_property_view = add_property_view
        panel_stack.addWidget(add_property_view)
        add_permit_view = self._build_add_permit_view(panel_host)
        self._panel_add_permit_view = add_permit_view
        panel_stack.addWidget(add_permit_view)
        panel_stack.setCurrentWidget(home_view)
        panel_host.hide()

    def _build_contacts_and_jurisdictions_view(self, parent: QWidget) -> QWidget:
        view = QWidget(parent)
        view.setObjectName("ContactsJurisdictionsView")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QFrame(view)
        header.setObjectName("TrackerPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)

        title = QLabel("Admin Panel", header)
        title.setObjectName("TrackerPanelTitle")
        header_layout.addWidget(title, 0)

        hint = QLabel(
            "Select an existing record on the left to edit it, or use the Add New button to create a new one.",
            header,
        )
        hint.setObjectName("TrackerPanelMeta")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(hint, 1)

        back_button = QPushButton("Back to Tracker", header)
        back_button.setObjectName("TrackerPanelActionButton")
        back_button.setProperty("adminBackButton", "true")
        back_button.setMinimumHeight(34)
        back_button.setMinimumWidth(150)
        back_button.clicked.connect(self._close_contacts_and_jurisdictions_view)
        header_layout.addWidget(back_button, 0)
        layout.addWidget(header, 0)

        content = QFrame(view)
        content.setObjectName("TrackerPanel")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        layout.addWidget(content, 1)

        tabs = QTabWidget(content)
        tabs.setObjectName("ContactsJurisdictionsTabs")
        tabs.tabBar().setExpanding(False)
        self._admin_tabs = tabs
        content_layout.addWidget(tabs, 1)

        contact_tab = QWidget(tabs)
        contact_layout = QHBoxLayout(contact_tab)
        contact_layout.setContentsMargins(0, 0, 0, 0)
        contact_layout.setSpacing(14)

        contact_left_card = QFrame(contact_tab)
        contact_left_card.setObjectName("AdminListPane")
        contact_left = QVBoxLayout(contact_left_card)
        contact_left.setContentsMargins(12, 12, 12, 12)
        contact_left.setSpacing(10)

        contacts_title = QLabel("Contacts Directory", contact_left_card)
        contacts_title.setObjectName("AdminListTitleChip")
        contact_left.addWidget(contacts_title, 0)

        contacts_hint = QLabel(
            "Find contacts quickly, then edit details and communication bundles on the right.",
            contact_left_card,
        )
        contacts_hint.setObjectName("AdminSectionHint")
        contacts_hint.setWordWrap(True)
        contact_left.addWidget(contacts_hint, 0)

        contacts_search = QLineEdit(contact_left_card)
        contacts_search.setObjectName("TrackerPanelSearch")
        contacts_search.setPlaceholderText("Search contacts (name, role, email, number, note)")
        contacts_search.setClearButtonEnabled(True)
        contacts_search.textChanged.connect(self._on_admin_contacts_search_changed)
        contact_left.addWidget(contacts_search, 0)
        self._admin_contacts_search_input = contacts_search

        add_contact_button = QPushButton("Add New Contact", contact_left_card)
        add_contact_button.setObjectName("TrackerPanelActionButton")
        add_contact_button.setProperty("adminPrimaryCta", "true")
        add_contact_button.setMinimumHeight(34)
        add_contact_button.clicked.connect(self._on_admin_add_new_contact_clicked)
        contact_left.addWidget(add_contact_button, 0)

        contacts_count_label = QLabel("0 contacts", contact_left_card)
        contacts_count_label.setObjectName("TrackerPanelMeta")
        contact_left.addWidget(contacts_count_label, 0)
        self._admin_contacts_count_label = contacts_count_label

        contacts_list_host = QWidget(contact_left_card)
        contacts_list_stack = QStackedLayout(contacts_list_host)
        contacts_list_stack.setContentsMargins(0, 0, 0, 0)
        contacts_list_stack.setSpacing(0)

        contacts_list = QListWidget(contacts_list_host)
        contacts_list.setObjectName("TrackerPanelList")
        contacts_list.setWordWrap(True)
        contacts_list.setSpacing(6)
        contacts_list.itemSelectionChanged.connect(self._on_admin_contact_selected)
        contacts_list_stack.addWidget(contacts_list)
        self._admin_contacts_list_widget = contacts_list

        contacts_empty_label = QLabel(
            "No contacts yet.\nUse Add New Contact to get started.",
            contacts_list_host,
        )
        contacts_empty_label.setObjectName("AdminListEmptyState")
        contacts_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        contacts_empty_label.setWordWrap(True)
        contacts_list_stack.addWidget(contacts_empty_label)
        self._admin_contacts_empty_label = contacts_empty_label
        self._admin_contacts_list_stack = contacts_list_stack

        contact_left.addWidget(contacts_list_host, 1)

        contact_layout.addWidget(contact_left_card, 1)

        contact_right_host = QWidget(contact_tab)
        contact_right_layout = QHBoxLayout(contact_right_host)
        contact_right_layout.setContentsMargins(0, 0, 0, 0)
        contact_right_layout.setSpacing(0)

        contact_scroll = _EdgeLockedScrollArea(contact_right_host)
        contact_scroll.setObjectName("AdminEditorScroll")
        contact_scroll.setWidgetResizable(True)
        contact_scroll.setFrameShape(QFrame.Shape.NoFrame)
        contact_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contact_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        contact_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        contact_scroll.viewport().setAutoFillBackground(False)
        contact_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        contact_form = QFrame(contact_scroll)
        contact_form.setObjectName("PermitFormCard")
        contact_form.setProperty("adminForm", "true")
        contact_form.setMinimumWidth(500)
        self._admin_contact_form_widget = contact_form
        contact_form_layout = QVBoxLayout(contact_form)
        contact_form_layout.setContentsMargins(14, 14, 14, 14)
        contact_form_layout.setSpacing(11)

        contact_header_bar = QFrame(contact_form)
        contact_header_bar.setObjectName("AdminHeaderBar")
        contact_header_row = QHBoxLayout(contact_header_bar)
        contact_header_row.setContentsMargins(10, 8, 10, 8)
        contact_header_row.setSpacing(8)

        contact_mode_label = QLabel("Adding New Contact", contact_form)
        contact_mode_label.setObjectName("AdminModeTitle")
        contact_header_row.addWidget(contact_mode_label, 0)
        self._admin_contact_mode_label = contact_mode_label

        contact_header_row.addStretch(1)

        save_contact_button = QPushButton("Create Contact", contact_form)
        save_contact_button.setObjectName("TrackerPanelActionButton")
        save_contact_button.setProperty("adminPrimaryCta", "true")
        save_contact_button.setMinimumHeight(32)
        save_contact_button.clicked.connect(self._admin_save_contact)
        contact_header_row.addWidget(save_contact_button, 0)
        self._admin_contact_save_button = save_contact_button

        delete_contact_button = QPushButton("Delete Contact", contact_form)
        delete_contact_button.setObjectName("PermitFormDangerButton")
        delete_contact_button.setMinimumHeight(32)
        delete_contact_button.clicked.connect(self._admin_delete_contact)
        contact_header_row.addWidget(delete_contact_button, 0)
        self._admin_contact_delete_button = delete_contact_button

        contact_form_layout.addWidget(contact_header_bar, 0)

        contact_details_row = QHBoxLayout()
        contact_details_row.setContentsMargins(0, 0, 0, 0)
        contact_details_row.setSpacing(8)

        contact_details_label = QLabel("Contact Details", contact_form)
        contact_details_label.setObjectName("AdminSectionTitle")
        contact_details_row.addWidget(contact_details_label, 0)

        contact_dirty_bubble = QLabel("Empty", contact_form)
        contact_dirty_bubble.setObjectName("AdminDirtyBubble")
        contact_dirty_bubble.setProperty("dirtyState", "empty")
        contact_dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        contact_dirty_bubble.setMinimumWidth(92)
        contact_dirty_bubble.setMinimumHeight(24)
        contact_details_row.addWidget(contact_dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._admin_contact_dirty_bubble = contact_dirty_bubble

        contact_details_row.addStretch(1)
        contact_form_layout.addLayout(contact_details_row)

        self._admin_contact_field_shells = []
        contact_fields = QFormLayout()
        contact_fields.setContentsMargins(0, 0, 0, 0)
        contact_fields.setHorizontalSpacing(10)
        contact_fields.setVerticalSpacing(8)

        self._admin_contact_name_input = QLineEdit(contact_form)
        self._admin_contact_name_input.setObjectName("PermitFormInput")
        self._admin_contact_name_input.setPlaceholderText("Full name")
        self._admin_contact_name_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_name_input.returnPressed.connect(self._admin_save_contact)
        contact_fields.addRow(
            self._build_admin_input_shell(
                label_text="Name",
                field_widget=self._admin_contact_name_input,
                parent=contact_form,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_roles_input = QLineEdit(contact_form)
        self._admin_contact_roles_input.setObjectName("PermitFormInput")
        self._admin_contact_roles_input.setPlaceholderText("client, contractor, owner...")
        self._admin_contact_roles_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_roles_input.returnPressed.connect(self._admin_save_contact)
        contact_fields.addRow(
            self._build_admin_input_shell(
                label_text="Roles",
                field_widget=self._admin_contact_roles_input,
                parent=contact_form,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )
        contact_form_layout.addLayout(contact_fields)

        methods_label = QLabel("Email + Number Bundles (0)", contact_form)
        methods_label.setObjectName("AdminSectionTitle")
        contact_form_layout.addWidget(methods_label, 0)
        self._admin_contact_methods_label = methods_label

        methods_hint = QLabel(
            "Group emails and numbers by context so teams know which channel to use.",
            contact_form,
        )
        methods_hint.setObjectName("AdminSectionHint")
        methods_hint.setWordWrap(True)
        contact_form_layout.addWidget(methods_hint, 0)

        contact_method_actions = QHBoxLayout()
        contact_method_actions.setContentsMargins(0, 0, 0, 0)
        contact_method_actions.setSpacing(8)

        add_method_button = QPushButton("Add Bundle", contact_form)
        add_method_button.setObjectName("TrackerPanelActionButton")
        add_method_button.setProperty("bundleAction", "update")
        add_method_button.setProperty("bundleEditing", "false")
        add_method_button.setMinimumHeight(30)
        add_method_button.setMinimumWidth(108)
        add_method_button.clicked.connect(self._admin_add_contact_method_bundle)
        contact_method_actions.addWidget(add_method_button, 0)
        self._admin_contact_add_method_button = add_method_button

        cancel_edit_bundle_button = QPushButton("Cancel Edit", contact_form)
        cancel_edit_bundle_button.setObjectName("TrackerPanelActionButton")
        cancel_edit_bundle_button.setProperty("bundleAction", "cancel")
        cancel_edit_bundle_button.setProperty("bundleEditing", "false")
        cancel_edit_bundle_button.setMinimumHeight(30)
        cancel_edit_bundle_button.setMinimumWidth(108)
        cancel_edit_bundle_button.clicked.connect(self._admin_cancel_contact_method_edit)
        cancel_edit_bundle_button.hide()
        contact_method_actions.addWidget(cancel_edit_bundle_button, 0)
        self._admin_contact_cancel_method_button = cancel_edit_bundle_button

        bundle_toggle_button = QPushButton(">", contact_form)
        bundle_toggle_button.setObjectName("TrackerPanelActionButton")
        bundle_toggle_button.setFixedSize(34, 30)
        bundle_toggle_button.clicked.connect(self._toggle_admin_contact_bundle_fields)
        contact_method_actions.addWidget(bundle_toggle_button, 0)
        self._admin_contact_bundle_toggle_button = bundle_toggle_button

        contact_method_actions.addStretch(1)
        contact_form_layout.addLayout(contact_method_actions)

        contact_bundle_fields_host = QWidget(contact_form)
        contact_bundle_fields_host.setMaximumHeight(0)
        contact_form_layout.addWidget(contact_bundle_fields_host, 0)
        self._admin_contact_bundle_fields_host = contact_bundle_fields_host

        contact_bundle_fields = QFormLayout(contact_bundle_fields_host)
        contact_bundle_fields.setContentsMargins(0, 0, 0, 0)
        contact_bundle_fields.setHorizontalSpacing(10)
        contact_bundle_fields.setVerticalSpacing(8)

        self._admin_contact_bundle_name_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_bundle_name_input.setObjectName("PermitFormInput")
        self._admin_contact_bundle_name_input.setPlaceholderText("Office, permit desk, after-hours...")
        self._admin_contact_bundle_name_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_bundle_name_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Name",
                field_widget=self._admin_contact_bundle_name_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_numbers_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_numbers_input.setObjectName("PermitFormInput")
        self._admin_contact_numbers_input.setPlaceholderText("comma/semicolon-separated")
        self._admin_contact_numbers_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_numbers_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Number(s)",
                field_widget=self._admin_contact_numbers_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_emails_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_emails_input.setObjectName("PermitFormInput")
        self._admin_contact_emails_input.setPlaceholderText("comma/semicolon-separated")
        self._admin_contact_emails_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_emails_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Email(s)",
                field_widget=self._admin_contact_emails_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_note_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_note_input.setObjectName("PermitFormInput")
        self._admin_contact_note_input.setPlaceholderText("note for this bundle (office, permit desk, after-hours...)")
        self._admin_contact_note_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_note_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Note",
                field_widget=self._admin_contact_note_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        bundle_animation = QPropertyAnimation(contact_bundle_fields_host, b"maximumHeight", contact_bundle_fields_host)
        bundle_animation.setDuration(170)
        bundle_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._admin_contact_bundle_fields_animation = bundle_animation
        self._set_admin_contact_bundle_fields_open(False, animate=False)
        self._sync_admin_contact_bundle_action_state()

        contact_methods_host = QWidget(contact_form)
        contact_methods_host.setObjectName("AttachedContactsHost")
        contact_methods_layout = QVBoxLayout(contact_methods_host)
        contact_methods_layout.setContentsMargins(0, 0, 0, 0)
        contact_methods_layout.setSpacing(6)
        self._admin_contact_methods_host = contact_methods_host
        contact_form_layout.addWidget(contact_methods_host, 1)

        contact_color_picker = self._build_admin_color_picker_widget(
            parent=contact_form,
            entity_kind="contact",
        )
        self._admin_contact_color_picker_host = contact_color_picker
        contact_color_shell = self._build_admin_input_shell(
            label_text="List Color Picker",
            field_widget=contact_color_picker,
            parent=contact_form,
            shell_bucket=None,
            field_stretch=0,
            left_align_field=True,
        )
        self._admin_contact_color_shell = contact_color_shell
        contact_form_layout.addWidget(contact_color_shell, 0, Qt.AlignmentFlag.AlignLeft)

        contact_right_layout.addStretch(15)
        contact_scroll.setWidget(contact_form)
        contact_right_layout.addWidget(contact_scroll, 70)
        contact_right_layout.addStretch(15)
        contact_layout.addWidget(contact_right_host, 3)

        jurisdiction_tab = QWidget(tabs)
        jurisdiction_layout = QHBoxLayout(jurisdiction_tab)
        jurisdiction_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_layout.setSpacing(14)

        jurisdiction_left_card = QFrame(jurisdiction_tab)
        jurisdiction_left_card.setObjectName("AdminListPane")
        jurisdiction_left = QVBoxLayout(jurisdiction_left_card)
        jurisdiction_left.setContentsMargins(12, 12, 12, 12)
        jurisdiction_left.setSpacing(10)

        jurisdictions_title = QLabel("Jurisdictions Directory", jurisdiction_left_card)
        jurisdictions_title.setObjectName("AdminListTitleChip")
        jurisdiction_left.addWidget(jurisdictions_title, 0)

        jurisdictions_hint = QLabel(
            "Keep permitting authorities organized and attach the right contacts to each one.",
            jurisdiction_left_card,
        )
        jurisdictions_hint.setObjectName("AdminSectionHint")
        jurisdictions_hint.setWordWrap(True)
        jurisdiction_left.addWidget(jurisdictions_hint, 0)

        jurisdictions_search = QLineEdit(jurisdiction_left_card)
        jurisdictions_search.setObjectName("TrackerPanelSearch")
        jurisdictions_search.setPlaceholderText("Search jurisdictions (name, type, portal, contact)")
        jurisdictions_search.setClearButtonEnabled(True)
        jurisdictions_search.textChanged.connect(self._on_admin_jurisdictions_search_changed)
        jurisdiction_left.addWidget(jurisdictions_search, 0)
        self._admin_jurisdictions_search_input = jurisdictions_search

        add_jurisdiction_button = QPushButton("Add New Jurisdiction", jurisdiction_left_card)
        add_jurisdiction_button.setObjectName("TrackerPanelActionButton")
        add_jurisdiction_button.setProperty("adminPrimaryCta", "true")
        add_jurisdiction_button.setMinimumHeight(34)
        add_jurisdiction_button.clicked.connect(self._on_admin_add_new_jurisdiction_clicked)
        jurisdiction_left.addWidget(add_jurisdiction_button, 0)

        jurisdictions_count_label = QLabel("0 jurisdictions", jurisdiction_left_card)
        jurisdictions_count_label.setObjectName("TrackerPanelMeta")
        jurisdiction_left.addWidget(jurisdictions_count_label, 0)
        self._admin_jurisdictions_count_label = jurisdictions_count_label

        jurisdictions_list_host = QWidget(jurisdiction_left_card)
        jurisdictions_list_stack = QStackedLayout(jurisdictions_list_host)
        jurisdictions_list_stack.setContentsMargins(0, 0, 0, 0)
        jurisdictions_list_stack.setSpacing(0)

        jurisdictions_list = QListWidget(jurisdictions_list_host)
        jurisdictions_list.setObjectName("TrackerPanelList")
        jurisdictions_list.setWordWrap(True)
        jurisdictions_list.setSpacing(6)
        jurisdictions_list.itemSelectionChanged.connect(self._on_admin_jurisdiction_selected)
        jurisdictions_list_stack.addWidget(jurisdictions_list)
        self._admin_jurisdictions_list_widget = jurisdictions_list

        jurisdictions_empty_label = QLabel(
            "No jurisdictions yet.\nUse Add New Jurisdiction to get started.",
            jurisdictions_list_host,
        )
        jurisdictions_empty_label.setObjectName("AdminListEmptyState")
        jurisdictions_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jurisdictions_empty_label.setWordWrap(True)
        jurisdictions_list_stack.addWidget(jurisdictions_empty_label)
        self._admin_jurisdictions_empty_label = jurisdictions_empty_label
        self._admin_jurisdictions_list_stack = jurisdictions_list_stack

        jurisdiction_left.addWidget(jurisdictions_list_host, 1)
        jurisdiction_layout.addWidget(jurisdiction_left_card, 1)

        jurisdiction_right_host = QWidget(jurisdiction_tab)
        jurisdiction_right_layout = QHBoxLayout(jurisdiction_right_host)
        jurisdiction_right_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_right_layout.setSpacing(0)

        jurisdiction_scroll = _EdgeLockedScrollArea(jurisdiction_right_host)
        jurisdiction_scroll.setObjectName("AdminEditorScroll")
        jurisdiction_scroll.setWidgetResizable(True)
        jurisdiction_scroll.setFrameShape(QFrame.Shape.NoFrame)
        jurisdiction_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        jurisdiction_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        jurisdiction_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        jurisdiction_scroll.viewport().setAutoFillBackground(False)
        jurisdiction_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        jurisdiction_form = QFrame(jurisdiction_scroll)
        jurisdiction_form.setObjectName("PermitFormCard")
        jurisdiction_form.setProperty("adminForm", "true")
        jurisdiction_form.setMinimumWidth(540)
        self._admin_jurisdiction_form_widget = jurisdiction_form
        jurisdiction_form_layout = QVBoxLayout(jurisdiction_form)
        jurisdiction_form_layout.setContentsMargins(14, 14, 14, 14)
        jurisdiction_form_layout.setSpacing(11)

        jurisdiction_header_bar = QFrame(jurisdiction_form)
        jurisdiction_header_bar.setObjectName("AdminHeaderBar")
        jurisdiction_header_row = QHBoxLayout(jurisdiction_header_bar)
        jurisdiction_header_row.setContentsMargins(10, 8, 10, 8)
        jurisdiction_header_row.setSpacing(8)

        jurisdiction_mode_label = QLabel("Adding New Jurisdiction", jurisdiction_form)
        jurisdiction_mode_label.setObjectName("AdminModeTitle")
        jurisdiction_header_row.addWidget(jurisdiction_mode_label, 0)
        self._admin_jurisdiction_mode_label = jurisdiction_mode_label

        jurisdiction_header_row.addStretch(1)

        save_jurisdiction_button = QPushButton("Create Jurisdiction", jurisdiction_form)
        save_jurisdiction_button.setObjectName("TrackerPanelActionButton")
        save_jurisdiction_button.setProperty("adminPrimaryCta", "true")
        save_jurisdiction_button.setMinimumHeight(32)
        save_jurisdiction_button.clicked.connect(self._admin_save_jurisdiction)
        jurisdiction_header_row.addWidget(save_jurisdiction_button, 0)
        self._admin_jurisdiction_save_button = save_jurisdiction_button

        delete_jurisdiction_button = QPushButton("Delete Jurisdiction", jurisdiction_form)
        delete_jurisdiction_button.setObjectName("PermitFormDangerButton")
        delete_jurisdiction_button.setMinimumHeight(32)
        delete_jurisdiction_button.clicked.connect(self._admin_delete_jurisdiction)
        jurisdiction_header_row.addWidget(delete_jurisdiction_button, 0)
        self._admin_jurisdiction_delete_button = delete_jurisdiction_button

        jurisdiction_form_layout.addWidget(jurisdiction_header_bar, 0)

        jurisdiction_details_row = QHBoxLayout()
        jurisdiction_details_row.setContentsMargins(0, 0, 0, 0)
        jurisdiction_details_row.setSpacing(8)

        jurisdiction_details_label = QLabel("Jurisdiction Details", jurisdiction_form)
        jurisdiction_details_label.setObjectName("AdminSectionTitle")
        jurisdiction_details_row.addWidget(jurisdiction_details_label, 0)

        jurisdiction_dirty_bubble = QLabel("Empty", jurisdiction_form)
        jurisdiction_dirty_bubble.setObjectName("AdminDirtyBubble")
        jurisdiction_dirty_bubble.setProperty("dirtyState", "empty")
        jurisdiction_dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jurisdiction_dirty_bubble.setMinimumWidth(92)
        jurisdiction_dirty_bubble.setMinimumHeight(24)
        jurisdiction_details_row.addWidget(jurisdiction_dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._admin_jurisdiction_dirty_bubble = jurisdiction_dirty_bubble

        jurisdiction_details_row.addStretch(1)
        jurisdiction_form_layout.addLayout(jurisdiction_details_row)

        self._admin_jurisdiction_field_shells = []
        jurisdiction_details_split_row = QHBoxLayout()
        jurisdiction_details_split_row.setContentsMargins(0, 0, 0, 0)
        jurisdiction_details_split_row.setSpacing(12)

        jurisdiction_fields_host = QWidget(jurisdiction_form)
        jurisdiction_fields_host_layout = QVBoxLayout(jurisdiction_fields_host)
        jurisdiction_fields_host_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_fields_host_layout.setSpacing(0)
        self._admin_jurisdiction_fields_host = jurisdiction_fields_host

        jurisdiction_fields = QFormLayout()
        jurisdiction_fields.setContentsMargins(0, 0, 0, 0)
        jurisdiction_fields.setHorizontalSpacing(10)
        jurisdiction_fields.setVerticalSpacing(8)

        self._admin_jurisdiction_name_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_name_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_name_input.setPlaceholderText("City of ... / County of ...")
        self._admin_jurisdiction_name_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        self._admin_jurisdiction_name_input.returnPressed.connect(self._admin_save_jurisdiction)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Name",
                field_widget=self._admin_jurisdiction_name_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_type_combo = QComboBox(jurisdiction_form)
        self._admin_jurisdiction_type_combo.setObjectName("PermitFormCombo")
        self._admin_jurisdiction_type_combo.addItem("City", "city")
        self._admin_jurisdiction_type_combo.addItem("County", "county")
        self._admin_jurisdiction_type_combo.currentIndexChanged.connect(
            self._on_admin_jurisdiction_form_changed
        )
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Type",
                field_widget=self._admin_jurisdiction_type_combo,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_parent_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_parent_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_parent_input.setPlaceholderText("Optional (usually for city jurisdictions)")
        self._admin_jurisdiction_parent_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Parent County (Optional)",
                field_widget=self._admin_jurisdiction_parent_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_portals_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_portals_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_portals_input.setPlaceholderText("comma-separated URLs")
        self._admin_jurisdiction_portals_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Portal URLs",
                field_widget=self._admin_jurisdiction_portals_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_vendor_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_vendor_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_vendor_input.setPlaceholderText("accela, click2gov, other")
        self._admin_jurisdiction_vendor_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Portal Vendor",
                field_widget=self._admin_jurisdiction_vendor_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_notes_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_notes_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_notes_input.setPlaceholderText("Internal notes for this jurisdiction")
        self._admin_jurisdiction_notes_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Notes",
                field_widget=self._admin_jurisdiction_notes_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )
        jurisdiction_fields_host_layout.addLayout(jurisdiction_fields)
        jurisdiction_fields_host_layout.addStretch(1)
        jurisdiction_details_split_row.addWidget(jurisdiction_fields_host, 1)

        attached_panel = QFrame(jurisdiction_form)
        attached_panel.setObjectName("AdminAttachedContactsPane")
        attached_panel_layout = QVBoxLayout(attached_panel)
        attached_panel_layout.setContentsMargins(10, 10, 10, 10)
        attached_panel_layout.setSpacing(8)
        self._admin_jurisdiction_attached_panel = attached_panel

        attached_label = QLabel("Attached Contacts (0)", attached_panel)
        attached_label.setObjectName("AdminSectionTitle")
        attached_panel_layout.addWidget(attached_label, 0)
        self._admin_jurisdiction_attached_label = attached_label

        attached_hint = QLabel(
            "Attach as many contacts as needed. Each card shows every saved bundle for quick lookup.",
            attached_panel,
        )
        attached_hint.setObjectName("AdminSectionHint")
        attached_hint.setWordWrap(True)
        attached_panel_layout.addWidget(attached_hint, 0)

        attached_picker_host = QWidget(attached_panel)
        attached_picker_host.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        attached_picker_row = QHBoxLayout(attached_picker_host)
        attached_picker_row.setContentsMargins(0, 0, 0, 0)
        attached_picker_row.setSpacing(8)
        attached_picker_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        add_attached_contact_button = QPushButton("Add", attached_picker_host)
        add_attached_contact_button.setObjectName("TrackerPanelActionButton")
        add_attached_contact_button.setFixedSize(108, 30)
        add_attached_contact_button.clicked.connect(self._admin_add_jurisdiction_contact)
        attached_picker_row.addWidget(add_attached_contact_button, 0)
        self._admin_jurisdiction_contact_add_button = add_attached_contact_button

        contact_picker_combo = QComboBox(attached_picker_host)
        contact_picker_combo.setObjectName("PermitFormCombo")
        contact_picker_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        contact_picker_combo.addItem("Select contact to attach...", "")
        attached_picker_row.addWidget(contact_picker_combo, 1)
        self._admin_jurisdiction_contact_picker_combo = contact_picker_combo

        attached_picker_row.addStretch(1)
        self._admin_jurisdiction_attached_picker_host = attached_picker_host
        attached_panel_layout.addWidget(attached_picker_host, 0, Qt.AlignmentFlag.AlignLeft)

        attached_contacts_host = QWidget(attached_panel)
        attached_contacts_host.setObjectName("AttachedContactsHost")
        attached_contacts_layout = QVBoxLayout(attached_contacts_host)
        attached_contacts_layout.setContentsMargins(0, 0, 0, 0)
        attached_contacts_layout.setSpacing(6)
        self._admin_jurisdiction_attached_contacts_host = attached_contacts_host
        attached_panel_layout.addWidget(attached_contacts_host, 1)

        jurisdiction_details_split_row.addWidget(attached_panel, 1)
        jurisdiction_form_layout.addLayout(jurisdiction_details_split_row, 1)

        jurisdiction_color_picker = self._build_admin_color_picker_widget(
            parent=jurisdiction_form,
            entity_kind="jurisdiction",
        )
        self._admin_jurisdiction_color_picker_host = jurisdiction_color_picker
        jurisdiction_color_shell = self._build_admin_input_shell(
            label_text="List Color Picker",
            field_widget=jurisdiction_color_picker,
            parent=jurisdiction_form,
            shell_bucket=None,
            field_stretch=0,
            left_align_field=True,
        )
        self._admin_jurisdiction_color_shell = jurisdiction_color_shell
        jurisdiction_form_layout.addWidget(jurisdiction_color_shell, 0, Qt.AlignmentFlag.AlignLeft)

        jurisdiction_right_layout.addStretch(15)
        jurisdiction_scroll.setWidget(jurisdiction_form)
        jurisdiction_right_layout.addWidget(jurisdiction_scroll, 70)
        jurisdiction_right_layout.addStretch(15)
        jurisdiction_layout.addWidget(jurisdiction_right_host, 3)

        tabs.addTab(contact_tab, "Contacts")
        tabs.addTab(jurisdiction_tab, "Jurisdictions")
        templates_tab = self._build_document_templates_view(tabs, as_tab=True)
        self._admin_templates_tab_index = tabs.addTab(templates_tab, "Document Templates")
        return view

    def _build_inline_form_view(self, parent: QWidget) -> tuple[QWidget, QFrame, QVBoxLayout]:
        view = QWidget(parent)
        view.setObjectName("PermitFormView")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = _EdgeLockedScrollArea(view)
        scroll.setObjectName("PermitInlineFormScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        scroll_content = QWidget(scroll)
        scroll_content.setObjectName("PermitInlineFormScrollContent")
        scroll_content_layout = QVBoxLayout(scroll_content)
        scroll_content_layout.setContentsMargins(0, 0, 0, 0)
        scroll_content_layout.setSpacing(0)

        row_host = QWidget(scroll_content)
        row = QHBoxLayout(row_host)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(0)
        row.addStretch(13)

        card = QFrame(row_host)
        card.setObjectName("PermitFormCard")
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        row.addWidget(card, 0)

        row.addStretch(13)
        scroll_content_layout.addWidget(row_host, 1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        self._inline_form_cards.append(card)
        return view, card, card_layout

    def _build_add_property_view(self, parent: QWidget) -> QWidget:
        view, card, card_layout = self._build_inline_form_view(parent)
        self._add_property_card = card

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Add Address", card)
        title.setObjectName("PermitFormTitle")
        header_row.addWidget(title, 0)
        self._add_property_title_label = title

        header_row.addStretch(1)

        dirty_bubble = QLabel("Empty", card)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "empty")
        header_row.addWidget(
            dirty_bubble,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._add_property_dirty_bubble = dirty_bubble

        card_layout.addLayout(header_row, 0)

        subtitle = QLabel(
            "Create a new property record to begin tracking permits and documents.",
            card,
        )
        subtitle.setObjectName("TrackerPanelMeta")
        subtitle.setWordWrap(True)
        card_layout.addWidget(subtitle, 0)
        self._add_property_subtitle_label = subtitle

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        address_label = QLabel("Address", card)
        address_label.setObjectName("InlineFormFieldLabel")
        address_input = QLineEdit(card)
        address_input.setObjectName("PermitFormInput")
        address_input.textChanged.connect(self._on_inline_property_form_changed)
        form.addRow(address_label, address_input)
        self._add_property_address_input = address_input

        parcel_label = QLabel("Parcel ID", card)
        parcel_label.setObjectName("InlineFormFieldLabel")
        parcel_input = QLineEdit(card)
        parcel_input.setObjectName("PermitFormInput")
        parcel_input.textChanged.connect(self._on_inline_property_form_changed)
        form.addRow(parcel_label, parcel_input)
        self._add_property_parcel_input = parcel_input

        jurisdiction_label = QLabel("Jurisdiction", card)
        jurisdiction_label.setObjectName("InlineFormFieldLabel")
        jurisdiction_combo = QComboBox(card)
        jurisdiction_combo.setObjectName("PermitFormCombo")
        jurisdiction_combo.addItem("Unassigned", "")
        jurisdiction_combo.currentIndexChanged.connect(self._on_inline_property_form_changed)
        form.addRow(jurisdiction_label, jurisdiction_combo)
        self._add_property_jurisdiction_combo = jurisdiction_combo

        tags_label = QLabel("Tags", card)
        tags_label.setObjectName("InlineFormFieldLabel")
        tags_input = QLineEdit(card)
        tags_input.setObjectName("PermitFormInput")
        tags_input.setPlaceholderText("comma-separated")
        tags_input.textChanged.connect(self._on_inline_property_form_changed)
        form.addRow(tags_label, tags_input)
        self._add_property_tags_input = tags_input

        notes_label = QLabel("Notes", card)
        notes_label.setObjectName("InlineFormFieldLabel")
        notes_input = QLineEdit(card)
        notes_input.setObjectName("PermitFormInput")
        notes_input.textChanged.connect(self._on_inline_property_form_changed)
        form.addRow(notes_label, notes_input)
        self._add_property_notes_input = notes_input

        card_layout.addLayout(form, 0)

        attached_panel = QFrame(card)
        attached_panel.setObjectName("AdminAttachedContactsPane")
        attached_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        attached_panel_layout = QVBoxLayout(attached_panel)
        attached_panel_layout.setContentsMargins(10, 10, 10, 10)
        attached_panel_layout.setSpacing(8)

        attached_label = QLabel("Attached Contacts (0)", attached_panel)
        attached_label.setObjectName("AdminSectionTitle")
        attached_panel_layout.addWidget(attached_label, 0)
        self._add_property_contacts_label = attached_label

        attached_hint = QLabel(
            "Attach default contacts for this address. New permits can reuse these contacts.",
            attached_panel,
        )
        attached_hint.setObjectName("AdminSectionHint")
        attached_hint.setWordWrap(True)
        attached_panel_layout.addWidget(attached_hint, 0)

        attached_picker_host = QWidget(attached_panel)
        attached_picker_row = QHBoxLayout(attached_picker_host)
        attached_picker_row.setContentsMargins(0, 0, 0, 0)
        attached_picker_row.setSpacing(8)
        attached_picker_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        add_attached_contact_button = QPushButton("Add", attached_picker_host)
        add_attached_contact_button.setObjectName("TrackerPanelActionButton")
        add_attached_contact_button.setFixedSize(108, 30)
        add_attached_contact_button.clicked.connect(self._inline_add_property_contact)
        attached_picker_row.addWidget(add_attached_contact_button, 0)
        self._add_property_contact_add_button = add_attached_contact_button

        contact_picker_combo = QComboBox(attached_picker_host)
        contact_picker_combo.setObjectName("PermitFormCombo")
        contact_picker_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        contact_picker_combo.addItem("Select contact to attach...", "")
        attached_picker_row.addWidget(contact_picker_combo, 1)
        self._add_property_contact_picker_combo = contact_picker_combo

        attached_picker_row.addStretch(1)
        attached_panel_layout.addWidget(attached_picker_host, 0, Qt.AlignmentFlag.AlignLeft)

        attached_contacts_scroll = _EdgeLockedScrollArea(attached_panel)
        attached_contacts_scroll.setObjectName("InlineAttachedContactsScroll")
        attached_contacts_scroll.setWidgetResizable(True)
        attached_contacts_scroll.setFrameShape(QFrame.Shape.NoFrame)
        attached_contacts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        attached_contacts_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        attached_contacts_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        attached_contacts_scroll.setMinimumHeight(170)

        attached_contacts_host = QWidget(attached_contacts_scroll)
        attached_contacts_host.setObjectName("AttachedContactsHost")
        attached_contacts_layout = QVBoxLayout(attached_contacts_host)
        attached_contacts_layout.setContentsMargins(0, 0, 0, 0)
        attached_contacts_layout.setSpacing(6)
        self._add_property_attached_contacts_host = attached_contacts_host
        attached_contacts_scroll.setWidget(attached_contacts_host)
        attached_panel_layout.addWidget(attached_contacts_scroll, 1)
        card_layout.addWidget(attached_panel, 2)

        address_color_picker = self._build_admin_color_picker_widget(
            parent=card,
            entity_kind="property",
        )
        self._add_property_color_picker_host = address_color_picker
        address_color_shell = self._build_admin_input_shell(
            label_text="List Bubble Color",
            field_widget=address_color_picker,
            parent=card,
            field_stretch=1,
            left_align_field=False,
        )
        self._add_property_color_shell = address_color_shell
        card_layout.addWidget(address_color_shell, 0)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(self._close_inline_form_view)
        footer.addWidget(cancel_button, 0)

        save_button = QPushButton("Create Address", card)
        save_button.setObjectName("PermitFormPrimaryButton")
        save_button.clicked.connect(self._save_add_property_from_inline_form)
        footer.addWidget(save_button, 0)
        self._add_property_submit_button = save_button

        card_layout.addLayout(footer, 0)
        return view

    def _build_add_permit_view(self, parent: QWidget) -> QWidget:
        view, card, card_layout = self._build_inline_form_view(parent)
        self._add_permit_card = card

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Add Permit", card)
        title.setObjectName("PermitFormTitle")
        header_row.addWidget(title, 0)
        self._add_permit_title_label = title

        header_row.addStretch(1)

        dirty_bubble = QLabel("Empty", card)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "empty")
        header_row.addWidget(
            dirty_bubble,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._add_permit_dirty_bubble = dirty_bubble

        card_layout.addLayout(header_row, 0)

        context_label = QLabel("Select an address before creating a permit.", card)
        context_label.setObjectName("TrackerPanelMeta")
        context_label.setWordWrap(True)
        self._add_permit_context_label = context_label
        card_layout.addWidget(context_label, 0)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        permit_type_label = QLabel("Permit Type", card)
        permit_type_label.setObjectName("InlineFormFieldLabel")
        permit_type_combo = QComboBox(card)
        permit_type_combo.setObjectName("PermitFormCombo")
        for permit_type, label in _PERMIT_TYPE_OPTIONS:
            if permit_type == "all":
                continue
            permit_type_combo.addItem(label, permit_type)
        permit_type_combo.currentIndexChanged.connect(self._on_add_permit_type_changed)
        form.addRow(permit_type_label, permit_type_combo)
        self._add_permit_type_combo = permit_type_combo

        template_label = QLabel("Checklist Template", card)
        template_label.setObjectName("InlineFormFieldLabel")
        template_combo = QComboBox(card)
        template_combo.setObjectName("PermitFormCombo")
        template_combo.currentIndexChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(template_label, template_combo)
        self._add_permit_template_combo = template_combo

        permit_number_label = QLabel("Permit #", card)
        permit_number_label.setObjectName("InlineFormFieldLabel")
        permit_number_input = QLineEdit(card)
        permit_number_input.setObjectName("PermitFormInput")
        permit_number_input.setPlaceholderText("Portal case number (optional)")
        permit_number_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(permit_number_label, permit_number_input)
        self._add_permit_number_input = permit_number_input

        next_action_label = QLabel("Next Action", card)
        next_action_label.setObjectName("InlineFormFieldLabel")
        next_action_input = QLineEdit(card)
        next_action_input.setObjectName("PermitFormInput")
        next_action_input.setPlaceholderText("What should happen next?")
        next_action_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(next_action_label, next_action_input)
        self._add_permit_next_action_input = next_action_input

        next_action_due_label = QLabel("Next Action Due", card)
        next_action_due_label.setObjectName("InlineFormFieldLabel")
        next_action_due_input = QLineEdit(card)
        next_action_due_input.setObjectName("PermitFormInput")
        next_action_due_input.setPlaceholderText("YYYY-MM-DD")
        next_action_due_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(next_action_due_label, next_action_due_input)
        self._add_permit_next_action_due_input = next_action_due_input

        request_date_label = QLabel("Request Date", card)
        request_date_label.setObjectName("InlineFormFieldLabel")
        request_date_input = QLineEdit(card)
        request_date_input.setObjectName("PermitFormInput")
        request_date_input.setPlaceholderText("YYYY-MM-DD")
        request_date_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(request_date_label, request_date_input)
        self._add_permit_request_date_input = request_date_input

        application_date_label = QLabel("Application Date", card)
        application_date_label.setObjectName("InlineFormFieldLabel")
        application_date_input = QLineEdit(card)
        application_date_input.setObjectName("PermitFormInput")
        application_date_input.setPlaceholderText("YYYY-MM-DD")
        application_date_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(application_date_label, application_date_input)
        self._add_permit_application_date_input = application_date_input

        issued_date_label = QLabel("Issued Date", card)
        issued_date_label.setObjectName("InlineFormFieldLabel")
        issued_date_input = QLineEdit(card)
        issued_date_input.setObjectName("PermitFormInput")
        issued_date_input.setPlaceholderText("YYYY-MM-DD")
        issued_date_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(issued_date_label, issued_date_input)
        self._add_permit_issued_date_input = issued_date_input

        final_date_label = QLabel("Final Date", card)
        final_date_label.setObjectName("InlineFormFieldLabel")
        final_date_input = QLineEdit(card)
        final_date_input.setObjectName("PermitFormInput")
        final_date_input.setPlaceholderText("YYYY-MM-DD")
        final_date_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(final_date_label, final_date_input)
        self._add_permit_final_date_input = final_date_input

        completion_date_label = QLabel("Completion Date", card)
        completion_date_label.setObjectName("InlineFormFieldLabel")
        completion_date_input = QLineEdit(card)
        completion_date_input.setObjectName("PermitFormInput")
        completion_date_input.setPlaceholderText("YYYY-MM-DD")
        completion_date_input.textChanged.connect(self._on_inline_permit_form_changed)
        form.addRow(completion_date_label, completion_date_input)
        self._add_permit_completion_date_input = completion_date_input

        card_layout.addLayout(form, 0)

        attached_panel = QFrame(card)
        attached_panel.setObjectName("AdminAttachedContactsPane")
        attached_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        attached_panel_layout = QVBoxLayout(attached_panel)
        attached_panel_layout.setContentsMargins(10, 10, 10, 10)
        attached_panel_layout.setSpacing(8)

        attached_label = QLabel("Attached Contacts (0)", attached_panel)
        attached_label.setObjectName("AdminSectionTitle")
        attached_panel_layout.addWidget(attached_label, 0)
        self._add_permit_contacts_label = attached_label

        attached_hint = QLabel(
            "Attach contacts specific to this permit (applicant, contractor, owner, etc.).",
            attached_panel,
        )
        attached_hint.setObjectName("AdminSectionHint")
        attached_hint.setWordWrap(True)
        attached_panel_layout.addWidget(attached_hint, 0)

        attached_picker_host = QWidget(attached_panel)
        attached_picker_row = QHBoxLayout(attached_picker_host)
        attached_picker_row.setContentsMargins(0, 0, 0, 0)
        attached_picker_row.setSpacing(8)
        attached_picker_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        add_attached_contact_button = QPushButton("Add", attached_picker_host)
        add_attached_contact_button.setObjectName("TrackerPanelActionButton")
        add_attached_contact_button.setFixedSize(108, 30)
        add_attached_contact_button.clicked.connect(self._inline_add_permit_contact)
        attached_picker_row.addWidget(add_attached_contact_button, 0)
        self._add_permit_contact_add_button = add_attached_contact_button

        contact_picker_combo = QComboBox(attached_picker_host)
        contact_picker_combo.setObjectName("PermitFormCombo")
        contact_picker_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        contact_picker_combo.addItem("Select contact to attach...", "")
        attached_picker_row.addWidget(contact_picker_combo, 1)
        self._add_permit_contact_picker_combo = contact_picker_combo

        attached_picker_row.addStretch(1)
        attached_panel_layout.addWidget(attached_picker_host, 0, Qt.AlignmentFlag.AlignLeft)

        attached_contacts_scroll = _EdgeLockedScrollArea(attached_panel)
        attached_contacts_scroll.setObjectName("InlineAttachedContactsScroll")
        attached_contacts_scroll.setWidgetResizable(True)
        attached_contacts_scroll.setFrameShape(QFrame.Shape.NoFrame)
        attached_contacts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        attached_contacts_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        attached_contacts_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        attached_contacts_scroll.setMinimumHeight(170)

        attached_contacts_host = QWidget(attached_contacts_scroll)
        attached_contacts_host.setObjectName("AttachedContactsHost")
        attached_contacts_layout = QVBoxLayout(attached_contacts_host)
        attached_contacts_layout.setContentsMargins(0, 0, 0, 0)
        attached_contacts_layout.setSpacing(6)
        self._add_permit_attached_contacts_host = attached_contacts_host
        attached_contacts_scroll.setWidget(attached_contacts_host)
        attached_panel_layout.addWidget(attached_contacts_scroll, 1)
        card_layout.addWidget(attached_panel, 2)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", card)
        cancel_button.setObjectName("PermitFormSecondaryButton")
        cancel_button.clicked.connect(self._close_inline_form_view)
        footer.addWidget(cancel_button, 0)

        create_button = QPushButton("Create Permit", card)
        create_button.setObjectName("PermitFormPrimaryButton")
        create_button.clicked.connect(self._save_add_permit_from_inline_form)
        footer.addWidget(create_button, 0)
        self._add_permit_submit_button = create_button

        card_layout.addLayout(footer, 0)
        return view

    def _build_document_templates_view(self, parent: QWidget, *, as_tab: bool = False) -> QWidget:
        view = QWidget(parent)
        view.setObjectName("DocumentTemplatesView")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10 if as_tab else 12)

        if not as_tab:
            header = QFrame(view)
            header.setObjectName("TrackerPanel")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(14, 12, 14, 12)
            header_layout.setSpacing(8)

            title = QLabel("Document Templates Directory", header)
            title.setObjectName("TrackerPanelTitle")
            header_layout.addWidget(title, 0)

            hint = QLabel(
                "Create reusable checklist templates and set defaults per permit type.",
                header,
            )
            hint.setObjectName("TrackerPanelMeta")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(hint, 1)

            back_button = QPushButton("Back to Tracker", header)
            back_button.setObjectName("TrackerPanelActionButton")
            back_button.setProperty("adminBackButton", "true")
            back_button.setMinimumHeight(34)
            back_button.setMinimumWidth(150)
            back_button.clicked.connect(self._close_document_templates_view)
            header_layout.addWidget(back_button, 0)
            layout.addWidget(header, 0)

        if as_tab:
            content = QWidget(view)
            content_layout = QHBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(14)
        else:
            content = QFrame(view)
            content.setObjectName("TrackerPanel")
            content_layout = QHBoxLayout(content)
            content_layout.setContentsMargins(12, 10, 12, 14)
            content_layout.setSpacing(14)
        layout.addWidget(content, 1)

        left_card = QFrame(content)
        left_card.setObjectName("AdminListPane")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        left_title = QLabel("Document Templates Directory", left_card)
        left_title.setObjectName("AdminListTitleChip")
        left_layout.addWidget(left_title, 0)

        left_hint = QLabel(
            "Pick a template to edit or add a new one.",
            left_card,
        )
        left_hint.setObjectName("AdminSectionHint")
        left_hint.setWordWrap(True)
        left_layout.addWidget(left_hint, 0)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)

        search_input = QLineEdit(left_card)
        search_input.setObjectName("TrackerPanelSearch")
        search_input.setPlaceholderText("Search templates (name, type, folder)")
        search_input.setClearButtonEnabled(True)
        search_input.textChanged.connect(self._on_templates_search_changed)
        search_input.returnPressed.connect(self._on_templates_search_changed)
        search_row.addWidget(search_input, 1)
        self._templates_search_input = search_input

        left_layout.addLayout(search_row, 0)

        add_button = QPushButton("Add New Template", left_card)
        add_button.setObjectName("TrackerPanelActionButton")
        add_button.setProperty("adminPrimaryCta", "true")
        add_button.setMinimumHeight(34)
        add_button.clicked.connect(
            lambda _checked=False: self._template_new(require_confirm=True, action_label="Add New Template")
        )
        left_layout.addWidget(add_button, 0)

        templates_count_label = QLabel("0 templates", left_card)
        templates_count_label.setObjectName("TrackerPanelMeta")
        left_layout.addWidget(templates_count_label, 0)
        self._templates_count_label = templates_count_label

        list_host = QWidget(left_card)
        list_stack = QStackedLayout(list_host)
        list_stack.setContentsMargins(0, 0, 0, 0)
        list_stack.setSpacing(0)

        list_widget = QListWidget(list_host)
        list_widget.setObjectName("TrackerPanelList")
        list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        list_widget.setWordWrap(True)
        list_widget.setSpacing(6)
        list_widget.itemSelectionChanged.connect(self._on_template_selected)
        list_stack.addWidget(list_widget)
        self._templates_list_widget = list_widget

        empty_label = QLabel("No templates yet. Add a new template to get started.", list_host)
        empty_label.setObjectName("AdminListEmptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setWordWrap(True)
        list_stack.addWidget(empty_label)
        list_stack.setCurrentWidget(empty_label)
        self._templates_list_stack = list_stack
        self._templates_empty_label = empty_label
        left_layout.addWidget(list_host, 1)

        content_layout.addWidget(left_card, 1)

        right_host = QWidget(content)
        right_layout = QHBoxLayout(right_host)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        template_scroll = _EdgeLockedScrollArea(right_host)
        template_scroll.setObjectName("AdminEditorScroll")
        template_scroll.setWidgetResizable(True)
        template_scroll.setFrameShape(QFrame.Shape.NoFrame)
        template_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        template_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        template_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        template_scroll.viewport().setAutoFillBackground(False)
        template_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        form = QFrame(template_scroll)
        form.setObjectName("PermitFormCard")
        form.setProperty("adminForm", "true")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(14, 12, 14, 14)
        form_layout.setSpacing(10)
        self._template_form_widget = form

        header_bar = QFrame(form)
        header_bar.setObjectName("AdminHeaderBar")
        header_row = QHBoxLayout(header_bar)
        header_row.setContentsMargins(10, 8, 10, 8)
        header_row.setSpacing(8)

        mode_label = QLabel("Adding Template: New Template", form)
        mode_label.setObjectName("AdminModeTitle")
        header_row.addWidget(mode_label, 0)
        self._template_mode_label = mode_label

        header_row.addStretch(1)

        save_button = QPushButton("Create Template", form)
        save_button.setObjectName("TrackerPanelActionButton")
        save_button.setProperty("adminPrimaryCta", "true")
        save_button.setMinimumHeight(32)
        save_button.clicked.connect(self._save_document_template)
        header_row.addWidget(save_button, 0)
        self._template_save_button = save_button

        default_button = QPushButton("Set as Default", form)
        default_button.setObjectName("TrackerPanelActionButton")
        default_button.setMinimumHeight(32)
        default_button.clicked.connect(self._set_template_as_default)
        header_row.addWidget(default_button, 0)
        self._template_set_default_button = default_button

        delete_button = QPushButton("Delete Template", form)
        delete_button.setObjectName("PermitFormDangerButton")
        delete_button.setMinimumHeight(32)
        delete_button.clicked.connect(self._delete_document_template)
        header_row.addWidget(delete_button, 0)
        self._template_delete_button = delete_button

        form_layout.addWidget(header_bar, 0)

        details_row = QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(8)

        details_title = QLabel("Template Details", form)
        details_title.setObjectName("AdminSectionTitle")
        details_row.addWidget(details_title, 0)

        dirty_bubble = QLabel("Empty", form)
        dirty_bubble.setObjectName("AdminDirtyBubble")
        dirty_bubble.setProperty("dirtyState", "empty")
        dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dirty_bubble.setMinimumWidth(92)
        dirty_bubble.setMinimumHeight(24)
        details_row.addWidget(dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._template_dirty_bubble = dirty_bubble

        details_row.addStretch(1)
        form_layout.addLayout(details_row, 0)

        name_input = QLineEdit(form)
        name_input.setObjectName("PermitFormInput")
        name_input.setPlaceholderText("Template name")
        name_input.textChanged.connect(self._on_template_form_changed)
        self._template_name_input = name_input
        form_layout.addWidget(
            self._build_admin_input_shell(
                label_text="Name",
                field_widget=name_input,
                parent=form,
                shell_bucket=self._template_field_shells,
                left_align_field=False,
            ),
            0,
        )

        type_combo = QComboBox(form)
        type_combo.setObjectName("PermitFormCombo")
        type_combo.addItem("Building", "building")
        type_combo.addItem("Demolition", "demolition")
        type_combo.addItem("Remodeling", "remodeling")
        type_combo.currentIndexChanged.connect(self._on_template_type_changed)
        self._template_type_combo = type_combo
        form_layout.addWidget(
            self._build_admin_input_shell(
                label_text="Permit Type",
                field_widget=type_combo,
                parent=form,
                shell_bucket=self._template_field_shells,
                left_align_field=False,
            ),
            0,
        )

        notes_input = QLineEdit(form)
        notes_input.setObjectName("PermitFormInput")
        notes_input.setPlaceholderText("Notes (optional)")
        notes_input.textChanged.connect(self._on_template_form_changed)
        self._template_notes_input = notes_input
        form_layout.addWidget(
            self._build_admin_input_shell(
                label_text="Notes",
                field_widget=notes_input,
                parent=form,
                shell_bucket=self._template_field_shells,
                left_align_field=False,
            ),
            0,
        )

        slots_title = QLabel("Template Folders", form)
        slots_title.setObjectName("AdminSectionTitle")
        form_layout.addWidget(slots_title, 0)

        slots_hint = QLabel(
            "Each slot becomes a permit folder/checklist row.",
            form,
        )
        slots_hint.setObjectName("AdminSectionHint")
        slots_hint.setWordWrap(True)
        form_layout.addWidget(slots_hint, 0)

        slot_label_input = QLineEdit(form)
        slot_label_input.setObjectName("PermitFormInput")
        slot_label_input.setPlaceholderText("Folder name (e.g. Application)")
        slot_label_input.textChanged.connect(self._on_template_form_changed)
        self._template_slot_label_input = slot_label_input
        form_layout.addWidget(
            self._build_admin_input_shell(
                label_text="Folder Name",
                field_widget=slot_label_input,
                parent=form,
                shell_bucket=self._template_field_shells,
            ),
            0,
        )

        slot_required_combo = QComboBox(form)
        slot_required_combo.setObjectName("PermitFormCombo")
        slot_required_combo.addItem("Required", True)
        slot_required_combo.addItem("Optional", False)
        slot_required_combo.currentIndexChanged.connect(self._on_template_form_changed)
        self._template_slot_required_combo = slot_required_combo
        form_layout.addWidget(
            self._build_admin_input_shell(
                label_text="Requirement",
                field_widget=slot_required_combo,
                parent=form,
                shell_bucket=self._template_field_shells,
            ),
            0,
        )

        slot_actions = QHBoxLayout()
        slot_actions.setContentsMargins(0, 0, 0, 0)
        slot_actions.setSpacing(8)

        add_slot_button = QPushButton("Add Slot", form)
        add_slot_button.setObjectName("TrackerPanelActionButton")
        add_slot_button.clicked.connect(self._add_template_slot)
        slot_actions.addWidget(add_slot_button, 0)
        self._template_slot_add_button = add_slot_button

        update_slot_button = QPushButton("Update Slot", form)
        update_slot_button.setObjectName("TrackerPanelActionButton")
        update_slot_button.clicked.connect(self._update_template_slot)
        slot_actions.addWidget(update_slot_button, 0)
        self._template_slot_update_button = update_slot_button

        remove_slot_button = QPushButton("Remove Slot", form)
        remove_slot_button.setObjectName("PermitFormDangerButton")
        remove_slot_button.clicked.connect(self._remove_template_slot)
        slot_actions.addWidget(remove_slot_button, 0)
        self._template_slot_remove_button = remove_slot_button

        slot_actions.addStretch(1)
        form_layout.addLayout(slot_actions, 0)

        slots_list = QListWidget(form)
        slots_list.setObjectName("TrackerPanelList")
        slots_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        slots_list.itemSelectionChanged.connect(self._on_template_slot_selected)
        slots_list.setMinimumHeight(190)
        self._template_slots_list_widget = slots_list
        form_layout.addWidget(slots_list, 1)

        right_layout.addStretch(15)
        template_scroll.setWidget(form)
        right_layout.addWidget(template_scroll, 70)
        right_layout.addStretch(15)
        content_layout.addWidget(right_host, 3)
        content_layout.setStretch(0, 1)
        content_layout.setStretch(1, 3)
        self._template_new(require_confirm=False)
        return view

    def _template_by_id(self, template_id: str) -> DocumentChecklistTemplate | None:
        target = str(template_id or "").strip()
        if not target:
            return None
        for record in self._document_templates:
            if record.template_id == target:
                return record
        return None

    def _is_builtin_template_id(self, template_id: str) -> bool:
        target = str(template_id or "").strip()
        return target in {_TEMPLATE_BUILTIN_BUILDING_ID, _TEMPLATE_BUILTIN_DEMOLITION_ID}

    def _builtin_template_by_id(self, template_id: str) -> DocumentChecklistTemplate | None:
        target = str(template_id or "").strip()
        if target == _TEMPLATE_BUILTIN_BUILDING_ID:
            return DocumentChecklistTemplate(
                template_id=target,
                name="Built-in Default: Building / Remodeling",
                permit_type="building",
                slots=build_default_document_slots("building"),
                notes="View-only built-in template used by Building and Remodeling permits.",
            )
        if target == _TEMPLATE_BUILTIN_DEMOLITION_ID:
            return DocumentChecklistTemplate(
                template_id=target,
                name="Built-in Default: Demolition",
                permit_type="demolition",
                slots=build_default_document_slots("demolition"),
                notes="View-only built-in template used by Demolition permits.",
            )
        return None

    def _resolve_template_editor_record(
        self,
        template_id: str,
    ) -> tuple[DocumentChecklistTemplate | None, bool]:
        target = str(template_id or "").strip()
        if not target:
            return (None, False)
        record = self._template_by_id(target)
        if record is not None:
            return (record, False)
        builtin = self._builtin_template_by_id(target)
        if builtin is not None:
            return (builtin, True)
        return (None, False)

    def _set_template_form_read_only(self, read_only: bool) -> None:
        self._template_form_read_only = bool(read_only)
        editable = not self._template_form_read_only
        for widget in (
            self._template_name_input,
            self._template_type_combo,
            self._template_notes_input,
            self._template_slot_label_input,
            self._template_slot_required_combo,
        ):
            if widget is not None:
                widget.setEnabled(editable)
        self._refresh_template_slot_editor_state()

    def _template_slots_snapshot(
        self,
        slots: Sequence[PermitDocumentSlot],
    ) -> tuple[tuple[str, str, bool, str], ...]:
        rows: list[tuple[str, str, bool, str]] = []
        for slot in slots:
            slot_id = normalize_slot_id(slot.slot_id) or normalize_slot_id(slot.label)
            if not slot_id:
                continue
            rows.append(
                (
                    slot_id,
                    str(slot.label or "").strip(),
                    bool(slot.required),
                    str(slot.notes or "").strip(),
                )
            )
        return tuple(rows)

    def _template_form_snapshot(self) -> tuple[object, ...]:
        name = self._template_name_input.text().strip() if self._template_name_input is not None else ""
        permit_type = (
            normalize_permit_type(self._template_type_combo.currentData())
            if self._template_type_combo is not None
            else "building"
        )
        notes = self._template_notes_input.text().strip() if self._template_notes_input is not None else ""
        slot_rows = self._template_slots_snapshot(self._template_slot_rows)
        return (name, permit_type, notes, slot_rows)

    def _template_form_is_empty(self) -> bool:
        snapshot = self._template_form_snapshot()
        if len(snapshot) < 4:
            return False
        name = str(snapshot[0] or "").strip()
        permit_type = normalize_permit_type(snapshot[1] if len(snapshot) >= 2 else "building")
        notes = str(snapshot[2] or "").strip()
        slot_rows = tuple(snapshot[3]) if isinstance(snapshot[3], tuple) else tuple()
        default_slot_rows = self._template_slots_snapshot(
            build_default_document_slots(permit_type)
        )
        has_non_default_slots = bool(slot_rows and slot_rows != default_slot_rows)
        return not any((name, notes, has_non_default_slots))

    def _template_dirty_bubble_state(self) -> str:
        if self._template_dirty:
            return "dirty"
        if not self._template_selected_id and self._template_form_is_empty():
            return "empty"
        return "clean"

    def _set_template_dirty(self, dirty: bool) -> None:
        self._template_dirty = bool(dirty)
        self._set_admin_dirty_bubble_state(
            self._template_dirty_bubble,
            state=self._template_dirty_bubble_state(),
        )

    def _rebase_template_dirty_tracking(self) -> None:
        self._template_baseline_snapshot = self._template_form_snapshot()
        self._set_template_dirty(False)

    def _sync_template_dirty_state(self) -> None:
        if self._template_form_loading:
            return
        if not self._template_baseline_snapshot:
            self._rebase_template_dirty_tracking()
            return
        self._set_template_dirty(self._template_form_snapshot() != self._template_baseline_snapshot)

    def _on_template_form_changed(self, *_args: object) -> None:
        self._sync_template_dirty_state()

    def _on_template_type_changed(self, *_args: object) -> None:
        self._update_template_default_label()
        self._sync_template_dirty_state()

    def _confirm_discard_template_changes(self, *, action_label: str) -> bool:
        if not self._template_dirty:
            return True
        return self._confirm_dialog(
            "Unsaved Template Changes",
            (
                "You have unsaved template changes. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _template_new(
        self,
        *,
        require_confirm: bool = True,
        action_label: str = "Add New Template",
    ) -> None:
        if require_confirm and not self._confirm_discard_template_changes(action_label=action_label):
            return
        self._template_form_loading = True
        try:
            self._set_template_form_read_only(False)
            self._template_selected_id = ""
            self._template_slot_edit_index = -1
            if self._template_name_input is not None:
                self._template_name_input.clear()
                self._template_name_input.setFocus()
            if self._template_type_combo is not None:
                default_type = normalize_permit_type(self._active_permit_type_filter)
                if default_type == "all":
                    default_type = "building"
                desired_index = self._template_type_combo.findData(default_type)
                if desired_index < 0:
                    desired_index = 0
                self._template_type_combo.setCurrentIndex(desired_index)
            if self._template_notes_input is not None:
                self._template_notes_input.clear()
            permit_type = (
                normalize_permit_type(self._template_type_combo.currentData())
                if self._template_type_combo is not None
                else "building"
            )
            self._template_slot_rows = build_default_document_slots(permit_type)
            self._refresh_template_slots_list(select_slot_id="")
            self._reset_template_slot_editor()
            self._refresh_templates_list(select_id="")
            if self._template_mode_label is not None:
                self._template_mode_label.setText("Adding Template: New Template")
            if self._template_save_button is not None:
                self._template_save_button.setText("Create Template")
                self._template_save_button.setEnabled(True)
            if self._template_delete_button is not None:
                self._template_delete_button.setText("Delete Template")
                self._template_delete_button.setEnabled(False)
            if self._template_set_default_button is not None:
                self._template_set_default_button.setText("Set as Default")
                self._template_set_default_button.setEnabled(False)
            self._update_template_default_label()
        finally:
            self._template_form_loading = False
        self._rebase_template_dirty_tracking()

    def _refresh_templates_list(self, *, select_id: str = "") -> None:
        widget = self._templates_list_widget
        list_stack = self._templates_list_stack
        empty_label = self._templates_empty_label
        if widget is None:
            return

        search = self._current_search(self._templates_search_input)
        selected_id = str(select_id or self._template_selected_id).strip()
        widget.blockSignals(True)
        widget.clear()
        shown_templates = 0

        builtin_rows: tuple[tuple[str, str, str, str, list[PermitDocumentSlot]], ...] = (
            (
                _TEMPLATE_BUILTIN_BUILDING_ID,
                "Built-in Default: Building / Remodeling",
                "Building + Remodeling",
                "building",
                build_default_document_slots("building"),
            ),
            (
                _TEMPLATE_BUILTIN_DEMOLITION_ID,
                "Built-in Default: Demolition",
                "Demolition",
                "demolition",
                build_default_document_slots("demolition"),
            ),
        )
        for template_id, title, subtitle, permit_type, slots in builtin_rows:
            if search:
                slot_labels = ", ".join(slot.label for slot in slots)
                haystack = " | ".join(
                    part.casefold()
                    for part in (
                        title,
                        subtitle,
                        _permit_type_label(permit_type),
                        "built in",
                        "view only",
                        slot_labels,
                    )
                    if str(part or "").strip()
                )
                if search not in haystack:
                    continue
            slot_count = len(slots)
            card = self._build_admin_entity_card(
                title=title,
                title_field="address",
                subtitle=f"{subtitle} • View Only",
                subtitle_field="parcel",
                meta=f"{slot_count} slot(s)",
                meta_field="size",
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, template_id)
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            widget.addItem(item)
            widget.setItemWidget(item, card)
            shown_templates += 1

        visible = sorted(
            self._document_templates,
            key=lambda row: (normalize_permit_type(row.permit_type), row.name.casefold(), row.template_id),
        )
        for record in visible:
            if search:
                slot_labels = ", ".join(slot.label for slot in record.slots)
                haystack = " | ".join(
                    part.casefold()
                    for part in (
                        record.name,
                        _permit_type_label(record.permit_type),
                        record.notes,
                        slot_labels,
                    )
                    if str(part or "").strip()
                )
                if search not in haystack:
                    continue
            slot_count = len(record.slots)
            default_id = str(self._active_document_template_ids.get(normalize_permit_type(record.permit_type), "")).strip()
            is_default = record.template_id == default_id
            subtitle = f"{_permit_type_label(record.permit_type)}{' • Default' if is_default else ''}"
            card = self._build_admin_entity_card(
                title=record.name or "(Unnamed Template)",
                title_field="address",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=f"{slot_count} slot(s)",
                meta_field="size",
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, record.template_id)
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            widget.addItem(item)
            widget.setItemWidget(item, card)
            shown_templates += 1

        total_templates = len(builtin_rows) + len(self._document_templates)
        if self._templates_count_label is not None:
            noun = "template" if total_templates == 1 else "templates"
            if search:
                self._templates_count_label.setText(f"{shown_templates} of {total_templates} {noun}")
            else:
                self._templates_count_label.setText(f"{total_templates} {noun}")

        if list_stack is not None and empty_label is not None:
            if shown_templates > 0:
                list_stack.setCurrentWidget(widget)
            else:
                if search:
                    empty_label.setText(
                        "No templates match this search.\nTry another term or clear the search."
                    )
                else:
                    empty_label.setText(
                        "No templates available.\nUse Add New Template to get started."
                    )
                list_stack.setCurrentWidget(empty_label)

        target_id = selected_id
        if not target_id and self._template_selected_id:
            target_id = self._template_selected_id
        selected_item_found = False
        if target_id:
            for index in range(widget.count()):
                item = widget.item(index)
                if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target_id:
                    continue
                widget.setCurrentItem(item)
                selected_item_found = True
                break
        widget.blockSignals(False)
        if target_id and not selected_item_found and search:
            self._set_admin_list_card_selection(widget)
            return
        self._set_admin_list_card_selection(widget)

    def _refresh_template_slot_editor_state(self) -> None:
        if self._template_form_read_only:
            if self._template_slot_update_button is not None:
                self._template_slot_update_button.setEnabled(False)
            if self._template_slot_remove_button is not None:
                self._template_slot_remove_button.setEnabled(False)
            if self._template_slot_add_button is not None:
                self._template_slot_add_button.setEnabled(False)
            return
        editing = self._template_slot_edit_index >= 0
        if self._template_slot_update_button is not None:
            self._template_slot_update_button.setEnabled(editing)
        if self._template_slot_remove_button is not None:
            self._template_slot_remove_button.setEnabled(editing)
        if self._template_slot_add_button is not None:
            self._template_slot_add_button.setEnabled(True)

    def _refresh_template_slots_list(self, *, select_slot_id: str = "") -> None:
        widget = self._template_slots_list_widget
        if widget is None:
            return
        selected_id = str(select_slot_id or "").strip()
        widget.blockSignals(True)
        widget.clear()
        for index, slot in enumerate(self._template_slot_rows):
            slot_id = normalize_slot_id(slot.slot_id) or normalize_slot_id(slot.label)
            card = DocumentChecklistSlotCard(
                slot_label=slot.label or slot_id,
                slot_id=slot_id,
                required=bool(slot.required),
                status="missing",
                file_count=0,
                parent=widget,
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, slot_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, index)
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            widget.addItem(item)
            widget.setItemWidget(item, card)
            if selected_id and slot_id == selected_id:
                widget.setCurrentItem(item)
        widget.blockSignals(False)
        self._set_admin_list_card_selection(widget)
        self._refresh_template_slot_editor_state()

    def _reset_template_slot_editor(self) -> None:
        self._template_slot_edit_index = -1
        if self._template_slots_list_widget is not None:
            self._template_slots_list_widget.blockSignals(True)
            self._template_slots_list_widget.clearSelection()
            self._template_slots_list_widget.setCurrentRow(-1)
            self._template_slots_list_widget.blockSignals(False)
            self._set_admin_list_card_selection(self._template_slots_list_widget)
        if self._template_slot_label_input is not None:
            self._template_slot_label_input.clear()
        if self._template_slot_required_combo is not None:
            self._template_slot_required_combo.setCurrentIndex(0)
        self._refresh_template_slot_editor_state()

    def _template_slot_editor_value(self) -> PermitDocumentSlot | None:
        label = self._template_slot_label_input.text().strip() if self._template_slot_label_input is not None else ""
        slot_id = normalize_slot_id(label)
        if not label or not slot_id:
            return None
        required = (
            bool(self._template_slot_required_combo.currentData())
            if self._template_slot_required_combo is not None
            else True
        )
        return PermitDocumentSlot(
            slot_id=slot_id,
            label=label,
            required=required,
            status="missing",
            folder_id=slot_id,
            notes="",
        )

    def _add_template_slot(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are view-only. Create a new template to customize slots.",
            )
            return
        slot = self._template_slot_editor_value()
        if slot is None:
            self._show_warning_dialog("Missing Folder Name", "Folder name is required.")
            return
        for existing in self._template_slot_rows:
            if normalize_slot_id(existing.slot_id) == slot.slot_id:
                self._show_warning_dialog(
                    "Duplicate Folder",
                    "A folder with this name already exists in this template.",
                )
                return
        self._template_slot_rows.append(slot)
        self._refresh_template_slots_list(select_slot_id=slot.slot_id)
        self._reset_template_slot_editor()
        self._sync_template_dirty_state()

    def _update_template_slot(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are view-only. Create a new template to customize slots.",
            )
            return
        index = int(self._template_slot_edit_index)
        if index < 0 or index >= len(self._template_slot_rows):
            return
        slot = self._template_slot_editor_value()
        if slot is None:
            self._show_warning_dialog("Missing Folder Name", "Folder name is required.")
            return
        for row_index, existing in enumerate(self._template_slot_rows):
            if row_index == index:
                continue
            if normalize_slot_id(existing.slot_id) == slot.slot_id:
                self._show_warning_dialog(
                    "Duplicate Folder",
                    "A folder with this name already exists in this template.",
                )
                return
        self._template_slot_rows[index] = slot
        self._refresh_template_slots_list(select_slot_id=slot.slot_id)
        self._reset_template_slot_editor()
        self._sync_template_dirty_state()

    def _remove_template_slot(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are view-only. Create a new template to customize slots.",
            )
            return
        index = int(self._template_slot_edit_index)
        if index < 0 or index >= len(self._template_slot_rows):
            return
        slot = self._template_slot_rows[index]
        confirmed = self._confirm_dialog(
            "Remove Slot",
            f"Remove slot '{slot.label or slot.slot_id}' from this template?",
            confirm_text="Remove",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        del self._template_slot_rows[index]
        self._refresh_template_slots_list(select_slot_id="")
        self._reset_template_slot_editor()
        self._sync_template_dirty_state()

    def _on_template_slot_selected(self) -> None:
        widget = self._template_slots_list_widget
        self._set_admin_list_card_selection(widget)
        item = widget.currentItem() if widget is not None else None
        index = int(item.data(Qt.ItemDataRole.UserRole + 1)) if item is not None else -1
        if index < 0 or index >= len(self._template_slot_rows):
            self._template_slot_edit_index = -1
            self._refresh_template_slot_editor_state()
            return
        slot = self._template_slot_rows[index]
        self._template_slot_edit_index = index
        if self._template_slot_label_input is not None:
            self._template_slot_label_input.setText(slot.label)
        if self._template_slot_required_combo is not None:
            desired_index = self._template_slot_required_combo.findData(bool(slot.required))
            self._template_slot_required_combo.setCurrentIndex(desired_index if desired_index >= 0 else 0)
        self._refresh_template_slot_editor_state()
        self._sync_template_dirty_state()

    def _on_template_selected(self) -> None:
        if self._template_selection_guard:
            return
        widget = self._templates_list_widget
        self._set_admin_list_card_selection(widget)
        item = widget.currentItem() if widget is not None else None
        template_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
        previous_id = str(self._template_selected_id or "").strip()
        if template_id == previous_id:
            return
        if not self._confirm_discard_template_changes(action_label="Switch Template"):
            if widget is None:
                return
            self._template_selection_guard = True
            try:
                widget.blockSignals(True)
                for index in range(widget.count()):
                    row_item = widget.item(index)
                    row_id = str(row_item.data(Qt.ItemDataRole.UserRole) or "").strip()
                    if row_id == previous_id:
                        widget.setCurrentItem(row_item)
                        break
                widget.blockSignals(False)
                self._set_admin_list_card_selection(widget)
            finally:
                self._template_selection_guard = False
            return
        template, read_only = self._resolve_template_editor_record(template_id)
        self._apply_template_to_form(template, read_only=read_only)

    def _apply_template_to_form(
        self,
        template: DocumentChecklistTemplate | None,
        *,
        read_only: bool = False,
    ) -> None:
        self._template_form_loading = True
        try:
            self._template_slot_edit_index = -1
            self._template_selected_id = template.template_id if template is not None else ""
            if self._template_name_input is not None:
                self._template_name_input.setText(template.name if template is not None else "")
            if self._template_type_combo is not None:
                desired_type = normalize_permit_type(template.permit_type) if template is not None else "building"
                desired_index = self._template_type_combo.findData(desired_type)
                self._template_type_combo.setCurrentIndex(desired_index if desired_index >= 0 else 0)
            if self._template_notes_input is not None:
                self._template_notes_input.setText(template.notes if template is not None else "")
            if template is not None:
                self._template_slot_rows = [
                    PermitDocumentSlot(
                        slot_id=normalize_slot_id(row.slot_id) or row.slot_id,
                        label=row.label,
                        required=bool(row.required),
                        status="missing",
                        folder_id=normalize_slot_id(row.folder_id) or row.folder_id or row.slot_id,
                        notes=row.notes,
                    )
                    for row in template.slots
                ]
            else:
                permit_type = (
                    normalize_permit_type(self._template_type_combo.currentData())
                    if self._template_type_combo is not None
                    else "building"
                )
                self._template_slot_rows = build_default_document_slots(permit_type)
            self._refresh_template_slots_list(select_slot_id="")
            self._reset_template_slot_editor()
            if self._template_mode_label is not None:
                if template is None:
                    self._template_mode_label.setText("Adding Template: New Template")
                elif read_only:
                    template_name = template.name.strip() or "(unnamed)"
                    self._template_mode_label.setText(f"Editing Template: {template_name} (View Only)")
                else:
                    template_name = template.name.strip() or "(unnamed)"
                    self._template_mode_label.setText(f"Editing Template: {template_name}")
            if self._template_save_button is not None:
                self._template_save_button.setText("Update Template" if template is not None else "Create Template")
                self._template_save_button.setEnabled(not read_only)
            if self._template_delete_button is not None:
                self._template_delete_button.setText("Delete Template")
                self._template_delete_button.setEnabled(template is not None and not read_only)
            if self._template_set_default_button is not None:
                self._template_set_default_button.setText("Set as Default")
                self._template_set_default_button.setEnabled(template is not None and not read_only)
            self._set_template_form_read_only(read_only)
            self._update_template_default_label()
        finally:
            self._template_form_loading = False
        self._rebase_template_dirty_tracking()

    def _save_document_template(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are view-only. Add a new template to make changes.",
            )
            return
        name = self._template_name_input.text().strip() if self._template_name_input is not None else ""
        if not name:
            self._show_warning_dialog("Missing Name", "Please provide a template name.")
            return
        permit_type = (
            normalize_permit_type(self._template_type_combo.currentData())
            if self._template_type_combo is not None
            else "building"
        )
        slots = build_document_slots_from_template(
            DocumentChecklistTemplate(
                template_id=self._template_selected_id or uuid4().hex,
                name=name,
                permit_type=permit_type,
                slots=list(self._template_slot_rows),
                notes=self._template_notes_input.text().strip() if self._template_notes_input is not None else "",
            ),
            permit_type=permit_type,
        )
        notes = self._template_notes_input.text().strip() if self._template_notes_input is not None else ""

        existing = self._template_by_id(self._template_selected_id)
        if existing is None:
            record = DocumentChecklistTemplate(
                template_id=uuid4().hex,
                name=name,
                permit_type=permit_type,
                slots=slots,
                notes=notes,
            )
            self._document_templates.append(record)
        else:
            existing.name = name
            existing.permit_type = permit_type
            existing.slots = slots
            existing.notes = notes
            record = existing

        self._template_selected_id = record.template_id
        self._prune_active_document_template_ids()
        self._persist_tracker_data()
        self._refresh_templates_list(select_id=record.template_id)
        self._apply_template_to_form(record)
        self._refresh_add_permit_template_options()

    def _delete_document_template(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are view-only and cannot be deleted.",
            )
            return
        record = self._template_by_id(self._template_selected_id)
        if record is None:
            return
        confirmed = self._confirm_dialog(
            "Delete Template",
            f"Delete template '{record.name or '(unnamed)'}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        self._document_templates = [
            row for row in self._document_templates if row.template_id != record.template_id
        ]
        self._prune_active_document_template_ids()
        self._persist_tracker_data()
        self._template_new(require_confirm=False)
        self._refresh_add_permit_template_options()

    def _set_template_as_default(self) -> None:
        if self._template_form_read_only:
            self._show_info_dialog(
                "Built-in Template",
                "Built-in templates are always available and do not need to be set as default.",
            )
            return
        record = self._template_by_id(self._template_selected_id)
        if record is None:
            return
        template_name = record.name.strip() or "(unnamed)"
        target_permit_type = self._pick_template_default_target_type(template_name=template_name)
        if not target_permit_type:
            return
        self._active_document_template_ids[target_permit_type] = record.template_id
        self._prune_active_document_template_ids()
        self._persist_tracker_data()
        self._refresh_templates_list(select_id=record.template_id)
        self._update_template_default_label()
        self._refresh_add_permit_template_options()

    def _update_template_default_label(self) -> None:
        button = self._template_set_default_button
        if button is None:
            return
        permit_type = (
            normalize_permit_type(self._template_type_combo.currentData())
            if self._template_type_combo is not None
            else "building"
        )
        default_id = str(self._active_document_template_ids.get(permit_type) or "").strip()
        default_template = self._template_by_id(default_id)
        if default_template is None:
            default_text = "Built-in"
        else:
            default_text = default_template.name.strip() or "(unnamed)"
        selected_template_id = str(self._template_selected_id or "").strip()
        selected_default_types = [
            _permit_type_label(raw_type)
            for raw_type, raw_template_id in self._active_document_template_ids.items()
            if str(raw_template_id or "").strip() == selected_template_id
        ]
        tooltip = f"Current default for {_permit_type_label(permit_type)}: {default_text}"
        if selected_default_types:
            tooltip = (
                f"{tooltip}\n\nSelected template is default for: "
                f"{', '.join(sorted(selected_default_types))}"
            )
        button.setToolTip(tooltip)

        if selected_default_types:
            button.setText("Default Selected")
            return
        button.setText("Set as Default")

    def _prune_active_document_template_ids(self) -> None:
        valid_ids = {record.template_id for record in self._document_templates}
        normalized: dict[str, str] = {}
        for raw_type, raw_template_id in list(self._active_document_template_ids.items()):
            permit_type = normalize_permit_type(raw_type)
            template_id = str(raw_template_id or "").strip()
            if not template_id:
                continue
            if template_id not in valid_ids:
                continue
            normalized[permit_type] = template_id
        self._active_document_template_ids = normalized

    def _refresh_add_permit_template_options(self, *, selected_template_id: str = "") -> None:
        combo = self._add_permit_template_combo
        if combo is None:
            return
        permit_type = (
            normalize_permit_type(self._add_permit_type_combo.currentData())
            if self._add_permit_type_combo is not None
            else "building"
        )
        current_id = str(selected_template_id or combo.currentData() or "").strip()
        default_id = str(self._active_document_template_ids.get(permit_type) or "").strip()

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Built-in Default", _TEMPLATE_DEFAULT_SENTINEL)
        for template in sorted(
            (row for row in self._document_templates if normalize_permit_type(row.permit_type) == permit_type),
            key=lambda row: (row.name.casefold(), row.template_id),
        ):
            combo.addItem(template.name or "(Unnamed Template)", template.template_id)

        desired_id = current_id or default_id or _TEMPLATE_DEFAULT_SENTINEL
        desired_index = combo.findData(desired_id)
        if desired_index < 0:
            desired_index = combo.findData(_TEMPLATE_DEFAULT_SENTINEL)
        combo.setCurrentIndex(desired_index if desired_index >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_document_template_apply_options(self, *, selected_template_id: str = "") -> None:
        combo = self._document_template_apply_combo
        button = self._document_template_apply_button
        permit = self._selected_permit()
        if combo is None:
            if button is not None:
                button.setEnabled(False)
            return

        if permit is None:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Select a permit first", "")
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
            combo.setEnabled(False)
            if button is not None:
                button.setEnabled(False)
            return

        permit_type = normalize_permit_type(permit.permit_type)
        current_id = str(selected_template_id or combo.currentData() or "").strip()

        combo.blockSignals(True)
        combo.clear()
        combo.addItem(
            f"Built-in Default ({_permit_type_label(permit_type)})",
            _TEMPLATE_DEFAULT_SENTINEL,
        )
        for template in sorted(
            (row for row in self._document_templates if normalize_permit_type(row.permit_type) == permit_type),
            key=lambda row: (row.name.casefold(), row.template_id),
        ):
            combo.addItem(template.name or "(Unnamed Template)", template.template_id)

        desired_id = current_id or _TEMPLATE_DEFAULT_SENTINEL
        desired_index = combo.findData(desired_id)
        if desired_index < 0:
            desired_index = combo.findData(_TEMPLATE_DEFAULT_SENTINEL)
        combo.setCurrentIndex(desired_index if desired_index >= 0 else 0)
        combo.blockSignals(False)
        combo.setEnabled(True)
        if button is not None:
            button.setEnabled(True)

    def _apply_selected_document_template_to_permit(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit before applying a template.")
            return

        combo = self._document_template_apply_combo
        selected_id = str(combo.currentData() or "").strip() if combo is not None else ""
        permit_type = normalize_permit_type(permit.permit_type)

        selected_template: DocumentChecklistTemplate | None = None
        if selected_id and selected_id != _TEMPLATE_DEFAULT_SENTINEL:
            selected_template = self._template_by_id(selected_id)
            if selected_template is None:
                self._show_warning_dialog(
                    "Template Missing",
                    "The selected template could not be found. Refresh and try again.",
                )
                self._refresh_document_template_apply_options()
                return
            if normalize_permit_type(selected_template.permit_type) != permit_type:
                self._show_warning_dialog(
                    "Template Type Mismatch",
                    "The selected template does not match this permit type.",
                )
                self._refresh_document_template_apply_options()
                return

        new_slots = build_document_slots_from_template(
            selected_template,
            permit_type=permit_type,
        )
        if not new_slots:
            self._show_warning_dialog("Template Empty", "The selected template has no usable folders.")
            return

        def _folder_id_from_slot(slot: PermitDocumentSlot) -> str:
            return normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)

        existing_folder_by_id: dict[str, PermitDocumentFolder] = {}
        for folder in permit.document_folders:
            folder_id = normalize_slot_id(folder.folder_id)
            if folder_id and folder_id not in existing_folder_by_id:
                existing_folder_by_id[folder_id] = folder

        old_folder_ids = {
            _folder_id_from_slot(slot)
            for slot in permit.document_slots
            if _folder_id_from_slot(slot)
        }
        new_folder_ids = {
            _folder_id_from_slot(slot)
            for slot in new_slots
            if _folder_id_from_slot(slot)
        }
        preserved_folder_ids = old_folder_ids & new_folder_ids
        deleted_folder_ids = old_folder_ids - new_folder_ids

        template_label = (
            selected_template.name.strip()
            if selected_template is not None and selected_template.name.strip()
            else f"Built-in Default ({_permit_type_label(permit_type)})"
        )
        if not self._confirm_dialog(
            "Apply Document Template",
            (
                f"Apply '{template_label}' to this permit?\n\n"
                "This will delete current template folders and files that do not share the same slot id.\n"
                "Folders with matching slot ids will be kept."
            ),
            confirm_text="Apply Template",
            cancel_text="Cancel",
            danger=True,
        ):
            return

        delete_failures: list[str] = []
        for folder_id in sorted(deleted_folder_ids):
            folder = existing_folder_by_id.get(folder_id)
            if folder is None:
                continue
            try:
                self._document_store.delete_folder_tree(permit, folder)
            except Exception as exc:
                delete_failures.append(f"{folder.name or folder.folder_id}: {exc}")

        permit.documents = [
            row
            for row in permit.documents
            if normalize_slot_id(row.folder_id) in preserved_folder_ids
        ]

        rebuilt_folders: list[PermitDocumentFolder] = []
        seen_folder_ids: set[str] = set()
        for slot in new_slots:
            folder_id = _folder_id_from_slot(slot)
            if not folder_id or folder_id in seen_folder_ids:
                continue
            seen_folder_ids.add(folder_id)
            existing_folder = existing_folder_by_id.get(folder_id)
            if existing_folder is not None:
                rebuilt_folders.append(
                    PermitDocumentFolder(
                        folder_id=folder_id,
                        name=slot.label or existing_folder.name or folder_id,
                        parent_folder_id="",
                    )
                )
            else:
                rebuilt_folders.append(
                    PermitDocumentFolder(
                        folder_id=folder_id,
                        name=slot.label or folder_id,
                        parent_folder_id="",
                    )
                )

        permit.document_slots = new_slots
        permit.document_folders = rebuilt_folders
        refresh_slot_status_from_documents(permit)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog(
                "Storage Error",
                f"Template applied, but some folders could not be prepared.\n\n{exc}",
            )

        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        self._persist_tracker_data()
        self._refresh_selected_permit_view()
        self._refresh_document_template_apply_options(selected_template_id=selected_id)

        if delete_failures:
            preview = "\n".join(delete_failures[:6])
            suffix = ""
            if len(delete_failures) > 6:
                suffix = f"\n...and {len(delete_failures) - 6} more."
            self._show_warning_dialog(
                "Some Folders Could Not Be Removed",
                f"{preview}{suffix}",
            )

    def _resolved_template_for_new_permit(self, permit_type: str) -> DocumentChecklistTemplate | None:
        combo = self._add_permit_template_combo
        selected_id = str(combo.currentData() or "").strip() if combo is not None else ""
        if selected_id and selected_id != _TEMPLATE_DEFAULT_SENTINEL:
            record = self._template_by_id(selected_id)
            if record is not None and normalize_permit_type(record.permit_type) == normalize_permit_type(permit_type):
                return record
        default_id = str(self._active_document_template_ids.get(normalize_permit_type(permit_type)) or "").strip()
        default_template = self._template_by_id(default_id)
        return default_template

    def _on_add_permit_type_changed(self, *_args: object) -> None:
        if self._add_permit_form_loading:
            return
        self._refresh_add_permit_template_options()
        self._sync_inline_permit_dirty_state()

    def _open_document_templates_view(self) -> None:
        self._open_contacts_and_jurisdictions_dialog(preferred_tab="templates")

    def _close_document_templates_view(self) -> None:
        self._close_contacts_and_jurisdictions_view()

    def _refresh_document_templates_view(self) -> None:
        self._prune_active_document_template_ids()
        if self._templates_list_widget is not None:
            self._refresh_templates_list(select_id=self._template_selected_id)
        template, read_only = self._resolve_template_editor_record(self._template_selected_id)
        if template is not None and not self._template_dirty and not self._template_form_loading:
            self._apply_template_to_form(template, read_only=read_only)
        elif template is None and not self._template_dirty and not self._template_form_loading:
            self._template_selected_id = ""
            self._template_new(require_confirm=False)
        self._update_template_default_label()
        self._refresh_add_permit_template_options()
        self._refresh_document_template_apply_options()

    def _normalize_valid_contact_ids(self, selected_ids: Sequence[str]) -> list[str]:
        existing_ids = {record.contact_id for record in self._contacts}
        rows: list[str] = []
        seen: set[str] = set()
        for raw_id in selected_ids:
            contact_id = str(raw_id or "").strip()
            if not contact_id or contact_id in seen:
                continue
            if contact_id not in existing_ids:
                continue
            seen.add(contact_id)
            rows.append(contact_id)
        return rows

    def _inline_contact_picker_preview(self, contact: ContactRecord) -> str:
        method_rows = self._contact_methods_from_record(contact)
        detail_parts: list[str] = []
        if method_rows and method_rows[0].emails:
            detail_parts.append(method_rows[0].emails[0])
        elif contact.emails:
            detail_parts.append(contact.emails[0])
        if method_rows and method_rows[0].numbers:
            detail_parts.append(method_rows[0].numbers[0])
        elif contact.numbers:
            detail_parts.append(contact.numbers[0])
        detail_text = f" ({' | '.join(detail_parts)})" if detail_parts else ""
        return f"{contact.name or '(Unnamed)'}{detail_text}"

    def _inline_contact_chip_details(self, contact: ContactRecord) -> list[str]:
        method_rows = self._contact_methods_from_record(contact)
        detail_lines: list[str] = []
        if contact.roles:
            detail_lines.append(f"Roles: {', '.join(contact.roles)}")
        if method_rows:
            for method in method_rows:
                bundle_title = self._contact_method_title(method)
                detail_lines.append(f"Bundle: {bundle_title}")
                detail_lines.extend(self._contact_method_summary_lines(method))
        else:
            detail_lines.append("No bundles yet.")
        return detail_lines

    def _refresh_add_property_contacts_picker(self, *, selected_ids: Sequence[str]) -> None:
        combo = self._add_property_contact_picker_combo
        host = self._add_property_attached_contacts_host
        if combo is None or host is None:
            return

        self._add_property_attached_contact_ids = self._normalize_valid_contact_ids(selected_ids)
        if self._add_property_contacts_label is not None:
            self._add_property_contacts_label.setText(
                f"Attached Contacts ({len(self._add_property_attached_contact_ids)})"
            )

        combo.blockSignals(True)
        combo.clear()
        default_label = "Select contact to attach..." if self._contacts else "No contacts available yet"
        combo.addItem(default_label, "")
        for contact in sorted(self._contacts, key=lambda row: (row.name.casefold(), row.contact_id)):
            combo.addItem(self._inline_contact_picker_preview(contact), contact.contact_id)
        combo.setCurrentIndex(0)
        combo.setEnabled(bool(self._contacts))
        combo.blockSignals(False)

        if self._add_property_contact_add_button is not None:
            self._add_property_contact_add_button.setEnabled(bool(self._contacts))

        layout = host.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        if not self._add_property_attached_contact_ids:
            empty_label = QLabel("No contacts attached yet.", host)
            empty_label.setObjectName("TrackerPanelMeta")
            layout.addWidget(empty_label, 0)
            layout.addStretch(1)
            return

        for contact_id in self._add_property_attached_contact_ids:
            contact = self._contact_by_id(contact_id)
            if contact is None:
                continue
            chip = AttachedContactChip(
                title=contact.name or "(Unnamed)",
                detail_lines=self._inline_contact_chip_details(contact),
                metadata_layout="bundle_groups",
                on_remove=lambda cid=contact.contact_id: self._inline_remove_property_contact(cid),
                parent=host,
            )
            layout.addWidget(chip, 0)
        layout.addStretch(1)

    def _inline_add_property_contact(self) -> None:
        combo = self._add_property_contact_picker_combo
        if combo is None:
            return
        contact_id = str(combo.currentData() or "").strip()
        if not contact_id:
            return
        if self._contact_by_id(contact_id) is None:
            return
        if contact_id in self._add_property_attached_contact_ids:
            combo.setCurrentIndex(0)
            return
        self._add_property_attached_contact_ids.append(contact_id)
        self._refresh_add_property_contacts_picker(
            selected_ids=self._add_property_attached_contact_ids
        )
        self._sync_inline_property_dirty_state()

    def _inline_remove_property_contact(self, contact_id: str) -> None:
        target = str(contact_id or "").strip()
        if not target:
            return
        self._add_property_attached_contact_ids = [
            row_id for row_id in self._add_property_attached_contact_ids if row_id != target
        ]
        self._refresh_add_property_contacts_picker(
            selected_ids=self._add_property_attached_contact_ids
        )
        self._sync_inline_property_dirty_state()

    def _refresh_add_permit_contacts_picker(self, *, selected_ids: Sequence[str]) -> None:
        combo = self._add_permit_contact_picker_combo
        host = self._add_permit_attached_contacts_host
        if combo is None or host is None:
            return

        self._add_permit_attached_contact_ids = self._normalize_valid_contact_ids(selected_ids)
        if self._add_permit_contacts_label is not None:
            self._add_permit_contacts_label.setText(
                f"Attached Contacts ({len(self._add_permit_attached_contact_ids)})"
            )

        combo.blockSignals(True)
        combo.clear()
        default_label = "Select contact to attach..." if self._contacts else "No contacts available yet"
        combo.addItem(default_label, "")
        for contact in sorted(self._contacts, key=lambda row: (row.name.casefold(), row.contact_id)):
            combo.addItem(self._inline_contact_picker_preview(contact), contact.contact_id)
        combo.setCurrentIndex(0)
        combo.setEnabled(bool(self._contacts))
        combo.blockSignals(False)

        if self._add_permit_contact_add_button is not None:
            self._add_permit_contact_add_button.setEnabled(bool(self._contacts))

        layout = host.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        if not self._add_permit_attached_contact_ids:
            empty_label = QLabel("No contacts attached yet.", host)
            empty_label.setObjectName("TrackerPanelMeta")
            layout.addWidget(empty_label, 0)
            layout.addStretch(1)
            return

        for contact_id in self._add_permit_attached_contact_ids:
            contact = self._contact_by_id(contact_id)
            if contact is None:
                continue
            chip = AttachedContactChip(
                title=contact.name or "(Unnamed)",
                detail_lines=self._inline_contact_chip_details(contact),
                metadata_layout="bundle_groups",
                on_remove=lambda cid=contact.contact_id: self._inline_remove_permit_contact(cid),
                parent=host,
            )
            layout.addWidget(chip, 0)
        layout.addStretch(1)

    def _inline_add_permit_contact(self) -> None:
        combo = self._add_permit_contact_picker_combo
        if combo is None:
            return
        contact_id = str(combo.currentData() or "").strip()
        if not contact_id:
            return
        if self._contact_by_id(contact_id) is None:
            return
        if contact_id in self._add_permit_attached_contact_ids:
            combo.setCurrentIndex(0)
            return
        self._add_permit_attached_contact_ids.append(contact_id)
        self._refresh_add_permit_contacts_picker(
            selected_ids=self._add_permit_attached_contact_ids
        )
        self._sync_inline_permit_dirty_state()

    def _inline_remove_permit_contact(self, contact_id: str) -> None:
        target = str(contact_id or "").strip()
        if not target:
            return
        self._add_permit_attached_contact_ids = [
            row_id for row_id in self._add_permit_attached_contact_ids if row_id != target
        ]
        self._refresh_add_permit_contacts_picker(
            selected_ids=self._add_permit_attached_contact_ids
        )
        self._sync_inline_permit_dirty_state()

    def _inline_property_form_snapshot(self) -> tuple[object, ...]:
        address = (
            self._add_property_address_input.text().strip()
            if self._add_property_address_input is not None
            else ""
        )
        parcel_id = (
            self._add_property_parcel_input.text().strip()
            if self._add_property_parcel_input is not None
            else ""
        )
        jurisdiction_id = (
            str(self._add_property_jurisdiction_combo.currentData() or "").strip()
            if self._add_property_jurisdiction_combo is not None
            else ""
        )
        tags = (
            tuple(_parse_multi_values(self._add_property_tags_input.text()))
            if self._add_property_tags_input is not None
            else tuple()
        )
        notes = (
            self._add_property_notes_input.text().strip()
            if self._add_property_notes_input is not None
            else ""
        )
        contact_ids = tuple(self._add_property_attached_contact_ids)
        list_color = normalize_list_color(self._add_property_list_color)
        return (
            address,
            parcel_id,
            jurisdiction_id,
            tags,
            notes,
            contact_ids,
            list_color,
        )

    def _inline_permit_form_snapshot(self) -> tuple[object, ...]:
        permit_type = (
            normalize_permit_type(self._add_permit_type_combo.currentData())
            if self._add_permit_type_combo is not None
            else "building"
        )
        template_id = (
            str(self._add_permit_template_combo.currentData() or "").strip()
            if self._add_permit_template_combo is not None
            else _TEMPLATE_DEFAULT_SENTINEL
        )
        permit_number = (
            self._add_permit_number_input.text().strip()
            if self._add_permit_number_input is not None
            else ""
        )
        next_action = (
            self._add_permit_next_action_input.text().strip()
            if self._add_permit_next_action_input is not None
            else ""
        )
        next_action_due = (
            self._add_permit_next_action_due_input.text().strip()
            if self._add_permit_next_action_due_input is not None
            else ""
        )
        request_date = (
            self._add_permit_request_date_input.text().strip()
            if self._add_permit_request_date_input is not None
            else ""
        )
        application_date = (
            self._add_permit_application_date_input.text().strip()
            if self._add_permit_application_date_input is not None
            else ""
        )
        issued_date = (
            self._add_permit_issued_date_input.text().strip()
            if self._add_permit_issued_date_input is not None
            else ""
        )
        final_date = (
            self._add_permit_final_date_input.text().strip()
            if self._add_permit_final_date_input is not None
            else ""
        )
        completion_date = (
            self._add_permit_completion_date_input.text().strip()
            if self._add_permit_completion_date_input is not None
            else ""
        )
        contact_ids = tuple(self._add_permit_attached_contact_ids)
        return (
            str(self._add_permit_property_id or "").strip(),
            permit_type,
            template_id,
            permit_number,
            next_action,
            next_action_due,
            request_date,
            application_date,
            issued_date,
            final_date,
            completion_date,
            contact_ids,
        )

    def _inline_property_form_is_empty(self) -> bool:
        snapshot = self._inline_property_form_snapshot()
        if len(snapshot) < 7:
            return False
        address = str(snapshot[0] or "").strip()
        parcel_id = str(snapshot[1] or "").strip()
        jurisdiction_id = str(snapshot[2] or "").strip()
        tags = tuple(snapshot[3]) if isinstance(snapshot[3], tuple) else tuple()
        notes = str(snapshot[4] or "").strip()
        contact_ids = tuple(snapshot[5]) if len(snapshot) > 5 and isinstance(snapshot[5], tuple) else tuple()
        list_color = normalize_list_color(snapshot[6] if len(snapshot) > 6 else "")
        return not any((address, parcel_id, jurisdiction_id, tags, notes, contact_ids, list_color))

    def _inline_permit_form_is_empty(self) -> bool:
        snapshot = self._inline_permit_form_snapshot()
        if len(snapshot) < 12:
            return False
        permit_number = str(snapshot[3] or "").strip()
        next_action = str(snapshot[4] or "").strip()
        next_action_due = str(snapshot[5] or "").strip()
        request_date = str(snapshot[6] or "").strip()
        application_date = str(snapshot[7] or "").strip()
        issued_date = str(snapshot[8] or "").strip()
        final_date = str(snapshot[9] or "").strip()
        completion_date = str(snapshot[10] or "").strip()
        contact_ids = tuple(snapshot[11]) if len(snapshot) > 11 and isinstance(snapshot[11], tuple) else tuple()
        return not any(
            (
                permit_number,
                next_action,
                next_action_due,
                request_date,
                application_date,
                issued_date,
                final_date,
                completion_date,
                contact_ids,
            )
        )

    def _inline_property_dirty_bubble_state(self) -> str:
        if self._add_property_form_dirty:
            return "dirty"
        if not self._add_property_editing_id and self._inline_property_form_is_empty():
            return "empty"
        return "clean"

    def _inline_permit_dirty_bubble_state(self) -> str:
        if self._add_permit_form_dirty:
            return "dirty"
        if not self._add_permit_editing_id and self._inline_permit_form_is_empty():
            return "empty"
        return "clean"

    def _set_inline_property_dirty(self, dirty: bool) -> None:
        self._add_property_form_dirty = bool(dirty)
        self._set_admin_dirty_bubble_state(
            self._add_property_dirty_bubble,
            state=self._inline_property_dirty_bubble_state(),
        )

    def _set_inline_permit_dirty(self, dirty: bool) -> None:
        self._add_permit_form_dirty = bool(dirty)
        self._set_admin_dirty_bubble_state(
            self._add_permit_dirty_bubble,
            state=self._inline_permit_dirty_bubble_state(),
        )

    def _rebase_inline_property_dirty_tracking(self) -> None:
        self._add_property_baseline_snapshot = self._inline_property_form_snapshot()
        self._set_inline_property_dirty(False)

    def _rebase_inline_permit_dirty_tracking(self) -> None:
        self._add_permit_baseline_snapshot = self._inline_permit_form_snapshot()
        self._set_inline_permit_dirty(False)

    def _sync_inline_property_dirty_state(self) -> None:
        if self._add_property_form_loading:
            return
        if not self._add_property_baseline_snapshot:
            self._rebase_inline_property_dirty_tracking()
            return
        self._set_inline_property_dirty(
            self._inline_property_form_snapshot() != self._add_property_baseline_snapshot
        )

    def _sync_inline_permit_dirty_state(self) -> None:
        if self._add_permit_form_loading:
            return
        if not self._add_permit_baseline_snapshot:
            self._rebase_inline_permit_dirty_tracking()
            return
        self._set_inline_permit_dirty(
            self._inline_permit_form_snapshot() != self._add_permit_baseline_snapshot
        )

    def _on_inline_property_form_changed(self, *_args: object) -> None:
        self._sync_inline_property_dirty_state()

    def _on_inline_permit_form_changed(self, *_args: object) -> None:
        self._sync_inline_permit_dirty_state()

    def _confirm_discard_inline_form_changes(self, *, action_label: str) -> bool:
        mode = str(self._active_inline_form_view or "").strip().casefold()
        if mode in {"add_property", "edit_property"} and self._add_property_form_dirty:
            return self._confirm_dialog(
                "Unsaved Address Changes",
                (
                    "You have unsaved changes in the address form. "
                    f"Discard them and continue with '{action_label}'?"
                ),
                confirm_text="Discard Changes",
                cancel_text="Keep Editing",
                danger=True,
            )
        if mode in {"add_permit", "edit_permit"} and self._add_permit_form_dirty:
            return self._confirm_dialog(
                "Unsaved Permit Changes",
                (
                    "You have unsaved changes in the permit form. "
                    f"Discard them and continue with '{action_label}'?"
                ),
                confirm_text="Discard Changes",
                cancel_text="Keep Editing",
                danger=True,
            )
        return True

    def _reset_add_property_form(self) -> None:
        self._add_property_form_loading = True
        try:
            self._add_property_editing_id = ""
            if self._add_property_title_label is not None:
                self._add_property_title_label.setText("Add Address")
            if self._add_property_subtitle_label is not None:
                self._add_property_subtitle_label.setText(
                    "Create a new property record to begin tracking permits and documents."
                )
            if self._add_property_submit_button is not None:
                self._add_property_submit_button.setText("Create Address")
            self._refresh_add_property_jurisdiction_options(selected_id="")
            self._add_property_attached_contact_ids = []
            self._refresh_add_property_contacts_picker(selected_ids=[])
            self._add_property_color_picker_open = False
            self._set_admin_entity_list_color(
                entity_kind="property",
                color_hex="",
                custom_color="",
                notify=False,
            )
            if self._add_property_address_input is not None:
                self._add_property_address_input.clear()
            if self._add_property_parcel_input is not None:
                self._add_property_parcel_input.clear()
            if self._add_property_tags_input is not None:
                self._add_property_tags_input.clear()
            if self._add_property_notes_input is not None:
                self._add_property_notes_input.clear()
        finally:
            self._add_property_form_loading = False
        self._rebase_inline_property_dirty_tracking()

    def _reset_add_permit_form(self, *, property_record: PropertyRecord, default_type: str) -> None:
        self._add_permit_form_loading = True
        try:
            self._add_permit_editing_id = ""
            if self._add_permit_title_label is not None:
                self._add_permit_title_label.setText("Add Permit")
            if self._add_permit_submit_button is not None:
                self._add_permit_submit_button.setText("Create Permit")
            self._add_permit_property_id = property_record.property_id
            self._add_permit_attached_contact_ids = self._normalize_valid_contact_ids(
                property_record.contact_ids
            )
            if self._add_permit_context_label is not None:
                self._add_permit_context_label.setText(
                    f"Address: {property_record.display_address or '(no address)'}\n"
                    f"Parcel: {property_record.parcel_id or '(none)'}"
                )
            if self._add_permit_type_combo is not None:
                self._add_permit_type_combo.setEnabled(True)
                self._add_permit_type_combo.setToolTip("")
                desired_type = normalize_permit_type(default_type)
                desired_index = self._add_permit_type_combo.findData(desired_type)
                if desired_index < 0:
                    desired_index = self._add_permit_type_combo.findData("building")
                if desired_index >= 0:
                    self._add_permit_type_combo.setCurrentIndex(desired_index)
            if self._add_permit_template_combo is not None:
                self._add_permit_template_combo.setEnabled(True)
                self._add_permit_template_combo.setToolTip("")
                desired_type = (
                    normalize_permit_type(self._add_permit_type_combo.currentData())
                    if self._add_permit_type_combo is not None
                    else "building"
                )
                preferred_template_id = str(self._active_document_template_ids.get(desired_type) or "").strip()
                self._refresh_add_permit_template_options(
                    selected_template_id=preferred_template_id or _TEMPLATE_DEFAULT_SENTINEL
                )
            self._refresh_add_permit_contacts_picker(
                selected_ids=self._add_permit_attached_contact_ids
            )
            for field in (
                self._add_permit_number_input,
                self._add_permit_next_action_input,
                self._add_permit_next_action_due_input,
                self._add_permit_request_date_input,
                self._add_permit_application_date_input,
                self._add_permit_issued_date_input,
                self._add_permit_final_date_input,
                self._add_permit_completion_date_input,
            ):
                if field is not None:
                    field.clear()
        finally:
            self._add_permit_form_loading = False
        self._rebase_inline_permit_dirty_tracking()

    def _populate_edit_property_form(self, property_record: PropertyRecord) -> None:
        self._add_property_form_loading = True
        try:
            self._add_property_editing_id = property_record.property_id
            if self._add_property_title_label is not None:
                address_label = property_record.display_address.strip() or "(no address)"
                self._add_property_title_label.setText(f"Editing Address: {address_label}")
            if self._add_property_subtitle_label is not None:
                self._add_property_subtitle_label.setText(
                    "Update this property record. Existing permits remain linked to this address."
                )
            if self._add_property_submit_button is not None:
                self._add_property_submit_button.setText("Update Address")
            self._refresh_add_property_jurisdiction_options(selected_id=property_record.jurisdiction_id)
            self._add_property_attached_contact_ids = self._normalize_valid_contact_ids(
                property_record.contact_ids
            )
            self._refresh_add_property_contacts_picker(
                selected_ids=self._add_property_attached_contact_ids
            )
            self._add_property_color_picker_open = False
            property_color = normalize_list_color(property_record.list_color)
            property_custom_color = (
                property_color
                if property_color and property_color not in _ADMIN_LIST_COLOR_PRESETS
                else ""
            )
            self._set_admin_entity_list_color(
                entity_kind="property",
                color_hex=property_color,
                custom_color=property_custom_color,
                notify=False,
            )
            if self._add_property_address_input is not None:
                self._add_property_address_input.setText(property_record.display_address)
            if self._add_property_parcel_input is not None:
                self._add_property_parcel_input.setText(property_record.parcel_id)
            if self._add_property_tags_input is not None:
                self._add_property_tags_input.setText(_join_multi_values(property_record.tags))
            if self._add_property_notes_input is not None:
                self._add_property_notes_input.setText(property_record.notes)
        finally:
            self._add_property_form_loading = False
        self._rebase_inline_property_dirty_tracking()

    def _populate_edit_permit_form(
        self,
        *,
        property_record: PropertyRecord,
        permit: PermitRecord,
    ) -> None:
        self._add_permit_form_loading = True
        try:
            self._add_permit_editing_id = permit.permit_id
            self._add_permit_property_id = property_record.property_id
            if self._add_permit_title_label is not None:
                permit_label = permit.permit_number.strip() or "(no permit # yet)"
                self._add_permit_title_label.setText(f"Editing Permit: {permit_label}")
            if self._add_permit_submit_button is not None:
                self._add_permit_submit_button.setText("Update Permit")
            selected_contact_ids: list[str] = []
            seen_contact_ids: set[str] = set()
            for party in permit.parties:
                contact_id = str(party.contact_id or "").strip()
                if not contact_id or contact_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(contact_id)
                selected_contact_ids.append(contact_id)
            self._add_permit_attached_contact_ids = self._normalize_valid_contact_ids(selected_contact_ids)
            if self._add_permit_context_label is not None:
                self._add_permit_context_label.setText(
                    f"Address: {property_record.display_address or '(no address)'}\n"
                    f"Parcel: {property_record.parcel_id or '(none)'}"
                )
            if self._add_permit_type_combo is not None:
                desired_index = self._add_permit_type_combo.findData(normalize_permit_type(permit.permit_type))
                if desired_index >= 0:
                    self._add_permit_type_combo.setCurrentIndex(desired_index)
                self._add_permit_type_combo.setEnabled(False)
                self._add_permit_type_combo.setToolTip("Permit type is locked after creation.")
            if self._add_permit_template_combo is not None:
                self._refresh_add_permit_template_options(
                    selected_template_id=_TEMPLATE_DEFAULT_SENTINEL
                )
                self._add_permit_template_combo.setEnabled(False)
                self._add_permit_template_combo.setToolTip(
                    "Checklist template is used only when creating a permit."
                )
            self._refresh_add_permit_contacts_picker(
                selected_ids=self._add_permit_attached_contact_ids
            )
            if self._add_permit_number_input is not None:
                self._add_permit_number_input.setText(permit.permit_number)
            if self._add_permit_next_action_input is not None:
                self._add_permit_next_action_input.setText(permit.next_action_text)
            if self._add_permit_next_action_due_input is not None:
                self._add_permit_next_action_due_input.setText(permit.next_action_due)
            if self._add_permit_request_date_input is not None:
                self._add_permit_request_date_input.setText(permit.request_date)
            if self._add_permit_application_date_input is not None:
                self._add_permit_application_date_input.setText(permit.application_date)
            if self._add_permit_issued_date_input is not None:
                self._add_permit_issued_date_input.setText(permit.issued_date)
            if self._add_permit_final_date_input is not None:
                self._add_permit_final_date_input.setText(permit.final_date)
            if self._add_permit_completion_date_input is not None:
                self._add_permit_completion_date_input.setText(permit.completion_date)
        finally:
            self._add_permit_form_loading = False
        self._rebase_inline_permit_dirty_tracking()

    def _refresh_add_property_jurisdiction_options(self, *, selected_id: str = "") -> None:
        combo = self._add_property_jurisdiction_combo
        if combo is None:
            return
        selected = str(selected_id or "").strip() or str(combo.currentData() or "").strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Unassigned", "")
        for jurisdiction in sorted(self._jurisdictions, key=lambda row: row.name.casefold()):
            combo.addItem(jurisdiction.name or "(Unnamed)", jurisdiction.jurisdiction_id)
        index = combo.findData(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _open_add_property_view(self) -> None:
        if self._panel_stack is None or self._panel_add_property_view is None:
            return
        if not self._confirm_discard_template_changes(action_label="Add Address"):
            return
        if not self._confirm_discard_inline_form_changes(action_label="Add Address"):
            return
        self._active_inline_form_view = "add_property"
        self._reset_add_property_form()
        self._panel_stack.setCurrentWidget(self._panel_add_property_view)
        self._sync_foreground_layout()
        if self._add_property_address_input is not None:
            self._add_property_address_input.setFocus()

    def _open_edit_property_view(self, property_record: PropertyRecord) -> None:
        if self._panel_stack is None or self._panel_add_property_view is None:
            return
        if not self._confirm_discard_template_changes(action_label="Edit Address"):
            return
        if not self._confirm_discard_inline_form_changes(action_label="Edit Address"):
            return
        self._active_inline_form_view = "edit_property"
        self._populate_edit_property_form(property_record)
        self._panel_stack.setCurrentWidget(self._panel_add_property_view)
        self._sync_foreground_layout()
        if self._add_property_address_input is not None:
            self._add_property_address_input.setFocus()

    def _open_add_permit_view(self) -> None:
        if self._panel_stack is None or self._panel_add_permit_view is None:
            return
        if not self._confirm_discard_template_changes(action_label="Add Permit"):
            return
        if not self._confirm_discard_inline_form_changes(action_label="Add Permit"):
            return
        selected_property = self._selected_property()
        if selected_property is None:
            self._show_info_dialog("Select Address", "Select an address before creating a permit.")
            return
        default_type = self._active_permit_type_filter
        if default_type == "all":
            default_type = "building"
        self._active_inline_form_view = "add_permit"
        self._reset_add_permit_form(property_record=selected_property, default_type=default_type)
        self._panel_stack.setCurrentWidget(self._panel_add_permit_view)
        self._sync_foreground_layout()
        if self._add_permit_number_input is not None:
            self._add_permit_number_input.setFocus()

    def _open_edit_permit_view(self, *, property_record: PropertyRecord, permit: PermitRecord) -> None:
        if self._panel_stack is None or self._panel_add_permit_view is None:
            return
        if not self._confirm_discard_template_changes(action_label="Edit Permit"):
            return
        if not self._confirm_discard_inline_form_changes(action_label="Edit Permit"):
            return
        self._active_inline_form_view = "edit_permit"
        self._populate_edit_permit_form(property_record=property_record, permit=permit)
        self._panel_stack.setCurrentWidget(self._panel_add_permit_view)
        self._sync_foreground_layout()
        if self._add_permit_number_input is not None:
            self._add_permit_number_input.setFocus()

    def _close_inline_form_view(self, *, require_confirm: bool = True, action_label: str = "Cancel") -> None:
        if require_confirm and not self._confirm_discard_inline_form_changes(action_label=action_label):
            return
        self._active_inline_form_view = ""
        self._add_property_editing_id = ""
        self._add_permit_editing_id = ""
        self._add_permit_property_id = ""
        self._set_inline_property_dirty(False)
        self._set_inline_permit_dirty(False)
        if self._panel_stack is None or self._panel_home_view is None:
            return
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._sync_foreground_layout()

    def _save_add_property_from_inline_form(self) -> None:
        address = self._add_property_address_input.text().strip() if self._add_property_address_input else ""
        if not address:
            self._show_warning_dialog("Missing Address", "Please provide a display address.")
            return
        edit_id = str(self._add_property_editing_id or "").strip()
        existing = self._property_by_id(edit_id) if edit_id else None
        if edit_id and existing is None:
            self._show_warning_dialog(
                "Address Not Found",
                "The address being edited no longer exists. Please reopen it from the list.",
            )
            self._close_inline_form_view(require_confirm=False)
            return
        parcel_id = self._add_property_parcel_input.text().strip() if self._add_property_parcel_input else ""
        selected_contact_ids = self._normalize_valid_contact_ids(self._add_property_attached_contact_ids)
        list_color = normalize_list_color(self._add_property_list_color)
        property_record = PropertyRecord(
            property_id=existing.property_id if existing is not None else uuid4().hex,
            display_address=address,
            parcel_id=parcel_id,
            parcel_id_norm=normalize_parcel_id(parcel_id),
            jurisdiction_id=(
                str(self._add_property_jurisdiction_combo.currentData() or "").strip()
                if self._add_property_jurisdiction_combo is not None
                else ""
            ),
            contact_ids=selected_contact_ids,
            list_color=list_color,
            tags=_parse_multi_values(self._add_property_tags_input.text())
            if self._add_property_tags_input is not None
            else [],
            notes=self._add_property_notes_input.text().strip()
            if self._add_property_notes_input is not None
            else "",
        )
        if not self._confirm_duplicate_property_parcel(property_record):
            return
        if existing is None:
            self._properties.append(property_record)
        else:
            for index, row in enumerate(self._properties):
                if row.property_id != existing.property_id:
                    continue
                self._properties[index] = property_record
                break
        self._selected_property_id = property_record.property_id
        self._close_inline_form_view(require_confirm=False)
        self._persist_tracker_data()
        self._refresh_all_views()

    def _save_add_permit_from_inline_form(self) -> None:
        property_record = self._property_by_id(self._add_permit_property_id) or self._selected_property()
        if property_record is None:
            self._show_info_dialog("Select Address", "Select an address before creating a permit.")
            self._close_inline_form_view(require_confirm=False)
            return

        edit_id = str(self._add_permit_editing_id or "").strip()
        existing = self._permit_by_id(edit_id) if edit_id else None
        if edit_id and existing is None:
            self._show_warning_dialog(
                "Permit Not Found",
                "The permit being edited no longer exists. Please reopen it from the list.",
            )
            self._close_inline_form_view(require_confirm=False)
            return
        permit_type = (
            normalize_permit_type(self._add_permit_type_combo.currentData())
            if self._add_permit_type_combo is not None
            else "building"
        )
        request_date = (
            self._add_permit_request_date_input.text().strip()
            if self._add_permit_request_date_input is not None
            else ""
        )
        application_date = (
            self._add_permit_application_date_input.text().strip()
            if self._add_permit_application_date_input is not None
            else ""
        )
        issued_date = (
            self._add_permit_issued_date_input.text().strip()
            if self._add_permit_issued_date_input is not None
            else ""
        )
        final_date = (
            self._add_permit_final_date_input.text().strip()
            if self._add_permit_final_date_input is not None
            else ""
        )
        completion_date = (
            self._add_permit_completion_date_input.text().strip()
            if self._add_permit_completion_date_input is not None
            else ""
        )

        permit_number = (
            self._add_permit_number_input.text().strip()
            if self._add_permit_number_input is not None
            else ""
        )
        next_action_text = (
            self._add_permit_next_action_input.text().strip()
            if self._add_permit_next_action_input is not None
            else ""
        )
        next_action_due = (
            self._add_permit_next_action_due_input.text().strip()
            if self._add_permit_next_action_due_input is not None
            else ""
        )
        selected_contact_ids = self._normalize_valid_contact_ids(self._add_permit_attached_contact_ids)
        selected_template = self._resolved_template_for_new_permit(permit_type) if existing is None else None

        created_new = existing is None
        if existing is None:
            slots = build_document_slots_from_template(
                selected_template,
                permit_type=permit_type,
            )
            permit = PermitRecord(
                permit_id=uuid4().hex,
                property_id=property_record.property_id,
                permit_type=permit_type,
                permit_number=permit_number,
                status="requested",
                next_action_text=next_action_text,
                next_action_due=next_action_due,
                request_date=request_date,
                application_date=application_date,
                issued_date=issued_date,
                final_date=final_date,
                completion_date=completion_date,
                parties=[
                    PermitParty(contact_id=contact_id, role="", note="")
                    for contact_id in selected_contact_ids
                ],
                events=_prefill_permit_events_from_milestones(
                    request_date=request_date,
                    application_date=application_date,
                    issued_date=issued_date,
                    final_date=final_date,
                    completion_date=completion_date,
                    next_action_text=next_action_text,
                    next_action_due=next_action_due,
                ),
                document_slots=slots,
                document_folders=[],
                documents=[],
            )
            permit.document_folders = build_document_folders_from_slots(permit.document_slots)
            self._permits.append(permit)
        else:
            permit = existing
            permit.property_id = property_record.property_id
            permit.permit_type = permit_type
            permit.permit_number = permit_number
            permit.next_action_text = next_action_text
            permit.next_action_due = next_action_due
            permit.request_date = request_date
            permit.application_date = application_date
            permit.issued_date = issued_date
            permit.final_date = final_date
            permit.completion_date = completion_date
            existing_party_by_contact: dict[str, PermitParty] = {}
            for party in permit.parties:
                contact_id = str(party.contact_id or "").strip()
                if not contact_id or contact_id in existing_party_by_contact:
                    continue
                existing_party_by_contact[contact_id] = party
            permit.parties = [
                PermitParty(
                    contact_id=contact_id,
                    role=existing_party_by_contact[contact_id].role if contact_id in existing_party_by_contact else "",
                    note=existing_party_by_contact[contact_id].note if contact_id in existing_party_by_contact else "",
                )
                for contact_id in selected_contact_ids
            ]

        ensure_default_document_structure(permit)
        permit.status = compute_permit_status(permit.events, fallback=permit.status)
        if created_new:
            try:
                self._document_store.ensure_folder_structure(permit)
            except Exception as exc:
                self._show_warning_dialog(
                    "Document Storage Error",
                    f"The permit was created, but default document folders could not be prepared.\n\n{exc}",
                )

        self._selected_property_id = property_record.property_id
        self._selected_permit_id = permit.permit_id
        self._active_permit_type_filter = normalize_permit_type(permit.permit_type)
        self._close_inline_form_view(require_confirm=False)
        self._persist_tracker_data()
        self._refresh_all_views()

    def _open_contacts_and_jurisdictions_dialog(self, *, preferred_tab: str = "contacts") -> None:
        if self._panel_stack is None or self._panel_admin_view is None:
            return
        tab_key = str(preferred_tab or "").strip().casefold()
        action_label = "Open Document Templates" if tab_key == "templates" else "Open Admin Panel"
        if not self._confirm_discard_template_changes(action_label=action_label):
            return
        if not self._confirm_discard_inline_form_changes(action_label=action_label):
            return
        self._refresh_admin_views()
        self._refresh_document_templates_view()
        tabs = self._admin_tabs
        if tabs is not None:
            if tab_key == "jurisdictions":
                tabs.setCurrentIndex(1 if tabs.count() > 1 else 0)
            elif tab_key == "templates" and self._admin_templates_tab_index >= 0:
                tabs.setCurrentIndex(self._admin_templates_tab_index)
            else:
                tabs.setCurrentIndex(0)
        self._panel_stack.setCurrentWidget(self._panel_admin_view)
        self._sync_foreground_layout()

    def _close_contacts_and_jurisdictions_view(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        if not self._confirm_discard_admin_view_changes(action_label="Back to Tracker"):
            return
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._refresh_all_views()
        self._sync_foreground_layout()

    def _refresh_admin_views(self) -> None:
        if self._admin_contacts_list_widget is None or self._admin_jurisdictions_list_widget is None:
            return
        self._refresh_admin_contacts_list(select_id=self._admin_selected_contact_id)
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)
        selected_jurisdiction = self._jurisdiction_by_id(self._admin_selected_jurisdiction_id)
        if selected_jurisdiction is None:
            self._refresh_admin_jurisdiction_contacts_picker(
                selected_ids=self._admin_jurisdiction_attached_contact_ids
            )
        else:
            self._refresh_admin_jurisdiction_contacts_picker(selected_ids=selected_jurisdiction.contact_ids)
        self._admin_set_contact_form_mode(
            editing=self._contact_by_id(self._admin_selected_contact_id) is not None
        )
        self._admin_set_jurisdiction_form_mode(
            editing=self._jurisdiction_by_id(self._admin_selected_jurisdiction_id) is not None
        )
        self._set_admin_contact_bundle_fields_open(
            self._admin_contact_bundle_fields_open,
            animate=False,
        )
        self._refresh_admin_contact_methods_list()
        self._sync_admin_contact_dirty_state()
        self._sync_admin_jurisdiction_dirty_state()
        self._sync_admin_editor_field_widths()

    def _set_widget_max_width(self, widget: QWidget | None, width: int, *, min_width: int = 220) -> None:
        if widget is None:
            return
        widget.setMaximumWidth(max(int(min_width), int(width)))

    def _list_item_size_hint_for_card(self, card: QWidget) -> object:
        hint = card.sizeHint()

        min_width = max(0, int(card.minimumWidth()))
        if min_width > 0:
            hint.setWidth(max(hint.width(), min_width))
        max_width = int(card.maximumWidth())
        if 0 < max_width < 16777215:
            hint.setWidth(min(hint.width(), max_width))

        min_height = max(0, int(card.minimumHeight()))
        if min_height > 0:
            hint.setHeight(max(hint.height(), min_height))
        max_height = int(card.maximumHeight())
        if 0 < max_height < 16777215:
            hint.setHeight(min(hint.height(), max_height))

        return hint

    def _sync_admin_editor_field_widths(self) -> None:
        contact_form = self._admin_contact_form_widget
        if contact_form is not None and contact_form.width() > 0:
            contact_half = max(280, int(contact_form.width() * 0.75))
            contact_quarter = max(96, int(contact_form.width() * 0.25))
            contact_color_shell_width = max(240, contact_quarter + 156)
            for shell in self._admin_contact_field_shells:
                self._set_widget_max_width(shell, contact_half)
            self._set_widget_max_width(self._admin_contact_name_input, contact_half)
            self._set_widget_max_width(self._admin_contact_roles_input, contact_half)
            self._set_widget_max_width(self._admin_contact_bundle_fields_host, contact_half)
            self._set_widget_max_width(self._admin_contact_methods_host, contact_half)
            self._set_widget_max_width(
                self._admin_contact_color_shell,
                contact_color_shell_width,
                min_width=240,
            )
            self._set_widget_max_width(
                self._admin_contact_color_picker_host,
                contact_quarter,
                min_width=96,
            )
            self._set_admin_entity_color_picker_open(
                "contact",
                self._admin_contact_color_picker_open,
                animate=False,
            )

        jurisdiction_form = self._admin_jurisdiction_form_widget
        if jurisdiction_form is not None and jurisdiction_form.width() > 0:
            jurisdiction_half = max(260, int(jurisdiction_form.width() * 0.5))
            jurisdiction_quarter = max(96, int(jurisdiction_form.width() * 0.25))
            jurisdiction_color_shell_width = max(240, jurisdiction_quarter + 156)
            for shell in self._admin_jurisdiction_field_shells:
                self._set_widget_max_width(shell, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_name_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_type_combo, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_parent_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_portals_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_vendor_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_notes_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_fields_host, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_attached_panel, jurisdiction_half)
            add_button_width = 0
            if self._admin_jurisdiction_contact_add_button is not None:
                add_button_width = self._admin_jurisdiction_contact_add_button.width() or 48
            picker_width = max(180, jurisdiction_half - add_button_width - 12)
            self._set_widget_max_width(self._admin_jurisdiction_contact_picker_combo, picker_width)
            self._set_widget_max_width(self._admin_jurisdiction_attached_picker_host, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_attached_contacts_host, jurisdiction_half)
            self._set_widget_max_width(
                self._admin_jurisdiction_color_shell,
                jurisdiction_color_shell_width,
                min_width=240,
            )
            self._set_widget_max_width(
                self._admin_jurisdiction_color_picker_host,
                jurisdiction_quarter,
                min_width=96,
            )
            self._set_admin_entity_color_picker_open(
                "jurisdiction",
                self._admin_jurisdiction_color_picker_open,
                animate=False,
            )

        template_form = self._template_form_widget
        if template_form is not None and template_form.width() > 0:
            template_half = max(280, int(template_form.width() * 0.75))
            for shell in self._template_field_shells:
                self._set_widget_max_width(shell, template_half)
            self._set_widget_max_width(self._template_name_input, template_half)
            self._set_widget_max_width(self._template_type_combo, template_half)
            self._set_widget_max_width(self._template_notes_input, template_half)
            self._set_widget_max_width(self._template_slot_label_input, template_half)
            self._set_widget_max_width(self._template_slot_required_combo, template_half)
            self._set_widget_max_width(self._template_slots_list_widget, template_half)

    def _contact_method_rows_snapshot(
        self, rows: Sequence[ContactMethodRecord]
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]:
        snapshot_rows: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = []
        for row in rows:
            label = str(row.label or "").strip()
            emails = tuple(_parse_multi_values(_join_multi_values(row.emails)))
            numbers = tuple(_parse_multi_values(_join_multi_values(row.numbers)))
            note = str(row.note or "").strip()
            if not any((label, emails, numbers, note)):
                continue
            snapshot_rows.append((label, emails, numbers, note))
        return tuple(snapshot_rows)

    def _admin_contact_form_snapshot(self) -> tuple[object, ...]:
        name = self._admin_contact_name_input.text().strip() if self._admin_contact_name_input else ""
        roles = (
            tuple(_parse_multi_values(self._admin_contact_roles_input.text()))
            if self._admin_contact_roles_input
            else tuple()
        )
        pending_numbers = (
            tuple(_parse_multi_values(self._admin_contact_numbers_input.text()))
            if self._admin_contact_numbers_input
            else tuple()
        )
        pending_emails = (
            tuple(_parse_multi_values(self._admin_contact_emails_input.text()))
            if self._admin_contact_emails_input
            else tuple()
        )
        pending_label = (
            self._admin_contact_bundle_name_input.text().strip()
            if self._admin_contact_bundle_name_input
            else ""
        )
        pending_note = self._admin_contact_note_input.text().strip() if self._admin_contact_note_input else ""
        methods = self._contact_method_rows_snapshot(self._admin_contact_method_rows)
        list_color = normalize_list_color(self._admin_contact_list_color)
        return (
            name,
            roles,
            methods,
            pending_label,
            pending_numbers,
            pending_emails,
            pending_note,
            self._admin_contact_editing_bundle_index,
            list_color,
        )

    def _admin_jurisdiction_form_snapshot(self) -> tuple[object, ...]:
        name = (
            self._admin_jurisdiction_name_input.text().strip()
            if self._admin_jurisdiction_name_input is not None
            else ""
        )
        jurisdiction_type = (
            str(self._admin_jurisdiction_type_combo.currentData() or "city")
            if self._admin_jurisdiction_type_combo is not None
            else "city"
        )
        parent_county = (
            self._admin_jurisdiction_parent_input.text().strip()
            if self._admin_jurisdiction_parent_input is not None
            else ""
        )
        portal_urls = (
            tuple(_parse_multi_values(self._admin_jurisdiction_portals_input.text()))
            if self._admin_jurisdiction_portals_input is not None
            else tuple()
        )
        portal_vendor = (
            self._admin_jurisdiction_vendor_input.text().strip()
            if self._admin_jurisdiction_vendor_input is not None
            else ""
        )
        notes = (
            self._admin_jurisdiction_notes_input.text().strip()
            if self._admin_jurisdiction_notes_input is not None
            else ""
        )
        contact_ids = tuple(sorted(self._admin_selected_jurisdiction_contact_ids()))
        list_color = normalize_list_color(self._admin_jurisdiction_list_color)
        return (
            name,
            jurisdiction_type,
            parent_county,
            portal_urls,
            portal_vendor,
            notes,
            contact_ids,
            list_color,
        )

    def _set_admin_contact_dirty(self, dirty: bool) -> None:
        self._admin_contact_dirty = bool(dirty)
        self._set_admin_dirty_bubble_state(
            self._admin_contact_dirty_bubble,
            state=self._admin_contact_dirty_bubble_state(),
        )
        self._admin_set_contact_form_mode(editing=self._admin_contact_editing_mode)

    def _set_admin_jurisdiction_dirty(self, dirty: bool) -> None:
        self._admin_jurisdiction_dirty = bool(dirty)
        self._set_admin_dirty_bubble_state(
            self._admin_jurisdiction_dirty_bubble,
            state=self._admin_jurisdiction_dirty_bubble_state(),
        )
        self._admin_set_jurisdiction_form_mode(editing=self._admin_jurisdiction_editing_mode)

    def _admin_contact_bubble_is_empty(self) -> bool:
        snapshot = self._admin_contact_form_snapshot()
        if len(snapshot) < 9:
            return False
        name = str(snapshot[0] or "").strip()
        roles = tuple(snapshot[1]) if isinstance(snapshot[1], tuple) else tuple()
        methods = tuple(snapshot[2]) if isinstance(snapshot[2], tuple) else tuple()
        pending_label = str(snapshot[3] or "").strip()
        pending_numbers = tuple(snapshot[4]) if isinstance(snapshot[4], tuple) else tuple()
        pending_emails = tuple(snapshot[5]) if isinstance(snapshot[5], tuple) else tuple()
        pending_note = str(snapshot[6] or "").strip()
        editing_bundle_index = int(snapshot[7]) if isinstance(snapshot[7], int) else -1
        list_color = normalize_list_color(snapshot[8] if len(snapshot) >= 9 else "")
        return not any(
            (
                name,
                roles,
                methods,
                pending_label,
                pending_numbers,
                pending_emails,
                pending_note,
                editing_bundle_index >= 0,
                list_color,
            )
        )

    def _admin_jurisdiction_bubble_is_empty(self) -> bool:
        snapshot = self._admin_jurisdiction_form_snapshot()
        if len(snapshot) < 8:
            return False
        name = str(snapshot[0] or "").strip()
        jurisdiction_type = str(snapshot[1] or "").strip().casefold()
        parent_county = str(snapshot[2] or "").strip()
        portal_urls = tuple(snapshot[3]) if isinstance(snapshot[3], tuple) else tuple()
        portal_vendor = str(snapshot[4] or "").strip()
        notes = str(snapshot[5] or "").strip()
        contact_ids = tuple(snapshot[6]) if isinstance(snapshot[6], tuple) else tuple()
        list_color = normalize_list_color(snapshot[7] if len(snapshot) >= 8 else "")
        return not any(
            (
                name,
                jurisdiction_type not in {"", "city"},
                parent_county,
                portal_urls,
                portal_vendor,
                notes,
                contact_ids,
                list_color,
            )
        )

    def _admin_contact_dirty_bubble_state(self) -> str:
        if self._admin_contact_dirty:
            return "dirty"
        if not self._admin_contact_editing_mode and self._admin_contact_bubble_is_empty():
            return "empty"
        return "clean"

    def _admin_jurisdiction_dirty_bubble_state(self) -> str:
        if self._admin_jurisdiction_dirty:
            return "dirty"
        if not self._admin_jurisdiction_editing_mode and self._admin_jurisdiction_bubble_is_empty():
            return "empty"
        return "clean"

    def _set_admin_dirty_bubble_state(self, bubble: QLabel | None, *, state: str) -> None:
        if bubble is None:
            return
        normalized_state = str(state or "").strip().casefold()
        if normalized_state not in {"dirty", "clean", "empty"}:
            normalized_state = "clean"
        bubble_text = {
            "dirty": "Unsaved",
            "clean": "Saved",
            "empty": "Empty",
        }.get(normalized_state, "Saved")
        bubble.setText(bubble_text)
        bubble.setProperty("dirtyState", normalized_state)
        style = bubble.style()
        style.unpolish(bubble)
        style.polish(bubble)
        bubble.update()

    def _rebase_admin_contact_dirty_tracking(self) -> None:
        self._admin_contact_baseline_snapshot = self._admin_contact_form_snapshot()
        self._set_admin_contact_dirty(False)

    def _rebase_admin_jurisdiction_dirty_tracking(self) -> None:
        self._admin_jurisdiction_baseline_snapshot = self._admin_jurisdiction_form_snapshot()
        self._set_admin_jurisdiction_dirty(False)

    def _sync_admin_contact_dirty_state(self) -> None:
        if self._admin_contact_form_loading:
            return
        if not self._admin_contact_baseline_snapshot:
            self._rebase_admin_contact_dirty_tracking()
            return
        self._set_admin_contact_dirty(
            self._admin_contact_form_snapshot() != self._admin_contact_baseline_snapshot
        )

    def _sync_admin_jurisdiction_dirty_state(self) -> None:
        if self._admin_jurisdiction_form_loading:
            return
        if not self._admin_jurisdiction_baseline_snapshot:
            self._rebase_admin_jurisdiction_dirty_tracking()
            return
        self._set_admin_jurisdiction_dirty(
            self._admin_jurisdiction_form_snapshot() != self._admin_jurisdiction_baseline_snapshot
        )

    def _on_admin_contact_form_changed(self, *_args: object) -> None:
        self._sync_admin_contact_dirty_state()

    def _on_admin_jurisdiction_form_changed(self, *_args: object) -> None:
        self._sync_admin_jurisdiction_dirty_state()

    def _on_admin_contacts_search_changed(self, *_args: object) -> None:
        self._refresh_admin_contacts_list(select_id=self._admin_selected_contact_id)

    def _on_admin_jurisdictions_search_changed(self, *_args: object) -> None:
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)

    def _on_templates_search_changed(self, *_args: object) -> None:
        self._refresh_templates_list(select_id=self._template_selected_id)

    def _admin_contact_panel_dirty_excluding_bundle_edit(self) -> bool:
        baseline = self._admin_contact_baseline_snapshot
        if not baseline:
            return False
        current = self._admin_contact_form_snapshot()
        if not isinstance(current, tuple) or not isinstance(baseline, tuple):
            return self._admin_contact_dirty and not self._admin_contact_bundle_edit_is_dirty()
        if len(current) != len(baseline):
            return self._admin_contact_dirty and not self._admin_contact_bundle_edit_is_dirty()
        # Ignore bundle-edit buffer fields while editing an existing bundle so the panel
        # check only tracks true form changes outside that temporary edit state.
        if self._admin_contact_editing_bundle_index >= 0:
            normalized_current = list(current)
            for index in (3, 4, 5, 6, 7):
                normalized_current[index] = baseline[index]
            return tuple(normalized_current) != baseline
        return current != baseline

    def _confirm_discard_admin_contact_panel_changes(self, *, action_label: str) -> bool:
        if not self._admin_contact_panel_dirty_excluding_bundle_edit():
            return True
        return self._confirm_dialog(
            "Unsaved Contact Changes",
            (
                "You have unsaved changes in the contact form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _confirm_discard_admin_view_changes(self, *, action_label: str) -> bool:
        if not self._confirm_discard_admin_contact_editor_changes(action_label=action_label):
            return False
        if not self._confirm_discard_admin_jurisdiction_changes(action_label=action_label):
            return False
        if not self._confirm_discard_template_changes(action_label=action_label):
            return False
        return True

    def _confirm_discard_admin_contact_editor_changes(self, *, action_label: str) -> bool:
        if self._admin_contact_bundle_edit_is_dirty():
            if not self._confirm_discard_admin_contact_bundle_changes(action_label=action_label):
                return False
        if not self._confirm_discard_admin_contact_panel_changes(action_label=action_label):
            return False
        return True

    def _confirm_discard_admin_contact_changes(self, *, action_label: str) -> bool:
        if not self._admin_contact_dirty:
            return True
        return self._confirm_dialog(
            "Unsaved Contact Changes",
            (
                "You have unsaved changes in the contact form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _confirm_discard_admin_jurisdiction_changes(self, *, action_label: str) -> bool:
        if not self._admin_jurisdiction_dirty:
            return True
        return self._confirm_dialog(
            "Unsaved Jurisdiction Changes",
            (
                "You have unsaved changes in the jurisdiction form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _on_admin_add_new_contact_clicked(self) -> None:
        self._admin_new_contact(require_confirm=True, action_label="Add New Contact")

    def _on_admin_add_new_jurisdiction_clicked(self) -> None:
        self._admin_new_jurisdiction(require_confirm=True, action_label="Add New Jurisdiction")

    def _admin_entity_color_values(self, entity_kind: str) -> tuple[str, str]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                normalize_list_color(self._admin_contact_list_color),
                normalize_list_color(self._admin_contact_custom_list_color),
            )
        if normalized_entity == "property":
            return (
                normalize_list_color(self._add_property_list_color),
                normalize_list_color(self._add_property_custom_list_color),
            )
        return (
            normalize_list_color(self._admin_jurisdiction_list_color),
            normalize_list_color(self._admin_jurisdiction_custom_list_color),
        )

    def _admin_set_entity_color_values(self, entity_kind: str, *, selected: str, custom: str) -> None:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            self._admin_contact_list_color = normalize_list_color(selected)
            self._admin_contact_custom_list_color = normalize_list_color(custom)
            return
        if normalized_entity == "property":
            self._add_property_list_color = normalize_list_color(selected)
            self._add_property_custom_list_color = normalize_list_color(custom)
            return
        self._admin_jurisdiction_list_color = normalize_list_color(selected)
        self._admin_jurisdiction_custom_list_color = normalize_list_color(custom)

    def _admin_entity_color_controls(
        self, entity_kind: str
    ) -> tuple[dict[str, QPushButton], QPushButton | None, QLabel | None]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                self._admin_contact_color_buttons,
                self._admin_contact_custom_color_dot,
                self._admin_contact_color_selected_label,
            )
        if normalized_entity == "property":
            return (
                self._add_property_color_buttons,
                self._add_property_custom_color_dot,
                self._add_property_color_selected_label,
            )
        return (
            self._admin_jurisdiction_color_buttons,
            self._admin_jurisdiction_custom_color_dot,
            self._admin_jurisdiction_color_selected_label,
        )

    def _admin_entity_color_picker_controls(
        self, entity_kind: str
    ) -> tuple[QPushButton | None, QWidget | None, QPropertyAnimation | None]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                self._admin_contact_color_toggle_button,
                self._admin_contact_color_content_host,
                self._admin_contact_color_animation,
            )
        if normalized_entity == "property":
            return (
                self._add_property_color_toggle_button,
                self._add_property_color_content_host,
                self._add_property_color_animation,
            )
        return (
            self._admin_jurisdiction_color_toggle_button,
            self._admin_jurisdiction_color_content_host,
            self._admin_jurisdiction_color_animation,
        )

    def _admin_entity_color_picker_is_open(self, entity_kind: str) -> bool:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return bool(self._admin_contact_color_picker_open)
        if normalized_entity == "property":
            return bool(self._add_property_color_picker_open)
        return bool(self._admin_jurisdiction_color_picker_open)

    def _admin_color_picker_content_target_height(self, host: QWidget | None) -> int:
        if host is None:
            return 0
        hint = host.sizeHint().height()
        if hint <= 0 and host.layout() is not None:
            hint = host.layout().sizeHint().height()
        return max(0, hint)

    def _set_admin_entity_color_picker_open(
        self,
        entity_kind: str,
        expanded: bool,
        *,
        animate: bool,
    ) -> None:
        normalized_entity = str(entity_kind or "").strip().casefold()
        toggle_button, content_host, animation = self._admin_entity_color_picker_controls(normalized_entity)
        if content_host is None or toggle_button is None:
            return
        target_height = self._admin_color_picker_content_target_height(content_host) if expanded else 0
        if animate and animation is not None:
            animation.stop()
            animation.setStartValue(content_host.maximumHeight())
            animation.setEndValue(target_height)
            animation.start()
        else:
            content_host.setMaximumHeight(target_height)

        if normalized_entity == "contact":
            self._admin_contact_color_picker_open = bool(expanded)
        elif normalized_entity == "property":
            self._add_property_color_picker_open = bool(expanded)
        else:
            self._admin_jurisdiction_color_picker_open = bool(expanded)
        toggle_button.setText("v" if expanded else "^")
        toggle_button.setToolTip("Collapse list color picker" if expanded else "Expand list color picker")

    def _toggle_admin_entity_color_picker(self, entity_kind: str) -> None:
        self._set_admin_entity_color_picker_open(
            entity_kind,
            not self._admin_entity_color_picker_is_open(entity_kind),
            animate=True,
        )

    def _set_admin_color_dot_button_style(
        self,
        button: QPushButton,
        *,
        color_hex: str,
        selected: bool,
        placeholder: bool = False,
    ) -> None:
        normalized = normalize_list_color(color_hex)
        if placeholder or not normalized:
            border = "rgba(99, 124, 150, 186)"
            if selected:
                border = "rgba(214, 230, 247, 234)"
            button.setStyleSheet(
                (
                    "QPushButton {"
                    "border-radius: 10px;"
                    f"border: 2px dashed {border};"
                    "background: rgba(0, 0, 0, 0);"
                    "padding: 0px;"
                    "}"
                    "QPushButton:hover {"
                    f"border: 2px dashed {border};"
                    "background: rgba(0, 0, 0, 0);"
                    "}"
                )
            )
            button.setText("")
            return

        channels = _normalize_card_tint_channels(_hex_color_channels(normalized))
        border = _dot_ring_color(channels, selected=selected)
        hover_border = _dot_ring_color(channels, selected=True)
        border_width = 3 if selected else 1
        button.setStyleSheet(
            (
                "QPushButton {"
                "border-radius: 10px;"
                f"border: {border_width}px solid {border};"
                f"background: {_rgba_text(channels, 255)};"
                "padding: 0px;"
                "}"
                "QPushButton:hover {"
                f"border: {max(2, border_width)}px solid {hover_border};"
                f"background: {_rgba_text(channels, 255)};"
                "}"
                "QPushButton:disabled {"
                "border: 1px dashed rgba(121, 146, 173, 186);"
                f"background: {_rgba_text(channels, 104)};"
                "}"
            )
        )
        button.setText("")

    def _refresh_admin_color_picker_controls(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        color_buttons, custom_dot, selected_label = self._admin_entity_color_controls(entity_kind)
        preset_set = set(_ADMIN_LIST_COLOR_PRESETS)
        for color_hex, button in color_buttons.items():
            self._set_admin_color_dot_button_style(
                button,
                color_hex=color_hex,
                selected=(selected == color_hex),
            )
            button.setToolTip(f"Use {color_hex}")

        if custom_dot is not None:
            if custom:
                custom_dot.setEnabled(True)
                self._set_admin_color_dot_button_style(
                    custom_dot,
                    color_hex=custom,
                    selected=(selected == custom),
                )
                custom_dot.setToolTip(f"Use custom color {custom}")
            else:
                custom_dot.setEnabled(False)
                self._set_admin_color_dot_button_style(
                    custom_dot,
                    color_hex="",
                    selected=False,
                    placeholder=True,
                )
                custom_dot.setToolTip("Pick a custom color first.")

        if selected_label is not None:
            if selected:
                selected_suffix = " (custom)" if selected == custom and selected not in preset_set else ""
                selected_label.setText(f"Selected: {selected}{selected_suffix}")
            else:
                selected_label.setText("Selected: Theme default")
        self._set_admin_entity_color_picker_open(
            entity_kind,
            self._admin_entity_color_picker_is_open(entity_kind),
            animate=False,
        )

    def _set_admin_entity_list_color(
        self,
        *,
        entity_kind: str,
        color_hex: str,
        custom_color: str | None = None,
        notify: bool,
    ) -> None:
        selected_current, custom_current = self._admin_entity_color_values(entity_kind)
        selected = normalize_list_color(color_hex)
        if custom_color is None:
            custom = custom_current
        else:
            custom = normalize_list_color(custom_color)

        if selected and selected not in _ADMIN_LIST_COLOR_PRESETS and not custom:
            custom = selected
        if not selected and custom_color == "":
            custom = ""

        if selected == selected_current and custom == custom_current:
            self._refresh_admin_color_picker_controls(entity_kind)
            return

        self._admin_set_entity_color_values(entity_kind, selected=selected, custom=custom)
        self._refresh_admin_color_picker_controls(entity_kind)
        if not notify:
            return
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            self._on_admin_contact_form_changed()
        elif normalized_entity == "property":
            self._on_inline_property_form_changed()
        else:
            self._on_admin_jurisdiction_form_changed()

    def _on_admin_color_preset_clicked(self, entity_kind: str, color_hex: str) -> None:
        selected, _custom = self._admin_entity_color_values(entity_kind)
        normalized = normalize_list_color(color_hex)
        if not normalized:
            return
        next_color = "" if selected == normalized else normalized
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=next_color,
            notify=True,
        )

    def _on_admin_custom_color_dot_clicked(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        if not custom:
            return
        next_color = "" if selected == custom else custom
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=next_color,
            notify=True,
        )

    def _open_admin_custom_color_picker(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        initial_hex = selected or custom or _ADMIN_LIST_COLOR_PRESETS[0]
        initial_color = QColor(initial_hex)
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            title_prefix = "Contact"
        elif normalized_entity == "property":
            title_prefix = "Address"
        else:
            title_prefix = "Jurisdiction"
        picked = QColorDialog.getColor(initial_color, self, f"Pick {title_prefix} List Color")
        if not picked.isValid():
            return
        picked_hex = normalize_list_color(picked.name(QColor.NameFormat.HexRgb))
        if not picked_hex:
            return
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=picked_hex,
            custom_color=picked_hex,
            notify=True,
        )

    def _build_admin_color_picker_widget(self, *, parent: QWidget, entity_kind: str) -> QWidget:
        normalized_entity = str(entity_kind or "").strip().casefold()
        host = QWidget(parent)
        if normalized_entity == "property":
            host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            host.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)

        toggle_host = QWidget(host)
        toggle_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toggle_row = QHBoxLayout(toggle_host)
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(6)
        toggle_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        toggle_button = QPushButton("^", toggle_host)
        toggle_button.setObjectName("TrackerPanelActionButton")
        toggle_button.setFixedSize(34, 30)
        toggle_button.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._toggle_admin_entity_color_picker(kind)
        )
        toggle_row.addStretch(1)
        toggle_row.addWidget(toggle_button, 0)
        host_layout.addWidget(toggle_host, 0)

        content_host = QWidget(host)
        if normalized_entity == "property":
            content_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            content_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout = QVBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)

        presets_host = QWidget(content_host)
        presets_layout = QGridLayout(presets_host)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.setHorizontalSpacing(6)
        presets_layout.setVerticalSpacing(6)

        color_buttons: dict[str, QPushButton] = {}
        for index, color_hex in enumerate(_ADMIN_LIST_COLOR_PRESETS):
            dot_button = QPushButton("", presets_host)
            dot_button.setFixedSize(22, 22)
            dot_button.setCursor(Qt.CursorShape.PointingHandCursor)
            dot_button.clicked.connect(
                lambda _checked=False, kind=normalized_entity, value=color_hex: self._on_admin_color_preset_clicked(
                    kind, value
                )
            )
            color_buttons[color_hex] = dot_button
            presets_layout.addWidget(dot_button, index // 6, index % 6)
        content_layout.addWidget(presets_host, 0)

        custom_host = QWidget(content_host)
        custom_row = QHBoxLayout(custom_host)
        custom_row.setContentsMargins(0, 0, 0, 0)
        custom_row.setSpacing(8)

        custom_button = QPushButton("Custom Color...", custom_host)
        custom_button.setObjectName("TrackerPanelActionButton")
        custom_button.setMinimumHeight(30)
        custom_button.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._open_admin_custom_color_picker(kind)
        )
        custom_row.addWidget(custom_button, 0)

        custom_dot = QPushButton("", custom_host)
        custom_dot.setFixedSize(22, 22)
        custom_dot.setCursor(Qt.CursorShape.PointingHandCursor)
        custom_dot.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._on_admin_custom_color_dot_clicked(kind)
        )
        custom_row.addWidget(custom_dot, 0)

        selected_label = QLabel("Selected: Theme default", custom_host)
        selected_label.setObjectName("TrackerPanelMeta")
        selected_label.setWordWrap(True)
        custom_row.addWidget(selected_label, 1)
        content_layout.addWidget(custom_host, 0)
        host_layout.addWidget(content_host, 0)

        picker_animation = QPropertyAnimation(content_host, b"maximumHeight", content_host)
        picker_animation.setDuration(170)
        picker_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        if normalized_entity == "contact":
            self._admin_contact_color_buttons = color_buttons
            self._admin_contact_custom_color_button = custom_button
            self._admin_contact_custom_color_dot = custom_dot
            self._admin_contact_color_selected_label = selected_label
            self._admin_contact_color_toggle_button = toggle_button
            self._admin_contact_color_content_host = content_host
            self._admin_contact_color_animation = picker_animation
        elif normalized_entity == "jurisdiction":
            self._admin_jurisdiction_color_buttons = color_buttons
            self._admin_jurisdiction_custom_color_button = custom_button
            self._admin_jurisdiction_custom_color_dot = custom_dot
            self._admin_jurisdiction_color_selected_label = selected_label
            self._admin_jurisdiction_color_toggle_button = toggle_button
            self._admin_jurisdiction_color_content_host = content_host
            self._admin_jurisdiction_color_animation = picker_animation
        else:
            self._add_property_color_buttons = color_buttons
            self._add_property_custom_color_button = custom_button
            self._add_property_custom_color_dot = custom_dot
            self._add_property_color_selected_label = selected_label
            self._add_property_color_toggle_button = toggle_button
            self._add_property_color_content_host = content_host
            self._add_property_color_animation = picker_animation
        self._set_admin_entity_color_picker_open(normalized_entity, False, animate=False)
        self._refresh_admin_color_picker_controls(normalized_entity)
        return host

    def _apply_admin_entity_card_color_style(self, card: QFrame, *, color_hex: str) -> None:
        normalized = normalize_list_color(color_hex)
        if not normalized:
            card.setStyleSheet("")
            return

        tint = _normalize_card_tint_channels(_hex_color_channels(normalized))
        border_idle = _mix_color_channels(tint, (26, 38, 52), 0.22)
        border_hover = _mix_color_channels(tint, (26, 38, 52), 0.12)
        border_selected = _mix_color_channels(tint, (244, 250, 255), 0.18)

        card.setStyleSheet(
            (
                f"QFrame#TrackerListCard {{"
                f"border: 1px solid {_rgba_text(border_idle, 188)};"
                f"background: {_rgba_text(tint, 42)};"
                "}"
                f"QFrame#TrackerListCard:hover {{"
                f"border: 1px solid {_rgba_text(border_hover, 214)};"
                f"background: {_rgba_text(tint, 60)};"
                "}"
                f"QFrame#TrackerListCard[selected=\"true\"] {{"
                f"border: 1px solid {_rgba_text(border_selected, 238)};"
                f"background: {_rgba_text(tint, 88)};"
                "}"
            )
        )

    def _build_admin_entity_card(
        self,
        *,
        title: str,
        title_field: str,
        subtitle: str,
        subtitle_field: str,
        meta: str,
        meta_field: str,
        accent_color: str = "",
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("TrackerListCard")
        card.setProperty("selected", "false")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setFixedHeight(106)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        title_label = QLabel(str(title or "").replace("\n", " "), card)
        title_label.setObjectName("TrackerListFieldValue")
        title_label.setProperty("field", title_field)
        title_label.setWordWrap(False)
        layout.addWidget(title_label, 0)

        if subtitle.strip():
            subtitle_label = QLabel(str(subtitle or "").replace("\n", " "), card)
            subtitle_label.setObjectName("TrackerListFieldValue")
            subtitle_label.setProperty("field", subtitle_field)
            subtitle_label.setWordWrap(False)
            layout.addWidget(subtitle_label, 0)

        if meta.strip():
            meta_label = QLabel(str(meta or "").replace("\n", " "), card)
            meta_label.setObjectName("TrackerListFieldValue")
            meta_label.setProperty("field", meta_field)
            meta_label.setWordWrap(False)
            layout.addWidget(meta_label, 0)

        self._apply_admin_entity_card_color_style(card, color_hex=accent_color)
        return card

    def _build_tracker_entity_card(
        self,
        *,
        title: str,
        title_field: str,
        subtitle: str,
        subtitle_field: str,
        meta: str,
        meta_field: str,
        accent_color: str = "",
        on_edit: Callable[[], None] | None = None,
        on_remove: Callable[[], None] | None = None,
    ) -> QFrame:
        card = TrackerHoverEntityCard(
            title=title,
            title_field=title_field,
            subtitle=subtitle,
            subtitle_field=subtitle_field,
            meta=meta,
            meta_field=meta_field,
            on_edit=on_edit,
            on_remove=on_remove,
        )
        self._apply_admin_entity_card_color_style(card, color_hex=accent_color)
        return card

    def _build_admin_input_shell(
        self,
        *,
        label_text: str,
        field_widget: QWidget,
        parent: QWidget,
        shell_bucket: list[QFrame] | None = None,
        field_stretch: int = 1,
        left_align_field: bool = False,
    ) -> QFrame:
        shell = QFrame(parent)
        shell.setObjectName("AdminInputShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(10, 6, 10, 6)
        shell_layout.setSpacing(10)
        label = QLabel(str(label_text or "").strip(), shell)
        label.setObjectName("AdminInputShellLabel")
        label.setMinimumWidth(136)
        shell_layout.addWidget(label, 0)
        if left_align_field:
            shell_layout.addWidget(
                field_widget,
                max(0, int(field_stretch)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            shell_layout.addStretch(1)
        else:
            shell_layout.addWidget(field_widget, max(0, int(field_stretch)))
        if shell_bucket is not None:
            shell_bucket.append(shell)
        return shell

    def _set_admin_list_card_selection(self, widget: QListWidget | None) -> None:
        if widget is None:
            return
        for index in range(widget.count()):
            item = widget.item(index)
            card = widget.itemWidget(item)
            if card is None:
                continue
            selected_value = "true" if item.isSelected() else "false"
            if str(card.property("selected") or "") == selected_value:
                continue
            card.setProperty("selected", selected_value)
            style = card.style()
            style.unpolish(card)
            style.polish(card)
            card.update()

    def _admin_contact_bundle_fields_target_height(self) -> int:
        host = self._admin_contact_bundle_fields_host
        if host is None:
            return 0
        hint = host.sizeHint().height()
        if hint <= 0 and host.layout() is not None:
            hint = host.layout().sizeHint().height()
        return max(0, hint)

    def _set_admin_contact_bundle_fields_open(self, expanded: bool, *, animate: bool) -> None:
        host = self._admin_contact_bundle_fields_host
        toggle_button = self._admin_contact_bundle_toggle_button
        if host is None or toggle_button is None:
            return
        target_height = self._admin_contact_bundle_fields_target_height() if expanded else 0
        animation = self._admin_contact_bundle_fields_animation
        if animate and animation is not None:
            animation.stop()
            animation.setStartValue(host.maximumHeight())
            animation.setEndValue(target_height)
            animation.start()
        else:
            host.setMaximumHeight(target_height)
        self._admin_contact_bundle_fields_open = bool(expanded)
        toggle_button.setText("v" if expanded else ">")
        toggle_button.setToolTip("Collapse bundle inputs" if expanded else "Expand bundle inputs")

    def _toggle_admin_contact_bundle_fields(self) -> None:
        self._set_admin_contact_bundle_fields_open(
            not self._admin_contact_bundle_fields_open,
            animate=True,
        )

    def _contact_methods_from_record(self, contact: ContactRecord) -> list[ContactMethodRecord]:
        methods: list[ContactMethodRecord] = []
        for entry in contact.contact_methods:
            label = str(entry.label or "").strip()
            emails = _parse_multi_values(_join_multi_values(entry.emails))
            numbers = _parse_multi_values(_join_multi_values(entry.numbers))
            note = str(entry.note or "").strip()
            if not any((label, emails, numbers, note)):
                continue
            methods.append(ContactMethodRecord(label=label, emails=emails, numbers=numbers, note=note))
        if methods:
            return methods
        if contact.emails or contact.numbers:
            return [
                ContactMethodRecord(
                    label="",
                    emails=_parse_multi_values(_join_multi_values(contact.emails)),
                    numbers=_parse_multi_values(_join_multi_values(contact.numbers)),
                    note="",
                )
            ]
        return []

    def _refresh_admin_contact_methods_list(self) -> None:
        host = self._admin_contact_methods_host
        if host is None:
            return
        if self._admin_contact_methods_label is not None:
            self._admin_contact_methods_label.setText(
                f"Email + Number Bundles ({len(self._admin_contact_method_rows)})"
            )
        layout = host.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        if not self._admin_contact_method_rows:
            empty_label = QLabel("No contact bundles yet. Add one above.", host)
            empty_label.setObjectName("TrackerPanelMeta")
            layout.addWidget(empty_label, 0)
            layout.addStretch(1)
            self._sync_admin_contact_bundle_action_state()
            return

        for index, method in enumerate(self._admin_contact_method_rows):
            chip = AttachedContactChip(
                title=self._contact_method_title(method),
                detail_lines=self._contact_method_summary_lines(method),
                metadata_layout="contact_bundle_values",
                on_edit=lambda row_index=index: self._admin_edit_contact_method_bundle(row_index),
                edit_tooltip="Edit bundle",
                on_remove=lambda row_index=index: self._admin_remove_contact_method_bundle(row_index),
                remove_tooltip="Remove bundle",
                parent=host,
            )
            layout.addWidget(chip, 0)
        layout.addStretch(1)
        self._sync_admin_contact_bundle_action_state()

    def _contact_method_summary_lines(self, method: ContactMethodRecord) -> list[str]:
        emails_text = ", ".join(method.emails) if method.emails else "No emails"
        numbers_text = ", ".join(method.numbers) if method.numbers else "No numbers"
        detail_lines = [f"Email(s): {emails_text}", f"Number(s): {numbers_text}"]
        note = str(method.note or "").strip()
        if note:
            detail_lines.append(f"Note: {note}")
        return detail_lines

    def _contact_method_summary_line(self, method: ContactMethodRecord) -> str:
        return " | ".join(self._contact_method_summary_lines(method))

    def _contact_method_title(self, method: ContactMethodRecord) -> str:
        label = str(method.label or "").strip()
        if label:
            return label
        return "Unnamed Bundle"

    def _admin_contact_bundle_inputs_snapshot(self) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        label = self._admin_contact_bundle_name_input.text().strip() if self._admin_contact_bundle_name_input else ""
        emails = (
            tuple(_parse_multi_values(self._admin_contact_emails_input.text()))
            if self._admin_contact_emails_input
            else tuple()
        )
        numbers = (
            tuple(_parse_multi_values(self._admin_contact_numbers_input.text()))
            if self._admin_contact_numbers_input
            else tuple()
        )
        note = self._admin_contact_note_input.text().strip() if self._admin_contact_note_input else ""
        return (label, emails, numbers, note)

    def _contact_method_snapshot(self, method: ContactMethodRecord) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        label = str(method.label or "").strip()
        emails = tuple(_parse_multi_values(_join_multi_values(method.emails)))
        numbers = tuple(_parse_multi_values(_join_multi_values(method.numbers)))
        note = str(method.note or "").strip()
        return (label, emails, numbers, note)

    def _admin_contact_bundle_edit_is_dirty(self) -> bool:
        edit_index = self._admin_contact_editing_bundle_index
        if edit_index < 0 or edit_index >= len(self._admin_contact_method_rows):
            return False
        target_snapshot = self._contact_method_snapshot(self._admin_contact_method_rows[edit_index])
        pending_snapshot = self._admin_contact_bundle_inputs_snapshot()
        return pending_snapshot != target_snapshot

    def _confirm_discard_admin_contact_bundle_changes(self, *, action_label: str) -> bool:
        if not self._admin_contact_bundle_edit_is_dirty():
            return True
        return self._confirm_dialog(
            "Unsaved Bundle Changes",
            (
                "You have unsaved changes in the current bundle edit. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _pending_contact_method_from_inputs(self) -> ContactMethodRecord | None:
        label = self._admin_contact_bundle_name_input.text().strip() if self._admin_contact_bundle_name_input else ""
        emails_raw = self._admin_contact_emails_input.text() if self._admin_contact_emails_input else ""
        numbers_raw = self._admin_contact_numbers_input.text() if self._admin_contact_numbers_input else ""
        note = self._admin_contact_note_input.text().strip() if self._admin_contact_note_input else ""
        emails = _parse_multi_values(emails_raw)
        numbers = _parse_multi_values(numbers_raw)
        if not any((label, emails, numbers, note)):
            return None
        return ContactMethodRecord(
            label=label,
            emails=emails,
            numbers=numbers,
            note=note,
        )

    def _set_pending_contact_method_inputs(self, method: ContactMethodRecord) -> None:
        if self._admin_contact_bundle_name_input is not None:
            self._admin_contact_bundle_name_input.setText(str(method.label or "").strip())
        if self._admin_contact_emails_input is not None:
            self._admin_contact_emails_input.setText(_join_multi_values(method.emails))
        if self._admin_contact_numbers_input is not None:
            self._admin_contact_numbers_input.setText(_join_multi_values(method.numbers))
        if self._admin_contact_note_input is not None:
            self._admin_contact_note_input.setText(str(method.note or "").strip())

    def _clear_pending_contact_method_inputs(self) -> None:
        if self._admin_contact_bundle_name_input is not None:
            self._admin_contact_bundle_name_input.clear()
        if self._admin_contact_emails_input is not None:
            self._admin_contact_emails_input.clear()
        if self._admin_contact_numbers_input is not None:
            self._admin_contact_numbers_input.clear()
        if self._admin_contact_note_input is not None:
            self._admin_contact_note_input.clear()

    def _sync_admin_contact_bundle_action_state(self) -> None:
        is_editing = 0 <= self._admin_contact_editing_bundle_index < len(self._admin_contact_method_rows)
        if self._admin_contact_add_method_button is not None:
            self._admin_contact_add_method_button.setText("Update Bundle" if is_editing else "Add Bundle")
            self._admin_contact_add_method_button.setProperty("bundleEditing", "true" if is_editing else "false")
            add_style = self._admin_contact_add_method_button.style()
            add_style.unpolish(self._admin_contact_add_method_button)
            add_style.polish(self._admin_contact_add_method_button)
            self._admin_contact_add_method_button.update()
        if self._admin_contact_cancel_method_button is not None:
            self._admin_contact_cancel_method_button.setVisible(is_editing)
            self._admin_contact_cancel_method_button.setProperty("bundleEditing", "true" if is_editing else "false")
            cancel_style = self._admin_contact_cancel_method_button.style()
            cancel_style.unpolish(self._admin_contact_cancel_method_button)
            cancel_style.polish(self._admin_contact_cancel_method_button)
            self._admin_contact_cancel_method_button.update()

    def _admin_edit_contact_method_bundle(self, bundle_index: int) -> None:
        if bundle_index < 0 or bundle_index >= len(self._admin_contact_method_rows):
            return
        if self._admin_contact_editing_bundle_index >= 0 and self._admin_contact_editing_bundle_index != bundle_index:
            if not self._confirm_discard_admin_contact_bundle_changes(action_label="Edit Another Bundle"):
                return
        if self._admin_contact_editing_bundle_index == bundle_index:
            if self._admin_contact_bundle_name_input is not None:
                self._admin_contact_bundle_name_input.setFocus()
            return
        self._admin_contact_form_loading = True
        try:
            self._admin_contact_editing_bundle_index = bundle_index
            self._set_pending_contact_method_inputs(self._admin_contact_method_rows[bundle_index])
            if not self._admin_contact_bundle_fields_open:
                self._set_admin_contact_bundle_fields_open(True, animate=True)
            self._sync_admin_contact_bundle_action_state()
        finally:
            self._admin_contact_form_loading = False
        if self._admin_contact_bundle_name_input is not None:
            self._admin_contact_bundle_name_input.setFocus()

    def _admin_cancel_contact_method_edit(self) -> None:
        if self._admin_contact_editing_bundle_index < 0:
            return
        if not self._confirm_discard_admin_contact_bundle_changes(action_label="Cancel Edit"):
            return
        self._admin_contact_form_loading = True
        try:
            self._admin_contact_editing_bundle_index = -1
            self._clear_pending_contact_method_inputs()
            self._sync_admin_contact_bundle_action_state()
        finally:
            self._admin_contact_form_loading = False
        self._sync_admin_contact_dirty_state()

    def _apply_pending_contact_method(self) -> bool:
        pending = self._pending_contact_method_from_inputs()
        if pending is None:
            return False
        edit_index = self._admin_contact_editing_bundle_index
        if 0 <= edit_index < len(self._admin_contact_method_rows):
            self._admin_contact_method_rows[edit_index] = pending
        else:
            self._admin_contact_method_rows.append(pending)
        self._admin_contact_editing_bundle_index = -1
        self._clear_pending_contact_method_inputs()
        self._sync_admin_contact_bundle_action_state()
        return True

    def _admin_add_contact_method_bundle(self) -> None:
        pending = self._pending_contact_method_from_inputs()
        if pending is None:
            if not self._admin_contact_bundle_fields_open:
                self._set_admin_contact_bundle_fields_open(True, animate=True)
                if self._admin_contact_bundle_name_input is not None:
                    self._admin_contact_bundle_name_input.setFocus()
            self._show_info_dialog(
                "Contact Bundle",
                "Add a bundle name, an email, a number, or a note before saving the bundle.",
            )
            return
        self._apply_pending_contact_method()
        self._refresh_admin_contact_methods_list()
        self._sync_admin_contact_dirty_state()

    def _admin_remove_contact_method_bundle(self, bundle_index: int) -> None:
        if bundle_index < 0 or bundle_index >= len(self._admin_contact_method_rows):
            return
        target_bundle = self._admin_contact_method_rows[bundle_index]
        bundle_title = self._contact_method_title(target_bundle)
        delete_message = f"Delete bundle '{bundle_title}'?"
        if self._admin_contact_editing_bundle_index == bundle_index and self._admin_contact_bundle_edit_is_dirty():
            delete_message = f"{delete_message}\n\nUnsaved bundle edits will be lost."
        if not self._confirm_dialog(
            "Delete Bundle",
            delete_message,
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        ):
            return
        self._admin_contact_method_rows.pop(bundle_index)
        if self._admin_contact_editing_bundle_index == bundle_index:
            self._admin_contact_editing_bundle_index = -1
            self._clear_pending_contact_method_inputs()
        elif self._admin_contact_editing_bundle_index > bundle_index:
            self._admin_contact_editing_bundle_index -= 1
        self._sync_admin_contact_bundle_action_state()
        self._refresh_admin_contact_methods_list()
        self._sync_admin_contact_dirty_state()

    def _admin_set_contact_form_mode(self, *, editing: bool) -> None:
        self._admin_contact_editing_mode = bool(editing)
        if self._admin_contact_mode_label is not None:
            mode_text = "Adding New Contact"
            if editing:
                contact_name = ""
                contact = self._contact_by_id(self._admin_selected_contact_id)
                if contact is not None:
                    contact_name = contact.name.strip()
                elif self._admin_contact_name_input is not None:
                    contact_name = self._admin_contact_name_input.text().strip()
                mode_text = f"Editing Contact: {contact_name or '(Unnamed)'}"
            self._admin_contact_mode_label.setText(mode_text)
        if self._admin_contact_save_button is not None:
            self._admin_contact_save_button.setText("Update Contact" if editing else "Create Contact")
        if self._admin_contact_delete_button is not None:
            self._admin_contact_delete_button.setEnabled(bool(editing))

    def _admin_set_jurisdiction_form_mode(self, *, editing: bool) -> None:
        self._admin_jurisdiction_editing_mode = bool(editing)
        if self._admin_jurisdiction_mode_label is not None:
            mode_text = "Adding New Jurisdiction"
            if editing:
                jurisdiction_name = ""
                jurisdiction = self._jurisdiction_by_id(self._admin_selected_jurisdiction_id)
                if jurisdiction is not None:
                    jurisdiction_name = jurisdiction.name.strip()
                elif self._admin_jurisdiction_name_input is not None:
                    jurisdiction_name = self._admin_jurisdiction_name_input.text().strip()
                mode_text = f"Editing Jurisdiction: {jurisdiction_name or '(Unnamed)'}"
            self._admin_jurisdiction_mode_label.setText(mode_text)
        if self._admin_jurisdiction_save_button is not None:
            self._admin_jurisdiction_save_button.setText(
                "Update Jurisdiction" if editing else "Create Jurisdiction"
            )
        if self._admin_jurisdiction_delete_button is not None:
            self._admin_jurisdiction_delete_button.setEnabled(bool(editing))

    def _refresh_admin_contacts_list(self, *, select_id: str = "") -> None:
        widget = self._admin_contacts_list_widget
        if widget is None:
            return
        total_contacts = len(self._contacts)
        search = self._current_search(self._admin_contacts_search_input)
        widget.blockSignals(True)
        widget.clear()
        shown_contacts = 0
        for contact in sorted(self._contacts, key=lambda row: (row.name.casefold(), row.contact_id)):
            method_rows = self._contact_methods_from_record(contact)
            search_parts = [contact.name, _join_multi_values(contact.roles)]
            for row in method_rows:
                search_parts.extend((row.note, _join_multi_values(row.emails), _join_multi_values(row.numbers)))
            if search:
                haystack = " | ".join(str(part or "").casefold() for part in search_parts)
                if search not in haystack:
                    continue
            role_summary = ", ".join(contact.roles[:3]) if contact.roles else "No roles assigned"
            if len(contact.roles) > 3:
                role_summary = f"{role_summary}, +{len(contact.roles) - 3}"
            if method_rows:
                primary_email = method_rows[0].emails[0] if method_rows[0].emails else "No email"
                primary_number = method_rows[0].numbers[0] if method_rows[0].numbers else "No number"
            else:
                primary_email = contact.emails[0] if contact.emails else "No email"
                primary_number = contact.numbers[0] if contact.numbers else "No number"

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, contact.contact_id)
            widget.addItem(item)
            card = self._build_admin_entity_card(
                title=contact.name or "(Unnamed)",
                title_field="client",
                subtitle=f"Roles: {role_summary} | Bundles: {len(method_rows)}",
                subtitle_field="request",
                meta=f"{primary_email} | {primary_number}",
                meta_field="email",
                accent_color=contact.list_color,
            )
            widget.setItemWidget(item, card)
            # Re-apply the enforced row hint after setItemWidget(). Qt can replace
            # item hints with the embedded widget's raw sizeHint, causing overlap.
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            shown_contacts += 1
        widget.blockSignals(False)
        if self._admin_contacts_count_label is not None:
            noun = "contact" if total_contacts == 1 else "contacts"
            if search:
                self._admin_contacts_count_label.setText(f"{shown_contacts} of {total_contacts} {noun}")
            else:
                self._admin_contacts_count_label.setText(f"{total_contacts} {noun}")
        if self._admin_contacts_list_stack is not None and self._admin_contacts_empty_label is not None:
            if shown_contacts > 0:
                self._admin_contacts_list_stack.setCurrentWidget(widget)
            else:
                if total_contacts == 0:
                    self._admin_contacts_empty_label.setText(
                        "No contacts yet.\nUse Add New Contact to get started."
                    )
                elif search:
                    self._admin_contacts_empty_label.setText(
                        "No contacts match this search.\nTry another term or clear the search."
                    )
                else:
                    self._admin_contacts_empty_label.setText(
                        "No contacts available.\nUse Add New Contact to get started."
                    )
                self._admin_contacts_list_stack.setCurrentWidget(self._admin_contacts_empty_label)
        if not select_id:
            self._set_admin_list_card_selection(widget)
            return
        if not self._select_admin_contact_item(select_id):
            if search:
                self._set_admin_list_card_selection(widget)
                return
            self._admin_selected_contact_id = ""
            self._admin_set_contact_form_mode(editing=False)
        self._set_admin_list_card_selection(widget)

    def _refresh_admin_jurisdictions_list(self, *, select_id: str = "") -> None:
        widget = self._admin_jurisdictions_list_widget
        if widget is None:
            return
        total_jurisdictions = len(self._jurisdictions)
        search = self._current_search(self._admin_jurisdictions_search_input)
        widget.blockSignals(True)
        widget.clear()
        shown_jurisdictions = 0
        for jurisdiction in sorted(self._jurisdictions, key=lambda row: (row.name.casefold(), row.jurisdiction_id)):
            if search:
                contact_names = [
                    contact.name
                    for contact_id in jurisdiction.contact_ids
                    for contact in [self._contact_by_id(contact_id)]
                    if contact is not None
                ]
                haystack = " | ".join(
                    part.casefold()
                    for part in (
                        jurisdiction.name,
                        jurisdiction.jurisdiction_type,
                        jurisdiction.parent_county,
                        _join_multi_values(jurisdiction.portal_urls),
                        jurisdiction.portal_vendor,
                        jurisdiction.notes,
                        _join_multi_values(contact_names),
                    )
                    if str(part or "").strip()
                )
                if search not in haystack:
                    continue
            subtitle = f"{jurisdiction.jurisdiction_type.title()} jurisdiction"
            meta_parts = [f"{len(jurisdiction.contact_ids)} contact(s)"]
            if jurisdiction.portal_urls:
                meta_parts.append(jurisdiction.portal_urls[0])
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, jurisdiction.jurisdiction_id)
            widget.addItem(item)
            card = self._build_admin_entity_card(
                title=jurisdiction.name or "(Unnamed)",
                title_field="county",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=" | ".join(meta_parts),
                meta_field="url",
                accent_color=jurisdiction.list_color,
            )
            widget.setItemWidget(item, card)
            # Re-apply the enforced row hint after setItemWidget(). Qt can replace
            # item hints with the embedded widget's raw sizeHint, causing overlap.
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            shown_jurisdictions += 1
        widget.blockSignals(False)
        if self._admin_jurisdictions_count_label is not None:
            noun = "jurisdiction" if total_jurisdictions == 1 else "jurisdictions"
            if search:
                self._admin_jurisdictions_count_label.setText(
                    f"{shown_jurisdictions} of {total_jurisdictions} {noun}"
                )
            else:
                self._admin_jurisdictions_count_label.setText(f"{total_jurisdictions} {noun}")
        if self._admin_jurisdictions_list_stack is not None and self._admin_jurisdictions_empty_label is not None:
            if shown_jurisdictions > 0:
                self._admin_jurisdictions_list_stack.setCurrentWidget(widget)
            else:
                if total_jurisdictions == 0:
                    self._admin_jurisdictions_empty_label.setText(
                        "No jurisdictions yet.\nUse Add New Jurisdiction to get started."
                    )
                elif search:
                    self._admin_jurisdictions_empty_label.setText(
                        "No jurisdictions match this search.\nTry another term or clear the search."
                    )
                else:
                    self._admin_jurisdictions_empty_label.setText(
                        "No jurisdictions available.\nUse Add New Jurisdiction to get started."
                    )
                self._admin_jurisdictions_list_stack.setCurrentWidget(self._admin_jurisdictions_empty_label)
        if not select_id:
            self._set_admin_list_card_selection(widget)
            return
        if not self._select_admin_jurisdiction_item(select_id):
            if search:
                self._set_admin_list_card_selection(widget)
                return
            self._admin_selected_jurisdiction_id = ""
            self._admin_set_jurisdiction_form_mode(editing=False)
        self._set_admin_list_card_selection(widget)

    def _refresh_admin_jurisdiction_contacts_picker(self, *, selected_ids: Sequence[str]) -> None:
        combo = self._admin_jurisdiction_contact_picker_combo
        host = self._admin_jurisdiction_attached_contacts_host
        if combo is None or host is None:
            return

        existing_ids = {record.contact_id for record in self._contacts}
        normalized_ids: list[str] = []
        seen_ids: set[str] = set()
        for raw_id in selected_ids:
            contact_id = str(raw_id or "").strip()
            if not contact_id or contact_id in seen_ids:
                continue
            if contact_id not in existing_ids:
                continue
            seen_ids.add(contact_id)
            normalized_ids.append(contact_id)
        self._admin_jurisdiction_attached_contact_ids = normalized_ids
        if self._admin_jurisdiction_attached_label is not None:
            self._admin_jurisdiction_attached_label.setText(
                f"Attached Contacts ({len(self._admin_jurisdiction_attached_contact_ids)})"
            )

        combo.blockSignals(True)
        combo.clear()
        default_label = "Select contact to attach..." if self._contacts else "No contacts available yet"
        combo.addItem(default_label, "")
        for contact in sorted(self._contacts, key=lambda row: (row.name.casefold(), row.contact_id)):
            method_rows = self._contact_methods_from_record(contact)
            detail_parts: list[str] = []
            if method_rows and method_rows[0].emails:
                detail_parts.append(method_rows[0].emails[0])
            elif contact.emails:
                detail_parts.append(contact.emails[0])
            if method_rows and method_rows[0].numbers:
                detail_parts.append(method_rows[0].numbers[0])
            elif contact.numbers:
                detail_parts.append(contact.numbers[0])
            details = f" ({' | '.join(detail_parts)})" if detail_parts else ""
            combo.addItem(f"{contact.name or '(Unnamed)'}{details}", contact.contact_id)
        combo.setCurrentIndex(0)
        combo.setEnabled(bool(self._contacts))
        combo.blockSignals(False)

        if self._admin_jurisdiction_contact_add_button is not None:
            self._admin_jurisdiction_contact_add_button.setEnabled(bool(self._contacts))

        layout = host.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        if not self._admin_jurisdiction_attached_contact_ids:
            empty_label = QLabel("No contacts attached yet.", host)
            empty_label.setObjectName("TrackerPanelMeta")
            layout.addWidget(empty_label, 0)
            layout.addStretch(1)
            return

        for contact_id in self._admin_jurisdiction_attached_contact_ids:
            contact = self._contact_by_id(contact_id)
            if contact is None:
                continue
            method_rows = self._contact_methods_from_record(contact)
            detail_lines: list[str] = []
            if contact.roles:
                detail_lines.append(f"Roles: {', '.join(contact.roles)}")
            if method_rows:
                for method in method_rows:
                    bundle_title = self._contact_method_title(method)
                    detail_lines.append(f"Bundle: {bundle_title}")
                    detail_lines.extend(self._contact_method_summary_lines(method))
            else:
                detail_lines.append("No bundles yet.")
            chip = AttachedContactChip(
                title=contact.name or "(Unnamed)",
                detail_lines=detail_lines,
                metadata_layout="bundle_groups",
                on_remove=lambda cid=contact.contact_id: self._admin_remove_jurisdiction_contact(cid),
                parent=host,
            )
            layout.addWidget(chip, 0)
        layout.addStretch(1)

    def _admin_add_jurisdiction_contact(self) -> None:
        combo = self._admin_jurisdiction_contact_picker_combo
        if combo is None:
            return
        contact_id = str(combo.currentData() or "").strip()
        if not contact_id:
            return
        if self._contact_by_id(contact_id) is None:
            return
        if contact_id in self._admin_jurisdiction_attached_contact_ids:
            combo.setCurrentIndex(0)
            return
        self._admin_jurisdiction_attached_contact_ids.append(contact_id)
        self._refresh_admin_jurisdiction_contacts_picker(
            selected_ids=self._admin_jurisdiction_attached_contact_ids
        )
        self._sync_admin_jurisdiction_dirty_state()

    def _admin_remove_jurisdiction_contact(self, contact_id: str) -> None:
        target = str(contact_id or "").strip()
        if not target:
            return
        self._admin_jurisdiction_attached_contact_ids = [
            row_id for row_id in self._admin_jurisdiction_attached_contact_ids if row_id != target
        ]
        self._refresh_admin_jurisdiction_contacts_picker(
            selected_ids=self._admin_jurisdiction_attached_contact_ids
        )
        self._sync_admin_jurisdiction_dirty_state()

    def _select_admin_contact_item(self, contact_id: str) -> bool:
        widget = self._admin_contacts_list_widget
        if widget is None:
            return False
        target = str(contact_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            return True
        self._set_admin_list_card_selection(widget)
        return False

    def _select_admin_jurisdiction_item(self, jurisdiction_id: str) -> bool:
        widget = self._admin_jurisdictions_list_widget
        if widget is None:
            return False
        target = str(jurisdiction_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            return True
        self._set_admin_list_card_selection(widget)
        return False

    def _restore_admin_contact_selection(self, contact_id: str) -> None:
        widget = self._admin_contacts_list_widget
        if widget is None:
            return
        self._admin_contact_selection_guard = True
        widget.blockSignals(True)
        try:
            target = str(contact_id or "").strip()
            if target and self._select_admin_contact_item(target):
                return
            widget.clearSelection()
            widget.setCurrentRow(-1)
            self._set_admin_list_card_selection(widget)
        finally:
            widget.blockSignals(False)
            self._admin_contact_selection_guard = False

    def _restore_admin_jurisdiction_selection(self, jurisdiction_id: str) -> None:
        widget = self._admin_jurisdictions_list_widget
        if widget is None:
            return
        self._admin_jurisdiction_selection_guard = True
        widget.blockSignals(True)
        try:
            target = str(jurisdiction_id or "").strip()
            if target and self._select_admin_jurisdiction_item(target):
                return
            widget.clearSelection()
            widget.setCurrentRow(-1)
            self._set_admin_list_card_selection(widget)
        finally:
            widget.blockSignals(False)
            self._admin_jurisdiction_selection_guard = False

    def _admin_selected_jurisdiction_contact_ids(self) -> list[str]:
        rows: list[str] = []
        seen_ids: set[str] = set()
        for raw_id in self._admin_jurisdiction_attached_contact_ids:
            contact_id = str(raw_id or "").strip()
            if not contact_id or contact_id in seen_ids:
                continue
            if self._contact_by_id(contact_id) is None:
                continue
            seen_ids.add(contact_id)
            rows.append(contact_id)
        return rows

    def _admin_new_contact(
        self, *, require_confirm: bool = True, action_label: str = "Add New Contact"
    ) -> None:
        if require_confirm and not self._confirm_discard_admin_contact_changes(action_label=action_label):
            return
        self._admin_contact_form_loading = True
        try:
            self._admin_selected_contact_id = ""
            widget = self._admin_contacts_list_widget
            if widget is not None:
                widget.blockSignals(True)
                widget.clearSelection()
                widget.setCurrentRow(-1)
                widget.blockSignals(False)
                self._set_admin_list_card_selection(widget)
            if self._admin_contact_name_input is not None:
                self._admin_contact_name_input.clear()
                self._admin_contact_name_input.setFocus()
            if self._admin_contact_bundle_name_input is not None:
                self._admin_contact_bundle_name_input.clear()
            if self._admin_contact_numbers_input is not None:
                self._admin_contact_numbers_input.clear()
            if self._admin_contact_emails_input is not None:
                self._admin_contact_emails_input.clear()
            if self._admin_contact_note_input is not None:
                self._admin_contact_note_input.clear()
            if self._admin_contact_roles_input is not None:
                self._admin_contact_roles_input.clear()
            self._set_admin_entity_list_color(
                entity_kind="contact",
                color_hex="",
                custom_color="",
                notify=False,
            )
            self._admin_contact_editing_bundle_index = -1
            self._sync_admin_contact_bundle_action_state()
            self._admin_contact_method_rows = []
            self._refresh_admin_contact_methods_list()
            self._admin_set_contact_form_mode(editing=False)
        finally:
            self._admin_contact_form_loading = False
        self._rebase_admin_contact_dirty_tracking()

    def _on_admin_contact_selected(self) -> None:
        if self._admin_contact_selection_guard:
            return
        widget = self._admin_contacts_list_widget
        self._set_admin_list_card_selection(widget)
        item = widget.currentItem() if widget is not None else None
        contact_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
        previous_contact_id = str(self._admin_selected_contact_id or "").strip()
        if contact_id != previous_contact_id:
            if not self._confirm_discard_admin_contact_editor_changes(action_label="Switch Contact"):
                self._restore_admin_contact_selection(previous_contact_id)
                return
        contact = self._contact_by_id(contact_id)
        self._admin_contact_form_loading = True
        try:
            if contact is None:
                self._admin_selected_contact_id = ""
                self._admin_set_contact_form_mode(editing=False)
                self._set_admin_entity_list_color(
                    entity_kind="contact",
                    color_hex="",
                    custom_color="",
                    notify=False,
                )
                self._admin_contact_editing_bundle_index = -1
                if self._admin_contact_bundle_name_input is not None:
                    self._admin_contact_bundle_name_input.clear()
                self._sync_admin_contact_bundle_action_state()
                self._admin_contact_method_rows = []
                self._refresh_admin_contact_methods_list()
            else:
                self._admin_selected_contact_id = contact.contact_id
                self._admin_set_contact_form_mode(editing=True)
                if self._admin_contact_name_input is not None:
                    self._admin_contact_name_input.setText(contact.name)
                if self._admin_contact_bundle_name_input is not None:
                    self._admin_contact_bundle_name_input.clear()
                if self._admin_contact_numbers_input is not None:
                    self._admin_contact_numbers_input.clear()
                if self._admin_contact_emails_input is not None:
                    self._admin_contact_emails_input.clear()
                if self._admin_contact_note_input is not None:
                    self._admin_contact_note_input.clear()
                if self._admin_contact_roles_input is not None:
                    self._admin_contact_roles_input.setText(_join_multi_values(contact.roles))
                contact_color = normalize_list_color(contact.list_color)
                contact_custom_color = (
                    contact_color
                    if contact_color and contact_color not in _ADMIN_LIST_COLOR_PRESETS
                    else ""
                )
                self._set_admin_entity_list_color(
                    entity_kind="contact",
                    color_hex=contact_color,
                    custom_color=contact_custom_color,
                    notify=False,
                )
                self._admin_contact_editing_bundle_index = -1
                self._sync_admin_contact_bundle_action_state()
                self._admin_contact_method_rows = self._contact_methods_from_record(contact)
                self._refresh_admin_contact_methods_list()
        finally:
            self._admin_contact_form_loading = False
        self._rebase_admin_contact_dirty_tracking()

    def _admin_save_contact(self) -> None:
        name = self._admin_contact_name_input.text().strip() if self._admin_contact_name_input else ""
        if not name:
            self._show_warning_dialog("Missing Name", "Contact name is required.")
            return

        edit_id = self._admin_selected_contact_id.strip()
        widget = self._admin_contacts_list_widget
        if widget is not None:
            current_item = widget.currentItem()
            current_id = (
                str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if current_item is not None
                else ""
            )
            if not current_id or current_id != edit_id:
                edit_id = ""
        existing = self._contact_by_id(edit_id) if edit_id else None

        if not self._admin_contact_dirty:
            self._show_info_dialog("No Changes", "No contact changes to save.")
            return
        save_title = "Update Contact" if existing is not None else "Create Contact"
        if not self._confirm_dialog(
            save_title,
            f"{save_title} '{name}'?",
            confirm_text=save_title.split(" ", 1)[0],
            cancel_text="Cancel",
        ):
            return

        roles = _parse_multi_values(self._admin_contact_roles_input.text()) if self._admin_contact_roles_input else []
        self._admin_contact_form_loading = True
        try:
            self._apply_pending_contact_method()
        finally:
            self._admin_contact_form_loading = False

        methods: list[ContactMethodRecord] = []
        for row in self._admin_contact_method_rows:
            label = str(row.label or "").strip()
            emails = _parse_multi_values(_join_multi_values(row.emails))
            numbers = _parse_multi_values(_join_multi_values(row.numbers))
            note = str(row.note or "").strip()
            if not any((label, emails, numbers, note)):
                continue
            methods.append(ContactMethodRecord(label=label, emails=emails, numbers=numbers, note=note))

        emails: list[str] = []
        numbers: list[str] = []
        seen_emails: set[str] = set()
        seen_numbers: set[str] = set()
        for method in methods:
            for value in method.emails:
                key = value.casefold()
                if key in seen_emails:
                    continue
                seen_emails.add(key)
                emails.append(value)
            for value in method.numbers:
                key = value.casefold()
                if key in seen_numbers:
                    continue
                seen_numbers.add(key)
                numbers.append(value)
        list_color = normalize_list_color(self._admin_contact_list_color)

        if existing is None:
            record = ContactRecord(
                contact_id=uuid4().hex,
                name=name,
                numbers=numbers,
                emails=emails,
                roles=roles,
                contact_methods=methods,
                list_color=list_color,
            )
            self._contacts.append(record)
        else:
            existing.name = name
            existing.numbers = numbers
            existing.emails = emails
            existing.roles = roles
            existing.contact_methods = methods
            existing.list_color = list_color
            record = existing

        self._admin_selected_contact_id = record.contact_id
        self._admin_contact_editing_bundle_index = -1
        self._admin_contact_method_rows = [
            ContactMethodRecord(
                label=str(row.label or "").strip(),
                emails=list(row.emails),
                numbers=list(row.numbers),
                note=row.note,
            )
            for row in methods
        ]
        self._sync_admin_contact_bundle_action_state()
        self._refresh_admin_contact_methods_list()
        selected_jurisdiction_contacts = list(self._admin_selected_jurisdiction_contact_ids())
        self._persist_tracker_data()
        self._refresh_admin_contacts_list(select_id=record.contact_id)
        self._refresh_admin_jurisdiction_contacts_picker(selected_ids=selected_jurisdiction_contacts)
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)
        self._admin_set_contact_form_mode(editing=True)
        self._refresh_selected_permit_view()
        self._rebase_admin_contact_dirty_tracking()

    def _admin_delete_contact(self) -> None:
        contact = self._contact_by_id(self._admin_selected_contact_id)
        if contact is None:
            return
        delete_message = f"Delete contact '{contact.name or '(Unnamed)'}'?"
        if self._admin_contact_dirty:
            delete_message = f"{delete_message}\n\nUnsaved contact edits will be lost."
        confirmed = self._confirm_dialog(
            "Delete Contact",
            delete_message,
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        self._contacts = [row for row in self._contacts if row.contact_id != contact.contact_id]
        for jurisdiction in self._jurisdictions:
            jurisdiction.contact_ids = [
                contact_id for contact_id in jurisdiction.contact_ids if contact_id != contact.contact_id
            ]
        for property_record in self._properties:
            property_record.contact_ids = [
                contact_id for contact_id in property_record.contact_ids if contact_id != contact.contact_id
            ]
        for permit in self._permits:
            permit.parties = [
                party
                for party in permit.parties
                if str(party.contact_id or "").strip() != contact.contact_id
            ]
        self._admin_jurisdiction_attached_contact_ids = [
            contact_id
            for contact_id in self._admin_jurisdiction_attached_contact_ids
            if contact_id != contact.contact_id
        ]
        self._add_property_attached_contact_ids = [
            contact_id
            for contact_id in self._add_property_attached_contact_ids
            if contact_id != contact.contact_id
        ]
        self._add_permit_attached_contact_ids = [
            contact_id
            for contact_id in self._add_permit_attached_contact_ids
            if contact_id != contact.contact_id
        ]
        self._refresh_add_property_contacts_picker(
            selected_ids=self._add_property_attached_contact_ids
        )
        self._refresh_add_permit_contacts_picker(
            selected_ids=self._add_permit_attached_contact_ids
        )
        self._sync_inline_property_dirty_state()
        self._sync_inline_permit_dirty_state()
        self._admin_new_contact(require_confirm=False)
        self._persist_tracker_data()
        selected_jurisdiction = self._jurisdiction_by_id(self._admin_selected_jurisdiction_id)
        selected_ids = (
            list(selected_jurisdiction.contact_ids)
            if selected_jurisdiction is not None
            else list(self._admin_jurisdiction_attached_contact_ids)
        )
        self._refresh_admin_contacts_list()
        self._refresh_admin_jurisdiction_contacts_picker(selected_ids=selected_ids)
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)
        self._refresh_selected_permit_view()

    def _admin_new_jurisdiction(
        self, *, require_confirm: bool = True, action_label: str = "Add New Jurisdiction"
    ) -> None:
        if require_confirm and not self._confirm_discard_admin_jurisdiction_changes(
            action_label=action_label
        ):
            return
        self._admin_jurisdiction_form_loading = True
        try:
            self._admin_selected_jurisdiction_id = ""
            widget = self._admin_jurisdictions_list_widget
            if widget is not None:
                widget.blockSignals(True)
                widget.clearSelection()
                widget.setCurrentRow(-1)
                widget.blockSignals(False)
                self._set_admin_list_card_selection(widget)
            if self._admin_jurisdiction_name_input is not None:
                self._admin_jurisdiction_name_input.clear()
                self._admin_jurisdiction_name_input.setFocus()
            if self._admin_jurisdiction_type_combo is not None:
                self._admin_jurisdiction_type_combo.setCurrentIndex(0)
            if self._admin_jurisdiction_parent_input is not None:
                self._admin_jurisdiction_parent_input.clear()
            if self._admin_jurisdiction_portals_input is not None:
                self._admin_jurisdiction_portals_input.clear()
            if self._admin_jurisdiction_vendor_input is not None:
                self._admin_jurisdiction_vendor_input.clear()
            if self._admin_jurisdiction_notes_input is not None:
                self._admin_jurisdiction_notes_input.clear()
            self._set_admin_entity_list_color(
                entity_kind="jurisdiction",
                color_hex="",
                custom_color="",
                notify=False,
            )
            self._refresh_admin_jurisdiction_contacts_picker(selected_ids=[])
            self._admin_set_jurisdiction_form_mode(editing=False)
        finally:
            self._admin_jurisdiction_form_loading = False
        self._rebase_admin_jurisdiction_dirty_tracking()

    def _on_admin_jurisdiction_selected(self) -> None:
        if self._admin_jurisdiction_selection_guard:
            return
        widget = self._admin_jurisdictions_list_widget
        self._set_admin_list_card_selection(widget)
        item = widget.currentItem() if widget is not None else None
        jurisdiction_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
        previous_jurisdiction_id = str(self._admin_selected_jurisdiction_id or "").strip()
        if jurisdiction_id != previous_jurisdiction_id:
            if not self._confirm_discard_admin_jurisdiction_changes(action_label="Switch Jurisdiction"):
                self._restore_admin_jurisdiction_selection(previous_jurisdiction_id)
                return
        jurisdiction = self._jurisdiction_by_id(jurisdiction_id)
        self._admin_jurisdiction_form_loading = True
        try:
            if jurisdiction is None:
                self._admin_selected_jurisdiction_id = ""
                self._refresh_admin_jurisdiction_contacts_picker(selected_ids=[])
                self._admin_set_jurisdiction_form_mode(editing=False)
                self._set_admin_entity_list_color(
                    entity_kind="jurisdiction",
                    color_hex="",
                    custom_color="",
                    notify=False,
                )
            else:
                self._admin_selected_jurisdiction_id = jurisdiction.jurisdiction_id
                self._admin_set_jurisdiction_form_mode(editing=True)
                if self._admin_jurisdiction_name_input is not None:
                    self._admin_jurisdiction_name_input.setText(jurisdiction.name)
                if self._admin_jurisdiction_type_combo is not None:
                    idx = self._admin_jurisdiction_type_combo.findData(jurisdiction.jurisdiction_type)
                    if idx >= 0:
                        self._admin_jurisdiction_type_combo.setCurrentIndex(idx)
                if self._admin_jurisdiction_parent_input is not None:
                    self._admin_jurisdiction_parent_input.setText(jurisdiction.parent_county)
                if self._admin_jurisdiction_portals_input is not None:
                    self._admin_jurisdiction_portals_input.setText(
                        _join_multi_values(jurisdiction.portal_urls)
                    )
                if self._admin_jurisdiction_vendor_input is not None:
                    self._admin_jurisdiction_vendor_input.setText(jurisdiction.portal_vendor)
                if self._admin_jurisdiction_notes_input is not None:
                    self._admin_jurisdiction_notes_input.setText(jurisdiction.notes)
                jurisdiction_color = normalize_list_color(jurisdiction.list_color)
                jurisdiction_custom_color = (
                    jurisdiction_color
                    if jurisdiction_color and jurisdiction_color not in _ADMIN_LIST_COLOR_PRESETS
                    else ""
                )
                self._set_admin_entity_list_color(
                    entity_kind="jurisdiction",
                    color_hex=jurisdiction_color,
                    custom_color=jurisdiction_custom_color,
                    notify=False,
                )
                self._refresh_admin_jurisdiction_contacts_picker(selected_ids=jurisdiction.contact_ids)
        finally:
            self._admin_jurisdiction_form_loading = False
        self._rebase_admin_jurisdiction_dirty_tracking()

    def _admin_save_jurisdiction(self) -> None:
        name = (
            self._admin_jurisdiction_name_input.text().strip()
            if self._admin_jurisdiction_name_input is not None
            else ""
        )
        if not name:
            self._show_warning_dialog("Missing Name", "Jurisdiction name is required.")
            return

        edit_id = self._admin_selected_jurisdiction_id.strip()
        widget = self._admin_jurisdictions_list_widget
        if widget is not None:
            current_item = widget.currentItem()
            current_id = (
                str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if current_item is not None
                else ""
            )
            if not current_id or current_id != edit_id:
                edit_id = ""
        existing = self._jurisdiction_by_id(edit_id) if edit_id else None

        if not self._admin_jurisdiction_dirty:
            self._show_info_dialog("No Changes", "No jurisdiction changes to save.")
            return
        save_title = "Update Jurisdiction" if existing is not None else "Create Jurisdiction"
        if not self._confirm_dialog(
            save_title,
            f"{save_title} '{name}'?",
            confirm_text=save_title.split(" ", 1)[0],
            cancel_text="Cancel",
        ):
            return

        jurisdiction_type = (
            str(self._admin_jurisdiction_type_combo.currentData() or "city")
            if self._admin_jurisdiction_type_combo is not None
            else "city"
        )
        parent_county = (
            self._admin_jurisdiction_parent_input.text().strip()
            if self._admin_jurisdiction_parent_input is not None
            else ""
        )
        portal_urls = (
            _parse_multi_values(self._admin_jurisdiction_portals_input.text())
            if self._admin_jurisdiction_portals_input is not None
            else []
        )
        portal_vendor = (
            self._admin_jurisdiction_vendor_input.text().strip()
            if self._admin_jurisdiction_vendor_input is not None
            else ""
        )
        notes = (
            self._admin_jurisdiction_notes_input.text().strip()
            if self._admin_jurisdiction_notes_input is not None
            else ""
        )
        contact_ids = sorted(self._admin_selected_jurisdiction_contact_ids())
        list_color = normalize_list_color(self._admin_jurisdiction_list_color)

        if existing is None:
            record = JurisdictionRecord(
                jurisdiction_id=uuid4().hex,
                name=name,
                jurisdiction_type=jurisdiction_type,
                parent_county=parent_county,
                portal_urls=portal_urls,
                contact_ids=contact_ids,
                portal_vendor=portal_vendor,
                notes=notes,
                list_color=list_color,
            )
            self._jurisdictions.append(record)
        else:
            existing.name = name
            existing.jurisdiction_type = jurisdiction_type
            existing.parent_county = parent_county
            existing.portal_urls = portal_urls
            existing.contact_ids = contact_ids
            existing.portal_vendor = portal_vendor
            existing.notes = notes
            existing.list_color = list_color
            record = existing

        self._admin_selected_jurisdiction_id = record.jurisdiction_id
        self._persist_tracker_data()
        self._refresh_admin_jurisdiction_contacts_picker(selected_ids=contact_ids)
        self._refresh_admin_jurisdictions_list(select_id=record.jurisdiction_id)
        self._admin_set_jurisdiction_form_mode(editing=True)
        self._refresh_property_filters()
        self._refresh_property_list()
        self._refresh_selected_permit_view()
        self._rebase_admin_jurisdiction_dirty_tracking()

    def _admin_delete_jurisdiction(self) -> None:
        jurisdiction = self._jurisdiction_by_id(self._admin_selected_jurisdiction_id)
        if jurisdiction is None:
            return
        delete_message = f"Delete jurisdiction '{jurisdiction.name or '(Unnamed)'}'?"
        if self._admin_jurisdiction_dirty:
            delete_message = f"{delete_message}\n\nUnsaved jurisdiction edits will be lost."
        confirmed = self._confirm_dialog(
            "Delete Jurisdiction",
            delete_message,
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        self._jurisdictions = [
            row for row in self._jurisdictions if row.jurisdiction_id != jurisdiction.jurisdiction_id
        ]
        for property_record in self._properties:
            if property_record.jurisdiction_id == jurisdiction.jurisdiction_id:
                property_record.jurisdiction_id = ""
        self._admin_new_jurisdiction(require_confirm=False)
        self._persist_tracker_data()
        self._refresh_admin_jurisdictions_list()
        self._refresh_property_filters()
        self._refresh_property_list()
        self._refresh_selected_permit_view()

    def _refresh_all_views(self) -> None:
        self._timeline_debug(
            "refresh_all_views_start",
            contacts=len(self._contacts),
            jurisdictions=len(self._jurisdictions),
            properties=len(self._properties),
            permits=len(self._permits),
        )
        self._refresh_property_filters()
        self._refresh_property_list()
        self._sync_permit_type_buttons()
        self._refresh_permit_list()
        self._refresh_selected_permit_view()
        self._refresh_add_property_contacts_picker(
            selected_ids=self._add_property_attached_contact_ids
        )
        self._refresh_add_permit_contacts_picker(
            selected_ids=self._add_permit_attached_contact_ids
        )
        self._refresh_admin_views()
        self._refresh_document_templates_view()
        self._timeline_debug("refresh_all_views_done")

    def _set_result_label(self, label: QLabel | None, *, shown: int, total: int, noun: str) -> None:
        if label is None:
            return
        if total <= 0:
            label.setText(f"0 {noun}")
            return
        label.setText(f"{shown} of {total} {noun}")

    def _set_workspace_title_state(self, state_text: str) -> None:
        if self._workspace_title_label is None:
            return
        text = str(state_text or "").strip()
        if not text:
            text = "Select Permit"
        self._workspace_title_label.setText(f"Permit Workspace - {text}")

    def _set_workspace_info_values(self, **values: str) -> None:
        for key, label in self._workspace_info_values.items():
            text = str(values.get(key, "") or "").strip()
            if not text:
                text = "—"
            label.setText(text)
            label.setToolTip("" if text == "—" else text)

    def _set_workspace_next_step_hint(self, hint_text: str) -> None:
        label = self._workspace_next_step_label
        if label is None:
            return
        text = str(hint_text or "").strip()
        label.setText(text)
        label.setToolTip(text)
        self._sync_permit_workspace_blur_overlay()

    def _set_permit_workspace_blur(self, enabled: bool) -> None:
        panel = self._permit_workspace_panel
        target = self._permit_workspace_content_host or panel
        if target is None:
            return
        should_enable = bool(enabled)
        if self._permit_workspace_blurred == should_enable:
            return

        if should_enable:
            # Do not reuse cached effects: Qt may delete the previous effect
            # when the widget graphics effect is cleared.
            effect = QGraphicsBlurEffect(target)
            effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
            effect.setBlurRadius(10.0)
            target.setGraphicsEffect(effect)
            self._permit_workspace_blur_effect = effect
        else:
            target.setGraphicsEffect(None)
            self._permit_workspace_blur_effect = None

        self._permit_workspace_blurred = should_enable
        if panel is not None:
            panel.setProperty("workspaceBlurred", "true" if should_enable else "false")
        target.setProperty("workspaceBlurred", "true" if should_enable else "false")
        style = target.style()
        style.unpolish(target)
        style.polish(target)
        target.update()
        self._sync_permit_workspace_blur_overlay()

    def _sync_permit_workspace_blur_overlay(self) -> None:
        panel = self._permit_workspace_content_host or self._permit_workspace_panel
        overlay = self._permit_workspace_blur_overlay
        workspace_panel = self._permit_workspace_panel
        next_step_label = self._workspace_next_step_label
        if panel is None or overlay is None:
            return
        if overlay.parentWidget() is not panel:
            overlay.setParent(panel)
        overlay.setGeometry(panel.rect())
        overlay.setVisible(self._permit_workspace_blurred)
        if self._permit_workspace_blurred:
            overlay.raise_()

        if workspace_panel is None or next_step_label is None:
            return

        hint_text = str(next_step_label.text() or "").strip()
        if not hint_text:
            next_step_label.setVisible(False)
            return

        content_host = self._permit_workspace_content_host
        if content_host is not None:
            try:
                target_top_left = content_host.mapTo(workspace_panel, QPoint(0, 0))
                target_rect = QRect(target_top_left, content_host.size())
            except Exception:
                target_rect = workspace_panel.rect()
        else:
            target_rect = workspace_panel.rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            next_step_label.setVisible(False)
            return

        margin = 18
        available_width = max(220, target_rect.width() - (margin * 2))
        max_width = max(240, min(int(round(target_rect.width() * 0.74)), available_width))
        next_step_label.setMaximumWidth(max_width)
        height_hint = next_step_label.heightForWidth(max_width)
        if height_hint <= 0:
            height_hint = next_step_label.sizeHint().height()
        target_height = max(56, min(height_hint + 8, max(72, target_rect.height() - (margin * 2))))
        target_width = min(max_width, available_width)
        x = target_rect.x() + max(0, int((target_rect.width() - target_width) / 2))
        y = target_rect.y() + max(0, int((target_rect.height() - target_height) / 2))
        next_step_label.setGeometry(x, y, target_width, target_height)
        next_step_label.setVisible(True)
        next_step_label.raise_()

    def _current_search(self, widget: QLineEdit | None) -> str:
        if widget is None:
            return ""
        return widget.text().strip().casefold()

    def _current_filter_value(self, combo: QComboBox | None) -> str:
        if combo is None:
            return "all"
        value = str(combo.currentData() or "").strip().lower()
        return value or "all"

    def _contact_by_id(self, contact_id: str) -> ContactRecord | None:
        target = str(contact_id or "").strip()
        if not target:
            return None
        for record in self._contacts:
            if record.contact_id == target:
                return record
        return None

    def _property_by_id(self, property_id: str) -> PropertyRecord | None:
        target = str(property_id or "").strip()
        if not target:
            return None
        for record in self._properties:
            if record.property_id == target:
                return record
        return None

    def _jurisdiction_by_id(self, jurisdiction_id: str) -> JurisdictionRecord | None:
        target = str(jurisdiction_id or "").strip()
        if not target:
            return None
        for record in self._jurisdictions:
            if record.jurisdiction_id == target:
                return record
        return None

    def _permit_by_id(self, permit_id: str) -> PermitRecord | None:
        target = str(permit_id or "").strip()
        if not target:
            return None
        for record in self._permits:
            if record.permit_id == target:
                return record
        return None

    def _selected_property(self) -> PropertyRecord | None:
        return self._property_by_id(self._selected_property_id)

    def _selected_permit(self) -> PermitRecord | None:
        return self._permit_by_id(self._selected_permit_id)

    def _permits_for_property(self, property_id: str) -> list[PermitRecord]:
        target = str(property_id or "").strip()
        if not target:
            return []
        return [record for record in self._permits if record.property_id == target]

    def _refresh_property_filters(self) -> None:
        combo = self._property_filter_combo
        if combo is None:
            return
        current = self._current_filter_value(combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All", "all")
        combo.addItem("Has Overdue", "overdue")
        combo.addItem("Has Missing Docs", "missing_docs")
        for jurisdiction in sorted(self._jurisdictions, key=lambda row: row.name.casefold()):
            combo.addItem(f"By Jurisdiction: {jurisdiction.name}", f"jurisdiction:{jurisdiction.jurisdiction_id}")
        index = combo.findData(current)
        if index < 0:
            index = 0
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _property_overdue_count(self, property_record: PropertyRecord) -> int:
        count = 0
        today = date.today()
        for permit in self._permits_for_property(property_record.property_id):
            due_date = _parse_iso_date(permit.next_action_due)
            if due_date is None:
                continue
            if permit.status in {"closed", "canceled", "finaled"}:
                continue
            if due_date < today:
                count += 1
        return count

    def _permit_missing_required_docs_count(self, permit: PermitRecord) -> int:
        ensure_default_document_structure(permit)
        refresh_slot_status_from_documents(permit)
        count = 0
        for slot in permit.document_slots:
            if not slot.required:
                continue
            status = normalize_slot_status(slot.status)
            if status in {"missing", "rejected", "superseded"}:
                count += 1
        return count

    def _property_missing_docs_count(self, property_record: PropertyRecord) -> int:
        count = 0
        for permit in self._permits_for_property(property_record.property_id):
            count += self._permit_missing_required_docs_count(permit)
        return count

    def _property_matches_filter(self, property_record: PropertyRecord, filter_mode: str) -> bool:
        if filter_mode == "overdue":
            return self._property_overdue_count(property_record) > 0
        if filter_mode == "missing_docs":
            return self._property_missing_docs_count(property_record) > 0
        if filter_mode.startswith("jurisdiction:"):
            target_jurisdiction = filter_mode.split(":", 1)[1].strip()
            return target_jurisdiction and property_record.jurisdiction_id == target_jurisdiction
        return True

    def _refresh_property_list(self) -> None:
        widget = self._properties_list_widget
        if widget is None:
            return
        list_stack = self._properties_list_stack
        empty_label = self._properties_empty_label

        search = self._current_search(self._property_search_input)
        filter_mode = self._current_filter_value(self._property_filter_combo)

        candidates = sorted(
            self._properties,
            key=lambda row: (
                row.display_address.casefold(),
                row.parcel_id.casefold(),
                row.property_id,
            ),
        )
        filtered: list[PropertyRecord] = []
        for record in candidates:
            if search:
                haystack = " ".join((
                    record.display_address,
                    record.parcel_id,
                    record.parcel_id_norm,
                    record.notes,
                )).casefold()
                if search not in haystack:
                    continue
            if not self._property_matches_filter(record, filter_mode):
                continue
            filtered.append(record)

        selected_id = self._selected_property_id

        widget.blockSignals(True)
        widget.clear()
        for property_record in filtered:
            jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id)
            jurisdiction_name = (
                jurisdiction.name
                if jurisdiction is not None and jurisdiction.name.strip()
                else "Unassigned"
            )
            overdue_count = self._property_overdue_count(property_record)
            missing_docs = self._property_missing_docs_count(property_record)
            subtitle = f"{property_record.parcel_id or '(no parcel)'}  •  {jurisdiction_name}"
            badges = f"{overdue_count} overdue  •  {missing_docs} missing docs"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, property_record.property_id)
            widget.addItem(item)
            card = self._build_tracker_entity_card(
                title=property_record.display_address or "(no address)",
                title_field="address",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=badges,
                meta_field="request",
                accent_color=property_record.list_color,
                on_edit=lambda property_id=property_record.property_id: self._edit_property_record(property_id),
                on_remove=lambda property_id=property_record.property_id: self._delete_property_record(property_id),
            )
            item_hint = card.sizeHint()
            row_height = max(
                item_hint.height(),
                card.minimumSizeHint().height(),
                card.minimumHeight(),
            )
            item_hint.setHeight(row_height + 6)
            item.setSizeHint(item_hint)
            widget.setItemWidget(item, card)

        if selected_id and any(row.property_id == selected_id for row in filtered):
            self._select_property_item(selected_id)
        else:
            self._selected_property_id = filtered[0].property_id if filtered else ""
            if self._selected_property_id:
                self._select_property_item(self._selected_property_id)

        widget.blockSignals(False)
        self._set_admin_list_card_selection(widget)
        self._set_result_label(self._property_result_label, shown=len(filtered), total=len(candidates), noun="addresses")

        if list_stack is not None and empty_label is not None:
            if filtered:
                list_stack.setCurrentWidget(widget)
            else:
                if not candidates:
                    message = "No addresses yet.\nClick Add Address to create your first property."
                elif search:
                    message = "No addresses match this search.\nTry another search or clear filters."
                elif filter_mode == "overdue":
                    message = "No addresses with overdue next actions."
                elif filter_mode == "missing_docs":
                    message = "No addresses with missing required documents."
                elif filter_mode.startswith("jurisdiction:"):
                    message = "No addresses found for this jurisdiction filter."
                else:
                    message = "No addresses available for the current filters."
                empty_label.setText(message)
                list_stack.setCurrentWidget(empty_label)

        self._refresh_permit_list()

    def _select_property_item(self, property_id: str) -> None:
        widget = self._properties_list_widget
        if widget is None:
            return
        target = str(property_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            return

    def _on_property_selection_changed(self) -> None:
        widget = self._properties_list_widget
        self._set_admin_list_card_selection(widget)
        current_item = widget.currentItem() if widget is not None else None
        selected_id = str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        if selected_id:
            self._set_left_column_expanded_panel("permit")
        self._timeline_debug(
            "property_selection_changed",
            selected_property_id=selected_id,
            previous_property_id=self._selected_property_id,
        )
        if selected_id == self._selected_property_id:
            return
        self._selected_property_id = selected_id
        self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._refresh_permit_list()
        self._refresh_selected_permit_view()

    def _sync_permit_type_buttons(self) -> None:
        for permit_type, button in self._permit_type_buttons.items():
            button.setChecked(self._active_permit_type_filter == permit_type)

    def _set_active_permit_type_filter(self, permit_type: str) -> None:
        normalized = str(permit_type or "").strip().lower()
        if normalized not in {"all", "building", "demolition", "remodeling"}:
            normalized = "all"
        if self._active_permit_type_filter == normalized:
            self._sync_permit_type_buttons()
            return
        self._active_permit_type_filter = normalized
        self._sync_permit_type_buttons()
        self._refresh_permit_list()

    def _permit_matches_filter(self, permit: PermitRecord, filter_mode: str) -> bool:
        status = normalize_event_type(permit.status)
        if filter_mode == "open":
            return status not in {"closed", "canceled", "finaled"}
        if filter_mode == "closed":
            return status in {"closed", "canceled", "finaled"}
        if filter_mode == "overdue":
            due_date = _parse_iso_date(permit.next_action_due)
            if due_date is None:
                return False
            return due_date < date.today() and status not in {"closed", "canceled", "finaled"}
        if filter_mode != "all":
            return status == filter_mode
        return True

    def _refresh_permit_list(self, *, keep_selected_visible: bool = False) -> None:
        widget = self._permits_list_widget
        if widget is None:
            return
        list_stack = self._permits_list_stack
        empty_label = self._permits_empty_label

        selected_property = self._selected_property()
        selected_id = str(self._selected_permit_id or "").strip()
        selected_permit = self._permit_by_id(selected_id) if selected_id else None
        if keep_selected_visible and selected_permit is not None:
            if selected_property is None or selected_property.property_id != selected_permit.property_id:
                selected_property = self._property_by_id(selected_permit.property_id)
                if selected_property is not None:
                    self._selected_property_id = selected_property.property_id
        properties_count = len(self._properties)
        permits_for_property = (
            self._permits_for_property(selected_property.property_id)
            if selected_property is not None
            else []
        )
        if (
            keep_selected_visible
            and selected_permit is not None
            and all(row.permit_id != selected_permit.permit_id for row in permits_for_property)
        ):
            permits_for_property.append(selected_permit)

        if self._permit_header_label is not None:
            if properties_count <= 0:
                self._permit_header_label.setText("Add an address to start tracking permits")
            elif selected_property is None:
                self._permit_header_label.setText("Select an address to view permits")
            else:
                self._permit_header_label.setText(
                    f"For: {selected_property.display_address or '(no address)'}"
                )
        has_selected_property = selected_property is not None
        if self._permit_controls_host is not None:
            self._permit_controls_host.setVisible(has_selected_property)
        if self._permit_type_picker_host is not None:
            self._permit_type_picker_host.setVisible(has_selected_property)
        if self._add_permit_button is not None:
            self._add_permit_button.setEnabled(has_selected_property)
            self._add_permit_button.setToolTip(
                "" if has_selected_property else "Select an address first"
            )

        filter_mode = self._current_filter_value(self._permit_filter_combo)
        search = self._current_search(self._permit_search_input)
        type_filter = self._active_permit_type_filter
        self._timeline_debug(
            "refresh_permit_list_start",
            keep_selected_visible=keep_selected_visible,
            selected_property_id=selected_property.property_id if selected_property is not None else "",
            selected_permit_id=selected_id,
            filter_mode=filter_mode,
            type_filter=type_filter,
            search=search,
            permits_for_property_count=len(permits_for_property),
        )

        filtered: list[PermitRecord] = []
        selected_permit_filter_details: dict[str, object] = {}
        for permit in permits_for_property:
            ensure_default_document_structure(permit)
            refresh_slot_status_from_documents(permit)
            permit.status = compute_permit_status(permit.events, fallback=permit.status)

            matches_filters = True
            reasons: list[str] = []
            if type_filter != "all" and normalize_permit_type(permit.permit_type) != type_filter:
                matches_filters = False
                reasons.append("type_filter")
            if matches_filters and not self._permit_matches_filter(permit, filter_mode):
                matches_filters = False
                reasons.append("status_filter")
            if search:
                haystack = " ".join(
                    (
                        permit.permit_number,
                        permit.status,
                        permit.next_action_text,
                        permit.next_action_due,
                        permit.request_date,
                    )
                ).casefold()
                if search not in haystack:
                    matches_filters = False
                    reasons.append("search")
            if selected_id and permit.permit_id == selected_id:
                selected_permit_filter_details = {
                    "permit_id": permit.permit_id,
                    "status": permit.status,
                    "permit_type": permit.permit_type,
                    "matches_filters": matches_filters,
                    "reasons": reasons,
                    "event_count": len(permit.events),
                }
            if not matches_filters:
                if not (
                    keep_selected_visible
                    and selected_id
                    and permit.permit_id == selected_id
                ):
                    continue
            filtered.append(permit)

        filtered.sort(
            key=lambda row: (
                row.next_action_due or "9999-12-31",
                row.permit_number.casefold(),
                row.permit_id,
            )
        )

        widget.blockSignals(True)
        widget.clear()
        for permit in filtered:
            permit_number = permit.permit_number or "(no permit # yet)"
            status_text = event_type_label(permit.status)
            due_text = permit.next_action_due or "No due date"
            missing_docs_count = self._permit_missing_required_docs_count(permit)
            subtitle = f"{_permit_type_label(permit.permit_type)}  •  {status_text}"
            meta_parts = [f"Due {due_text}"]
            if missing_docs_count > 0:
                meta_parts.append(f"{missing_docs_count} missing docs")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, permit.permit_id)
            widget.addItem(item)
            card = self._build_tracker_entity_card(
                title=permit_number,
                title_field="document",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=" | ".join(meta_parts),
                meta_field="request",
                on_edit=lambda permit_id=permit.permit_id: self._edit_permit_record(permit_id),
                on_remove=lambda permit_id=permit.permit_id: self._delete_permit_record(permit_id),
            )
            next_action_text = str(permit.next_action_text or "").strip()
            if next_action_text:
                card.setToolTip(f"Next action: {next_action_text}")
            item_hint = card.sizeHint()
            row_height = max(
                item_hint.height(),
                card.minimumSizeHint().height(),
                card.minimumHeight(),
            )
            item_hint.setHeight(row_height + 6)
            item.setSizeHint(item_hint)
            widget.setItemWidget(item, card)

        if selected_id and any(row.permit_id == selected_id for row in filtered):
            self._select_permit_item(selected_id)
        else:
            self._selected_permit_id = filtered[0].permit_id if filtered else ""
            if self._selected_permit_id:
                self._select_permit_item(self._selected_permit_id)

        widget.blockSignals(False)
        self._set_admin_list_card_selection(widget)
        self._set_result_label(self._permit_result_label, shown=len(filtered), total=len(permits_for_property), noun="permits")

        if list_stack is not None and empty_label is not None:
            if filtered:
                list_stack.setCurrentWidget(widget)
            else:
                if properties_count <= 0:
                    message = "No permits yet.\nCreate your first address above to unlock permits."
                elif selected_property is None:
                    message = "Select an address above to see permits for that property."
                elif not permits_for_property:
                    message = (
                        f"No permits for:\n{selected_property.display_address or '(no address)'}\n"
                        "Click Add Permit to create the first one."
                    )
                elif search:
                    message = "No permits match this search.\nTry another search or clear filters."
                elif filter_mode != "all":
                    message = "No permits match the selected status filter."
                elif type_filter != "all":
                    message = f"No {_permit_type_label(type_filter)} permits for this address."
                else:
                    message = "No permits available for the current filters."
                empty_label.setText(message)
                list_stack.setCurrentWidget(empty_label)

        self._timeline_debug(
            "refresh_permit_list_done",
            keep_selected_visible=keep_selected_visible,
            selected_permit_before=selected_id,
            selected_permit_after=self._selected_permit_id,
            selected_permit_filter_details=selected_permit_filter_details,
            filtered_count=len(filtered),
            filtered_ids=[row.permit_id for row in filtered[:20]],
            list_widget_count=widget.count(),
        )
        self._refresh_selected_permit_view()

    def _select_permit_item(self, permit_id: str) -> None:
        widget = self._permits_list_widget
        if widget is None:
            return
        target = str(permit_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            self._timeline_debug(
                "select_permit_item_found",
                permit_id=target,
                index=index,
                widget_count=widget.count(),
            )
            return
        self._timeline_debug(
            "select_permit_item_missing",
            permit_id=target,
            widget_count=widget.count(),
        )

    def _on_permit_selection_changed(self) -> None:
        widget = self._permits_list_widget
        self._set_admin_list_card_selection(widget)
        current_item = widget.currentItem() if widget is not None else None
        selected_id = str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        self._timeline_debug(
            "permit_selection_changed",
            widget_count=widget.count() if widget is not None else 0,
            selected_id=selected_id,
            previously_selected_id=self._selected_permit_id,
        )
        if selected_id == self._selected_permit_id:
            self._refresh_selected_permit_view()
            return
        self._selected_permit_id = selected_id
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._refresh_selected_permit_view()

    def _portal_url_for_property(self, property_record: PropertyRecord | None) -> str:
        if property_record is None:
            return ""
        jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id)
        if jurisdiction is None or not jurisdiction.portal_urls:
            return ""
        return jurisdiction.portal_urls[0].strip()

    def _workspace_contacts_portal_text(
        self,
        *,
        property_record: PropertyRecord | None,
        permit: PermitRecord | None,
        jurisdiction: JurisdictionRecord | None,
        portal_url: str,
    ) -> str:
        contact_preview = ""
        ordered_contact_ids: list[str] = []
        seen_contact_ids: set[str] = set()

        if permit is not None:
            for party in permit.parties:
                contact_id = str(party.contact_id or "").strip()
                if not contact_id or contact_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(contact_id)
                ordered_contact_ids.append(contact_id)
        if property_record is not None:
            for contact_id in property_record.contact_ids:
                normalized_id = str(contact_id or "").strip()
                if not normalized_id or normalized_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(normalized_id)
                ordered_contact_ids.append(normalized_id)
        if jurisdiction is not None:
            for contact_id in jurisdiction.contact_ids:
                normalized_id = str(contact_id or "").strip()
                if not normalized_id or normalized_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(normalized_id)
                ordered_contact_ids.append(normalized_id)

        if ordered_contact_ids:
            contact_names: list[str] = []
            for contact_id in ordered_contact_ids:
                contact = self._contact_by_id(contact_id)
                if contact is None:
                    continue
                name = contact.name.strip()
                if not name:
                    continue
                contact_names.append(name)
            if contact_names:
                contact_preview = ", ".join(contact_names[:2])
                if len(contact_names) > 2:
                    contact_preview = f"{contact_preview}, +{len(contact_names) - 2}"
        portal_preview = ""
        if portal_url:
            try:
                parsed = urlsplit(portal_url)
                portal_preview = parsed.netloc.strip() or parsed.path.strip() or portal_url
            except Exception:
                portal_preview = portal_url
        if contact_preview and portal_preview:
            return f"{contact_preview} | {portal_preview}"
        if contact_preview:
            return contact_preview
        if portal_preview:
            return portal_preview
        return "No contacts or portal"

    def _event_sort_key(self, event: PermitEventRecord, index: int) -> tuple[datetime, int]:
        parsed = _parse_iso_datetime(event.event_date)
        if parsed is None:
            parsed = datetime.min.replace(tzinfo=timezone.utc)
        return parsed, index

    def _timeline_default_business_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        ordered_events = list(enumerate(permit.events))
        ordered_events.sort(
            key=lambda pair: self._event_sort_key(pair[1], pair[0]),
        )

        contacts_by_id = {row.contact_id: row for row in self._contacts}
        timeline_rows: list[_TimelineRenderRow] = []
        for _index, event in ordered_events:
            event_type = normalize_event_type(event.event_type)
            if event_type == "note":
                continue

            detail_lines: list[str] = []
            detail_text = str(event.detail or "").strip()
            if detail_text:
                detail_lines.append(detail_text)

            actor = contacts_by_id.get(str(event.actor_contact_id or "").strip())
            if actor is not None and actor.name.strip():
                detail_lines.append(f"Actor: {actor.name.strip()}")

            attachment_count = len(
                [
                    str(document_id or "").strip()
                    for document_id in event.attachments
                    if str(document_id or "").strip()
                ]
            )
            if attachment_count > 0:
                detail_lines.append(f"Attachments: {attachment_count}")

            timeline_rows.append(
                (
                    str(event.event_date or "").strip(),
                    event_type,
                    str(event.summary or "").strip(),
                    tuple(detail_lines),
                    str(event.event_id or "").strip(),
                )
            )
        return timeline_rows

    def _timeline_next_action_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        ordered_events = list(enumerate(permit.events))
        ordered_events.sort(
            key=lambda pair: self._event_sort_key(pair[1], pair[0]),
        )

        contacts_by_id = {row.contact_id: row for row in self._contacts}
        timeline_rows: list[_TimelineRenderRow] = []
        for _index, event in ordered_events:
            event_type = normalize_event_type(event.event_type)
            if event_type != "note":
                continue

            detail_lines: list[str] = []
            detail_text = str(event.detail or "").strip()
            if detail_text:
                detail_lines.append(detail_text)

            actor = contacts_by_id.get(str(event.actor_contact_id or "").strip())
            if actor is not None and actor.name.strip():
                detail_lines.append(f"Actor: {actor.name.strip()}")

            attachment_count = len(
                [
                    str(document_id or "").strip()
                    for document_id in event.attachments
                    if str(document_id or "").strip()
                ]
            )
            if attachment_count > 0:
                detail_lines.append(f"Attachments: {attachment_count}")

            summary_text = str(event.summary or "").strip() or "Next Action"
            timeline_rows.append(
                (
                    str(event.event_date or "").strip(),
                    event_type,
                    summary_text,
                    tuple(detail_lines),
                    str(event.event_id or "").strip(),
                )
            )
        return timeline_rows

    def _sync_timeline_mode_chrome(self, permit: PermitRecord | None) -> None:
        show_next_action_mode = bool(self._timeline_show_next_action_mode)
        title_label = self._timeline_title_label
        if title_label is not None:
            title_label.setText("Next Action Timeline" if show_next_action_mode else "Timeline")
        hint_label = self._timeline_hint_label
        if hint_label is not None:
            if show_next_action_mode:
                hint_label.setText(
                    "Saved next-action notes only. Use Set Next Action to add informal timeline updates."
                )
            else:
                hint_label.setText(
                    "Oldest on the left, newest on the right. Only saved events appear here."
                )
        toggle_button = self._timeline_mode_toggle_button
        if toggle_button is not None:
            toggle_button.setText(
                "Show Business Timeline" if show_next_action_mode else "Show Next Action Timeline"
            )
            toggle_button.setEnabled(permit is not None)

    def _toggle_timeline_mode(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return
        self._timeline_show_next_action_mode = not self._timeline_show_next_action_mode
        self._refresh_selected_permit_view()

    def _timeline_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        mode_name = "next_action_saved" if self._timeline_show_next_action_mode else "default_business"
        self._timeline_debug(
            "timeline_rows_build_start",
            permit_id=permit.permit_id,
            raw_event_count=len(permit.events),
            mode=mode_name,
        )
        if self._timeline_show_next_action_mode:
            timeline_rows = self._timeline_next_action_rows_for_permit(permit)
        else:
            timeline_rows = self._timeline_default_business_rows_for_permit(permit)
        self._timeline_debug(
            "timeline_rows_build_done",
            permit_id=permit.permit_id,
            row_count=len(timeline_rows),
            mode=mode_name,
            rows_preview=[
                {
                    "event_date": row[0],
                    "event_type": row[1],
                    "summary": row[2],
                    "detail_lines_count": len(row[3]),
                    "event_id": row[4],
                }
                for row in timeline_rows[:20]
            ],
        )
        return timeline_rows

    def _timeline_connector_icon_path(self) -> str:
        if self._dark_mode_enabled:
            white_arrow = Path(asset_path("icons", "white-arrow-right.png"))
            if white_arrow.exists():
                return str(white_arrow)
        return asset_path("icons", "black-arrow-right.png")

    def _refresh_timeline_list(self, permit: PermitRecord | None) -> None:
        track_widget = self._timeline_track_widget
        track_layout = self._timeline_track_layout
        timeline_scroll = self._timeline_scroll_area
        if track_widget is None or track_layout is None:
            self._timeline_debug(
                "timeline_refresh_skipped",
                reason="missing_track_widget_or_layout",
                has_track_widget=bool(track_widget is not None),
                has_track_layout=bool(track_layout is not None),
            )
            return
        self._timeline_render_sequence += 1
        render_id = self._timeline_render_sequence
        permit_id = str(permit.permit_id or "").strip() if permit is not None else ""
        self._timeline_debug(
            "timeline_refresh_start",
            render_id=render_id,
            permit_id=permit_id,
            event_count=len(permit.events) if permit is not None else 0,
            existing_layout_count=track_layout.count(),
        )

        def sync_track_width(*, stage: str) -> None:
            track_layout.activate()
            margins = track_layout.contentsMargins()
            measured_widgets: list[QWidget] = []
            hidden_widget_count = 0
            for index in range(track_layout.count()):
                item = track_layout.itemAt(index)
                child = item.widget() if item is not None else None
                if child is None:
                    continue
                measured_widgets.append(child)
                if child.isHidden():
                    hidden_widget_count += 1
            content_width = margins.left() + margins.right()
            if measured_widgets:
                content_width += sum(
                    max(
                        child.sizeHint().width(),
                        child.minimumSizeHint().width(),
                        child.minimumWidth(),
                    )
                    for child in measured_widgets
                )
                content_width += max(0, len(measured_widgets) - 1) * max(0, track_layout.spacing())
            viewport_width = timeline_scroll.viewport().width() if timeline_scroll is not None else 0
            target_width = max(1, viewport_width, content_width)
            track_widget.setFixedWidth(target_width)
            self._timeline_debug(
                "timeline_sync_track_width",
                stage=stage,
                render_id=render_id,
                permit_id=permit_id,
                measured_widget_count=len(measured_widgets),
                hidden_widget_count=hidden_widget_count,
                measured_widget_widths=[child.sizeHint().width() for child in measured_widgets],
                layout_spacing=track_layout.spacing(),
                margins=[margins.left(), margins.right()],
                content_width=content_width,
                viewport_width=viewport_width,
                target_width=target_width,
            )

        def render_empty_state(message: str) -> None:
            empty_label = QLabel(message, track_widget)
            empty_label.setObjectName("TrackerPanelEmptyState")
            empty_label.setProperty("timeline", "true")
            empty_label.setWordWrap(True)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setMinimumWidth(360)
            empty_label.setMaximumWidth(420)
            empty_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            track_layout.addWidget(empty_label, 0, Qt.AlignmentFlag.AlignVCenter)
            empty_label.show()
            track_layout.addStretch(1)
            track_widget.adjustSize()
            sync_track_width(stage="empty_immediate")
            if timeline_scroll is not None:
                timeline_scroll.horizontalScrollBar().setValue(0)
                timeline_scroll.viewport().update()
            self._timeline_debug(
                "timeline_render_empty_state",
                render_id=render_id,
                permit_id=permit_id,
                message=message,
                layout_count=track_layout.count(),
            )
            self._timeline_debug_capture_widget_widths(
                stage=f"render_empty_state:render_id={render_id}",
                permit_id=permit_id,
            )

        removed_count = 0
        while track_layout.count():
            item = track_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                # Keep widget parented while deleting so it does not briefly
                # become a top-level window during timeline rebuilds.
                child.hide()
                child.deleteLater()
                removed_count += 1
        self._timeline_debug(
            "timeline_refresh_cleared_layout",
            render_id=render_id,
            permit_id=permit_id,
            removed_widget_count=removed_count,
            remaining_layout_count=track_layout.count(),
        )

        if permit is None:
            render_empty_state(
                "No permit selected.\nChoose a permit from the list to view and manage timeline events.",
            )
            return

        timeline_rows = self._timeline_rows_for_permit(permit)
        if not timeline_rows:
            if self._timeline_show_next_action_mode:
                render_empty_state(
                    "No saved next-action entries yet.\nUse Set Next Action to add the first one.",
                )
            else:
                render_empty_state(
                    "No timeline events yet.\nClick Add Event to record the first permitting milestone.",
                )
            return

        connector_icon_path = self._timeline_connector_icon_path()
        timeline_total = len(timeline_rows)
        for row_index, (event_date, event_type, summary, detail_lines, event_id) in enumerate(timeline_rows):
            normalized_event_id = str(event_id or "").strip()
            bubble = TimelineEventBubble(
                date_text=event_date or "(no date)",
                event_type_text=event_type_label(event_type),
                summary=summary or "(no summary)",
                detail_lines=list(detail_lines),
                on_edit=(
                    (lambda pid=permit.permit_id, eid=normalized_event_id: self._edit_timeline_event(
                        permit_id=pid,
                        event_id=eid,
                    ))
                    if normalized_event_id
                    else None
                ),
                on_remove=(
                    (lambda pid=permit.permit_id, eid=normalized_event_id: self._delete_timeline_event(
                        permit_id=pid,
                        event_id=eid,
                    ))
                    if normalized_event_id
                    else None
                ),
                parent=track_widget,
            )
            track_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignVCenter)
            bubble.show()
            self._timeline_debug(
                "timeline_render_bubble",
                render_id=render_id,
                permit_id=permit_id,
                event_date=event_date,
                event_type=event_type,
                summary=summary,
                detail_lines_count=len(detail_lines),
                bubble_size_hint_width=bubble.sizeHint().width(),
                bubble_minimum_width=bubble.minimumWidth(),
                bubble_maximum_width=bubble.maximumWidth(),
            )
            if row_index < (timeline_total - 1):
                connector = QLabel(track_widget)
                connector.setObjectName("PermitTimelineConnector")
                connector.setFixedSize(20, 20)
                connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                connector_icon = QPixmap(connector_icon_path)
                if connector_icon.isNull():
                    connector.setText(">")
                else:
                    connector.setPixmap(
                        connector_icon.scaled(
                            12,
                            12,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                track_layout.addWidget(connector, 0, Qt.AlignmentFlag.AlignVCenter)
                connector.show()
                self._timeline_debug(
                    "timeline_render_connector",
                    render_id=render_id,
                    permit_id=permit_id,
                    row_index=row_index,
                    icon_path=connector_icon_path,
                    icon_loaded=not connector_icon.isNull(),
                )
        bubble_widgets: list[TimelineEventBubble] = []
        for index in range(track_layout.count()):
            item = track_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, TimelineEventBubble):
                bubble_widgets.append(widget)

        unified_badge_width = 0
        unified_badge_height = 0
        if bubble_widgets:
            for widget in bubble_widgets:
                badge_width, badge_height = widget.event_type_badge_size_hint()
                unified_badge_width = max(unified_badge_width, badge_width)
                unified_badge_height = max(unified_badge_height, badge_height)
            for widget in bubble_widgets:
                widget.set_event_type_badge_size(unified_badge_width, unified_badge_height)
        self._timeline_debug(
            "timeline_unify_event_type_badge_size",
            render_id=render_id,
            permit_id=permit_id,
            bubble_count=len(bubble_widgets),
            unified_badge_width=unified_badge_width,
            unified_badge_height=unified_badge_height,
        )

        unified_height = 0
        if bubble_widgets:
            unified_height = max(
                max(widget.sizeHint().height(), widget.minimumSizeHint().height(), widget.minimumHeight())
                for widget in bubble_widgets
            )
            for widget in bubble_widgets:
                widget.setFixedHeight(unified_height)
        self._timeline_debug(
            "timeline_unify_bubble_height",
            render_id=render_id,
            permit_id=permit_id,
            bubble_count=len(bubble_widgets),
            unified_height=unified_height,
        )
        track_layout.addStretch(1)
        track_widget.adjustSize()
        sync_track_width(stage="events_immediate")
        if timeline_scroll is not None:
            timeline_scroll.horizontalScrollBar().setValue(0)
            timeline_scroll.viewport().update()
        self._timeline_debug(
            "timeline_refresh_done",
            render_id=render_id,
            permit_id=permit_id,
            timeline_row_count=len(timeline_rows),
            layout_count=track_layout.count(),
            scroll_value=(
                timeline_scroll.horizontalScrollBar().value()
                if timeline_scroll is not None
                else 0
            ),
        )
        self._timeline_debug_capture_widget_widths(
            stage=f"render_done_immediate:render_id={render_id}",
            permit_id=permit_id,
        )
        QTimer.singleShot(
            0,
            lambda rid=render_id, pid=permit_id: (
                sync_track_width(stage=f"deferred:render_id={rid}"),
                self._timeline_debug_capture_widget_widths(
                    stage=f"render_done_deferred:render_id={rid}",
                    permit_id=pid,
                ),
            ),
        )

    def _slot_by_id(self, permit: PermitRecord, slot_id: str) -> PermitDocumentSlot | None:
        target = str(slot_id or "").strip()
        if not target:
            return None
        for slot in permit.document_slots:
            if slot.slot_id == target:
                return slot
        return None

    def _folder_by_id(self, permit: PermitRecord, folder_id: str) -> PermitDocumentFolder | None:
        target = str(folder_id or "").strip()
        if not target:
            return None
        for folder in permit.document_folders:
            if folder.folder_id == target:
                return folder
        return None

    def _document_by_id(self, permit: PermitRecord, document_id: str) -> PermitDocumentRecord | None:
        target = str(document_id or "").strip()
        if not target:
            return None
        for document in permit.documents:
            if document.document_id == target:
                return document
        return None

    def _ensure_slot_folder(self, permit: PermitRecord, slot: PermitDocumentSlot) -> PermitDocumentFolder:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        existing = self._folder_by_id(permit, folder_id)
        if existing is not None:
            return existing
        created = PermitDocumentFolder(folder_id=folder_id, name=slot.label or folder_id, parent_folder_id="")
        permit.document_folders.append(created)
        return created

    def _slot_active_cycle(self, slot: PermitDocumentSlot) -> int:
        return self._safe_positive_int(slot.active_cycle, default=1)

    def _safe_positive_int(self, value: object, *, default: int = 1) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except Exception:
            parsed = int(default)
        return max(1, parsed)

    def _documents_for_slot(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
        *,
        active_cycle_only: bool,
    ) -> list[PermitDocumentRecord]:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if not folder_id:
            return []
        active_cycle = self._slot_active_cycle(slot)
        rows: list[PermitDocumentRecord] = []
        for record in permit.documents:
            if normalize_slot_id(record.folder_id) != folder_id:
                continue
            record_cycle = self._safe_positive_int(record.cycle_index, default=1)
            if active_cycle_only and record_cycle != active_cycle:
                continue
            rows.append(record)
        return rows

    def _next_slot_revision_index(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
        *,
        cycle_index: int,
    ) -> int:
        target_cycle = self._safe_positive_int(cycle_index, default=1)
        max_revision = 0
        for record in self._documents_for_slot(permit, slot, active_cycle_only=False):
            if self._safe_positive_int(record.cycle_index, default=1) != target_cycle:
                continue
            max_revision = max(
                max_revision,
                self._safe_positive_int(record.revision_index, default=1),
            )
        return max_revision + 1

    def _cycle_folder_segment(self, cycle_index: int) -> str:
        return f"cycle-{self._safe_positive_int(cycle_index, default=1):02d}"

    def _cycle_label(self, cycle_index: int) -> str:
        return f"Cycle {self._safe_positive_int(cycle_index, default=1):02d}"

    def _active_cycle_status_counts_for_slot(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._documents_for_slot(permit, slot, active_cycle_only=True):
            review_status = normalize_document_review_status(record.review_status)
            counts[review_status] = counts.get(review_status, 0) + 1
        return counts

    def _add_centered_list_empty_state(
        self,
        widget: QListWidget,
        message: str,
    ) -> None:
        text = str(message or "").strip()
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget.addItem(item)

        label = QLabel(text, widget)
        label.setObjectName("TrackerPanelEmptyState")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMargin(8)
        widget.setItemWidget(item, label)

        def sync_empty_state_size() -> None:
            try:
                viewport = widget.viewport()
                if viewport is None:
                    return
                if widget.itemWidget(item) is not label:
                    return
                viewport_size = viewport.size()
                target_width = max(120, viewport_size.width() - 2)
                target_height = max(108, viewport_size.height() - 2)
                item.setSizeHint(QSize(target_width, target_height))
                label.setMinimumHeight(max(96, target_height - 4))
            except RuntimeError:
                return

        sync_empty_state_size()
        QTimer.singleShot(0, sync_empty_state_size)

    def _refresh_document_slots(self, permit: PermitRecord | None) -> None:
        slot_widget = self._document_slot_list_widget
        file_widget = self._document_file_list_widget
        if slot_widget is None or file_widget is None:
            return

        slot_widget.blockSignals(True)
        slot_widget.clear()
        self._document_slot_cards = {}
        file_widget.blockSignals(True)
        file_widget.clear()
        self._selected_document_id = ""

        if permit is None:
            self._selected_document_slot_id = ""
            self._add_centered_list_empty_state(
                slot_widget,
                "No permit selected.\nChoose a permit to view its checklist slots.",
            )
            self._add_centered_list_empty_state(
                file_widget,
                "No permit selected.\nChoose a permit, then select a slot to view files.",
            )
            slot_widget.blockSignals(False)
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        changed = ensure_default_document_structure(permit)
        changed = refresh_slot_status_from_documents(permit) or changed
        if changed:
            self._persist_tracker_data(show_error_dialog=False)

        file_counts = document_file_count_by_slot(permit)

        for slot in permit.document_slots:
            status = normalize_slot_status(slot.status)
            count = file_counts.get(slot.slot_id, 0)
            status_counts = self._active_cycle_status_counts_for_slot(permit, slot)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, slot.slot_id)
            slot_widget.addItem(item)
            card = DocumentChecklistSlotCard(
                slot_label=slot.label,
                slot_id=slot.folder_id or slot.slot_id,
                required=bool(slot.required),
                status=status,
                file_count=count,
                status_counts=status_counts,
                parent=slot_widget,
            )
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            slot_widget.setItemWidget(item, card)
            self._document_slot_cards[slot.slot_id] = card

        if not permit.document_slots:
            self._add_centered_list_empty_state(
                slot_widget,
                "No checklist slots are configured for this permit.",
            )
            self._add_centered_list_empty_state(
                file_widget,
                "No checklist slots are configured for this permit.",
            )
            self._selected_document_slot_id = ""
            slot_widget.blockSignals(False)
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        if self._selected_document_slot_id and self._slot_by_id(permit, self._selected_document_slot_id) is not None:
            self._select_document_slot_item(self._selected_document_slot_id)
        else:
            self._selected_document_slot_id = permit.document_slots[0].slot_id if permit.document_slots else ""
            if self._selected_document_slot_id:
                self._select_document_slot_item(self._selected_document_slot_id)

        slot_widget.blockSignals(False)
        file_widget.blockSignals(False)
        self._set_admin_list_card_selection(slot_widget)
        self._refresh_document_files(permit)

    def _select_document_slot_item(self, slot_id: str) -> None:
        widget = self._document_slot_list_widget
        if widget is None:
            return
        target = str(slot_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            return

    def _document_file_icon_for_record(self, document: PermitDocumentRecord, display_name: str) -> QIcon:
        extension = Path(str(display_name or "").strip()).suffix.casefold()
        cache_key = extension or "__default__"
        cached_icon = self._document_file_icon_cache.get(cache_key)
        if cached_icon is not None and not cached_icon.isNull():
            return cached_icon

        provider = self._document_file_icon_provider
        if provider is None:
            provider = QFileIconProvider()
            self._document_file_icon_provider = provider

        resolved_path: Path | None = None
        try:
            resolved_path = self._document_store.resolve_document_path(document.relative_path)
        except Exception:
            resolved_path = None

        icon = QIcon()
        if resolved_path is not None:
            icon = provider.icon(QFileInfo(str(resolved_path)))
        if icon.isNull():
            probe_name = f"sample{extension}" if extension else "sample.txt"
            icon = provider.icon(QFileInfo(probe_name))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self._document_file_icon_cache[cache_key] = icon
        return icon

    def _refresh_document_files(self, permit: PermitRecord | None) -> None:
        file_widget = self._document_file_list_widget
        if file_widget is None:
            return

        previous_selected_document_id = str(self._selected_document_id or "").strip()
        file_widget.blockSignals(True)
        file_widget.clear()
        self._selected_document_id = ""

        if permit is None:
            self._add_centered_list_empty_state(
                file_widget,
                "No permit selected.\nChoose a permit, then select a checklist slot.",
            )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._add_centered_list_empty_state(
                file_widget,
                "Select a checklist slot to view or upload documents.",
            )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        active_cycle = self._slot_active_cycle(slot)
        documents = self._documents_for_slot(
            permit,
            slot,
            active_cycle_only=True,
        )
        documents.sort(
            key=lambda row: (
                self._safe_positive_int(row.revision_index, default=1),
                row.imported_at,
                row.original_name,
                row.document_id,
            ),
            reverse=True,
        )

        if not documents:
            self._add_centered_list_empty_state(
                file_widget,
                "No files in the active cycle yet.\nUse Upload to add documents to this checklist item.",
            )
            if self._document_status_label is not None:
                self._document_status_label.setText(
                    f"{slot.label} | {self._cycle_label(active_cycle)} | {normalize_slot_status(slot.status).title()} | 0 files"
                )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=True, has_file=False)
            return

        for document in documents:
            size_text = self._format_byte_size(document.byte_size)
            date_text = self._format_imported_value(document.imported_at)
            display_name = (
                str(document.original_name or "").strip()
                or str(document.stored_name or "").strip()
                or Path(str(document.relative_path or "").strip()).name
                or "Unnamed"
            )
            extension = Path(display_name).suffix.lstrip(".").upper() or "FILE"
            icon = self._document_file_icon_for_record(document, display_name)
            review_status = normalize_document_review_status(document.review_status)
            document_cycle = self._safe_positive_int(document.cycle_index, default=1)
            document_revision = self._safe_positive_int(document.revision_index, default=1)
            version_text = (
                f"{self._cycle_label(document_cycle)}  "
                f"Rev {document_revision:02d}"
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, document.document_id)
            file_widget.addItem(item)
            card = PermitDocumentFileCard(
                file_name=display_name,
                extension_label=extension,
                meta_text=f"{size_text} | {date_text}",
                version_text=version_text,
                review_status=review_status,
                icon=icon,
                parent=file_widget,
            )
            file_widget.setItemWidget(item, card)
            item.setSizeHint(self._list_item_size_hint_for_card(card))

        selected_document_id = previous_selected_document_id
        if not any(row.document_id == selected_document_id for row in documents):
            selected_document_id = ""
        self._selected_document_id = selected_document_id
        if self._selected_document_id:
            self._select_document_file_item(self._selected_document_id)

        if self._document_status_label is not None:
            self._document_status_label.setText(
                f"{slot.label} | {self._cycle_label(active_cycle)} | {normalize_slot_status(slot.status).title()} | {len(documents)} files"
            )

        file_widget.blockSignals(False)
        self._set_admin_list_card_selection(file_widget)
        self._sync_document_action_buttons(
            enabled=True,
            has_file=bool(self._selected_document_id),
        )

    def _select_document_file_item(self, document_id: str) -> None:
        widget = self._document_file_list_widget
        if widget is None:
            return
        target = str(document_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            return

    def _sync_document_action_buttons(self, *, enabled: bool, has_file: bool = False) -> None:
        for control in (
            self._document_upload_button,
            self._document_open_folder_button,
        ):
            if control is not None:
                control.setEnabled(enabled)
        if self._document_new_cycle_button is not None:
            self._document_new_cycle_button.setEnabled(enabled and has_file)
        if self._document_open_file_button is not None:
            self._document_open_file_button.setEnabled(enabled and has_file)
        if self._document_remove_file_button is not None:
            self._document_remove_file_button.setEnabled(enabled and has_file)
        for control in (self._document_mark_accepted_button, self._document_mark_rejected_button):
            if control is not None:
                control.setEnabled(enabled)

    def _refresh_selected_permit_view(self) -> None:
        permit = self._selected_permit()
        self._refresh_document_template_apply_options()
        property_record = self._selected_property()
        if permit is not None:
            property_record = self._property_by_id(permit.property_id) or property_record
        property_has_permits = bool(
            property_record is not None and self._permits_for_property(property_record.property_id)
        )
        if property_record is None:
            self._set_workspace_next_step_hint(
                "Next Action: Select an address in Address List, then choose an existing permit or click Add Permit."
            )
        elif not property_has_permits:
            self._set_workspace_next_step_hint(
                "Next Action: Click Add Permit to create the first permit for this address."
            )
        else:
            self._set_workspace_next_step_hint("")
        self._timeline_debug(
            "refresh_selected_permit_view_start",
            permit_id=permit.permit_id if permit is not None else "",
            property_id=property_record.property_id if property_record is not None else "",
            permit_event_count=len(permit.events) if permit is not None else 0,
        )
        self._set_permit_workspace_blur(permit is None)
        self._sync_timeline_mode_chrome(permit)
        any_properties = bool(self._properties)
        any_permits = bool(self._permits)

        if permit is None:
            selected_jurisdiction = (
                self._jurisdiction_by_id(property_record.jurisdiction_id)
                if property_record is not None
                else None
            )
            selected_portal = self._portal_url_for_property(property_record)
            contacts_portal_text = self._workspace_contacts_portal_text(
                property_record=property_record,
                permit=None,
                jurisdiction=selected_jurisdiction,
                portal_url=selected_portal,
            )
            if not any_properties:
                self._set_workspace_title_state("Add Your First Address")
            elif property_record is None:
                self._set_workspace_title_state("Select Address")
            elif not any_permits:
                self._set_workspace_title_state("Add Permit")
            else:
                self._set_workspace_title_state("Select Permit")
            if not any_properties:
                self._set_workspace_info_values(
                    address="No address yet",
                    parcel="—",
                    jurisdiction="—",
                    permit_number="—",
                    status="Not Started",
                    contacts_portal="Add jurisdiction portal in Admin",
                )
            elif property_record is None:
                self._set_workspace_info_values(
                    address="Select an address",
                    parcel="—",
                    jurisdiction="—",
                    permit_number="—",
                    status="Awaiting Selection",
                    contacts_portal="—",
                )
            elif not self._permits_for_property(property_record.property_id):
                self._set_workspace_info_values(
                    address=property_record.display_address or "(no address)",
                    parcel=property_record.parcel_id or "(no parcel)",
                    jurisdiction=(
                        selected_jurisdiction.name
                        if selected_jurisdiction is not None and selected_jurisdiction.name.strip()
                        else "Unassigned"
                    ),
                    permit_number="No permits yet",
                    status="Awaiting Permit",
                    contacts_portal=contacts_portal_text,
                )
            else:
                self._set_workspace_info_values(
                    address=property_record.display_address or "(no address)",
                    parcel=property_record.parcel_id or "(no parcel)",
                    jurisdiction=(
                        selected_jurisdiction.name
                        if selected_jurisdiction is not None and selected_jurisdiction.name.strip()
                        else "Unassigned"
                    ),
                    permit_number="Select permit",
                    status="Awaiting Selection",
                    contacts_portal=contacts_portal_text,
                )
            if self._next_action_label is not None:
                self._next_action_label.setText(
                    "No permit selected.\nSelect a permit, then set the next action and due date."
                )
            if self._open_portal_button is not None:
                self._open_portal_button.setEnabled(False)
            if self._set_next_action_button is not None:
                self._set_next_action_button.setEnabled(False)
            if self._add_event_button is not None:
                self._add_event_button.setEnabled(False)
            if self._document_status_label is not None:
                self._document_status_label.setText(
                    "Select a permit to manage its checklist and files."
                )
            self._refresh_timeline_list(None)
            self._refresh_document_slots(None)
            self._timeline_debug(
                "refresh_selected_permit_view_done",
                permit_id="",
                mode="empty_state",
            )
            return

        permit.status = compute_permit_status(permit.events, fallback=permit.status)

        property_record = self._property_by_id(permit.property_id)
        parcel_id = property_record.parcel_id if property_record is not None else ""
        permit_number_text = permit.permit_number.strip() or "(no permit # yet)"
        self._set_workspace_title_state(f"Viewing: {permit_number_text}")

        jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id) if property_record else None
        jurisdiction_name = jurisdiction.name if jurisdiction is not None else "Unassigned"
        portal_url = self._portal_url_for_property(property_record)
        contacts_portal_text = self._workspace_contacts_portal_text(
            property_record=property_record,
            permit=permit,
            jurisdiction=jurisdiction,
            portal_url=portal_url,
        )

        self._set_workspace_info_values(
            address=property_record.display_address if property_record is not None else "(unknown)",
            parcel=parcel_id or "(none)",
            jurisdiction=jurisdiction_name,
            permit_number=permit.permit_number or "(no permit # yet)",
            status=event_type_label(permit.status),
            contacts_portal=contacts_portal_text,
        )

        if self._open_portal_button is not None:
            self._open_portal_button.setEnabled(bool(portal_url))
        if self._set_next_action_button is not None:
            self._set_next_action_button.setEnabled(True)
        if self._add_event_button is not None:
            self._add_event_button.setEnabled(True)

        next_action_text = permit.next_action_text or "No next action set."
        due_text = permit.next_action_due or "No due date"
        if self._next_action_label is not None:
            self._next_action_label.setText(f"{next_action_text}\nDue: {due_text}")

        self._refresh_timeline_list(permit)
        self._refresh_document_slots(permit)
        self._timeline_debug(
            "refresh_selected_permit_view_done",
            permit_id=permit.permit_id,
            permit_status=permit.status,
            permit_event_count=len(permit.events),
        )

    def _on_document_slot_selection_changed(self) -> None:
        widget = self._document_slot_list_widget
        current_item = widget.currentItem() if widget is not None else None
        self._selected_document_slot_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        )
        self._set_admin_list_card_selection(widget)
        self._selected_document_id = ""
        self._refresh_document_files(self._selected_permit())

    def _on_document_file_selection_changed(self) -> None:
        widget = self._document_file_list_widget
        current_item = widget.currentItem() if widget is not None else None
        self._selected_document_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        )
        self._set_admin_list_card_selection(widget)
        self._sync_document_action_buttons(
            enabled=bool(self._selected_permit() and self._selected_document_slot_id),
            has_file=bool(self._selected_document_id),
        )

    def _confirm_duplicate_property_parcel(self, property_record: PropertyRecord) -> bool:
        if not property_record.parcel_id_norm:
            return True
        duplicate = next(
            (
                row
                for row in self._properties
                if row.parcel_id_norm
                and row.parcel_id_norm == property_record.parcel_id_norm
                and row.property_id != property_record.property_id
            ),
            None,
        )
        if duplicate is None:
            return True
        return self._confirm_dialog(
            "Duplicate Parcel",
            "Another address already uses this normalized parcel id.\n\n"
            f"Existing: {duplicate.display_address or '(no address)'}\n"
            f"New: {property_record.display_address or '(no address)'}\n\n"
            "Save anyway?",
            confirm_text="Save Anyway",
            cancel_text="Cancel",
        )

    def _edit_property_record(self, property_id: str) -> None:
        property_record = self._property_by_id(property_id)
        if property_record is None:
            return
        self._selected_property_id = property_record.property_id
        self._open_edit_property_view(property_record)

    def _delete_property_record(self, property_id: str) -> None:
        property_record = self._property_by_id(property_id)
        if property_record is None:
            return
        permits_for_property = [
            permit for permit in self._permits if permit.property_id == property_record.property_id
        ]
        delete_message = f"Delete address '{property_record.display_address or '(no address)'}'?"
        if permits_for_property:
            delete_message = (
                f"{delete_message}\n\n"
                f"This will also delete {len(permits_for_property)} permit(s) under this address."
            )
        confirmed = self._confirm_dialog(
            "Delete Address",
            delete_message,
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        for permit in permits_for_property:
            try:
                self._document_store.delete_permit_tree(permit)
            except Exception:
                pass
        self._properties = [row for row in self._properties if row.property_id != property_record.property_id]
        self._permits = [row for row in self._permits if row.property_id != property_record.property_id]
        if self._selected_property_id == property_record.property_id:
            self._selected_property_id = ""
        if self._selected_permit_id and self._permit_by_id(self._selected_permit_id) is None:
            self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_all_views()

    def _edit_permit_record(self, permit_id: str) -> None:
        permit = self._permit_by_id(permit_id)
        if permit is None:
            return
        property_record = self._property_by_id(permit.property_id)
        if property_record is None:
            self._show_warning_dialog(
                "Missing Address",
                "This permit is not linked to a valid address record.",
            )
            return
        self._selected_property_id = property_record.property_id
        self._selected_permit_id = permit.permit_id
        self._open_edit_permit_view(property_record=property_record, permit=permit)

    def _delete_permit_record(self, permit_id: str) -> None:
        permit = self._permit_by_id(permit_id)
        if permit is None:
            return
        permit_label = permit.permit_number.strip() or "(no permit # yet)"
        confirmed = self._confirm_dialog(
            "Delete Permit",
            f"Delete permit '{permit_label}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        try:
            self._document_store.delete_permit_tree(permit)
        except Exception:
            pass
        self._permits = [row for row in self._permits if row.permit_id != permit.permit_id]
        if self._selected_permit_id == permit.permit_id:
            self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_all_views()

    def _add_property(self) -> None:
        self._open_add_property_view()

    def _add_permit(self) -> None:
        self._open_add_permit_view()

    def _set_next_action(self) -> None:
        permit = self._selected_permit()
        self._timeline_debug(
            "set_next_action_start",
            permit_id=permit.permit_id if permit is not None else "",
            permit_status=permit.status if permit is not None else "",
        )
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return

        dialog = NextActionDialog(
            current_text=permit.next_action_text,
            current_due=permit.next_action_due,
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._timeline_debug(
                "set_next_action_cancelled",
                permit_id=permit.permit_id,
            )
            return

        previous_text = str(permit.next_action_text or "").strip()
        previous_due = str(permit.next_action_due or "").strip()
        action_text, action_due = dialog.values()
        permit.next_action_text = action_text
        permit.next_action_due = action_due
        appended_next_action_note = False
        if (
            (action_text or action_due)
            and (action_text != previous_text or action_due != previous_due)
        ):
            note_event_date = action_due if _parse_iso_date(action_due) is not None else _today_iso()
            permit.events.append(
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type="note",
                    event_date=note_event_date,
                    summary=action_text or "Next Action Updated",
                    detail=_next_action_detail_text(action_due),
                    actor_contact_id="",
                    attachments=[],
                )
            )
            appended_next_action_note = True
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        persisted = self._persist_tracker_data()
        self._timeline_debug(
            "set_next_action_saved",
            permit_id=permit.permit_id,
            next_action_text=permit.next_action_text,
            next_action_due=permit.next_action_due,
            appended_next_action_note=appended_next_action_note,
            persisted=persisted,
        )
        self._refresh_permit_list(keep_selected_visible=True)

    def _latest_note_event_id_for_permit(self, permit: PermitRecord) -> str:
        latest_note_event_id = ""
        latest_note_key: tuple[datetime, int] | None = None
        for index, event in enumerate(permit.events):
            if normalize_event_type(event.event_type) != "note":
                continue
            event_key = self._event_sort_key(event, index)
            if latest_note_key is None or event_key > latest_note_key:
                latest_note_key = event_key
                latest_note_event_id = str(event.event_id or "").strip()
        return latest_note_event_id

    def _edit_timeline_event(self, *, permit_id: str, event_id: str) -> None:
        normalized_permit_id = str(permit_id or "").strip()
        normalized_event_id = str(event_id or "").strip()
        if not normalized_permit_id or not normalized_event_id:
            return

        permit = self._permit_by_id(normalized_permit_id)
        if permit is None:
            self._show_info_dialog("Permit Missing", "The selected permit could not be found.")
            return

        target_event = next(
            (
                row
                for row in permit.events
                if str(row.event_id or "").strip() == normalized_event_id
            ),
            None,
        )
        if target_event is None:
            self._show_info_dialog("Event Missing", "The selected timeline event could not be found.")
            return

        event_type = normalize_event_type(target_event.event_type)
        if event_type == "note":
            previous_latest_note_event_id = self._latest_note_event_id_for_permit(permit)
            dialog = NextActionTimelineEntryDialog(
                current_date=str(target_event.event_date or "").strip() or _today_iso(),
                current_text=str(target_event.summary or "").strip(),
                current_due=_extract_due_from_next_action_detail(target_event.detail),
                parent=self,
                theme_mode=self._dialog_theme_mode(),
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            event_date, action_text, action_due = dialog.values()
            target_event.event_date = event_date
            target_event.summary = action_text
            target_event.detail = _next_action_detail_text(action_due)

            if normalized_event_id == previous_latest_note_event_id:
                permit.next_action_text = action_text
                permit.next_action_due = action_due
        else:
            dialog = TimelineEventEditDialog(
                event=target_event,
                contacts=self._contacts,
                parent=self,
                theme_mode=self._dialog_theme_mode(),
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            updated_type, updated_date, updated_summary, updated_detail, updated_actor = dialog.values()
            target_event.event_type = updated_type
            target_event.event_date = updated_date
            target_event.summary = updated_summary
            target_event.detail = updated_detail
            target_event.actor_contact_id = updated_actor

        permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        self._persist_tracker_data()
        self._refresh_permit_list(keep_selected_visible=True)

    def _delete_timeline_event(self, *, permit_id: str, event_id: str) -> None:
        normalized_permit_id = str(permit_id or "").strip()
        normalized_event_id = str(event_id or "").strip()
        if not normalized_permit_id or not normalized_event_id:
            return

        permit = self._permit_by_id(normalized_permit_id)
        if permit is None:
            self._show_info_dialog("Permit Missing", "The selected permit could not be found.")
            return

        target_event = next(
            (
                row
                for row in permit.events
                if str(row.event_id or "").strip() == normalized_event_id
            ),
            None,
        )
        if target_event is None:
            self._show_info_dialog("Event Missing", "The selected timeline event could not be found.")
            return

        event_title = str(target_event.summary or "").strip() or event_type_label(target_event.event_type)
        event_date = str(target_event.event_date or "").strip() or "no date"
        if not self._confirm_dialog(
            "Delete Timeline Event",
            f"Delete '{event_title}' dated {event_date} from this timeline?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        ):
            return

        remaining_events = [
            row
            for row in permit.events
            if str(row.event_id or "").strip() != normalized_event_id
        ]
        if len(remaining_events) == len(permit.events):
            return
        permit.events = remaining_events
        permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        self._persist_tracker_data()
        self._refresh_permit_list(keep_selected_visible=True)

    def _add_event(self) -> None:
        permit = self._selected_permit()
        self._timeline_debug(
            "add_event_start",
            permit_id=permit.permit_id if permit is not None else "",
            permit_event_count=len(permit.events) if permit is not None else 0,
        )
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return

        existing_event_types: set[str] = {
            normalize_event_type(event.event_type)
            for event in permit.events
            if normalize_event_type(event.event_type) in PERMIT_EVENT_TYPES
        }
        available_event_types = [
            event_type
            for event_type in PERMIT_EVENT_TYPES
            if event_type not in existing_event_types
        ]
        if not available_event_types:
            self._show_info_dialog(
                "No Event Types Available",
                "All event types are already present on this timeline.",
            )
            return

        dialog = PermitEventDialog(
            contacts=self._contacts,
            available_event_types=available_event_types,
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._timeline_debug(
                "add_event_cancelled",
                permit_id=permit.permit_id,
                permit_event_count=len(permit.events),
            )
            return

        event = dialog.build_event()
        self._timeline_debug(
            "add_event_built",
            permit_id=permit.permit_id,
            event_id=event.event_id,
            event_type=event.event_type,
            event_date=event.event_date,
            summary=event.summary,
            detail=event.detail,
            actor_contact_id=event.actor_contact_id,
        )
        permit.events.append(event)
        if event_affects_status(event.event_type):
            permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        persisted = self._persist_tracker_data()
        self._timeline_debug(
            "add_event_appended",
            permit_id=permit.permit_id,
            permit_event_count=len(permit.events),
            permit_status=permit.status,
            persisted=persisted,
        )
        self._refresh_permit_list(keep_selected_visible=True)
        selected_after_refresh = self._selected_permit()
        self._timeline_debug(
            "add_event_after_refresh",
            selected_permit_id=self._selected_permit_id,
            selected_property_id=self._selected_property_id,
            selected_permit_event_count=(
                len(selected_after_refresh.events)
                if selected_after_refresh is not None
                else 0
            ),
            permits_list_count=(
                self._permits_list_widget.count()
                if self._permits_list_widget is not None
                else 0
            ),
        )

    def _open_selected_portal(self) -> None:
        property_record = self._selected_property()
        permit = self._selected_permit()
        if permit is not None:
            property_record = self._property_by_id(permit.property_id) or property_record
        portal_url = self._portal_url_for_property(property_record)
        if not portal_url:
            self._show_info_dialog("Portal Missing", "No jurisdiction portal URL is configured.")
            return
        QDesktopServices.openUrl(QUrl(portal_url))

    def _upload_documents_to_slot(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return

        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._show_info_dialog("Select Slot", "Select a document checklist slot first.")
            return

        folder = self._ensure_slot_folder(permit, slot)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog("Storage Error", f"Could not prepare permit folders.\n\n{exc}")
            return

        file_paths, _filter_used = QFileDialog.getOpenFileNames(
            self,
            "Upload Documents",
            "",
            "All Files (*)",
        )
        if not file_paths:
            return

        active_cycle = self._slot_active_cycle(slot)
        cycle_segment = self._cycle_folder_segment(active_cycle)
        failures: list[str] = []
        imported_count = 0
        for raw_path in file_paths:
            source_path = Path(raw_path)
            try:
                document = self._document_store.import_document(
                    permit=permit,
                    folder=folder,
                    source_path=source_path,
                    cycle_folder=cycle_segment,
                )
            except Exception as exc:
                failures.append(f"{source_path.name}: {exc}")
                continue
            document.folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
            document.slot_id = slot.slot_id
            document.cycle_index = active_cycle
            document.revision_index = self._next_slot_revision_index(
                permit,
                slot,
                cycle_index=active_cycle,
            )
            document.review_status = "uploaded"
            document.reviewed_at = ""
            document.review_note = ""
            permit.documents.append(document)
            imported_count += 1
            self._selected_document_id = document.document_id

        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

        if failures:
            preview = "\n".join(failures[:6])
            suffix = ""
            if len(failures) > 6:
                suffix = f"\n...and {len(failures) - 6} more."
            self._show_warning_dialog("Some Uploads Failed", f"{preview}{suffix}")

    def _open_selected_slot_folder(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            return

        folder = self._ensure_slot_folder(permit, slot)
        try:
            folder_path = self._document_store.folder_path(permit, folder)
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_warning_dialog("Folder Error", f"Could not open folder.\n\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))

    def _start_selected_slot_new_cycle(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._show_info_dialog("Select Slot", "Select a checklist slot first.")
            return

        current_cycle = self._slot_active_cycle(slot)
        existing_cycle_docs = self._documents_for_slot(
            permit,
            slot,
            active_cycle_only=True,
        )
        if not existing_cycle_docs:
            self._show_info_dialog(
                "Cycle Already Empty",
                "The active cycle has no files yet. Upload files to this cycle first.",
            )
            return

        next_cycle = current_cycle + 1
        if not self._confirm_dialog(
            "Start New Cycle",
            (
                f"Start {self._cycle_label(next_cycle)} for '{slot.label or slot.slot_id}'?\n\n"
                "This keeps existing files in previous cycles and sets this slot back to Missing "
                "until new files are uploaded."
            ),
            confirm_text="Start New Cycle",
            cancel_text="Cancel",
        ):
            return

        slot.active_cycle = next_cycle
        slot.status = "missing"
        permit.events.append(
            PermitEventRecord(
                event_id=uuid4().hex,
                event_type="note",
                event_date=_today_iso(),
                summary=f"Started {self._cycle_label(next_cycle)} for {slot.label or slot.slot_id}",
                detail="Document resubmission cycle advanced.",
            )
        )
        refresh_slot_status_from_documents(permit)
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _open_selected_document(self, _item: QListWidgetItem | None = None) -> None:
        permit = self._selected_permit()
        if permit is None:
            return

        widget = self._document_file_list_widget
        if _item is None and widget is not None:
            _item = widget.currentItem()
        if _item is not None:
            self._selected_document_id = str(_item.data(Qt.ItemDataRole.UserRole) or "").strip()

        document = self._document_by_id(permit, self._selected_document_id)
        if document is None:
            return

        file_path = self._document_store.resolve_document_path(document.relative_path)
        if file_path is None or not file_path.exists():
            self._show_warning_dialog(
                "Missing File",
                "The selected file could not be found in local storage.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))

    def _remove_selected_document(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        document = self._document_by_id(permit, self._selected_document_id)
        if document is None:
            return

        document_name = document.original_name or document.stored_name or "selected document"
        confirmed = self._confirm_dialog(
            "Remove File",
            f"Remove '{document_name}' from this permit?",
            confirm_text="Remove",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        self._document_store.delete_document_file(document)
        permit.documents = [row for row in permit.documents if row.document_id != document.document_id]
        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _mark_selected_slot_status(self, status: str) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            return

        target_status = normalize_document_review_status(status)
        if target_status not in {"accepted", "rejected"}:
            return

        slot_folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        slot_documents = [
            record
            for record in permit.documents
            if normalize_slot_id(record.folder_id) == slot_folder_id
        ]
        if not slot_documents:
            self._show_info_dialog(
                "No Files",
                "This checklist slot has no files to mark yet.",
            )
            return

        widget = self._document_file_list_widget
        if widget is not None:
            current_item = widget.currentItem()
            if current_item is not None:
                self._selected_document_id = str(
                    current_item.data(Qt.ItemDataRole.UserRole) or ""
                ).strip()

        document = self._document_by_id(permit, self._selected_document_id)
        use_bulk_slot_mark = (
            document is None
            or normalize_slot_id(document.folder_id) != slot_folder_id
        )

        if use_bulk_slot_mark:
            action_text = "Accepted" if target_status == "accepted" else "Rejected"
            if not self._confirm_dialog(
                f"Mark {action_text}",
                (
                    f"No file is selected.\n\n"
                    f"Mark all files in '{slot.label or slot.slot_id}' as {action_text.lower()}?\n"
                    "This will update the status of every file in this folder."
                ),
                confirm_text=f"Mark All {action_text}",
                cancel_text="Cancel",
                danger=(target_status == "rejected"),
            ):
                return
            changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for record in slot_documents:
                record.review_status = target_status
                record.reviewed_at = changed_at
                record.review_note = ""
        else:
            document_cycle = self._safe_positive_int(document.cycle_index, default=1)
            document.review_status = target_status
            document.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            document.review_note = ""

            if target_status == "accepted":
                for record in permit.documents:
                    if record.document_id == document.document_id:
                        continue
                    if normalize_slot_id(record.folder_id) != slot_folder_id:
                        continue
                    if self._safe_positive_int(record.cycle_index, default=1) != document_cycle:
                        continue
                    peer_status = normalize_document_review_status(record.review_status)
                    if peer_status in {"uploaded", "accepted"}:
                        record.review_status = "superseded"
                        record.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _format_byte_size(self, raw_size: int) -> str:
        size = max(0, int(raw_size or 0))
        units = ("B", "KB", "MB", "GB")
        scaled = float(size)
        unit = units[0]
        for candidate in units:
            unit = candidate
            if scaled < 1024.0 or candidate == units[-1]:
                break
            scaled = scaled / 1024.0
        if unit == "B":
            return f"{int(scaled)} {unit}"
        return f"{scaled:.1f} {unit}"

    def _format_imported_value(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "Unknown import date"
        if len(text) >= 10:
            return text[:10]
        return text

    def _close_to_home_view(self) -> None:
        self._selected_property_id = ""
        self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        if self._panel_stack is not None and self._panel_home_view is not None:
            self._panel_stack.setCurrentWidget(self._panel_home_view)

    def _initialize_data_store(self) -> str:
        warning_lines: list[str] = []

        configured_backend = str(self._data_storage_backend or "").strip().lower()
        if configured_backend == BACKEND_SUPABASE:
            warning_lines.append("Supabase data storage is not enabled yet. Using local JSON storage.")
            self._data_storage_backend = BACKEND_LOCAL_JSON
        elif configured_backend != BACKEND_LOCAL_JSON:
            self._data_storage_backend = BACKEND_LOCAL_JSON

        if self._data_storage_backend != configured_backend:
            save_data_storage_backend(self._data_storage_backend)

        configured_folder = self._data_storage_folder
        self._data_storage_folder = normalize_data_storage_folder(configured_folder)
        if self._data_storage_folder != configured_folder:
            save_data_storage_folder(self._data_storage_folder)

        self._data_store = LocalJsonDataStore(self._data_storage_folder)
        self._document_store.update_data_root(self._data_storage_folder)

        load_result = self._data_store.load_bundle()
        migrated = self._apply_tracker_bundle(load_result.bundle, refresh_ui=False)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)
        if load_result.warning:
            warning_lines.append(load_result.warning)

        self._state_streamer.record(
            "data.loaded",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "source": load_result.source,
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
            },
        )

        return "\n\n".join(line for line in warning_lines if line.strip())

    def _snapshot_tracker_bundle(self) -> TrackerDataBundleV3:
        return TrackerDataBundleV3(
            contacts=[ContactRecord.from_mapping(record.to_mapping()) for record in self._contacts],
            jurisdictions=[JurisdictionRecord.from_mapping(record.to_mapping()) for record in self._jurisdictions],
            properties=[PropertyRecord.from_mapping(record.to_mapping()) for record in self._properties],
            permits=[PermitRecord.from_mapping(record.to_mapping()) for record in self._permits],
            document_templates=[
                DocumentChecklistTemplate.from_mapping(record.to_mapping())
                for record in self._document_templates
            ],
            active_document_template_ids=dict(self._active_document_template_ids),
        )

    def _apply_tracker_bundle(self, bundle: TrackerDataBundleV3, *, refresh_ui: bool) -> bool:
        cloned_bundle = bundle.clone()
        self._contacts = list(cloned_bundle.contacts)
        self._jurisdictions = list(cloned_bundle.jurisdictions)
        self._properties = list(cloned_bundle.properties)
        self._permits = list(cloned_bundle.permits)
        self._document_templates = list(cloned_bundle.document_templates)
        self._active_document_template_ids = dict(cloned_bundle.active_document_template_ids)
        before_active_templates = dict(self._active_document_template_ids)
        self._prune_active_document_template_ids()

        migrated = False
        if before_active_templates != self._active_document_template_ids:
            migrated = True

        for property_record in self._properties:
            normalized = normalize_parcel_id(property_record.parcel_id)
            if property_record.parcel_id_norm != normalized:
                property_record.parcel_id_norm = normalized
                migrated = True

        for permit in self._permits:
            before_status = permit.status
            changed = ensure_default_document_structure(permit)
            changed = refresh_slot_status_from_documents(permit) or changed
            permit.status = compute_permit_status(permit.events, fallback=permit.status)
            if changed or permit.status != before_status:
                migrated = True

        for template in self._document_templates:
            normalized_slots = build_document_slots_from_template(
                template,
                permit_type=template.permit_type,
            )
            if self._template_slots_snapshot(template.slots) != self._template_slots_snapshot(normalized_slots):
                template.slots = normalized_slots
                migrated = True

        if refresh_ui:
            self._refresh_all_views()

        return migrated

    def _persist_tracker_data(self, *, show_error_dialog: bool = True) -> bool:
        bundle = self._snapshot_tracker_bundle()
        try:
            self._data_store.save_bundle(bundle)
        except Exception as exc:
            if show_error_dialog:
                self._show_warning_dialog("Storage Error", f"Could not save local data.\n\n{exc}")
            self._state_streamer.record(
                "data.save_failed",
                source="main_window",
                payload={
                    "backend": self._data_storage_backend,
                    "folder": str(self._data_storage_folder),
                    "error": str(exc),
                },
            )
            return False

        self._state_streamer.record(
            "data.saved",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "path": str(self._data_store.storage_file_path),
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
                "document_templates": len(self._document_templates),
            },
        )
        return True

    def _on_data_storage_folder_changed(self, requested_folder: str) -> str:
        target_folder = normalize_data_storage_folder(requested_folder)
        if target_folder == self._data_storage_folder:
            return str(self._data_storage_folder)

        target_store = LocalJsonDataStore(target_folder)
        loaded_existing = False
        warning_message = ""

        try:
            if target_store.has_saved_data():
                load_result = target_store.load_bundle()
                if load_result.source == "empty" and load_result.warning:
                    raise RuntimeError(
                        "The selected folder contains unreadable data. "
                        "Choose a different folder or repair the data file first."
                    )
                target_bundle = load_result.bundle
                warning_message = load_result.warning
                loaded_existing = True
            else:
                target_bundle = TrackerDataBundleV3()
        except Exception as exc:
            self._show_warning_dialog(
                "Storage Folder Error",
                f"Could not switch storage folder.\n\n{exc}",
            )
            self._state_streamer.record(
                "data.folder_switch_failed",
                source="main_window",
                payload={
                    "from": str(self._data_storage_folder),
                    "to": str(target_folder),
                    "error": str(exc),
                },
            )
            return str(self._data_storage_folder)

        self._data_store = target_store
        self._data_storage_backend = BACKEND_LOCAL_JSON
        self._data_storage_folder = target_store.data_root
        self._document_store.update_data_root(self._data_storage_folder)
        save_data_storage_backend(self._data_storage_backend)
        save_data_storage_folder(self._data_storage_folder)

        self._close_to_home_view()
        migrated = self._apply_tracker_bundle(target_bundle, refresh_ui=True)
        if migrated:
            self._persist_tracker_data(show_error_dialog=False)

        self._state_streamer.record(
            "data.folder_switched",
            source="main_window",
            payload={
                "backend": self._data_storage_backend,
                "folder": str(self._data_storage_folder),
                "loaded_existing": loaded_existing,
                "contacts": len(self._contacts),
                "jurisdictions": len(self._jurisdictions),
                "properties": len(self._properties),
                "permits": len(self._permits),
            },
        )

        if warning_message:
            self._show_data_storage_warning(warning_message)
        elif loaded_existing:
            self._show_info_dialog(
                "Storage Folder Updated",
                f"Loaded existing data from:\n{self._data_storage_folder}",
            )
        else:
            self._show_info_dialog(
                "Storage Folder Updated",
                f"No saved data found in:\n{self._data_storage_folder}\n\n"
                "The tracker was reset to empty for this folder.",
            )

        return str(self._data_storage_folder)

    def _show_data_storage_warning(self, message: str) -> None:
        text = message.strip()
        if not text:
            return
        self._show_warning_dialog("Data Storage Notice", text)

    def _check_for_updates_on_startup(self) -> None:
        if not self._auto_update_github_repo:
            return
        self._check_for_updates(manual=False)

    def _set_update_settings_status(self, text: str, *, checking: bool) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            return
        dialog.set_update_status(text)
        dialog.set_update_check_running(checking)

    def _on_check_updates_requested(self) -> None:
        self._check_for_updates(manual=True)

    def _check_for_updates(self, *, manual: bool) -> None:
        if self._update_check_in_progress:
            if manual:
                self._show_info_dialog("Update Check", "An update check is already in progress.")
            return

        if not self._auto_update_github_repo:
            self._set_update_settings_status("Update source is not configured in this build.", checking=False)
            if manual:
                self._show_warning_dialog(
                    "Repository Required",
                    "This build does not define an update source.\n\n"
                    "Set GITHUB_RELEASE_REPO in src/erpermitsys/version.py and rebuild.",
                )
            return

        self._update_check_in_progress = True
        self._set_update_settings_status("Checking for updates...", checking=True)
        self._state_streamer.record(
            "updates.check_started",
            source="main_window",
            payload={
                "manual": manual,
                "repo": self._auto_update_github_repo,
                "asset_name": self._auto_update_asset_name,
            },
        )

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            result = self._updater.check_for_update(
                repo=self._auto_update_github_repo,
                current_version=self._app_version,
                asset_name=self._auto_update_asset_name,
            )
        finally:
            self._update_check_in_progress = False
            if app is not None and app.overrideCursor() is not None:
                app.restoreOverrideCursor()

        self._handle_update_check_result(result, manual=manual)

    def _handle_update_check_result(self, result: GitHubUpdateCheckResult, *, manual: bool) -> None:
        message = result.message.strip()
        status = result.status
        info = result.info

        if status == "update_available" and info is not None:
            self._set_update_settings_status(f"Update available: v{info.latest_version}.", checking=False)
            self._state_streamer.record(
                "updates.available",
                source="main_window",
                payload={
                    "current_version": info.current_version,
                    "latest_version": info.latest_version,
                    "repo": info.repo,
                    "asset": info.asset.name if info.asset is not None else "",
                },
            )
            confirm = self._confirm_dialog(
                "Update Available",
                self._format_update_confirmation_message(info),
                confirm_text="Update Now",
                cancel_text="Later",
            )
            if confirm:
                self._download_and_apply_update(info)
            else:
                self._set_update_settings_status("Update postponed.", checking=False)
            return

        if status == "up_to_date":
            self._set_update_settings_status(message or "You are on the latest version.", checking=False)
            if manual:
                self._show_info_dialog("Update Check", message or "You are on the latest version.")
            return

        if status in ("not_configured", "no_release", "no_compatible_asset"):
            self._set_update_settings_status(message, checking=False)
            if manual:
                self._show_warning_dialog("Update Check", message)
            return

        self._set_update_settings_status(message or "Update check failed.", checking=False)
        if manual:
            self._show_warning_dialog("Update Check Failed", message or "Unknown update error.")

    def _format_update_confirmation_message(self, info: GitHubUpdateInfo) -> str:
        lines: list[str] = [
            "A new version is available.",
            "",
            f"Current: v{info.current_version}",
            f"Latest: v{info.latest_version}",
        ]
        if info.published_at:
            lines.append(f"Published: {info.published_at}")
        if info.asset is not None:
            lines.append(f"Asset: {info.asset.name}")
        lines.append("")
        notes = info.notes.strip()
        if notes:
            compact_notes = " ".join(notes.split())
            if len(compact_notes) > 260:
                compact_notes = f"{compact_notes[:257]}..."
            lines.append(f"Release notes: {compact_notes}")
            lines.append("")
        lines.append("Install this update now?")
        return "\n".join(lines)

    def _download_and_apply_update(self, info: GitHubUpdateInfo) -> None:
        asset = info.asset
        if asset is None:
            self._show_warning_dialog("Update Download Missing", "This release does not include a downloadable asset.")
            self._set_update_settings_status("Release asset missing.", checking=False)
            return

        self._set_update_settings_status(f"Downloading {asset.name}...", checking=True)

        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            temp_root = Path(tempfile.mkdtemp(prefix="erpermitsys_update_"))
            archive_path = temp_root / asset.name
            downloaded_file = self._updater.download_asset(asset=asset, destination=archive_path)
        except Exception as exc:
            self._set_update_settings_status("Download failed.", checking=False)
            self._show_warning_dialog("Update Download Failed", f"Could not download update:\n\n{exc}")
            return
        finally:
            if app is not None and app.overrideCursor() is not None:
                app.restoreOverrideCursor()

        self._set_update_settings_status("Update downloaded.", checking=False)
        self._state_streamer.record(
            "updates.downloaded",
            source="main_window",
            payload={
                "latest_version": info.latest_version,
                "file": str(downloaded_file),
                "asset": asset.name,
            },
        )

        is_zip = downloaded_file.name.lower().endswith(".zip")
        if can_self_update_windows() and is_zip:
            started, launcher_detail = launch_windows_zip_updater(
                archive_path=downloaded_file,
                app_pid=int(QApplication.applicationPid()),
                target_dir=Path(sys.executable).resolve().parent,
                executable_path=Path(sys.executable).resolve(),
            )
            if not started:
                self._set_update_settings_status("Installer launch failed.", checking=False)
                self._show_warning_dialog(
                    "Update Install Failed",
                    launcher_detail or "Could not launch update installer.",
                )
                return

            self._set_update_settings_status("Installing update and restarting...", checking=False)
            message_lines = [
                "The updater was launched in a separate window.",
                "",
                "The app will close now and restart after files are replaced.",
            ]
            if launcher_detail:
                message_lines.extend(["", launcher_detail])
            self._show_info_dialog("Installing Update", "\n".join(message_lines))
            self.close()
            return

        if info.release_url:
            QDesktopServices.openUrl(QUrl(info.release_url))

        if is_packaged_runtime():
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "Automatic install is currently supported for Windows .zip release assets only.\n"
                "Please install this release manually."
            )
        else:
            guidance = (
                f"Update downloaded to:\n{downloaded_file}\n\n"
                "You are running from source, so auto-replace is skipped.\n"
                "Use the GitHub release page to deploy your next build."
            )
        self._show_info_dialog("Manual Update Required", guidance)

    def set_command_runtime(self, runtime: CommandRuntime) -> None:
        self._command_runtime = runtime
        runtime.configure_shortcut(
            enabled=self._palette_shortcut_enabled,
            shortcut_text=self._palette_shortcut_keybind,
        )
        self._palette_shortcut_enabled = runtime.shortcut_enabled
        self._palette_shortcut_keybind = runtime.shortcut_text

    def build_command_context(self) -> AppCommandContext:
        return AppCommandContext(
            open_settings_dialog=self.open_settings_dialog,
            close_settings_dialog=self.close_settings_dialog,
            is_settings_dialog_open=self.is_settings_dialog_open,
            minimize_window=self.showMinimized,
            close_app=self.close,
            expand_window=self.expand_window,
            shrink_window=self.shrink_window,
        )

    def open_settings_dialog(self) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            dialog = SettingsDialog(
                self._plugin_manager,
                parent=self,
                dark_mode_enabled=self._dark_mode_enabled,
                on_dark_mode_changed=self._on_dark_mode_changed,
                palette_shortcut_enabled=self._palette_shortcut_enabled,
                palette_shortcut_keybind=self._palette_shortcut_keybind,
                on_palette_shortcut_changed=self._on_palette_shortcut_changed,
                data_storage_folder=str(self._data_storage_folder),
                on_data_storage_folder_changed=self._on_data_storage_folder_changed,
                app_version=self._app_version,
                on_check_updates_requested=self._on_check_updates_requested,
            )
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.plugins_changed.connect(self._sync_background_from_plugins)
            dialog.finished.connect(self._on_settings_dialog_finished)
            self._settings_dialog = dialog

        mode = "dark" if self._dark_mode_enabled else "light"
        dialog.set_theme_mode(mode)
        dialog.set_update_check_running(False)
        source = self._auto_update_github_repo or "not configured"
        dialog.set_update_status(f"Current version: {self._app_version} • Source: {source}")
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._state_streamer.record("settings.opened", source="main_window", payload={})

    def close_settings_dialog(self) -> bool:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return False
        dialog.close()
        return True

    def is_settings_dialog_open(self) -> bool:
        dialog = self._settings_dialog
        return dialog is not None and dialog.isVisible()

    def expand_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        if self.isFullScreen():
            self.showNormal()
        if not self.isMaximized():
            self.showMaximized()

    def shrink_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
            return
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
            return

        current_w = max(1, self.width())
        current_h = max(1, self.height())
        target_w = max(self.minimumWidth(), int(round(current_w * 0.9)))
        target_h = max(self.minimumHeight(), int(round(current_h * 0.9)))
        if target_w == current_w and target_h == current_h:
            return
        self.resize(target_w, target_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_foreground_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_foreground_layout)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_inline_form_changes(action_label="Exit App"):
            event.ignore()
            return
        if not self._confirm_discard_admin_view_changes(action_label="Exit App"):
            event.ignore()
            return
        if not self._confirm_discard_template_changes(action_label="Exit App"):
            event.ignore()
            return
        self._persist_tracker_data(show_error_dialog=False)
        dialog = self._settings_dialog
        if dialog is not None:
            dialog.close()
        self._plugin_bridge.shutdown()
        self._plugin_api.shutdown()
        self._plugin_manager.shutdown()
        self._state_streamer.record("window.closed", source="main_window", payload={})
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scene_widget and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        ):
            QTimer.singleShot(0, self._sync_foreground_layout)
        return super().eventFilter(watched, event)

    def _on_settings_dialog_finished(self, _result: int) -> None:
        dialog = self._settings_dialog
        self._settings_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        self._state_streamer.record("settings.closed", source="main_window", payload={})
        self._sync_background_from_plugins()

    def _on_dark_mode_changed(self, enabled: bool) -> None:
        self._dark_mode_enabled = bool(enabled)
        save_dark_mode(self._dark_mode_enabled)
        mode = "dark" if self._dark_mode_enabled else "light"
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, mode=mode)
        self.set_theme_mode(mode)
        self.setWindowIcon(QIcon(icon_asset_path("resize_handle.png", mode=mode)))
        if self._settings_dialog is not None:
            self._settings_dialog.set_theme_mode(mode)
        # Rebuild permit workspace visuals so timeline connector arrows swap
        # between black/white assets immediately on theme change.
        self._refresh_selected_permit_view()
        self._refresh_document_templates_view()
        self._apply_settings_button_effect()
        self._sync_foreground_layout()
        self._state_streamer.record("theme.changed", source="main_window", payload={"mode": mode})

    def _on_palette_shortcut_changed(self, enabled: bool, keybind: str) -> None:
        self._palette_shortcut_enabled = bool(enabled)
        self._palette_shortcut_keybind = (
            keybind.strip() if isinstance(keybind, str) else ""
        ) or DEFAULT_PALETTE_SHORTCUT

        runtime = self._command_runtime
        if runtime is not None:
            runtime.configure_shortcut(
                enabled=self._palette_shortcut_enabled,
                shortcut_text=self._palette_shortcut_keybind,
            )
            self._palette_shortcut_enabled = runtime.shortcut_enabled
            self._palette_shortcut_keybind = runtime.shortcut_text

        save_palette_shortcut_settings(
            self._palette_shortcut_enabled,
            self._palette_shortcut_keybind,
        )
        self._state_streamer.record(
            "palette.shortcut_settings_changed",
            source="main_window",
            payload={
                "enabled": self._palette_shortcut_enabled,
                "keybind": self._palette_shortcut_keybind,
            },
        )

    def _sync_background_from_plugins(self) -> None:
        self._persist_active_plugins()
        background_url = self._plugin_manager.active_background_url()

        if self._settings_button is not None:
            active_count = len(self._plugin_manager.active_plugin_ids)
            self._settings_button.setToolTip(f"{active_count} plugin(s) active")

        if self._stack is None or self._fallback_widget is None:
            return

        if background_url and self._background_view is not None:
            self._stack.setCurrentWidget(self._background_view)
            self._sync_foreground_layout()
            self._schedule_background_url_load(background_url)
            QTimer.singleShot(0, self._restore_settings_dialog_z_order)
            return

        self._current_background_url = None
        self._stack.setCurrentWidget(self._fallback_widget)
        self._sync_foreground_layout()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_active_plugins(self) -> None:
        for plugin_id in load_active_plugin_ids(default=()):
            if self._plugin_manager.get_plugin(plugin_id) is None:
                continue
            try:
                self._plugin_manager.activate(plugin_id)
            except Exception:
                continue

    def _persist_active_plugins(self) -> None:
        save_active_plugin_ids(self._plugin_manager.active_plugin_ids)

    def _schedule_background_url_load(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        QTimer.singleShot(0, lambda url=background_url: self._load_background_url(url))

    def _load_background_url(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        if self._plugin_manager.active_background_url() != background_url:
            return
        self._background_view.setUrl(QUrl(background_url))
        self._current_background_url = background_url

    def _on_background_load_finished(self, _ok: bool) -> None:
        self._raise_foreground_widgets()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_settings_dialog_z_order(self) -> None:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return
        try:
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            return

    def _position_settings_button(self) -> None:
        if not self._settings_button or not self._scene_widget:
            return
        if self._scene_widget.width() <= 0 or self._scene_widget.height() <= 0:
            return
        margin = 16
        self._settings_button.adjustSize()
        x = margin
        y = max(margin, self._scene_widget.height() - self._settings_button.height() - margin)
        self._settings_button.move(x, y)
        if not self._settings_button.isVisible():
            self._settings_button.show()

    def _position_tracker_panels(self) -> None:
        if self._panel_host is None or self._scene_widget is None:
            return

        scene_width = self._scene_widget.width()
        scene_height = self._scene_widget.height()
        if scene_width <= 0 or scene_height <= 0:
            return

        desired_width = max(760, int(scene_width * 0.95))
        desired_height = max(520, int(scene_height * 0.86))

        content_width = min(desired_width, max(1, scene_width - 12))
        content_height = min(desired_height, max(1, scene_height - 12))

        x = max(0, int((scene_width - content_width) / 2))
        y = max(0, int((scene_height - content_height) / 2))
        self._panel_host.setGeometry(x, y, int(content_width), int(content_height))
        if not self._panel_host.isVisible():
            self._panel_host.show()

    def _raise_foreground_widgets(self) -> None:
        if self._panel_host is not None:
            self._panel_host.raise_()
        if self._settings_button is not None:
            self._settings_button.raise_()
        resize_handle = getattr(self, "_resize_handle", None)
        if resize_handle is not None:
            try:
                resize_handle.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
                resize_handle.raise_()
            except Exception:
                pass

    def _sync_foreground_layout(self) -> None:
        self._position_tracker_panels()
        self._sync_admin_editor_field_widths()
        self._sync_inline_form_card_widths()
        self._sync_permit_workspace_blur_overlay()
        self._position_settings_button()
        self._raise_foreground_widgets()

    def _sync_inline_form_card_widths(self) -> None:
        host = self._panel_host
        if host is None:
            return
        host_width = max(1, host.width())
        max_allowed = max(320, host_width - 56)
        target_width = max(360, int(round(host_width * 0.35)))
        target_width = min(target_width, 720, max_allowed)
        for card in self._inline_form_cards:
            if card is None:
                continue
            if card.width() == target_width and card.minimumWidth() == target_width:
                continue
            card.setMinimumWidth(target_width)
            card.setMaximumWidth(target_width)
        self._set_admin_entity_color_picker_open(
            "property",
            self._add_property_color_picker_open,
            animate=False,
        )

    def _apply_settings_button_effect(self) -> None:
        if self._settings_button is None:
            return
        shadow = self._settings_button_shadow
        if shadow is None:
            shadow = QGraphicsDropShadowEffect(self._settings_button)
            self._settings_button.setGraphicsEffect(shadow)
            self._settings_button_shadow = shadow

        if self._dark_mode_enabled:
            shadow.setBlurRadius(34.0)
            shadow.setOffset(0.0, 7.0)
            shadow.setColor(QColor(33, 109, 185, 138))
            return

        shadow.setBlurRadius(28.0)
        shadow.setOffset(0.0, 5.0)
        shadow.setColor(QColor(86, 150, 208, 112))



def run(argv: Sequence[str] | None = None) -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv or sys.argv))

    app.setApplicationName("erpermitsys")
    app.setOrganizationName("Bellboard")
    app.setApplicationVersion(APP_VERSION)

    state_streamer = StateStreamer()
    dark_mode_enabled = load_dark_mode(default=False)
    palette_shortcut_enabled = load_palette_shortcut_enabled(default=True)
    palette_shortcut_keybind = load_palette_shortcut_keybind(default=DEFAULT_PALETTE_SHORTCUT)
    theme_mode = "dark" if dark_mode_enabled else "light"
    apply_app_theme(app, mode=theme_mode)
    state_streamer.record(
        "app.started",
        source="app.main",
        payload={"theme_mode": theme_mode, "app_version": APP_VERSION},
    )

    window = ErPermitSysWindow(
        dark_mode_enabled=dark_mode_enabled,
        palette_shortcut_enabled=palette_shortcut_enabled,
        palette_shortcut_keybind=palette_shortcut_keybind,
        state_streamer=state_streamer,
    )
    command_runtime = CommandRuntime(
        app=app,
        context_provider=window.build_command_context,
        event_streamer=state_streamer,
        shortcut_enabled=palette_shortcut_enabled,
        shortcut_text=palette_shortcut_keybind,
        anchor_provider=lambda: window,
    )
    window.set_command_runtime(command_runtime)
    save_palette_shortcut_settings(
        command_runtime.shortcut_enabled,
        command_runtime.shortcut_text,
    )
    state_streamer.snapshot(
        "app.session",
        data={
            "window_title": window.windowTitle(),
            "theme_mode": theme_mode,
            "app_version": APP_VERSION,
        },
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from erpermitsys.app.tracker_models import (
    PERMIT_EVENT_TYPES,
    ContactRecord,
    JurisdictionRecord,
    PermitEventRecord,
    PermitRecord,
    PropertyRecord,
    build_default_document_slots,
    build_document_folders_from_slots,
    event_type_label,
    normalize_event_type,
    normalize_list_color,
    normalize_parcel_id,
    normalize_permit_type,
)
from erpermitsys.app.permit_workspace_helpers import (
    PERMIT_TYPE_OPTIONS as _PERMIT_TYPE_OPTIONS,
    extract_due_from_next_action_detail as _extract_due_from_next_action_detail,
    join_multi_values as _join_multi_values,
    next_action_detail_text as _next_action_detail_text,
    parse_multi_values as _parse_multi_values,
    prefill_permit_events_from_milestones as _prefill_permit_events_from_milestones,
    today_iso as _today_iso,
)
from erpermitsys.ui.window.app_dialogs import AppConfirmDialog, AppMessageDialog
from erpermitsys.ui.window.frameless_dialog import FramelessDialog


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



__all__ = [
    "PropertyEditorDialog",
    "PermitEditorDialog",
    "NextActionDialog",
    "NextActionTimelineEntryDialog",
    "PermitEventDialog",
    "TimelineEventEditDialog",
]

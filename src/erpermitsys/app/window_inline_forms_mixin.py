from __future__ import annotations

from typing import Sequence
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.permit_workspace_helpers import (
    PERMIT_TYPE_OPTIONS as _PERMIT_TYPE_OPTIONS,
    join_multi_values as _join_multi_values,
    parse_multi_values as _parse_multi_values,
    prefill_permit_events_from_milestones as _prefill_permit_events_from_milestones,
)
from erpermitsys.app.document_template_constants import TEMPLATE_DEFAULT_SENTINEL
from erpermitsys.app.tracker_models import (
    ContactRecord,
    PermitParty,
    PermitRecord,
    PropertyRecord,
    build_document_folders_from_slots,
    build_document_slots_from_template,
    compute_permit_status,
    ensure_default_document_structure,
    normalize_list_color,
    normalize_parcel_id,
    normalize_permit_type,
)
from erpermitsys.app.window_admin_shared import _ADMIN_LIST_COLOR_PRESETS
from erpermitsys.ui.widgets import AttachedContactChip, EdgeLockedScrollArea


class WindowInlineFormsMixin:
    def _build_inline_form_view(self, parent: QWidget) -> tuple[QWidget, QFrame, QVBoxLayout]:
        view = QWidget(parent)
        view.setObjectName("PermitFormView")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = EdgeLockedScrollArea(view)
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

        attached_contacts_scroll = EdgeLockedScrollArea(attached_panel)
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

        attached_contacts_scroll = EdgeLockedScrollArea(attached_panel)
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
            else TEMPLATE_DEFAULT_SENTINEL
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
                    selected_template_id=preferred_template_id or TEMPLATE_DEFAULT_SENTINEL
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
                    selected_template_id=TEMPLATE_DEFAULT_SENTINEL
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

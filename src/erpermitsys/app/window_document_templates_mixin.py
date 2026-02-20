from __future__ import annotations

from typing import Sequence
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.document_template_constants import (
    TEMPLATE_BUILTIN_BUILDING_ID,
    TEMPLATE_BUILTIN_DEMOLITION_ID,
    TEMPLATE_DEFAULT_SENTINEL,
)
from erpermitsys.app.tracker_models import (
    DocumentChecklistTemplate,
    PermitDocumentFolder,
    PermitDocumentSlot,
    build_default_document_slots,
    build_document_slots_from_template,
    normalize_permit_type,
    normalize_slot_id,
    refresh_slot_status_from_documents,
)
from erpermitsys.ui.widgets import DocumentChecklistSlotCard, EdgeLockedScrollArea


def _permit_type_label(permit_type: str) -> str:
    normalized = normalize_permit_type(permit_type)
    if normalized == "demolition":
        return "Demolition"
    if normalized == "remodeling":
        return "Remodeling"
    return "Building"


class WindowDocumentTemplatesMixin:
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

        template_scroll = EdgeLockedScrollArea(right_host)
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
        return target in {TEMPLATE_BUILTIN_BUILDING_ID, TEMPLATE_BUILTIN_DEMOLITION_ID}

    def _builtin_template_by_id(self, template_id: str) -> DocumentChecklistTemplate | None:
        target = str(template_id or "").strip()
        if target == TEMPLATE_BUILTIN_BUILDING_ID:
            return DocumentChecklistTemplate(
                template_id=target,
                name="Built-in Default: Building / Remodeling",
                permit_type="building",
                slots=build_default_document_slots("building"),
                notes="View-only built-in template used by Building and Remodeling permits.",
            )
        if target == TEMPLATE_BUILTIN_DEMOLITION_ID:
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
                TEMPLATE_BUILTIN_BUILDING_ID,
                "Built-in Default: Building / Remodeling",
                "Building + Remodeling",
                "building",
                build_default_document_slots("building"),
            ),
            (
                TEMPLATE_BUILTIN_DEMOLITION_ID,
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
        combo.addItem("Built-in Default", TEMPLATE_DEFAULT_SENTINEL)
        for template in sorted(
            (row for row in self._document_templates if normalize_permit_type(row.permit_type) == permit_type),
            key=lambda row: (row.name.casefold(), row.template_id),
        ):
            combo.addItem(template.name or "(Unnamed Template)", template.template_id)

        desired_id = current_id or default_id or TEMPLATE_DEFAULT_SENTINEL
        desired_index = combo.findData(desired_id)
        if desired_index < 0:
            desired_index = combo.findData(TEMPLATE_DEFAULT_SENTINEL)
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
            TEMPLATE_DEFAULT_SENTINEL,
        )
        for template in sorted(
            (row for row in self._document_templates if normalize_permit_type(row.permit_type) == permit_type),
            key=lambda row: (row.name.casefold(), row.template_id),
        ):
            combo.addItem(template.name or "(Unnamed Template)", template.template_id)

        desired_id = current_id or TEMPLATE_DEFAULT_SENTINEL
        desired_index = combo.findData(desired_id)
        if desired_index < 0:
            desired_index = combo.findData(TEMPLATE_DEFAULT_SENTINEL)
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
        if selected_id and selected_id != TEMPLATE_DEFAULT_SENTINEL:
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
        if selected_id and selected_id != TEMPLATE_DEFAULT_SENTINEL:
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

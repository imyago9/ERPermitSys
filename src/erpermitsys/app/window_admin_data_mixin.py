from __future__ import annotations

from typing import Sequence
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidgetItem, QVBoxLayout

from erpermitsys.app.permit_workspace_helpers import (
    join_multi_values as _join_multi_values,
    parse_multi_values as _parse_multi_values,
)
from erpermitsys.app.tracker_models import (
    ContactMethodRecord,
    ContactRecord,
    JurisdictionRecord,
    normalize_list_color,
)
from erpermitsys.app.window_admin_shared import _ADMIN_LIST_COLOR_PRESETS
from erpermitsys.ui.widgets import AttachedContactChip


class WindowAdminDataMixin:
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
